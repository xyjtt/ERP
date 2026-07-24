"""Read a redacted listing task, execution summary, and recent audit events."""

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

from stop_sale_audit import connect_app_database, resolve_stop_sale_app_config


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Inspect one formal 1688 listing audit task.")
    parser.add_argument("--task-id", required=True)
    parser.add_argument(
        "--shared-runtime-root",
        default=os.getenv("YYDD_1688_RUNTIME_ROOT", "D:/script_1688"),
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    config = resolve_stop_sale_app_config(args.shared_runtime_root)
    with connect_app_database(config) as connection:
        cursor = connection.cursor()
        task_row = cursor.execute(
            """
            SELECT task_id, platform, shop_name, account_key, company_sku, company_spu,
                   novelty_type, workflow_state, approval_status, approved_by,
                   approved_at, created_at, updated_at
            FROM app.ali1688_listing_task
            WHERE task_id = ?
            """,
            args.task_id,
        ).fetchone()
        if task_row is None:
            raise RuntimeError(f"Listing task not found: {args.task_id}")
        task_columns = [column[0] for column in cursor.description]
        task = dict(zip(task_columns, task_row))

        execution_rows = cursor.execute(
            """
            SELECT execution_id, execution_mode, status, offer_id, offer_url,
                   error_code, error_summary, started_at, finished_at
            FROM app.ali1688_listing_execution
            WHERE task_id = ?
            ORDER BY started_at DESC
            """,
            args.task_id,
        ).fetchall()
        execution_columns = [column[0] for column in cursor.description]
        executions = [dict(zip(execution_columns, row)) for row in execution_rows]

        audit_rows = cursor.execute(
            """
            SELECT TOP (20) event_type, from_state, to_state, operator_name, created_at
            FROM app.ali1688_listing_audit
            WHERE task_id = ?
            ORDER BY created_at DESC, id DESC
            """,
            args.task_id,
        ).fetchall()
        audit_columns = [column[0] for column in cursor.description]
        audits = [dict(zip(audit_columns, row)) for row in audit_rows]

    print(
        json.dumps(
            {
                "database": config.database,
                "schema": config.schema,
                "task": task,
                "execution_count": len(executions),
                "executions": executions,
                "recent_audits": audits,
            },
            ensure_ascii=False,
            indent=2,
            default=str,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
