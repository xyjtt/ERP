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
    resolve_stop_sale_app_config,
)


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
            f"SELECT status, finished_at FROM {run_table} WHERE run_id = ?", (run_id,)
        ).fetchone()
    if row is None:
        raise RuntimeError(f"Stop-sale audit run was not found: {run_id}")
    return {"status": str(row[0] or ""), "finished_at": row[1]}


def recover(args: argparse.Namespace) -> dict[str, Any]:
    if not args.yes:
        raise ValueError("Recovery requires --yes")
    run_id = str(args.run_id).strip()
    reason = str(args.reason).strip()
    lock_path = Path(args.shared_lock_path).resolve()
    lock_payload = _load_owned_interrupted_lock(lock_path, run_id)

    repository = StopSaleAuditRepository(
        resolve_stop_sale_app_config(Path(args.shared_runtime_root).resolve())
    )
    run_state = _query_run_state(repository, run_id)
    if run_state["status"] != "running" or run_state["finished_at"] is not None:
        raise RuntimeError(
            f"Stop-sale audit run is not recoverable: status={run_state['status']!r}, "
            f"finished_at={run_state['finished_at']!r}"
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
    summary = {
        "run_id": run_id,
        "audit_status": "failed",
        "error_type": "InterruptedExecutorProcess",
        "error_message": reason,
        "notification_sent": bool(notification_sent),
        "shared_lock_path": str(lock_path),
        "lock_owner_pid": int(lock_payload.get("pid") or 0),
        "recovered_at": datetime.now().isoformat(timespec="seconds"),
    }
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
