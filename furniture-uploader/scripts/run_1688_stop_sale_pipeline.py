from __future__ import annotations

import argparse
from contextlib import nullcontext
import json
import os
import re
import signal
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Callable


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_JUSHUITAN_ROOT = PROJECT_ROOT.parent / "jushuitan-sku-offline-batch"
DEFAULT_SHARED_RUNTIME_ROOT = Path(os.environ.get("SCRIPT_1688_ROOT", "D:/script_1688"))
DEFAULT_JST_LOGIN_URL = "https://www.erp321.com/login.aspx"
DEFAULT_JST_PRODUCT_URL = "https://www.erp321.com/epaas"
RPA_ROOT = PROJECT_ROOT / "rpa"
if str(RPA_ROOT) not in sys.path:
    sys.path.insert(0, str(RPA_ROOT))

from config_loader import load_json_with_local_override
from sku_offline_tasks import dedupe_offline_tasks, filter_offline_tasks, load_offline_tasks
from stop_sale_audit import (
    StopSaleAuditRepository,
    hydrate_dingtalk_credentials,
    load_jsonl_records,
    resolve_stop_sale_app_config,
    stop_sale_task_key,
)


class PipelineStageTimeoutError(TimeoutError):
    def __init__(self, stage: str, timeout_seconds: int) -> None:
        self.stage = stage
        self.timeout_seconds = timeout_seconds
        super().__init__(f"{stage} stage exceeded {timeout_seconds} seconds and its process tree was stopped")


def terminate_stage_process_tree(process: subprocess.Popen[Any]) -> None:
    if process.poll() is not None:
        return
    if sys.platform == "win32":
        subprocess.run(
            ["taskkill.exe", "/PID", str(process.pid), "/T", "/F"],
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )
    else:
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except (ProcessLookupError, PermissionError):
            process.terminate()
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


def run_stage_command(
    command: list[str],
    *,
    cwd: Path,
    timeout_seconds: int,
    stage: str,
    env: dict[str, str] | None = None,
    heartbeat: Callable[[], None] | None = None,
    heartbeat_interval_seconds: float = 30.0,
) -> subprocess.CompletedProcess[Any]:
    if heartbeat is not None and heartbeat_interval_seconds <= 0:
        raise ValueError("heartbeat_interval_seconds must be positive")

    popen_options: dict[str, Any] = {
        "cwd": cwd,
        "env": env,
    }
    if sys.platform == "win32":
        popen_options["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        popen_options["start_new_session"] = True
    process = subprocess.Popen(command, **popen_options)
    if heartbeat is None:
        try:
            return_code = process.wait(timeout=timeout_seconds)
        except subprocess.TimeoutExpired as exc:
            terminate_stage_process_tree(process)
            raise PipelineStageTimeoutError(stage, timeout_seconds) from exc
        return subprocess.CompletedProcess(command, return_code)

    deadline = time.monotonic() + timeout_seconds
    while True:
        remaining_seconds = deadline - time.monotonic()
        if remaining_seconds <= 0:
            terminate_stage_process_tree(process)
            raise PipelineStageTimeoutError(stage, timeout_seconds)
        try:
            return_code = process.wait(
                timeout=min(float(heartbeat_interval_seconds), remaining_seconds)
            )
            break
        except subprocess.TimeoutExpired as exc:
            if time.monotonic() >= deadline:
                terminate_stage_process_tree(process)
                raise PipelineStageTimeoutError(stage, timeout_seconds) from exc
            try:
                heartbeat()
            except Exception:
                terminate_stage_process_tree(process)
                raise
    return subprocess.CompletedProcess(command, return_code)


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="1688 SKU offline + Jushuitan link cleanup pipeline")
    parser.add_argument("--file", required=True, help="CSV/XLSX stop-sale input file")
    parser.add_argument("--mode", choices=("preview", "execute"), default="preview")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument(
        "--run-id",
        default="",
        help="Optional externally assigned run ID for scheduler and audit correlation.",
    )
    parser.add_argument("--yes", action="store_true", help="Required for live execute mode")
    login_mode = parser.add_mutually_exclusive_group()
    login_mode.add_argument(
        "--skip-login",
        dest="skip_login",
        action="store_true",
        default=True,
        help="Reuse the mapped logged-in Profile (default for the production pipeline).",
    )
    login_mode.add_argument(
        "--require-manual-login",
        dest="skip_login",
        action="store_false",
        help="Run the legacy interactive login prompt before opening product management.",
    )
    parser.add_argument("--no-notify", action="store_true")
    parser.add_argument("--jushuitan-root", default=str(DEFAULT_JUSHUITAN_ROOT))
    parser.add_argument("--shared-runtime-root", default=str(DEFAULT_SHARED_RUNTIME_ROOT))
    parser.add_argument("--shared-lock-path", default="")
    parser.add_argument("--lock-wait-seconds", type=int, default=7200)
    parser.add_argument("--lock-stale-seconds", type=int, default=21600)
    parser.add_argument("--lock-poll-seconds", type=float, default=10.0)
    parser.add_argument(
        "--active-stop-sale-max-age-minutes",
        type=int,
        default=240,
        help="Block cross-machine execution while another recent audit run is still running.",
    )
    parser.add_argument(
        "--1688-timeout-seconds",
        dest="timeout_1688_seconds",
        type=int,
        default=2700,
        help="Maximum total runtime for the 1688 subprocess before its owned process tree is stopped.",
    )
    parser.add_argument(
        "--jushuitan-timeout-seconds",
        dest="timeout_jushuitan_seconds",
        type=int,
        default=1200,
        help="Maximum total runtime for the Jushuitan subprocess before its owned process tree is stopped.",
    )
    parser.add_argument(
        "--crawler-worker-task-name",
        default="YYDD-1688-Crawler-Worker",
        help="Scheduled crawler Worker that must be paused before execute mode.",
    )
    parser.add_argument(
        "--source-database",
        default=(
            os.getenv("STOP_SALE_SOURCE_SQLSERVER_DATABASE")
            or os.getenv("STOP_SALE_SQLSERVER_DATABASE")
            or "JSDataMiddlePlatform"
        ),
        help="Read-only business source database recorded in the audit trail.",
    )
    parser.add_argument(
        "--source-table",
        default=os.getenv("STOP_SALE_SOURCE_TABLE", "dbo.op_stop_sale"),
        help="Read-only business source table recorded in the audit trail.",
    )
    parser.add_argument(
        "--no-shared-lock",
        action="store_true",
        help="Disable the shared D:/script_1688 lock. Only safe on an isolated machine.",
    )
    return parser


