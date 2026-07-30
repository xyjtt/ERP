from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
RPA_ROOT = PROJECT_ROOT / "rpa"
if str(RPA_ROOT) not in sys.path:
    sys.path.insert(0, str(RPA_ROOT))

from operation_saga import OperationSagaRepository
from stop_sale_audit import connect_app_database, resolve_stop_sale_app_config


DDL_PATH = PROJECT_ROOT / "sql" / "362_ali1688_operation_saga_outbox.sql"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Validate or apply ERP 1688 Saga/Outbox DDL")
    parser.add_argument(
        "--shared-runtime-root",
        default=os.getenv("YYDD_1688_RUNTIME_ROOT", "D:/script_1688"),
    )
    parser.add_argument("--apply", action="store_true", help="Commit DDL; default validates then rolls back")
    return parser.parse_args()


def split_batches(sql: str) -> list[str]:
    return [batch.strip() for batch in re.split(r"(?im)^\s*GO\s*$", sql) if batch.strip()]


def main() -> int:
    args = parse_args()
    config = resolve_stop_sale_app_config(args.shared_runtime_root)
    with connect_app_database(config) as connection:
        cursor = connection.cursor()
        database_name = str(cursor.execute("SELECT DB_NAME()").fetchone()[0])
        if database_name != "JSReportReplica":
            raise RuntimeError(f"DDL target mismatch: {database_name}")
        for batch in split_batches(DDL_PATH.read_text(encoding="utf-8-sig")):
            cursor.execute(batch)
        if args.apply:
            connection.commit()
        else:
            connection.rollback()
    result = {
        "mode": "apply" if args.apply else "validate_only",
        "database": config.database,
        "schema": config.schema,
        "ddl": str(DDL_PATH),
        "transaction": "committed" if args.apply else "rolled_back",
    }
    if args.apply:
        result["contract"] = OperationSagaRepository(config).check_contract()
    print(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
