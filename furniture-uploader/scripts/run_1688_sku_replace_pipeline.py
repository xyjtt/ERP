from __future__ import annotations

import argparse
from contextlib import nullcontext
from datetime import datetime
import json
import os
from pathlib import Path
import re
import sys
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_JUSHUITAN_ROOT = PROJECT_ROOT.parent / "jushuitan-sku-offline-batch"
DEFAULT_SHARED_RUNTIME_ROOT = Path(os.environ.get("SCRIPT_1688_ROOT", "D:/script_1688"))
RPA_ROOT = PROJECT_ROOT / "rpa"
if str(RPA_ROOT) not in sys.path:
    sys.path.insert(0, str(RPA_ROOT))

from stop_sale_audit import StopSaleAuditRepository
from stop_sale_audit import load_jsonl_records
from stop_sale_audit import hydrate_dingtalk_credentials
from stop_sale_audit import resolve_stop_sale_app_config
from sku_replace_audit import SkuReplaceAuditRepository
from config_loader import load_json_with_local_override
from cross_project_runtime import (
    RuntimeLeaseGuard,
    RuntimeLeaseRepository,
    resolve_build_sha,
    resolve_executor_binding,
)
from operation_saga import OperationSagaRepository
from sku_operation_saga import build_saga_operations, persist_ali1688_results
from sku_offline_main import resolve_store_account_binding
from sku_offline_tasks import (
    COMBINATION_SKU_REASON,
    build_combination_sku_skip_record,
    dedupe_offline_tasks,
    filter_offline_tasks,
    load_offline_tasks,
    partition_manual_combination_replacements,
    validate_tasks_for_operation,
)
from run_1688_stop_sale_pipeline import (
    DEFAULT_JST_LOGIN_URL,
    DEFAULT_JST_PRODUCT_URL,
    assert_crawler_worker_paused,
    build_jushuitan_lock,
    count_handoff_records,
    emit_pipeline_event,
    run_audit_heartbeat_process,
    run_stage_command,
    send_pipeline_notification,
    wait_for_active_crawler_tasks,
)


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="1688 SKU replacement + Jushuitan manual link-sync pipeline"
    )
    parser.add_argument("--file", required=True, help="CSV/XLSX input filtered for 全渠道替换")
    parser.add_argument("--mode", choices=("preview", "execute"), default="preview")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--run-id", default="")
    parser.add_argument("--yes", action="store_true")
    login_mode = parser.add_mutually_exclusive_group()
    login_mode.add_argument("--skip-login", dest="skip_login", action="store_true", default=True)
    login_mode.add_argument("--require-manual-login", dest="skip_login", action="store_false")
    parser.add_argument("--no-notify", action="store_true")
    parser.add_argument("--jushuitan-root", default=str(DEFAULT_JUSHUITAN_ROOT))
    parser.add_argument("--shared-runtime-root", default=str(DEFAULT_SHARED_RUNTIME_ROOT))
    parser.add_argument("--shared-lock-path", default="")
    parser.add_argument("--account-key", default="")
    parser.add_argument(
        "--lock-wait-seconds",
        type=int,
        default=0,
        help="Seconds to wait for the shared browser lock. Defaults to fail-fast to block duplicate runs.",
    )
    parser.add_argument("--lock-stale-seconds", type=int, default=21600)
    parser.add_argument("--lock-poll-seconds", type=float, default=10.0)
    parser.add_argument("--runtime-lease-wait-seconds", type=float, default=4800)
    parser.add_argument("--runtime-lease-poll-seconds", type=float, default=5.0)
    parser.add_argument("--jushuitan-lock-wait-seconds", type=int, default=3600)
    parser.add_argument("--1688-timeout-seconds", dest="timeout_1688_seconds", type=int, default=2700)
    parser.add_argument("--jushuitan-timeout-seconds", dest="timeout_jushuitan_seconds", type=int, default=1200)
    parser.add_argument("--crawler-worker-task-name", default="YYDD-1688-Crawler-Worker")
    parser.add_argument("--crawler-task-wait-seconds", type=int, default=80 * 60)
    parser.add_argument("--crawler-task-poll-seconds", type=float, default=10.0)
    parser.add_argument("--active-run-max-age-minutes", type=int, default=240)
    parser.add_argument(
        "--source-database",
        default=os.getenv("STOP_SALE_SOURCE_SQLSERVER_DATABASE", "JSDataMiddlePlatform"),
    )
    parser.add_argument("--source-table", default=os.getenv("STOP_SALE_SOURCE_TABLE", "dbo.op_stop_sale"))
    parser.add_argument("--no-shared-lock", action="store_true")
    return parser


