"""Validate or apply the title-engine DDL in one transaction."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from database.db_manager import DatabaseManager


DDL_PATHS = (
    PROJECT_ROOT / "sql" / "350_ddl_title_engine_app_2026-07-20.sql",
    PROJECT_ROOT / "sql" / "351_ddl_hsds_root_word_snapshot_2026-07-22.sql",
)
REQUIRED_TABLES = (
    "ali1688_title_keyword",
    "ali1688_title_generation_history",
    "ali1688_title_hsds_root_word_snapshot",
    "ali1688_title_source_sync_run",
)


def split_batches(sql: str) -> list[str]:
    return [batch.strip() for batch in re.split(r"(?im)^\s*GO\s*$", sql) if batch.strip()]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Validate or apply the title-engine DDL.")
    parser.add_argument("--apply", action="store_true", help="Commit the DDL. Default mode rolls back.")
    return parser.parse_args()


def check_contract(cursor, *, schema: str) -> dict[str, object]:
    missing = []
    for table in REQUIRED_TABLES:
        object_name = f"{schema}.{table}"
        if cursor.execute("SELECT OBJECT_ID(?, 'U')", object_name).fetchone()[0] is None:
            missing.append(object_name)
    return {"schema": schema, "missing_tables": missing, "ready": not missing}


def main() -> int:
    args = parse_args()
    manager = DatabaseManager()
    schema = str(manager._database_config.get("schema") or "app")
    with manager.get_connection() as connection:
        cursor = connection.cursor()
        database_name = str(cursor.execute("SELECT DB_NAME()").fetchone()[0])
        if database_name != "JSReportReplica":
            raise RuntimeError(f"DDL target mismatch: {database_name}")
        for path in DDL_PATHS:
            for batch in split_batches(path.read_text(encoding="utf-8-sig")):
                cursor.execute(batch)
        contract = check_contract(cursor, schema=schema)
        if args.apply:
            connection.commit()
        else:
            connection.rollback()

    print(
        json.dumps(
            {
                "mode": "apply" if args.apply else "validate_only",
                "database": database_name,
                "schema": schema,
                "ddl": [str(path) for path in DDL_PATHS],
                "transaction": "committed" if args.apply else "rolled_back",
                "contract": contract,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
