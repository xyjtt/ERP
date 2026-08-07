"""Run the unattended daily 1688 SKU-replacement queue.

The manager owns only orchestration. Every browser/database write is delegated
to ``run_1688_sku_replace_pipeline.py`` so the existing account lease, Saga,
Jushuitan outbox, combination-SKU skip, and platform-error classification
contracts remain authoritative.
"""

from __future__ import annotations

import argparse
from contextlib import nullcontext
from datetime import date, datetime
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from typing import Any, Mapping


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_ROOT = PROJECT_ROOT / "scripts"
DEFAULT_SHARED_RUNTIME_ROOT = Path(os.environ.get("SCRIPT_1688_ROOT", "D:/script_1688"))
DEFAULT_JUSHUITAN_ROOT = PROJECT_ROOT.parent / "jushuitan-sku-offline-batch"
DEFAULT_SOURCE_DATABASE = "JSReportReplica"
DEFAULT_SOURCE_TABLE = "app.op_stop_sale"
DEFAULT_SOURCE_DRIVER = "ODBC Driver 17 for SQL Server"
REPLACEMENT_HANDLING = "全渠道替换"
DEFAULT_MANAGER_TASK_NAME = "YYDD-1688-Replace-Daily"
DEFAULT_MANAGER_LOCK_STALE_SECONDS = 36 * 60 * 60

if str(PROJECT_ROOT / "rpa") not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT / "rpa"))
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

from config_loader import load_json_with_local_override
from manage_1688_stop_sale_daily import split_store_input
from sku_offline_main import resolve_store_account_binding


class ReplaceDailyManagerError(RuntimeError):
    pass


def _safe_id(value: Any) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]", "_", str(value or "").strip())[:96] or "unknown"


def _json_from_output(output: str) -> dict[str, Any]:
    decoder = json.JSONDecoder()
    text = str(output or "")
    matches = list(re.finditer(r"(?m)^\s*\{", text))
    for match in reversed(matches):
        try:
            payload, _ = decoder.raw_decode(text[match.start():])
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            return payload
    return {}