def build_1688_command(args: argparse.Namespace, handoff_path: Path) -> list[str]:
    command = [
        sys.executable,
        str(PROJECT_ROOT / "rpa" / "sku_offline_main.py"),
        "--system",
        "1688_sku_replace",
        "--mode",
        args.mode,
        "--file",
        str(Path(args.file).resolve()),
        "--jushuitan-handoff-out",
        str(handoff_path),
        "--shared-runtime-root",
        str(Path(args.shared_runtime_root).resolve()),
    ]
    if args.run_id:
        command.extend(["--run-id", args.run_id])
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
    results_dir: Path,
) -> list[str]:
    command = [
        sys.executable,
        str(PROJECT_ROOT / "scripts" / "run_1688_jushuitan_outbox_worker.py"),
        "--action",
        "sync",
        "--shared-runtime-root",
        str(Path(args.shared_runtime_root).resolve()),
        "--jushuitan-root",
        str(Path(getattr(args, "jushuitan_root", DEFAULT_JUSHUITAN_ROOT)).resolve()),
        "--timeout-seconds",
        str(getattr(args, "timeout_jushuitan_seconds", 1200)),
        "--limit",
        str(args.limit if args.limit > 0 else 10000),
        "--handoff-out",
        str(handoff_path),
        "--results-dir",
        str(results_dir.resolve()),
    ]
    if args.run_id:
        command.extend(["--run-id", args.run_id])
    if args.mode == "execute":
        command.append("--yes")
    return command


def build_jushuitan_environment(handoff_path: Path) -> dict[str, str]:
    environment = os.environ.copy()
    environment.setdefault("JST_LOGIN_URL", DEFAULT_JST_LOGIN_URL)
    environment.setdefault("JST_PRODUCT_URL", DEFAULT_JST_PRODUCT_URL)
    environment["EXCEL_PATH"] = str(handoff_path.resolve())
    return environment


