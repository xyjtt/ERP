"""Run and inspect the unattended daily 1688 stop-sale pipeline.

The Windows Task Scheduler wrapper calls this module.  It deliberately keeps
business-item failures separate from infrastructure failures: item failures
are audited and notified, while the remaining stores continue to run.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import subprocess
import sys
import time
from contextlib import contextmanager, nullcontext
from datetime import date, datetime
from pathlib import Path
from typing import Any, Iterator


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_ROOT = PROJECT_ROOT / "scripts"
DEFAULT_SHARED_RUNTIME_ROOT = Path(os.environ.get("SCRIPT_1688_ROOT", "D:/script_1688"))
DEFAULT_JUSHUITAN_ROOT = PROJECT_ROOT.parent / "jushuitan-sku-offline-batch"
DEFAULT_WORKER_TASK_NAME = "YYDD-1688-Crawler-Worker"
DEFAULT_MANAGER_TASK_NAME = "YYDD-1688-Stop-Sale-Daily"
DEFAULT_DAILY_TIME = "13:00"
DEFAULT_MANAGER_LOCK_STALE_SECONDS = 36 * 60 * 60

if str(PROJECT_ROOT / "rpa") not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT / "rpa"))
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

from dingtalk import post_dingtalk_text_message
from run_1688_stop_sale_pipeline import load_jsonl_records, query_crawler_worker_state
from stop_sale_audit import hydrate_dingtalk_credentials


class DailyManagerError(RuntimeError):
    pass


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Manage the daily 1688 stop-sale executor.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    run = subparsers.add_parser("run", help="Build today's input and execute all configured stores.")
    run.add_argument("--date", default=date.today().isoformat(), help="Metric date in YYYY-MM-DD format.")
    run.add_argument("--mode", choices=("preview", "execute"), default="preview")
    run.add_argument("--yes", action="store_true", help="Required for live execute mode.")
    run.add_argument("--store", action="append", dest="stores", help="Restrict the run to a store.")
    run.add_argument("--output-root", default=str(PROJECT_ROOT / "logs" / "sku_offline" / "scheduler"))
    run.add_argument("--shared-runtime-root", default=str(DEFAULT_SHARED_RUNTIME_ROOT))
    run.add_argument("--jushuitan-root", default=str(DEFAULT_JUSHUITAN_ROOT))
    run.add_argument("--worker-task-name", default=DEFAULT_WORKER_TASK_NAME)
    run.add_argument("--batch-size", type=int, default=25, help="Maximum input rows per recoverable store batch.")
    run.add_argument("--1688-timeout-seconds", dest="timeout_1688_seconds", type=int, default=10800)
    run.add_argument("--jushuitan-timeout-seconds", dest="timeout_jushuitan_seconds", type=int, default=7200)
    run.add_argument("--no-notify", action="store_true")

    return parser


def _validate_date(value: str) -> str:
    try:
        return date.fromisoformat(str(value).strip()).isoformat()
    except ValueError as exc:
        raise ValueError(f"Invalid --date: {value!r}; expected YYYY-MM-DD") from exc


def _safe_id(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]", "_", str(value or "").strip())[:64] or "unknown"


def _json_from_output(output: str) -> dict[str, Any]:
    decoder = json.JSONDecoder()
    matches = list(re.finditer(r"(?m)^\s*\{", str(output or "")))
    for match in reversed(matches):
        try:
            payload, _ = decoder.raw_decode(str(output)[match.start():])
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            return payload
    return {}


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def split_store_input(source: Path, output_dir: Path, batch_size: int) -> list[Path]:
    if batch_size <= 0:
        raise ValueError("--batch-size must be positive.")
    with source.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        fieldnames = list(reader.fieldnames or [])
        rows = list(reader)
    if not fieldnames:
        raise DailyManagerError(f"Stop-sale CSV has no header: {source}")
    if not rows:
        return []

    output_dir.mkdir(parents=True, exist_ok=True)
    paths: list[Path] = []
    for offset in range(0, len(rows), batch_size):
        batch_number = offset // batch_size + 1
        path = output_dir / f"batch_{batch_number:03d}.csv"
        with path.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows[offset: offset + batch_size])
        paths.append(path)
    return paths


def _load_preview_report(preview: dict[str, Any]) -> dict[str, Any]:
    """Use the full DB report when the command-line summary omits per-store files."""
    report_path_text = str(preview.get("report_path") or "").strip()
    if not report_path_text:
        return preview
    report_path = Path(report_path_text)
    if not report_path.is_absolute():
        report_path = PROJECT_ROOT / report_path
    try:
        report = json.loads(report_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return preview
    if not isinstance(report, dict):
        return preview
    merged = dict(report)
    merged.update(preview)
    return merged


def _run_capture(command: list[str], *, cwd: Path, log_path: Path) -> tuple[int, str]:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    completed = subprocess.run(
        command,
        cwd=cwd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        env=os.environ.copy(),
    )
    output = (completed.stdout or "") + ("\n" + completed.stderr if completed.stderr else "")
    log_path.write_text(output, encoding="utf-8")
    return completed.returncode, output


def _run_logged(command: list[str], *, cwd: Path, log_path: Path) -> int:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("w", encoding="utf-8") as handle:
        completed = subprocess.run(
            command,
            cwd=cwd,
            stdout=handle,
            stderr=subprocess.STDOUT,
            text=True,
            check=False,
            env=os.environ.copy(),
        )
    return completed.returncode


def build_preflight_command(args: argparse.Namespace) -> list[str]:
    return [
        sys.executable,
        str(SCRIPTS_ROOT / "preflight_1688_stop_sale_executor.py"),
        "--script-1688-root",
        str(Path(args.shared_runtime_root).resolve()),
        "--jushuitan-root",
        str(Path(args.jushuitan_root).resolve()),
    ]


def build_manager_lock(args: argparse.Namespace, manager_run_id: str):
    runtime_root = Path(args.shared_runtime_root).resolve()
    lock_module = runtime_root / "src" / "runtime" / "global_lock.py"
    if not lock_module.exists():
        raise FileNotFoundError(f"Shared 1688 runtime lock module not found: {lock_module}")
    if str(runtime_root) not in sys.path:
        sys.path.insert(0, str(runtime_root))

    from src.runtime.global_lock import GlobalFileLock

    lock_path = runtime_root / "artifacts" / "locks" / "ali1688_stop_sale_daily.lock"
    return GlobalFileLock(
        lock_path,
        stale_after_seconds=DEFAULT_MANAGER_LOCK_STALE_SECONDS,
        wait_timeout_seconds=0,
        poll_interval_seconds=1,
        metadata={
            "cycle": "1688_stop_sale_daily_manager",
            "manager_run_id": manager_run_id,
        },
    )


def build_preview_command(args: argparse.Namespace, output_dir: Path) -> list[str]:
    command = [
        sys.executable,
        str(SCRIPTS_ROOT / "build_1688_stop_sale_preview.py"),
        "--date",
        str(args.date),
        "--output-dir",
        str(output_dir.resolve()),
        "--limit",
        "0",
    ]
    for store in args.stores or []:
        command.extend(["--store", str(store)])
    return command


def build_pipeline_command(
    args: argparse.Namespace,
    input_file: Path,
    run_id: str,
) -> list[str]:
    return [
        sys.executable,
        str(SCRIPTS_ROOT / "run_1688_stop_sale_pipeline.py"),
        "--mode",
        "execute",
        "--file",
        str(input_file.resolve()),
        "--run-id",
        run_id,
        "--yes",
        "--skip-login",
        "--1688-timeout-seconds",
        str(args.timeout_1688_seconds),
        "--jushuitan-timeout-seconds",
        str(args.timeout_jushuitan_seconds),
        "--jushuitan-root",
        str(Path(args.jushuitan_root).resolve()),
        "--shared-runtime-root",
        str(Path(args.shared_runtime_root).resolve()),
        "--crawler-worker-task-name",
        str(args.worker_task_name),
    ]


def _powershell_task_action(action: str, task_name: str) -> None:
    if sys.platform != "win32":
        raise DailyManagerError("Worker lifecycle control is only supported on Windows.")
    escaped = str(task_name).replace("'", "''")
    script = f"{action}-ScheduledTask -TaskName '{escaped}' -ErrorAction Stop"
    result = subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        timeout=30,
    )
    if result.returncode != 0:
        raise DailyManagerError(f"Unable to {action.lower()} Worker scheduled task.")


def _wait_for_worker_quiet(task_name: str, timeout_seconds: int = 180) -> dict[str, Any]:
    deadline = time.time() + timeout_seconds
    last_state: dict[str, Any] = {}
    while time.time() < deadline:
        last_state = query_crawler_worker_state(task_name)
        if (
            str(last_state.get("scheduled_task_state") or "").lower() != "running"
            and int(last_state.get("worker_process_count") or 0) == 0
            and int(last_state.get("profile_edge_process_count") or 0) == 0
        ):
            return last_state
        time.sleep(2)
    raise DailyManagerError(f"Worker did not become idle before timeout: {last_state}")


@contextmanager
def paused_worker(task_name: str) -> Iterator[dict[str, Any]]:
    before = query_crawler_worker_state(task_name)
    if not before.get("scheduled_task_exists"):
        raise DailyManagerError(f"Worker scheduled task does not exist: {task_name}")

    original_state = str(before.get("scheduled_task_state") or "").strip().lower()
    if original_state != "disabled":
        _powershell_task_action("Disable", task_name)
    if original_state == "running" or int(before.get("worker_process_count") or 0) > 0:
        _powershell_task_action("Stop", task_name)

    paused = _wait_for_worker_quiet(task_name)
    lifecycle = {"before": before, "paused": paused, "restored": {}}
    try:
        yield lifecycle
    finally:
        restore_error = None
        try:
            if original_state != "disabled":
                _powershell_task_action("Enable", task_name)
            if original_state == "running":
                _powershell_task_action("Start", task_name)
            lifecycle["restored"] = query_crawler_worker_state(task_name)
        except Exception as exc:
            restore_error = str(exc)
        if restore_error:
            raise DailyManagerError(f"Worker restore failed: {restore_error}")


def _count_statuses(records: list[dict[str, Any]], status_key: str = "status") -> dict[str, int]:
    counts: dict[str, int] = {}
    for record in records:
        status = str(record.get(status_key) or "unknown").strip() or "unknown"
        counts[status] = counts.get(status, 0) + 1
    return counts


def _load_pipeline_result(project_root: Path, run_id: str, return_code: int) -> dict[str, Any]:
    summary_path = project_root / "logs" / "sku_offline" / "pipelines" / f"{run_id}.summary.json"
    summary: dict[str, Any] = {}
    if summary_path.exists():
        try:
            summary = json.loads(summary_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            summary = {}

    offline_path = project_root / "logs" / "sku_offline" / "run_reports" / f"{run_id}.jsonl"
    jushuitan_path = (
        project_root
        / "logs"
        / "sku_offline"
        / "pipelines"
        / f"{run_id}.jushuitan-results"
        / f"{run_id}.jsonl"
    )
    offline_records = load_jsonl_records(offline_path)
    jushuitan_records = load_jsonl_records(jushuitan_path)
    safety_categories = {
        "login_required",
        "risk_control",
        "store_mismatch",
        "identity_mismatch",
        "account_mapping",
        "browser_window_closed",
    }
    observed_categories = {
        str(record.get("error_category") or "").strip()
        for record in offline_records
        if str(record.get("error_category") or "").strip()
    }
    audit_status = str(summary.get("audit_status") or "").strip()
    if not summary:
        state = "failed"
    elif return_code == 0 and audit_status == "success":
        state = "success"
    else:
        state = "completed_with_exceptions"
    if return_code not in {0, 2}:
        state = "failed"
    return {
        "run_id": run_id,
        "return_code": return_code,
        "state": state,
        "audit_status": audit_status,
        "summary_path": str(summary_path),
        "offline_report_path": str(offline_path),
        "jushuitan_report_path": str(jushuitan_path) if jushuitan_path.exists() else "",
        "offline_counts": _count_statuses(offline_records),
        "jushuitan_counts": _count_statuses(jushuitan_records),
        "error_categories": sorted(observed_categories),
        "safety_stop": bool(observed_categories & safety_categories),
        "summary": summary,
    }


def _merge_counts(results: list[dict[str, Any]], field: str) -> dict[str, int]:
    merged: dict[str, int] = {}
    for result in results:
        for key, value in dict(result.get(field) or {}).items():
            merged[str(key)] = merged.get(str(key), 0) + int(value or 0)
    return dict(sorted(merged.items()))


def _manager_notification(summary: dict[str, Any]) -> str:
    lines = [
        "1688 停产下架每日任务",
        f"业务日期：{summary.get('business_date', '')}",
        f"批次：{summary.get('manager_run_id', '')}",
        f"状态：{summary.get('status', '')}",
        f"取数：{summary.get('selected_count', 0)} 条",
        f"店铺批次：{len(summary.get('stores', []))} 个",
        f"异常店铺批次：{summary.get('exception_store_count', 0)} 个",
        f"批次总数：{len(summary.get('batches', []))} 个",
        f"Worker：{summary.get('worker_status', '')}",
    ]
    for store in summary.get("stores", []):
        lines.append(
            f"{store.get('store_name', '')}：{store.get('state', '')}，"
            f"批次={store.get('completed_batch_count', 0)}/{store.get('batch_count', 0)}，"
            f"run_id={','.join(store.get('run_ids', [])) or '-'}"
        )
        for batch in store.get("batches", []):
            if batch.get("state") != "success":
                detail = str(
                    batch.get("summary", {}).get("error_message")
                    or batch.get("summary", {}).get("error_type")
                    or batch.get("state")
                )
                lines.append(f"  异常批次 {batch.get('run_id', '')}：{detail[:180]}")
    return "\n".join(lines)


def send_manager_notification(summary: dict[str, Any], shared_runtime_root: Path, disabled: bool) -> bool:
    if disabled:
        return False
    try:
        credentials = hydrate_dingtalk_credentials(shared_runtime_root)
        if not all(credentials.values()):
            return False
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
            _manager_notification(summary),
        )
    except Exception:
        return False


def run_daily(args: argparse.Namespace) -> tuple[int, dict[str, Any]]:
    args.date = _validate_date(args.date)
    manager_run_id = "daily_" + datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    manager_dir = Path(args.output_root).resolve() / manager_run_id
    manager_dir.mkdir(parents=True, exist_ok=True)
    summary: dict[str, Any] = {
        "manager_run_id": manager_run_id,
        "business_date": args.date,
        "mode": args.mode,
        "status": "failed",
        "started_at": datetime.now().isoformat(timespec="seconds"),
        "selected_count": 0,
        "stores": [],
        "batches": [],
        "exception_store_count": 0,
        "offline_counts": {},
        "jushuitan_counts": {},
        "worker_status": "not_started",
        "manager_dir": str(manager_dir),
        "preflight_log": str(manager_dir / "preflight.log"),
        "preview_log": str(manager_dir / "preview.log"),
        "preview_report_path": "",
        "notification_sent": False,
    }

    manager_lock = nullcontext()
    lock_entered = False
    try:
        if args.mode == "execute":
            manager_lock = build_manager_lock(args, manager_run_id)
        manager_lock.__enter__()
        lock_entered = True
        preflight_code, preflight_output = _run_capture(
            build_preflight_command(args),
            cwd=PROJECT_ROOT,
            log_path=manager_dir / "preflight.log",
        )
        preflight = _json_from_output(preflight_output)
        summary["preflight"] = preflight
        if preflight_code != 0 or preflight.get("status") != "ok":
            raise DailyManagerError("Preflight did not reach status=ok.")

        preview_code, preview_output = _run_capture(
            build_preview_command(args, manager_dir / "db_previews"),
            cwd=PROJECT_ROOT,
            log_path=manager_dir / "preview.log",
        )
        preview = _load_preview_report(_json_from_output(preview_output))
        summary["preview"] = preview
        summary["preview_report_path"] = str(preview.get("report_path") or "")
        summary["selected_count"] = int(preview.get("selected_count") or 0)
        if preview_code != 0:
            raise DailyManagerError("Database preview command failed.")
        if any(int(value or 0) for value in (preview.get("required_null_counts") or {}).values()):
            raise DailyManagerError("Database preview contains required fields with null values.")
        store_files = [
            item for item in preview.get("per_store_preview_csv", [])
            if int(item.get("count") or 0) > 0 and str(item.get("path") or "").strip()
        ]
        if summary["selected_count"] > 0 and not store_files:
            raise DailyManagerError("Database preview did not provide per-store input CSV files.")
        if sum(int(item.get("count") or 0) for item in store_files) != summary["selected_count"]:
            raise DailyManagerError("Database preview per-store counts do not match selected_count.")

        if args.mode == "preview" or summary["selected_count"] == 0:
            summary["status"] = "no_tasks" if summary["selected_count"] == 0 else "preview_only"
            return_code = 0
        else:
            if not args.yes:
                raise DailyManagerError("execute mode requires --yes.")
            infrastructure_failed = False
            with paused_worker(str(args.worker_task_name)) as worker_info:
                summary["worker_status"] = "paused"
                summary["worker_before"] = worker_info["before"]
                summary["worker_paused"] = worker_info["paused"]
                for index, item in enumerate(store_files, start=1):
                    store_name = str(item.get("store_name") or "").strip()
                    input_file = Path(str(item["path"])).resolve()
                    store_result: dict[str, Any] = {
                        "store_name": store_name,
                        "selected_count": int(item.get("count") or 0),
                        "state": "success",
                        "batch_count": 0,
                        "completed_batch_count": 0,
                        "safety_stopped": False,
                        "run_ids": [],
                        "batches": [],
                    }
                    if not input_file.exists():
                        store_result.update(
                            {
                                "state": "failed",
                                "error": f"Preview CSV not found: {input_file}",
                            }
                        )
                        summary["stores"].append(store_result)
                        infrastructure_failed = True
                        break

                    batches = split_store_input(
                        input_file,
                        manager_dir / "inputs" / f"store_{index:02d}_{_safe_id(store_name)}",
                        int(args.batch_size),
                    )
                    store_result["batch_count"] = len(batches)
                    for batch_index, batch_file in enumerate(batches, start=1):
                        run_id = f"{manager_run_id}_s{index:02d}_b{batch_index:03d}"
                        return_code = _run_logged(
                            build_pipeline_command(args, batch_file, run_id),
                            cwd=PROJECT_ROOT,
                            log_path=manager_dir / f"{_safe_id(store_name)}_{run_id}.log",
                        )
                        result = _load_pipeline_result(PROJECT_ROOT, run_id, return_code)
                        result["store_name"] = store_name
                        result["input_file"] = str(batch_file)
                        result["batch_number"] = batch_index
                        store_result["run_ids"].append(run_id)
                        store_result["batches"].append(result)
                        summary["batches"].append(result)
                        store_result["completed_batch_count"] = batch_index

                        if result.get("state") == "failed":
                            store_result["state"] = "failed"
                            infrastructure_failed = True
                            break
                        if result.get("state") == "completed_with_exceptions":
                            store_result["state"] = "completed_with_exceptions"
                        if result.get("safety_stop"):
                            store_result["safety_stopped"] = True
                            store_result["state"] = "completed_with_exceptions"
                            break

                    summary["stores"].append(store_result)
                    if infrastructure_failed:
                        break

            summary["worker_status"] = "restored"
            summary["worker_restored"] = worker_info.get("restored", {})
            summary["exception_store_count"] = sum(
                1 for item in summary["stores"] if item.get("state") != "success"
            )
            summary["offline_counts"] = _merge_counts(summary["batches"], "offline_counts")
            summary["jushuitan_counts"] = _merge_counts(summary["batches"], "jushuitan_counts")
            if infrastructure_failed or any(item.get("state") == "failed" for item in summary["stores"]):
                summary["status"] = "failed"
                return_code = 1
            elif any(item.get("state") == "completed_with_exceptions" for item in summary["stores"]):
                summary["status"] = "completed_with_exceptions"
                return_code = 0
            else:
                summary["status"] = "success"
                return_code = 0

        return_code = int(locals().get("return_code", 0))
        return return_code, summary
    except Exception as exc:
        summary["status"] = "failed"
        summary["error_type"] = type(exc).__name__
        summary["error_message"] = str(exc)
        return 1, summary
    finally:
        if lock_entered:
            manager_lock.__exit__(None, None, None)
        summary["finished_at"] = datetime.now().isoformat(timespec="seconds")
        summary["notification_sent"] = send_manager_notification(
            summary,
            Path(args.shared_runtime_root).resolve(),
            bool(args.no_notify),
        )
        _write_json(manager_dir / "summary.json", summary)
        _write_json(Path(args.output_root).resolve() / "latest.summary.json", summary)
        print(json.dumps(summary, ensure_ascii=False, indent=2))


def main(argv: list[str] | None = None) -> int:
    args = build_argument_parser().parse_args(argv)
    if args.command == "run":
        if args.batch_size <= 0:
            raise ValueError("--batch-size must be positive")
        if args.mode == "execute" and not args.yes:
            raise ValueError("execute mode requires --yes")
        return run_daily(args)[0]
    raise ValueError(f"Unsupported command: {args.command}")


if __name__ == "__main__":
    raise SystemExit(main())