def build_1688_command(args: argparse.Namespace, handoff_path: Path) -> list[str]:
    command = [
        sys.executable,
        str(PROJECT_ROOT / "rpa" / "sku_offline_main.py"),
        "--mode",
        args.mode,
        "--file",
        str(Path(args.file).resolve()),
        "--jushuitan-handoff-out",
        str(handoff_path),
    ]
    run_id = str(getattr(args, "run_id", "") or "").strip()
    if run_id:
        command.extend(["--run-id", run_id])
    if args.limit > 0:
        command.extend(["--limit", str(args.limit)])
    if args.mode == "execute":
        command.append("--yes")
    if args.skip_login:
        command.append("--skip-login")
    if args.no_notify:
        command.append("--no-notify")
    return command


def build_jushuitan_command(
    args: argparse.Namespace,
    handoff_path: Path,
    jushuitan_root: Path,
    results_dir: Path | None = None,
) -> list[str]:
    command = [
        "npm.cmd" if sys.platform == "win32" else "npm",
        "run",
        "cleanup:1688",
        "--",
        "--mode",
        args.mode,
        "--file",
        str(handoff_path),
    ]
    run_id = str(getattr(args, "run_id", "") or "").strip()
    if run_id:
        command.extend(["--run-id", run_id])
    if results_dir is not None:
        command.extend(["--results-dir", str(results_dir)])
    if args.mode == "execute":
        command.append("--yes")
    if args.no_notify:
        command.append("--no-notify")
    return command


def build_jushuitan_environment(handoff_path: Path) -> dict[str, str]:
    environment = os.environ.copy()
    environment.setdefault("JST_LOGIN_URL", DEFAULT_JST_LOGIN_URL)
    environment.setdefault("JST_PRODUCT_URL", DEFAULT_JST_PRODUCT_URL)
    # The cleanup CLI consumes JSONL directly; this only satisfies the shared legacy config schema.
    environment["EXCEL_PATH"] = str(handoff_path.resolve())
    return environment