def resolve_pipeline_account(
    args: argparse.Namespace,
    tasks: list[Any],
) -> tuple[str, str]:
    store_names = sorted(
        {str(task.store_name or "").strip() for task in tasks if str(task.store_name or "").strip()}
    )
    if len(store_names) != 1:
        raise RuntimeError("Each execute replacement pipeline must contain exactly one 1688 store.")
    system_config = load_json_with_local_override(
        PROJECT_ROOT / "config" / "systems" / "1688_sku_replace.json"
    )
    binding = resolve_store_account_binding(system_config, store_names[0])
    account_key = str(binding.get("account_key") or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", account_key):
        raise RuntimeError(f"Store '{store_names[0]}' has no valid account_key mapping.")
    requested = str(args.account_key or "").strip()
    if requested and requested != account_key:
        raise RuntimeError(
            f"Requested account_key '{requested}' does not match store mapping '{account_key}'."
        )
    return account_key, store_names[0]


def resolve_shared_lock_path(args: argparse.Namespace, account_key: str) -> Path:
    runtime_root = Path(args.shared_runtime_root).resolve()
    return (
        Path(args.shared_lock_path).resolve()
        if str(args.shared_lock_path).strip()
        else runtime_root / "artifacts" / "locks" / f"ali1688_account_{account_key}.lock"
    )


def build_shared_lock(args: argparse.Namespace, run_id: str, account_key: str):
    if args.mode != "execute" or args.no_shared_lock:
        return nullcontext(), ""
    runtime_root = Path(args.shared_runtime_root).resolve()
    lock_module = runtime_root / "src" / "runtime" / "global_lock.py"
    if not lock_module.exists():
        raise FileNotFoundError(f"Shared 1688 runtime lock module not found: {lock_module}")
    if str(runtime_root) not in sys.path:
        sys.path.insert(0, str(runtime_root))
    from src.runtime.global_lock import GlobalFileLock

    lock_path = resolve_shared_lock_path(args, account_key)
    lock = GlobalFileLock(
        lock_path,
        stale_after_seconds=args.lock_stale_seconds,
        wait_timeout_seconds=args.lock_wait_seconds,
        poll_interval_seconds=args.lock_poll_seconds,
        metadata={
            "cycle": "1688_sku_replace_pipeline",
            "run_id": run_id,
            "account_key": account_key,
            "source": str(Path(args.file).resolve()),
        },
    )
    return lock, str(lock_path)


def count_statuses(records: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for record in records:
        status = str(record.get("status") or "unknown")
        counts[status] = counts.get(status, 0) + 1
    return counts


def build_pipeline_notification(summary: dict[str, Any]) -> str:
    return (
        "【1688 SKU替换闭环】\n"
        f"批次：{summary.get('run_id', '')}\n"
        f"状态：{summary.get('status', '')}\n"
        f"1688：{json.dumps(summary.get('replace_counts', {}), ensure_ascii=False)}\n"
        f"聚水潭：{json.dumps(summary.get('jushuitan_counts', {}), ensure_ascii=False)}\n"
        f"组合货号跳过：{summary.get('business_skipped_count', 0)}\n"
        f"异常：{str(summary.get('error_message', '') or '无')[:500]}\n"
        f"报告：{summary.get('summary_path', '')}"
    )


def load_partitioned_replace_tasks(args: argparse.Namespace) -> tuple[list[Any], list[Any]]:
    system_config = load_json_with_local_override(
        PROJECT_ROOT / "config" / "systems" / "1688_sku_replace.json"
    )
    input_config = dict(system_config.get("input", {}))
    tasks = load_offline_tasks(Path(args.file).resolve(), input_config)
    selected, _ = filter_offline_tasks(tasks, dict(input_config.get("filters", {})))
    executable, business_skipped = partition_manual_combination_replacements(selected)
    validate_tasks_for_operation(executable, "replace")
    deduped, _ = dedupe_offline_tasks(executable)
    selected_tasks = deduped[: args.limit] if args.limit > 0 else deduped
    return selected_tasks, business_skipped


def load_selected_replace_tasks(args: argparse.Namespace) -> list[Any]:
    selected, _ = load_partitioned_replace_tasks(args)
    return selected


def _protocol_enforcement_active(runtime_guard: RuntimeLeaseGuard | None) -> bool:
    """True only when the cross-project lease protocol enforces bindings.

    Any failure fails closed to the legacy global worker-idle gate.
    """
    if runtime_guard is None:
        return False
    try:
        state = runtime_guard.repository.assert_protocol(
            component="erp-sku-replace-gate",
            build_sha=runtime_guard.build_sha,
        )
    except Exception:
        return False
    return bool(state.enforcement_enabled)


def run(args: argparse.Namespace) -> int:
    if args.mode == "execute" and not args.yes:
        raise ValueError("execute mode requires --yes")
    if args.limit < 0:
        raise ValueError("--limit must be non-negative")
    if args.lock_wait_seconds < 0 or args.jushuitan_lock_wait_seconds < 0:
        raise ValueError("lock wait values must be non-negative")
    if args.crawler_task_wait_seconds < 0 or args.crawler_task_poll_seconds <= 0:
        raise ValueError("crawler task wait must be non-negative and poll must be positive")
    if args.runtime_lease_wait_seconds < 0 or args.runtime_lease_poll_seconds <= 0:
        raise ValueError("runtime lease wait must be non-negative and poll must be positive")
    if args.timeout_1688_seconds <= 0 or args.timeout_jushuitan_seconds <= 0:
        raise ValueError("stage timeouts must be positive")
    run_id = str(args.run_id).strip() or datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    if not args.run_id:
        args.run_id = run_id
    started_at = datetime.now().isoformat(timespec="seconds")
    audit_tasks: list[Any] = []
    business_skipped_tasks: list[Any] = []
    if args.mode == "execute":
        audit_tasks, business_skipped_tasks = load_partitioned_replace_tasks(args)
        if not audit_tasks:
            pipeline_dir = PROJECT_ROOT / "logs" / "sku_replace" / "pipelines"
            pipeline_dir.mkdir(parents=True, exist_ok=True)
            summary_path = pipeline_dir / f"{run_id}.summary.json"
            summary = {
                "run_id": run_id,
                "mode": args.mode,
                "status": "business_skipped",
                "started_at": started_at,
                "finished_at": datetime.now().isoformat(timespec="seconds"),
                "input_file": str(Path(args.file).resolve()),
                "business_skipped_count": len(business_skipped_tasks),
                "business_skipped_tasks": [
                    build_combination_sku_skip_record(task) for task in business_skipped_tasks
                ],
                "exception_reason": COMBINATION_SKU_REASON,
                "online_actions_started": False,
                "summary_path": str(summary_path),
            }
            summary_path.write_text(
                json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            print(json.dumps(summary, ensure_ascii=False, indent=2))
            return 0
    if args.mode == "execute" and not args.no_notify:
        credentials = hydrate_dingtalk_credentials(args.shared_runtime_root)
        if not all(credentials.values()):
            raise RuntimeError(
                "DingTalk credentials are missing from both the process environment "
                "and the 1688 Credential Manager."
            )
    jushuitan_root = Path(args.jushuitan_root).resolve()
    if not (jushuitan_root / "package.json").exists():
        raise FileNotFoundError(f"Jushuitan project not found: {jushuitan_root}")
    pipeline_dir = PROJECT_ROOT / "logs" / "sku_replace" / "pipelines"
    pipeline_dir.mkdir(parents=True, exist_ok=True)
    handoff_path = pipeline_dir / f"{run_id}.jushuitan-sync.jsonl"
    results_dir = pipeline_dir / f"{run_id}.jushuitan-results"
    result_path = results_dir / f"{run_id}.jsonl"
    report_path = PROJECT_ROOT / "logs" / "sku_replace" / "run_reports" / f"{run_id}.jsonl"
    summary_path = pipeline_dir / f"{run_id}.summary.json"
    audit_repository: SkuReplaceAuditRepository | None = None
    crawler_repository: StopSaleAuditRepository | None = None
    audit_contract: dict[str, Any] | None = None
    account_key = ""
    store_name = ""
    runtime_guard: RuntimeLeaseGuard | None = None
    saga_repository: OperationSagaRepository | None = None
    saga_operations = []
    if args.mode == "execute":
        audit_config = resolve_stop_sale_app_config(args.shared_runtime_root)
        audit_repository = SkuReplaceAuditRepository(audit_config)
        crawler_repository = StopSaleAuditRepository(audit_config)
        audit_contract = audit_repository.check_contract()
        if not audit_contract.get("ready"):
            raise RuntimeError(
                "SKU replacement audit tables are missing: "
                + ", ".join(audit_contract.get("missing_tables", []))
            )
        account_key, store_name = resolve_pipeline_account(args, audit_tasks)
        system_config = load_json_with_local_override(
            PROJECT_ROOT / "config" / "systems" / "1688_sku_replace.json"
        )
        store_binding = resolve_store_account_binding(system_config, store_name)
        binding = resolve_executor_binding(
            account_key,
            fallback_profile_ref=str(store_binding.get("browser_profile_dir") or ""),
            fallback_cdp_port=int(store_binding.get("cdp_port") or 0),
        )
        build_sha = resolve_build_sha(PROJECT_ROOT.parent)
        saga_repository = OperationSagaRepository(audit_config)
        saga_contract = saga_repository.check_contract()
        if not saga_contract.get("ready"):
            raise RuntimeError(
                "ERP operation saga tables are missing: "
                + ", ".join(str(item) for item in saga_contract.get("missing_tables") or [])
            )
        saga_operations = build_saga_operations(
            "sku_replace",
            run_id,
            account_key,
            audit_tasks,
        )
        runtime_guard = RuntimeLeaseGuard(
            RuntimeLeaseRepository(audit_config),
            binding=binding,
            task_type="sku_replace",
            run_id=run_id,
            request_key=f"sku_replace:{run_id}:{account_key}",
            build_sha=build_sha,
            component="erp-sku-replace",
            wait_timeout_seconds=args.runtime_lease_wait_seconds,
            poll_interval_seconds=args.runtime_lease_poll_seconds,
        )
        if audit_repository.count_recent_active_runs(
            args.active_run_max_age_minutes,
            store_name=store_name,
        ) > 0:
            raise RuntimeError(f"A recent SKU replacement audit run is still active for {store_name}")
    lock, lock_path = build_shared_lock(args, run_id, account_key)
    jushuitan_lock, jushuitan_lock_path = build_jushuitan_lock(args, run_id)
    error_message = ""
    return_1688 = 1
    return_jushuitan: int | None = None
    audit_started = False

    try:
        emit_pipeline_event(
            run_id,
            "shared_lock_acquire_started",
            lock_path=lock_path,
            wait_seconds=args.lock_wait_seconds,
        )
        with lock:
            emit_pipeline_event(run_id, "shared_lock_acquired", lock_path=lock_path)
            worker_state = (
                assert_crawler_worker_paused(
                    args.crawler_worker_task_name,
                    account_key,
                    allow_active_worker=_protocol_enforcement_active(runtime_guard),
                )
                if args.mode == "execute"
                else {}
            )
            if args.mode == "execute":
                if runtime_guard is None or saga_repository is None:
                    raise RuntimeError("execute mode requires runtime lease and Saga repositories")
                # Register the high-priority write request BEFORE waiting for the
                # current crawler so the lease layer blocks new crawler claims
                # for this account while we wait (write-priority takes effect).
                runtime_guard.acquire()
                guard_outcome = "completed"
                try:
                    wait_for_active_crawler_tasks(
                        crawler_repository,
                        account_key=account_key,
                        timeout_seconds=args.crawler_task_wait_seconds,
                        poll_seconds=args.crawler_task_poll_seconds,
                    )
                except BaseException:
                    guard_outcome = "failed"
                    runtime_guard.release(guard_outcome, suppress_errors=True)
                    raise
            if audit_repository is not None:
                audit_repository.start_run(
                    run_id=run_id,
                    mode=args.mode,
                    input_file=args.file,
                    tasks=audit_tasks,
                    source_database=str(args.source_database),
                    source_table=str(args.source_table),
                )
                audit_started = True
            heartbeat = (
                (
                    lambda: run_audit_heartbeat_process(
                        kind="sku_replace",
                        run_id=run_id,
                        shared_runtime_root=args.shared_runtime_root,
                    )
                )
                if audit_started and audit_repository is not None
                else None
            )
            if args.mode == "execute":
                guard_outcome = "completed"
                try:
                    saga_repository.prepare_many(
                        saga_operations,
                        owner_token=runtime_guard.owner_token,
                        account_fencing_token=runtime_guard.account_fencing_token,
                        browser_slot_key=runtime_guard.browser_slot_key,
                        browser_slot_fencing_token=runtime_guard.browser_slot_fencing_token,
                    )
                    command_environment = os.environ.copy()
                    command_environment.update(runtime_guard.environment())
                    emit_pipeline_event(
                        run_id,
                        "1688_replace_stage_started",
                        timeout_seconds=args.timeout_1688_seconds,
                        account_fencing_token=runtime_guard.account_fencing_token,
                        browser_slot_key=runtime_guard.browser_slot_key,
                        browser_slot_fencing_token=runtime_guard.browser_slot_fencing_token,
                    )
                    stage_1688 = run_stage_command(
                        build_1688_command(args, handoff_path),
                        cwd=PROJECT_ROOT,
                        timeout_seconds=args.timeout_1688_seconds,
                        stage="1688_replace",
                        env=command_environment,
                        heartbeat=runtime_guard.assert_active,
                        heartbeat_interval_seconds=10,
                    )
                    return_1688 = stage_1688.returncode
                    runtime_guard.assert_active()
                    replace_records_now = load_jsonl_records(report_path)
                    persist_ali1688_results(
                        saga_repository,
                        task_type="sku_replace",
                        operations=saga_operations,
                        records=replace_records_now,
                        account_fencing_token=runtime_guard.account_fencing_token,
                    )
                    if heartbeat is not None:
                        heartbeat()
                except BaseException:
                    guard_outcome = "failed"
                    raise
                finally:
                    runtime_guard.release(guard_outcome, suppress_errors=True)
            else:
                emit_pipeline_event(
                    run_id,
                    "1688_replace_stage_started",
                    timeout_seconds=args.timeout_1688_seconds,
                )
                stage_1688 = run_stage_command(
                    build_1688_command(args, handoff_path),
                    cwd=PROJECT_ROOT,
                    timeout_seconds=args.timeout_1688_seconds,
                    stage="1688_replace",
                    heartbeat=heartbeat,
                )
                return_1688 = stage_1688.returncode
            emit_pipeline_event(run_id, "1688_replace_stage_finished", return_code=return_1688)
            handoff_count = count_handoff_records(handoff_path)
            if handoff_count > 0:
                emit_pipeline_event(
                    run_id,
                    "jushuitan_lock_wait_started",
                    lock_path=jushuitan_lock_path,
                )
                with jushuitan_lock:
                    emit_pipeline_event(
                        run_id,
                        "jushuitan_lock_acquired",
                        lock_path=jushuitan_lock_path,
                    )
                    emit_pipeline_event(
                        run_id,
                        "jushuitan_sync_stage_started",
                        timeout_seconds=args.timeout_jushuitan_seconds,
                        handoff_count=handoff_count,
                    )
                    stage_jst = run_stage_command(
                        build_jushuitan_command(args, handoff_path, results_dir),
                        cwd=jushuitan_root,
                        timeout_seconds=args.timeout_jushuitan_seconds,
                        stage="jushuitan_sync_by_link",
                        env=os.environ.copy(),
                        heartbeat=heartbeat,
                    )
                    return_jushuitan = stage_jst.returncode
                emit_pipeline_event(
                    run_id,
                    "jushuitan_sync_stage_finished",
                    return_code=return_jushuitan,
                )
            else:
                emit_pipeline_event(run_id, "jushuitan_sync_stage_skipped", reason="empty_handoff")
    except Exception as exc:
        worker_state = {}
        error_message = f"{type(exc).__name__}: {exc}"

    replace_records = load_jsonl_records(report_path)
    jushuitan_records = load_jsonl_records(result_path)
    replace_counts = count_statuses(replace_records)
    jushuitan_counts = count_statuses(jushuitan_records)
    failed = (
        bool(error_message)
        or return_1688 != 0
        or return_jushuitan not in {None, 0}
        or replace_counts.get("failed", 0) > 0
        or jushuitan_counts.get("failed", 0) > 0
    )
    if args.mode == "preview":
        status = "preview_only" if not error_message and return_1688 == 0 and (return_jushuitan in {None, 0}) else "failed"
    else:
        status = "partial" if failed and (replace_counts.get("success", 0) + replace_counts.get("already_replaced", 0)) > 0 else "failed" if failed else "success"
    if audit_started and audit_repository is not None:
        try:
            audit_repository.record_1688_results(run_id, replace_records)
            audit_repository.record_jushuitan_results(run_id, jushuitan_records)
            audit_repository.finish_run(
                run_id=run_id,
                status=status,
                replace_report_path=str(report_path),
                jushuitan_report_path=str(result_path) if result_path.exists() else "",
                summary_path=str(summary_path),
                error_message=error_message,
            )
        except Exception as exc:
            status = "partial" if replace_counts.get("success", 0) else "failed"
            error_message = (error_message + " | " if error_message else "") + f"audit: {type(exc).__name__}: {exc}"
    summary = {
        "run_id": run_id,
        "mode": args.mode,
        "status": status,
        "started_at": started_at,
        "finished_at": datetime.now().isoformat(timespec="seconds"),
        "input_file": str(Path(args.file).resolve()),
        "replace_return_code": return_1688,
        "jushuitan_return_code": return_jushuitan,
        "replace_counts": replace_counts,
        "jushuitan_counts": jushuitan_counts,
        "handoff_count": count_handoff_records(handoff_path),
        "handoff_path": str(handoff_path),
        "replace_report_path": str(report_path),
        "jushuitan_report_path": str(result_path),
        "shared_lock_path": lock_path,
        "account_key": account_key,
        "store_name": store_name,
        "jushuitan_lock_path": jushuitan_lock_path,
        "worker_state": worker_state,
        "audit_database": audit_contract,
        "audit_status": status if audit_started else None,
        "business_skipped_count": len(business_skipped_tasks),
        "business_skipped_tasks": [
            build_combination_sku_skip_record(task) for task in business_skipped_tasks
        ],
        "error_message": error_message,
        "summary_path": str(summary_path),
        "notification_sent": False,
    }
    if args.mode == "execute" and not args.no_notify:
        summary["notification_sent"] = send_pipeline_notification(
            build_pipeline_notification(summary),
            disabled=False,
        )
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if status in {"success", "preview_only"} else 2 if status == "partial" else 1


def main() -> int:
    args = build_argument_parser().parse_args()
    try:
        return run(args)
    except Exception as exc:
        if args.mode == "execute" and not args.no_notify:
            hydrate_dingtalk_credentials(args.shared_runtime_root)
            send_pipeline_notification(
                "【1688 SKU替换闭环】\n"
                f"批次：{str(args.run_id).strip() or '未创建'}\n"
                "状态：未启动或安全阻止\n"
                f"原因：{type(exc).__name__}: {str(exc)[:500]}\n"
                "处理：未继续执行线上操作，请查看执行日志。",
                disabled=False,
            )
        raise


if __name__ == "__main__":
    raise SystemExit(main())