def _load_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(dict(payload), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _validate_date(value: str) -> str:
    try:
        return date.fromisoformat(str(value).strip()).isoformat()
    except ValueError as exc:
        raise ValueError(f"Invalid --date: {value!r}; expected YYYY-MM-DD") from exc


def load_authoritative_store_names(shared_runtime_root: str | Path) -> list[str]:
    """Read task shop names from the shared runtime roster, preserving values."""
    roster_path = Path(shared_runtime_root).resolve() / "scripts" / "account_runtime_migration_roster.json"
    payload = _load_json(roster_path)
    rows = payload.get("accounts") or []
    if not isinstance(rows, list):
        raise ReplaceDailyManagerError(f"Authoritative account roster is invalid: {roster_path}")
    selected: list[tuple[int, str]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        if str(row.get("migration_group") or "normal").strip() == "disabled":
            continue
        shop_name = str(row.get("expected_shop_name") or "").strip()
        if not shop_name:
            raise ReplaceDailyManagerError(
                f"Authoritative roster row has no expected_shop_name: {roster_path}"
            )
        selected.append((int(row.get("migration_order") or 10**9), shop_name))
    names = [name for _, name in sorted(selected, key=lambda item: (item[0], item[1]))]
    if len(names) != len(set(names)):
        raise ReplaceDailyManagerError("Authoritative roster contains duplicate task shop names")
    if not names:
        raise ReplaceDailyManagerError(f"Authoritative roster has no enabled shops: {roster_path}")
    return names


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Manage the daily 1688 SKU-replacement pipeline.")
    subparsers = parser.add_subparsers(dest="command", required=True)
    run = subparsers.add_parser("run", help="Preview or execute the current replacement source.")
    run.add_argument("--date", default=date.today().isoformat())
    run.add_argument("--mode", choices=("preview", "execute"), default="preview")
    run.add_argument("--yes", action="store_true")
    run.add_argument("--store", action="append", dest="stores")
    run.add_argument("--output-root", default=str(PROJECT_ROOT / "logs" / "sku_replace" / "scheduler"))
    run.add_argument("--shared-runtime-root", default=str(DEFAULT_SHARED_RUNTIME_ROOT))
    run.add_argument("--jushuitan-root", default=str(DEFAULT_JUSHUITAN_ROOT))
    run.add_argument("--worker-task-name", default="YYDD-1688-Crawler-Worker")
    run.add_argument("--source-database", default=DEFAULT_SOURCE_DATABASE)
    run.add_argument("--source-table", default=DEFAULT_SOURCE_TABLE)
    run.add_argument("--source-driver", default=DEFAULT_SOURCE_DRIVER)
    run.add_argument("--batch-size", type=int, default=10)
    run.add_argument("--batch-max-attempts", type=int, default=2)
    run.add_argument("--lock-wait-seconds", type=int, default=0)
    run.add_argument("--jushuitan-lock-wait-seconds", type=int, default=3600)
    run.add_argument("--crawler-task-wait-seconds", type=int, default=80 * 60)
    run.add_argument("--1688-timeout-seconds", dest="timeout_1688_seconds", type=int, default=3600)
    run.add_argument("--jushuitan-timeout-seconds", dest="timeout_jushuitan_seconds", type=int, default=1800)
    run.add_argument("--no-notify", action="store_true")
    return parser


def build_preflight_command(args: argparse.Namespace) -> list[str]:
    command = [
        sys.executable,
        str(SCRIPTS_ROOT / "preflight_1688_stop_sale_executor.py"),
        "--script-1688-root",
        str(Path(args.shared_runtime_root).resolve()),
        "--jushuitan-root",
        str(Path(args.jushuitan_root).resolve()),
    ]
    if str(args.source_database).strip().casefold() == "jsreportreplica" and str(
        args.source_table
    ).strip().casefold().startswith("app."):
        command.append("--existing-input")
    return command


def build_preview_command(args: argparse.Namespace, output_dir: Path) -> list[str]:
    stores = [str(item).strip() for item in (args.stores or []) if str(item).strip()]
    if not stores:
        stores = load_authoritative_store_names(args.shared_runtime_root)
    command = [
        sys.executable,
        str(SCRIPTS_ROOT / "build_1688_stop_sale_preview.py"),
        "--date",
        str(args.date),
        "--output-dir",
        str(output_dir.resolve()),
        "--limit",
        "0",
        "--database",
        str(args.source_database),
        "--table",
        str(args.source_table),
        "--driver",
        str(args.source_driver),
        "--handling",
        REPLACEMENT_HANDLING,
        "--shared-runtime-root",
        str(Path(args.shared_runtime_root).resolve()),
    ]
    for store in stores:
        command.extend(["--store", store])
    return command


def build_batch_command(
    args: argparse.Namespace,
    input_dir: Path,
    account_key: str,
    queue_id: str,
    state_path: Path,
) -> list[str]:
    return [
        sys.executable,
        str(SCRIPTS_ROOT / "manage_1688_sku_replace_batches.py"),
        "--input-dir",
        str(input_dir.resolve()),
        "--account-key",
        account_key,
        "--queue-id",
        queue_id,
        "--jushuitan-root",
        str(Path(args.jushuitan_root).resolve()),
        "--shared-runtime-root",
        str(Path(args.shared_runtime_root).resolve()),
        "--max-attempts",
        str(args.batch_max_attempts),
        "--lock-wait-seconds",
        str(args.lock_wait_seconds),
        "--jushuitan-lock-wait-seconds",
        str(args.jushuitan_lock_wait_seconds),
        "--crawler-task-wait-seconds",
        str(args.crawler_task_wait_seconds),
        "--crawler-worker-task-name",
        str(args.worker_task_name),
        "--1688-timeout-seconds",
        str(args.timeout_1688_seconds),
        "--jushuitan-timeout-seconds",
        str(args.timeout_jushuitan_seconds),
        "--source-database",
        str(args.source_database),
        "--source-table",
        str(args.source_table),
        "--pipeline-script",
        str(SCRIPTS_ROOT / "run_1688_sku_replace_pipeline.py"),
        "--state-path",
        str(state_path.resolve()),
    ]


def build_manager_lock(args: argparse.Namespace, manager_run_id: str):
    runtime_root = Path(args.shared_runtime_root).resolve()
    lock_module = runtime_root / "src" / "runtime" / "global_lock.py"
    if not lock_module.exists():
        raise ReplaceDailyManagerError(f"Shared runtime lock module not found: {lock_module}")
    if str(runtime_root) not in sys.path:
        sys.path.insert(0, str(runtime_root))
    from src.runtime.global_lock import GlobalFileLock

    return GlobalFileLock(
        runtime_root / "artifacts" / "locks" / "ali1688_sku_replace_daily.lock",
        stale_after_seconds=DEFAULT_MANAGER_LOCK_STALE_SECONDS,
        wait_timeout_seconds=0,
        poll_interval_seconds=1,
        metadata={"cycle": "1688_sku_replace_daily_manager", "manager_run_id": manager_run_id},
    )


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
    )
    output = (completed.stdout or "") + ("\n" + completed.stderr if completed.stderr else "")
    log_path.write_text(output, encoding="utf-8")
    return completed.returncode, output


def _run_batch(
    command: list[str],
    *,
    cwd: Path,
    log_path: Path,
) -> tuple[int, dict[str, Any]]:
    code, output = _run_capture(command, cwd=cwd, log_path=log_path)
    return code, _json_from_output(output)


def _load_pipeline_summary(path_text: Any) -> dict[str, Any]:
    return _load_json(Path(str(path_text or "").strip())) if str(path_text or "").strip() else {}


def _system_prompt_records(summary: Mapping[str, Any]) -> list[dict[str, Any]]:
    report_path = str(summary.get("replace_report_path") or "").strip()
    if not report_path:
        return []
    records: list[dict[str, Any]] = []
    try:
        lines = Path(report_path).read_text(encoding="utf-8-sig").splitlines()
    except (OSError, UnicodeError):
        return []
    for line in lines:
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(record, dict):
            continue
        category = str(
            record.get("error_category") or record.get("page_error_category") or ""
        ).strip()
        if category != "system_prompt":
            continue
        raw_message = str(
            record.get("system_prompt")
            or record.get("page_error_text")
            or record.get("error_message")
            or ""
        ).strip()
        records.append(
            {
                "run_id": str(summary.get("run_id") or record.get("run_id") or ""),
                "task_key": str(record.get("task_key") or ""),
                "store_name": str(record.get("store_name") or ""),
                "product_id": str(record.get("product_id") or ""),
                "online_sku": str(record.get("online_sku") or ""),
                "error_category": "system_prompt",
                "raw_message": raw_message,
                "screenshot_path": str(record.get("screenshot_path") or ""),
                "html_snapshot_path": str(record.get("html_snapshot_path") or ""),
            }
        )
    return records


def summarize_batch_state(state: Mapping[str, Any]) -> dict[str, Any]:
    classifications: list[str] = []
    prompt_records: list[dict[str, Any]] = []
    unreported_prompt_count = 0
    for batch in (state.get("batches") or {}).values():
        if not isinstance(batch, dict):
            continue
        classification = str(batch.get("classification") or "").strip()
        attempts = batch.get("attempts") or []
        latest_attempt = attempts[-1] if isinstance(attempts, list) and attempts else {}
        if not classification and isinstance(latest_attempt, dict):
            classification = str(latest_attempt.get("classification") or "").strip()
        if classification:
            classifications.append(classification)
        if not isinstance(latest_attempt, dict):
            continue
        summary = _load_pipeline_summary(latest_attempt.get("summary_path"))
        records = _system_prompt_records(summary)
        prompt_records.extend(records)
        counts = summary.get("replace_counts") or {}
        explicit_count = int(counts.get("system_prompt", 0) or 0) if isinstance(counts, dict) else 0
        unreported_prompt_count += max(0, explicit_count - len(records))
    non_success = [item for item in classifications if item != "success"]
    return {
        "classifications": classifications,
        "non_success_classifications": non_success,
        "system_prompt_count": len(prompt_records) + unreported_prompt_count,
        "system_prompt_records": prompt_records,
        "business_terminal_count": sum(item in {"business_terminal", "notified_terminal"} for item in classifications),
    }


def _load_system_config() -> dict[str, Any]:
    return load_json_with_local_override(PROJECT_ROOT / "config" / "systems" / "1688_sku_replace.json")


def _store_binding(config: Mapping[str, Any], store_name: str) -> dict[str, Any]:
    try:
        return resolve_store_account_binding(dict(config), store_name)
    except Exception as exc:
        raise ReplaceDailyManagerError(f"No authoritative ERP mapping for {store_name}: {exc}") from exc


def run_daily(args: argparse.Namespace) -> tuple[int, dict[str, Any]]:
    args.date = _validate_date(args.date)
    if int(args.batch_size) <= 0 or int(args.batch_max_attempts) <= 0:
        raise ValueError("batch size and attempts must be positive")
    if args.mode == "execute" and not args.yes:
        raise ReplaceDailyManagerError("execute mode requires --yes")

    date_key = args.date.replace("-", "")
    manager_run_id = f"replace_daily_{date_key}"
    manager_dir = Path(args.output_root).resolve() / manager_run_id
    manager_dir.mkdir(parents=True, exist_ok=True)
    summary_path = manager_dir / "summary.json"
    summary: dict[str, Any] = {
        "manager_run_id": manager_run_id,
        "business_date": args.date,
        "mode": args.mode,
        "operation": "replace",
        "handling": REPLACEMENT_HANDLING,
        "status": "failed",
        "started_at": datetime.now().isoformat(timespec="seconds"),
        "manager_dir": str(manager_dir),
        "preflight_log": str(manager_dir / "preflight.log"),
        "preview_log": str(manager_dir / "preview.log"),
        "preview_report_path": "",
        "selected_count": 0,
        "business_skipped_count": 0,
        "business_skipped_reason_counts": {},
        "stores": [],
        "worker_policy": "account_runtime_lease_delegated_to_pipeline",
        "database_write_by_manager": False,
    }

    existing = _load_json(summary_path)
    if args.mode == "execute" and existing.get("status") in {
        "success",
        "completed_with_exceptions",
        "business_skipped",
        "no_tasks",
    }:
        existing["idempotency"] = "existing_terminal_summary"
        return 0, existing

    lock = build_manager_lock(args, manager_run_id) if args.mode == "execute" else nullcontext()
    try:
        with lock:
            preflight_code, preflight_output = _run_capture(
                build_preflight_command(args), cwd=PROJECT_ROOT, log_path=Path(summary["preflight_log"])
            )
            summary["preflight"] = _json_from_output(preflight_output)
            if preflight_code != 0 or summary["preflight"].get("status") != "ok":
                raise ReplaceDailyManagerError("Preflight did not reach status=ok.")

            preview_code, preview_output = _run_capture(
                build_preview_command(args, manager_dir / "db_previews"),
                cwd=PROJECT_ROOT,
                log_path=Path(summary["preview_log"]),
            )
            preview = _json_from_output(preview_output)
            report_path = str(preview.get("report_path") or "").strip()
            if report_path:
                report = _load_json(Path(report_path))
                if report:
                    merged = dict(report)
                    merged.update(preview)
                    preview = merged
            summary["preview"] = preview
            summary["preview_report_path"] = str(preview.get("report_path") or "")
            summary["selected_count"] = int(preview.get("selected_count") or 0)
            summary["business_skipped_count"] = int(preview.get("business_skipped_count") or 0)
            summary["business_skipped_reason_counts"] = dict(
                preview.get("business_skipped_reason_counts") or {}
            )
            if preview_code != 0:
                raise ReplaceDailyManagerError("Database replacement preview command failed.")
            if any(int(value or 0) for value in (preview.get("required_null_counts") or {}).values()):
                raise ReplaceDailyManagerError("Database replacement preview contains required null fields.")

            store_files = [
                item
                for item in preview.get("per_store_preview_csv", [])
                if isinstance(item, dict)
                and int(item.get("count") or 0) > 0
                and str(item.get("path") or "").strip()
            ]
            if sum(int(item.get("count") or 0) for item in store_files) != summary["selected_count"]:
                raise ReplaceDailyManagerError("Replacement preview per-store counts do not match selected_count.")
            if args.mode == "preview" or summary["selected_count"] == 0:
                if args.mode == "preview":
                    summary["status"] = (
                        "preview_only"
                        if summary["selected_count"] or summary["business_skipped_count"]
                        else "no_tasks"
                    )
                else:
                    summary["status"] = (
                        "business_skipped" if summary["business_skipped_count"] else "no_tasks"
                    )
                return_code = 0
            else:
                config = _load_system_config()
                return_code = 0
                any_failure = False
                any_mapping_block = False
                any_exception = summary["business_skipped_count"] > 0
                for index, item in enumerate(store_files, start=1):
                    store_name = str(item.get("store_name") or "").strip()
                    store_result: dict[str, Any] = {
                        "store_name": store_name,
                        "selected_count": int(item.get("count") or 0),
                        "state": "failed",
                        "account_key": "",
                        "queue_id": "",
                        "queue_state_path": "",
                        "batch_result": {},
                    }
                    try:
                        binding = _store_binding(config, store_name)
                        account_key = str(binding.get("account_key") or "").strip()
                        if not account_key:
                            raise ReplaceDailyManagerError(f"ERP mapping has no account_key: {store_name}")
                        store_result["account_key"] = account_key
                        input_dir = manager_dir / "inputs" / f"store_{index:02d}_{_safe_id(store_name)}"
                        input_dir.mkdir(parents=True, exist_ok=True)
                        source_path = Path(str(item["path"])).resolve()
                        batches = split_store_input(source_path, input_dir, int(args.batch_size))
                        store_result["batch_count"] = len(batches)
                        queue_id = f"{manager_run_id}_{_safe_id(account_key)}"
                        queue_state_path = manager_dir / "queues" / f"{_safe_id(account_key)}.summary.json"
                        store_result["queue_id"] = queue_id
                        store_result["queue_state_path"] = str(queue_state_path)
                        command = build_batch_command(
                            args, input_dir, account_key, queue_id, queue_state_path
                        )
                        code, batch_state = _run_batch(
                            command,
                            cwd=PROJECT_ROOT,
                            log_path=manager_dir / f"{_safe_id(account_key)}.batch.log",
                        )
                        if not batch_state:
                            batch_state = _load_json(queue_state_path)
                        batch_summary = summarize_batch_state(batch_state)
                        store_result["batch_result"] = {
                            "return_code": code,
                            "state": batch_state,
                            **batch_summary,
                        }
                        if code == 0 and str(batch_state.get("status") or "") == "completed" and not batch_summary["non_success_classifications"]:
                            store_result["state"] = "success"
                        elif str(batch_state.get("status") or "") == "completed":
                            store_result["state"] = "completed_with_exceptions"
                            any_exception = True
                        else:
                            store_result["state"] = "failed"
                            any_failure = True
                        if batch_summary["system_prompt_count"]:
                            store_result["system_prompt_count"] = batch_summary["system_prompt_count"]
                    except ReplaceDailyManagerError as exc:
                        store_result.update({"state": "mapping_blocked", "error": str(exc)})
                        any_mapping_block = True
                    summary["stores"].append(store_result)
                if any_mapping_block:
                    summary["status"] = "partial_mapping_blocked"
                    return_code = 2
                elif any_failure:
                    summary["status"] = "partial"
                    return_code = 2
                elif any_exception:
                    summary["status"] = "completed_with_exceptions"
                    return_code = 0
                else:
                    summary["status"] = "success"
                    return_code = 0
                summary["exception_store_count"] = sum(
                    1 for item in summary["stores"] if item.get("state") != "success"
                )
                summary["system_prompt_count"] = sum(
                    int(item.get("system_prompt_count") or 0) for item in summary["stores"]
                )
                summary["system_prompt_records"] = [
                    record
                    for item in summary["stores"]
                    for record in (item.get("batch_result") or {}).get(
                        "system_prompt_records", []
                    )
                    if isinstance(record, dict)
                ]
    except Exception as exc:
        summary["status"] = "failed"
        summary["error_type"] = type(exc).__name__
        summary["error"] = str(exc)
        return_code = 1
    finally:
        summary["finished_at"] = datetime.now().isoformat(timespec="seconds")
        _write_json(summary_path, summary)
        _write_json(Path(args.output_root).resolve() / "latest.summary.json", summary)
    return return_code, summary


def main(argv: list[str] | None = None) -> int:
    args = build_argument_parser().parse_args(argv)
    if args.command == "run":
        code, summary = run_daily(args)
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return code
    raise ValueError(f"Unsupported command: {args.command}")


if __name__ == "__main__":
    raise SystemExit(main())