def count_handoff_records(path: Path) -> int:
    if not path.exists():
        return 0
    return sum(1 for line in path.read_text(encoding="utf-8").splitlines() if line.strip())


def load_selected_audit_tasks(args: argparse.Namespace) -> list[Any]:
    system_config = load_json_with_local_override(
        PROJECT_ROOT / "config" / "systems" / "1688_sku_offline.json"
    )
    input_config = dict(system_config.get("input", {}))
    tasks = load_offline_tasks(Path(args.file).resolve(), input_config)
    selected, _ = filter_offline_tasks(tasks, dict(input_config.get("filters", {})))
    deduped, duplicates = dedupe_offline_tasks(selected)
    limited = deduped[: args.limit] if args.limit > 0 else deduped
    selected_keys = {task.dedupe_key for task in limited}
    candidates = [
        *limited,
        *[task for task in duplicates if task.dedupe_key in selected_keys],
    ]
    by_audit_key = {
        stop_sale_task_key(
            task.store_name,
            task.product_id,
            task.online_sku,
            task.platform_store_item_code,
        ): task
        for task in candidates
    }
    return list(by_audit_key.values())


def derive_audit_status(
    *,
    offline_return_code: int,
    jushuitan_return_code: int | None,
    offline_records: list[dict[str, Any]],
    expected_count: int = 0,
) -> str:
    successful = sum(
        1 for record in offline_records if record.get("status") in {"success", "already_offline"}
    )
    failed = sum(1 for record in offline_records if record.get("status") == "failed")
    missing = max(0, int(expected_count) - len(offline_records))
    if failed > 0 or missing > 0:
        return "partial" if successful > 0 else "failed"
    if offline_return_code == 0 and (jushuitan_return_code is None or jushuitan_return_code == 0):
        return "success"
    return "partial" if successful > 0 else "failed"


def send_pipeline_notification(content: str, *, disabled: bool) -> bool:
    if disabled:
        return False
    rpa_root = PROJECT_ROOT / "rpa"
    if str(rpa_root) not in sys.path:
        sys.path.insert(0, str(rpa_root))
    from dingtalk import post_dingtalk_text_message

    return post_dingtalk_text_message(
        {
            "notifications": {
                "dingtalk": {
                    "enabled": True,
                    "webhook_env": "DINGTALK_WEBHOOK",
                    "secret_env": "DINGTALK_SECRET",
                }
            }
        },
        content,
    )


def notify_execute_startup_failure(
    *,
    run_id: str,
    exc: Exception,
    disabled: bool,
) -> None:
    if disabled:
        return
    message = str(exc).replace("\r", " ").replace("\n", " ").strip()[:500]
    send_pipeline_notification(
        "【1688 停产下架】\n"
        f"批次：{run_id}\n"
        "状态：未启动或安全阻止\n"
        f"原因：{type(exc).__name__}: {message}\n"
        "处理：未执行线上商品操作，请查看审计和执行日志\n"
        "退出码：1",
        disabled=False,
    )


