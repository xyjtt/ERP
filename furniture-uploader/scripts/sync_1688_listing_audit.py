"""Synchronize an existing listing payload into the formal app audit tables."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
RPA_ROOT = PROJECT_ROOT / "rpa"
if str(RPA_ROOT) not in sys.path:
    sys.path.insert(0, str(RPA_ROOT))

from auto_listing import validate_listing_payload
from listing_audit import ListingAuditRepository
from stop_sale_audit import resolve_stop_sale_app_config


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Sync one 1688 listing payload to JSReportReplica.app.")
    parser.add_argument("--payload", required=True)
    parser.add_argument("--operator", required=True)
    parser.add_argument(
        "--shared-runtime-root",
        default=os.getenv("YYDD_1688_RUNTIME_ROOT", "D:/script_1688"),
    )
    parser.add_argument(
        "--record-latest-event",
        action="store_true",
        help="append the payload's latest workflow event to app.ali1688_listing_audit",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    payload = json.loads(Path(args.payload).read_text(encoding="utf-8-sig"))
    report = validate_listing_payload(payload, require_duplicate_clear=True)
    if report.get("status") != "passed":
        raise ValueError("listing payload preflight is not passed")

    config = resolve_stop_sale_app_config(args.shared_runtime_root)
    repository = ListingAuditRepository(config)
    contract = repository.check_contract()
    if not contract.get("ready"):
        missing = ", ".join(str(item) for item in contract.get("missing_tables") or [])
        raise RuntimeError(f"listing audit database contract is not ready: {missing}")
    repository.upsert_task(payload)
    if args.record_latest_event:
        repository.record_latest_event(payload, operator_name=args.operator)

    print(
        json.dumps(
            {
                "status": "synced",
                "task_id": payload.get("task_id"),
                "workflow_state": (payload.get("workflow") or {}).get("state"),
                "latest_event_recorded": bool(args.record_latest_event),
                "database": contract.get("database"),
                "schema": contract.get("schema"),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
