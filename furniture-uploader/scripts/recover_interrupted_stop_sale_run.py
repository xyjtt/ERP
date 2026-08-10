from __future__ import annotations

import argparse
import ctypes
import json
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
RPA_ROOT = PROJECT_ROOT / "rpa"
if str(RPA_ROOT) not in sys.path:
    sys.path.insert(0, str(RPA_ROOT))

from run_1688_stop_sale_pipeline import send_pipeline_notification
from stop_sale_audit import (
    StopSaleAuditRepository,
    connect_app_database,
    hydrate_dingtalk_credentials,
    load_jsonl_records,
    resolve_stop_sale_app_config,
    stop_sale_task_key,
)


RECORDED_TERMINAL_FAILURE_STATUS = "failed"
RECOVERABLE_UNSTARTED_ITEM_STATUSES = {"pending", "not_attempted"}


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Finalize a stop-sale run whose executor process was externally terminated."
    )
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--shared-runtime-root", required=True)
    parser.add_argument("--shared-lock-path", required=True)
    parser.add_argument("--reason", required=True)
    parser.add_argument("--yes", action="store_true")
    return parser


def _is_process_running(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        process_query_limited_information = 0x1000
        handle = ctypes.windll.kernel32.OpenProcess(  # type: ignore[attr-defined]
            process_query_limited_information, False, int(pid)
        )
        if not handle:
            return False
        ctypes.windll.kernel32.CloseHandle(handle)  # type: ignore[attr-defined]
        return True
    try:
        os.kill(pid, 0)
    except (OSError, ProcessLookupError):
        return False
    return True


def _load_owned_interrupted_lock(lock_path: Path, run_id: str) -> dict[str, Any]:
    if not lock_path.exists():
        raise FileNotFoundError(f"Shared lock does not exist: {lock_path}")
    payload = json.loads(lock_path.read_text(encoding="utf-8"))
    metadata = dict(payload.get("metadata") or {})
    if str(metadata.get("cycle") or "") != "1688_stop_sale_pipeline":
        raise RuntimeError("Shared lock does not belong to a stop-sale pipeline.")
    if str(metadata.get("run_id") or "") != str(run_id):
        raise RuntimeError("Shared lock run_id does not match the interrupted run.")
    owner_pid = int(payload.get("pid") or 0)
    if _is_process_running(owner_pid):
        raise RuntimeError(f"Interrupted run lock owner is still active: pid={owner_pid}")
    return payload


def _query_run_state(repository: StopSaleAuditRepository, run_id: str) -> dict[str, Any]:
    run_table = repository._table("ali1688_stop_sale_run")
    with connect_app_database(repository.config) as connection:
        row = connection.cursor().execute(
            f"""
            SELECT status, finished_at, error_message, summary_path, notification_sent
            FROM {run_table}
            WHERE run_id = ?
            """,
            (run_id,),
        ).fetchone()
    if row is None:
        raise RuntimeError(f"Stop-sale audit run was not found: {run_id}")
    return {
        "status": str(row[0] or ""),
        "finished_at": row[1],
        "error_message": str(row[2] or ""),
        "summary_path": str(row[3] or ""),
        "notification_sent": bool(row[4]),
    }


def _load_completed_recovery_summary(
    run_state: dict[str, Any],
    *,
    run_id: str,
    reason: str,
    lock_path: Path,
) -> tuple[dict[str, Any], Path]:
    if run_state["status"] != "failed" or run_state["finished_at"] is None:
        raise RuntimeError(
            f"Stop-sale audit run is not recoverable: status={run_state['status']!r}, "
            f"finished_at={run_state['finished_at']!r}"
        )
    if run_state["error_message"] != reason:
        raise RuntimeError("Completed recovery error message does not match the requested reason.")
    summary_path = Path(run_state["summary_path"]).resolve()
    if not summary_path.is_file():
        raise RuntimeError(f"Completed recovery summary does not exist: {summary_path}")
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    if str(summary.get("run_id") or "") != run_id:
        raise RuntimeError("Completed recovery summary run_id does not match.")
    if str(summary.get("error_message") or "") != reason:
        raise RuntimeError("Completed recovery summary reason does not match.")
    if not bool(summary.get("lock_removed")):
        raise RuntimeError("Completed recovery summary does not prove lock removal.")
    if Path(str(summary.get("shared_lock_path") or "")).resolve() != lock_path:
        raise RuntimeError("Completed recovery summary lock path does not match.")
    if not str(summary.get("finished_at") or "").strip():
        # Older recovery artifacts used recovered_at only. Preserve that
        # timestamp so manager closeout can validate the DB terminal time.
        summary["finished_at"] = str(
            summary.get("recovered_at") or datetime.now().isoformat(timespec="seconds")
        )
    return summary, summary_path


def _clean_text(value: Any, limit: int) -> str:
    return str(value or "").strip()[:limit]


def _load_recorded_terminal_failures(report_path: Path) -> dict[str, dict[str, Any]]:
    failures: dict[str, dict[str, Any]] = {}
    for record in load_jsonl_records(report_path):
        if str(record.get("operation") or "offline").strip() != "offline":
            continue
        status = str(record.get("status") or "").strip()
        operation_key = stop_sale_task_key(
            record.get("store_name"),
            record.get("product_id"),
            record.get("online_sku"),
            record.get("platform_store_item_code"),
        )
        if status in {"success", "already_offline"}:
            raise RuntimeError(
                "Interrupted recovery found a recorded successful 1688 action; "
                f"explicit page/Saga reconciliation is required: operation_key={operation_key}"
            )
        if status != RECORDED_TERMINAL_FAILURE_STATUS:
            continue
        if operation_key in failures:
            raise RuntimeError(
                "Interrupted recovery report contains duplicate terminal evidence: "
                f"operation_key={operation_key}"
            )
        failures[operation_key] = dict(record)
    return failures


def _reconcile_interrupted_audit(
    repository: StopSaleAuditRepository,
    *,
    run_id: str,
    reason: str,
    report_path: Path | None = None,
) -> dict[str, Any]:
    item_table = repository._table("ali1688_stop_sale_item")
    run_table = repository._table("ali1688_stop_sale_run")
    saga_table = repository._table("ali1688_operation_saga")
    outbox_table = repository._table("ali1688_operation_outbox")
    evidence = {
        "recovery": "interrupted_executor_process",
        "run_id": run_id,
        "reason": reason,
    }
    resolved_report_path = report_path or (
        PROJECT_ROOT / "logs" / "sku_offline" / "run_reports" / f"{run_id}.jsonl"
    )
    recorded_failures = _load_recorded_terminal_failures(resolved_report_path)
    with connect_app_database(repository.config) as connection:
        cursor = connection.cursor()
        rows = cursor.execute(
            f"""
            SELECT item.id, item.task_key, item.offline_status, item.attempts,
                   item.error_category, item.error_message,
                   saga.operation_key, saga.run_id, saga.task_type, saga.account_key,
                   saga.state, saga.ali1688_status,
                   saga.account_fencing_token, saga.evidence_json,
                   saga.error_code, saga.error_summary,
                   (SELECT COUNT_BIG(1) FROM {outbox_table} AS outbox_row
                     WHERE outbox_row.operation_key = item.task_key) AS outbox_count
            FROM {item_table} AS item WITH (UPDLOCK, HOLDLOCK)
            LEFT JOIN {saga_table} AS saga WITH (UPDLOCK, HOLDLOCK)
              ON saga.operation_key = item.task_key
            WHERE item.run_id = ?
            ORDER BY item.id
            """,
            (run_id,),
        ).fetchall()
        item_updates = 0
        saga_updates = 0
        recorded_failure_count = 0
        interrupted_failure_count = 0
        item_operation_keys: set[str] = set()
        for row in rows:
            item_id = int(row[0])
            operation_key = str(row[1] or "")
            offline_status = str(row[2] or "")
            error_category = str(row[4] or "")
            error_message = str(row[5] or "")
            saga_operation_key = str(row[6] or "")
            saga_run_id = str(row[7] or "")
            saga_task_type = str(row[8] or "")
            saga_state = str(row[10] or "")
            account_fencing_token = int(row[12] or 0)
            saga_evidence = str(row[13] or "")
            saga_error_code = str(row[14] or "")
            saga_error_summary = str(row[15] or "")
            outbox_count = int(row[16] or 0)
            item_operation_keys.add(operation_key)
            recorded_failure = recorded_failures.get(operation_key)
            if recorded_failure is not None:
                error_category = _clean_text(
                    recorded_failure.get("error_category")
                    or recorded_failure.get("page_error_category")
                    or "automation_error",
                    100,
                )
                error_message = _clean_text(
                    recorded_failure.get("error_message")
                    or recorded_failure.get("page_error_text")
                    or reason,
                    2000,
                )
                saga_error_code_expected = error_category
                saga_error_summary_expected = error_message
            else:
                error_category = "automation_error"
                error_message = reason
                saga_error_code_expected = "interrupted_executor_process"
                saga_error_summary_expected = reason
            if not operation_key or saga_operation_key != operation_key:
                raise RuntimeError(f"Interrupted recovery Saga is missing or mismatched: item_id={item_id}")
            if saga_run_id != run_id or saga_task_type != "stop_sale":
                raise RuntimeError(
                    f"Interrupted recovery Saga ownership changed: operation_key={operation_key}"
                )
            if outbox_count:
                raise RuntimeError(
                    f"Interrupted recovery unexpectedly has an Outbox row: operation_key={operation_key}"
                )
            if offline_status not in {*RECOVERABLE_UNSTARTED_ITEM_STATUSES, "failed"}:
                raise RuntimeError(
                    f"Interrupted recovery item has unexpected status: item_id={item_id}, "
                    f"status={offline_status!r}"
                )
            if offline_status == "failed" and (
                str(row[4] or "") != error_category or str(row[5] or "") != error_message
            ):
                raise RuntimeError(f"Interrupted recovery item terminal evidence changed: item_id={item_id}")
            if saga_state not in {"prepared", "failed_terminal"}:
                raise RuntimeError(
                    f"Interrupted recovery Saga has unexpected state: operation_key={operation_key}, "
                    f"state={saga_state!r}"
                )
            if saga_state == "failed_terminal" and (
                saga_error_code != saga_error_code_expected
                or saga_error_summary != saga_error_summary_expected
            ):
                raise RuntimeError(
                    f"Interrupted recovery Saga terminal evidence changed: operation_key={operation_key}"
                )
            if offline_status in RECOVERABLE_UNSTARTED_ITEM_STATUSES:
                attempts = max(1, int((recorded_failure or {}).get("attempts") or 0))
                screenshot_path = _clean_text(
                    (recorded_failure or {}).get("screenshot_path"), 1000
                )
                html_snapshot_path = _clean_text(
                    (recorded_failure or {}).get("html_snapshot_path"), 1000
                )
                cursor.execute(
                    f"""
                    UPDATE {item_table}
                    SET offline_status = 'failed',
                        attempts = CASE WHEN ISNULL(attempts, 0) < ? THEN ? ELSE attempts END,
                        error_category = ?, error_message = ?,
                        screenshot_path = ?, html_snapshot_path = ?,
                        updated_at = SYSUTCDATETIME()
                    WHERE id = ? AND run_id = ? AND offline_status = ?
                    """,
                    (
                        attempts,
                        attempts,
                        error_category,
                        error_message,
                        screenshot_path,
                        html_snapshot_path,
                        item_id,
                        run_id,
                        offline_status,
                    ),
                )
                if cursor.rowcount != 1:
                    raise RuntimeError(f"Interrupted recovery item CAS failed: item_id={item_id}")
                item_updates += 1
            if saga_state == "prepared":
                try:
                    current_evidence = json.loads(saga_evidence) if saga_evidence else {}
                except json.JSONDecodeError:
                    current_evidence = {"previous_evidence_raw": saga_evidence}
                current_evidence["interrupted_recovery"] = evidence
                if recorded_failure is not None:
                    current_evidence["recorded_terminal_failure"] = recorded_failure
                cursor.execute(
                    f"""
                    UPDATE {saga_table}
                    SET state = 'failed_terminal', ali1688_status = 'failed',
                        evidence_json = ?, error_code = ?,
                        error_summary = ?,
                        ali1688_finished_at = COALESCE(ali1688_finished_at, SYSUTCDATETIME()),
                        finished_at = COALESCE(finished_at, SYSUTCDATETIME()),
                        updated_at = SYSUTCDATETIME()
                    WHERE operation_key = ? AND run_id = ?
                      AND account_fencing_token = ? AND state = 'prepared'
                    """,
                    (
                        json.dumps(current_evidence, ensure_ascii=False, default=str),
                        saga_error_code_expected,
                        saga_error_summary_expected,
                        operation_key,
                        run_id,
                        account_fencing_token,
                    ),
                )
                if cursor.rowcount != 1:
                    raise RuntimeError(
                        f"Interrupted recovery Saga CAS failed: operation_key={operation_key}"
                    )
                saga_updates += 1
            if recorded_failure is not None:
                recorded_failure_count += 1
            else:
                interrupted_failure_count += 1
        unknown_report_keys = sorted(set(recorded_failures) - item_operation_keys)
        if unknown_report_keys:
            raise RuntimeError(
                "Interrupted recovery report contains evidence outside the audit run: "
                + ",".join(unknown_report_keys)
            )
        counts = cursor.execute(
            f"""
            SELECT
                COUNT(DISTINCT CASE WHEN offline_status = 'success' THEN offline_task_key END),
                COUNT(DISTINCT CASE WHEN offline_status = 'already_offline' THEN offline_task_key END),
                COUNT(DISTINCT CASE WHEN offline_status = 'failed' THEN offline_task_key END),
                COUNT(DISTINCT CASE WHEN offline_status = 'not_attempted' THEN offline_task_key END),
                SUM(CASE WHEN jushuitan_status = 'success' THEN 1 ELSE 0 END),
                SUM(CASE WHEN jushuitan_status = 'already_cleared' THEN 1 ELSE 0 END),
                SUM(CASE WHEN jushuitan_status = 'failed' THEN 1 ELSE 0 END)
            FROM {item_table}
            WHERE run_id = ?
            """,
            (run_id,),
        ).fetchone()
        count_values = [int(value or 0) for value in counts]
        cursor.execute(
            f"""
            UPDATE {run_table}
            SET offline_success_count = ?, offline_already_count = ?,
                offline_failed_count = ?, not_attempted_count = ?,
                jushuitan_success_count = ?, jushuitan_already_count = ?,
                jushuitan_failed_count = ?, updated_at = SYSUTCDATETIME()
            WHERE run_id = ?
            """,
            (*count_values, run_id),
        )
        if cursor.rowcount != 1:
            raise RuntimeError("Interrupted recovery run count refresh failed.")
        connection.commit()
    return {
        "item_count": len(rows),
        "item_terminalized_count": item_updates,
        "saga_terminalized_count": saga_updates,
        "recorded_terminal_failure_count": recorded_failure_count,
        "interrupted_failure_count": interrupted_failure_count,
        "offline_report_path": str(resolved_report_path),
        "outbox_count": 0,
        "jushuitan_action": "not_created_ali1688_failed",
    }


def recover(args: argparse.Namespace) -> dict[str, Any]:
    if not args.yes:
        raise ValueError("Recovery requires --yes")
    run_id = str(args.run_id).strip()
    reason = str(args.reason).strip()[:2000]
    if not reason:
        raise ValueError("Recovery reason is required")
    lock_path = Path(args.shared_lock_path).resolve()

    repository = StopSaleAuditRepository(
        resolve_stop_sale_app_config(Path(args.shared_runtime_root).resolve())
    )
    run_state = _query_run_state(repository, run_id)
    if not lock_path.exists():
        summary, summary_path = _load_completed_recovery_summary(
            run_state,
            run_id=run_id,
            reason=reason,
            lock_path=lock_path,
        )
        summary["audit_reconciliation"] = _reconcile_interrupted_audit(
            repository,
            run_id=run_id,
            reason=reason,
        )
        summary["audit_reconciled_at"] = datetime.now().isoformat(timespec="seconds")
        summary_path.write_text(
            json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        return summary

    lock_payload = _load_owned_interrupted_lock(lock_path, run_id)
    if run_state["status"] != "running" or run_state["finished_at"] is not None:
        raise RuntimeError(
            f"Stop-sale audit run is not recoverable: status={run_state['status']!r}, "
            f"finished_at={run_state['finished_at']!r}"
        )

    audit_reconciliation = _reconcile_interrupted_audit(
        repository,
        run_id=run_id,
        reason=reason,
    )

    credentials = hydrate_dingtalk_credentials(Path(args.shared_runtime_root).resolve())
    notification_sent = False
    if all(credentials.values()):
        notification_sent = send_pipeline_notification(
            "[1688 stop-sale pipeline]\n"
            f"run_id: {run_id}\n"
            "status: failed\n"
            "exception: interrupted_executor_process\n"
            f"error: {reason[:500]}\n"
            "action: audit finalized; exact orphan lock released; task may be retried",
            disabled=False,
        )

    pipeline_dir = PROJECT_ROOT / "logs" / "sku_offline" / "pipelines"
    pipeline_dir.mkdir(parents=True, exist_ok=True)
    summary_path = pipeline_dir / f"{run_id}.recovery.summary.json"
    offline_report_path = (
        PROJECT_ROOT / "logs" / "sku_offline" / "run_reports" / f"{run_id}.jsonl"
    )
    finished_at = datetime.now().isoformat(timespec="seconds")
    summary = {
        "run_id": run_id,
        "audit_status": "failed",
        "error_type": "InterruptedExecutorProcess",
        "error_message": reason,
        "notification_sent": bool(notification_sent),
        "shared_lock_path": str(lock_path),
        "lock_owner_pid": int(lock_payload.get("pid") or 0),
        "recovered_at": finished_at,
        "finished_at": finished_at,
    }
    summary["audit_reconciliation"] = audit_reconciliation
    summary_path.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    repository.finish_run(
        run_id=run_id,
        status="failed",
        offline_report_path=str(offline_report_path) if offline_report_path.exists() else "",
        jushuitan_report_path="",
        summary_path=str(summary_path),
        error_message=reason,
        notification_sent=bool(notification_sent),
    )

    current_payload = _load_owned_interrupted_lock(lock_path, run_id)
    if str(current_payload.get("token") or "") != str(lock_payload.get("token") or ""):
        raise RuntimeError("Shared lock token changed during interrupted-run recovery.")
    lock_path.unlink()
    summary["lock_removed"] = True
    summary_path.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return summary


def main() -> int:
    args = build_argument_parser().parse_args()
    print(json.dumps(recover(args), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