def query_crawler_worker_state(task_name: str) -> dict[str, Any]:
    if sys.platform != "win32":
        return {
            "scheduled_task_exists": False,
            "scheduled_task_state": "",
            "worker_process_count": 0,
            "profile_edge_process_count": 0,
        }
    escaped_task_name = str(task_name).replace("'", "''")
    script = (
        f"$task = Get-ScheduledTask -TaskName '{escaped_task_name}' -ErrorAction SilentlyContinue; "
        "$processes = @(Get-CimInstance Win32_Process | Where-Object { "
        "$_.Name -in @('python.exe','pythonw.exe') -and $_.CommandLine -and "
        "$_.CommandLine.Contains('run_crawler_task_worker.py') }); "
        "$profileEdges = @(Get-CimInstance Win32_Process | Where-Object { "
        "$_.Name -eq 'msedge.exe' -and $_.CommandLine -and "
        "$_.CommandLine -match 'YYDD[\\/]1688-crawler[\\/]profiles' }); "
        "[ordered]@{ scheduled_task_exists = ($null -ne $task); "
        "scheduled_task_state = if ($null -ne $task) { [string]$task.State } else { '' }; "
        "worker_process_count = $processes.Count; "
        "profile_edge_process_count = $profileEdges.Count } | ConvertTo-Json -Compress"
    )
    result = subprocess.run(
        ["powershell.exe", "-NoProfile", "-Command", script],
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    if result.returncode != 0:
        raise RuntimeError("Unable to verify the scheduled crawler Worker state.")
    lines = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    if not lines:
        raise RuntimeError("Scheduled crawler Worker state returned no result.")
    payload = json.loads(lines[-1])
    return payload if isinstance(payload, dict) else {}


def assert_crawler_worker_paused(task_name: str) -> dict[str, Any]:
    state = query_crawler_worker_state(task_name)
    task_running = str(state.get("scheduled_task_state") or "").strip().lower() == "running"
    process_count = int(state.get("worker_process_count") or 0)
    profile_edge_count = int(state.get("profile_edge_process_count") or 0)
    if task_running or process_count > 0 or profile_edge_count > 0:
        raise RuntimeError(
            "Crawler Worker or an account Profile Edge process is still active. Confirm no active crawler task, "
            "pause the scheduled Worker, and wait for its owned processes to exit before stop-sale execute."
        )
    return state


def assert_no_recent_stop_sale_runs(
    audit_repository: StopSaleAuditRepository,
    max_age_minutes: int,
) -> None:
    active_count = audit_repository.count_recent_active_stop_sale_runs(max_age_minutes)
    if active_count > 0:
        raise RuntimeError(
            f"Stop-sale audit has {active_count} recent running batch(es); "
            "cross-machine execute is blocked until they finish."
        )


def resolve_shared_lock_path(args: argparse.Namespace) -> Path:
    configured = str(args.shared_lock_path or "").strip()
    if configured:
        return Path(configured).resolve()
    return Path(args.shared_runtime_root).resolve() / "artifacts" / "locks" / "ali1688_full_cycle.lock"


def build_shared_lock(args: argparse.Namespace, run_id: str):
    if args.mode != "execute" or args.no_shared_lock:
        return nullcontext(), None, ""

    runtime_root = Path(args.shared_runtime_root).resolve()
    lock_module = runtime_root / "src" / "runtime" / "global_lock.py"
    if not lock_module.exists():
        raise FileNotFoundError(f"Shared 1688 runtime lock module not found: {lock_module}")
    if str(runtime_root) not in sys.path:
        sys.path.insert(0, str(runtime_root))

    from src.runtime.global_lock import GlobalFileLock, GlobalLockTimeoutError

    lock_path = resolve_shared_lock_path(args)
    lock = GlobalFileLock(
        lock_path,
        stale_after_seconds=args.lock_stale_seconds,
        wait_timeout_seconds=args.lock_wait_seconds,
        poll_interval_seconds=args.lock_poll_seconds,
        metadata={
            "cycle": "1688_stop_sale_pipeline",
            "run_id": run_id,
            "source": str(Path(args.file).resolve()),
        },
    )
    return lock, GlobalLockTimeoutError, str(lock_path)


def run_pipeline(
    args: argparse.Namespace,
    *,
    run_id: str,
    pipeline_dir: Path,
    jushuitan_root: Path,
    shared_lock_path: str,
    audit_repository: StopSaleAuditRepository | None = None,
    audit_tasks: list[Any] | None = None,
) -> int:
    handoff_path = pipeline_dir / f"{run_id}.jushuitan.jsonl"
    summary_path = pipeline_dir / f"{run_id}.summary.json"
    offline_report_path = PROJECT_ROOT / "logs" / "sku_offline" / "run_reports" / f"{run_id}.jsonl"
    jushuitan_results_dir = pipeline_dir / f"{run_id}.jushuitan-results"
    jushuitan_report_path = jushuitan_results_dir / f"{run_id}.jsonl"

    started_at = datetime.now().isoformat(timespec="seconds")
    audit_started = False
    result_1688_return_code = 1
    jushuitan_return_code: int | None = None
    offline_records: list[dict[str, Any]] = []
    error_message = ""
    pending_exception: Exception | None = None
    expected_count = (
        len({task.dedupe_key for task in (audit_tasks or [])})
        if args.mode == "execute"
        else 0
    )

    try:
        if args.mode == "execute":
            if audit_repository is None:
                raise RuntimeError("execute mode requires the JSReportReplica stop-sale audit repository")
            audit_repository.start_run(
                run_id=run_id,
                mode=args.mode,
                input_file=args.file,
                tasks=audit_tasks or [],
                source_database=str(args.source_database),
                source_table=str(args.source_table),
            )
            audit_started = True

        command_1688 = build_1688_command(args, handoff_path)
        audit_heartbeat = (
            (lambda: audit_repository.heartbeat_run(run_id))
            if audit_started and audit_repository is not None
            else None
        )
        result_1688 = run_stage_command(
            command_1688,
            cwd=PROJECT_ROOT,
            timeout_seconds=args.timeout_1688_seconds,
            stage="1688",
            heartbeat=audit_heartbeat,
        )
        result_1688_return_code = result_1688.returncode
        offline_records = load_jsonl_records(offline_report_path)
        if audit_started and audit_repository is not None:
            audit_repository.record_1688_results(run_id, offline_records)

        handoff_count = count_handoff_records(handoff_path)
        if result_1688_return_code == 0 and handoff_count > 0:
            command_jushuitan = build_jushuitan_command(
                args,
                handoff_path,
                jushuitan_root,
                jushuitan_results_dir,
            )
            result_jushuitan = run_stage_command(
                command_jushuitan,
                cwd=jushuitan_root,
                timeout_seconds=args.timeout_jushuitan_seconds,
                stage="jushuitan",
                env=build_jushuitan_environment(handoff_path),
                heartbeat=audit_heartbeat,
            )
            jushuitan_return_code = result_jushuitan.returncode
            if audit_started and audit_repository is not None:
                audit_repository.record_jushuitan_results(
                    run_id,
                    load_jsonl_records(jushuitan_report_path),
                )
    except Exception as exc:
        error_message = f"{type(exc).__name__}: {exc}"
        pending_exception = exc

    handoff_count = count_handoff_records(handoff_path)

    audit_status = derive_audit_status(
        offline_return_code=result_1688_return_code,
        jushuitan_return_code=jushuitan_return_code,
        offline_records=offline_records,
        expected_count=expected_count,
    )
    summary = {
        "run_id": run_id,
        "mode": args.mode,
        "input_file": str(Path(args.file).resolve()),
        "started_at": started_at,
        "finished_at": datetime.now().isoformat(timespec="seconds"),
        "1688_return_code": result_1688_return_code,
        "jushuitan_return_code": jushuitan_return_code,
        "jushuitan_handoff_path": str(handoff_path),
        "jushuitan_handoff_count": handoff_count,
        "shared_lock_path": shared_lock_path,
        "audit_database": (
            audit_repository.config.safe_dict() if audit_repository is not None else None
        ),
        "audit_status": audit_status if args.mode == "execute" else None,
        "error_type": type(pending_exception).__name__ if pending_exception is not None else "",
        "error_stage": (
            pending_exception.stage
            if isinstance(pending_exception, PipelineStageTimeoutError)
            else ""
        ),
        "1688_timeout_seconds": args.timeout_1688_seconds,
        "jushuitan_timeout_seconds": args.timeout_jushuitan_seconds,
        "summary_path": str(summary_path),
    }
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if audit_started and audit_repository is not None:
        audit_repository.finish_run(
            run_id=run_id,
            status=audit_status,
            offline_report_path=str(offline_report_path),
            jushuitan_report_path=(str(jushuitan_report_path) if jushuitan_report_path.exists() else ""),
            summary_path=str(summary_path),
            error_message=error_message,
        )
    print(json.dumps(summary, ensure_ascii=False, indent=2))

    if pending_exception is not None:
        raise pending_exception

    if result_1688_return_code != 0:
        return result_1688_return_code
    if args.mode == "execute" and audit_status != "success":
        return 2
    if handoff_count == 0:
        return 0
    return int(jushuitan_return_code or 0)


def main() -> int:
    args = build_argument_parser().parse_args()
    if args.run_id and not re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", str(args.run_id)):
        raise ValueError("--run-id must contain only letters, numbers, dot, underscore, or dash")
    if args.limit < 0:
        raise ValueError("--limit must be non-negative")
    if args.lock_wait_seconds < 0 or args.lock_stale_seconds <= 0 or args.lock_poll_seconds <= 0:
        raise ValueError("Shared lock timing values must be positive (wait may be zero)")
    if args.timeout_1688_seconds <= 0 or args.timeout_jushuitan_seconds <= 0:
        raise ValueError("Stage timeout values must be positive")
    if args.active_stop_sale_max_age_minutes <= 0:
        raise ValueError("--active-stop-sale-max-age-minutes must be positive")
    if args.mode == "execute" and not args.yes:
        raise ValueError("execute mode requires --yes")
    if args.mode == "execute" and not args.no_notify:
        dingtalk_credentials = hydrate_dingtalk_credentials(args.shared_runtime_root)
        if not all(dingtalk_credentials.values()):
            raise RuntimeError(
                "DingTalk credentials are missing from both the process environment and the 1688 Credential Manager."
            )

    jushuitan_root = Path(args.jushuitan_root).resolve()
    if not (jushuitan_root / "package.json").exists():
        raise FileNotFoundError(f"Jushuitan project not found: {jushuitan_root}")

    run_id = str(args.run_id or "").strip() or datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    args.run_id = run_id
    pipeline_dir = PROJECT_ROOT / "logs" / "sku_offline" / "pipelines"
    pipeline_dir.mkdir(parents=True, exist_ok=True)
    audit_repository: StopSaleAuditRepository | None = None
    audit_tasks: list[Any] = []
    if args.mode == "execute":
        try:
            audit_config = resolve_stop_sale_app_config(args.shared_runtime_root)
            audit_repository = StopSaleAuditRepository(audit_config)
            contract = audit_repository.check_contract()
            if not contract["ready"]:
                raise RuntimeError(
                    "JSReportReplica stop-sale audit tables are missing: "
                    + ", ".join(contract["missing_tables"])
                )
            audit_tasks = load_selected_audit_tasks(args)
            if not audit_tasks:
                raise ValueError("No executable stop-sale tasks were selected for execute mode.")
        except Exception as exc:
            notify_execute_startup_failure(
                run_id=run_id,
                exc=exc,
                disabled=args.no_notify,
            )
            raise
    lock, timeout_error, lock_path = build_shared_lock(args, run_id)
    try:
        with lock:
            if args.mode == "execute":
                assert_crawler_worker_paused(args.crawler_worker_task_name)
                active_crawler_tasks = audit_repository.count_active_crawler_tasks() if audit_repository else 0
                if active_crawler_tasks > 0:
                    raise RuntimeError(
                        f"Crawler task center still has {active_crawler_tasks} active task(s); "
                        "stop-sale execute is blocked until they reach a terminal state."
                    )
                assert_no_recent_stop_sale_runs(
                    audit_repository,
                    args.active_stop_sale_max_age_minutes,
                )
            return run_pipeline(
                args,
                run_id=run_id,
                pipeline_dir=pipeline_dir,
                jushuitan_root=jushuitan_root,
                shared_lock_path=lock_path,
                audit_repository=audit_repository,
                audit_tasks=audit_tasks,
            )
    except Exception as exc:
        if timeout_error is not None and isinstance(exc, timeout_error):
            print(f"Shared 1688 runtime lock timed out: {lock_path}")
            send_pipeline_notification(
                "【1688 停产下架】\n"
                f"批次：{run_id}\n"
                "状态：未启动\n"
                "原因：其他 1688 任务仍在运行，共享浏览器资源占用\n"
                "处理：调度器稍后重试\n"
                "退出码：75",
                disabled=args.no_notify,
            )
            return 75
        if isinstance(exc, PipelineStageTimeoutError):
            send_pipeline_notification(
                "【1688 停产下架】\n"
                f"批次：{run_id}\n"
                "状态：执行超时\n"
                f"阶段：{exc.stage}\n"
                f"超时：{exc.timeout_seconds} 秒\n"
                "处理：仅终止本批次拥有的进程树，后续任务未继续\n"
                "退出码：124",
                disabled=args.no_notify,
            )
            return 124
        if args.mode == "execute":
            notify_execute_startup_failure(
                run_id=run_id,
                exc=exc,
                disabled=args.no_notify,
            )
        raise


if __name__ == "__main__":
    raise SystemExit(main())
