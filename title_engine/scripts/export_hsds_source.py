"""Export the legacy HSDS source with a parameterized, read-only MySQL query."""

from __future__ import annotations

import argparse
import json
import os
import re
from datetime import date, timedelta
from pathlib import Path


IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def checked_identifier(value: str, field_name: str) -> str:
    text = str(value or "").strip()
    if not IDENTIFIER.fullmatch(text):
        raise ValueError(f"{field_name} contains unsupported characters")
    return text


def build_query(table: str, date_column: str) -> str:
    table_name = checked_identifier(table, "source table")
    column_name = checked_identifier(date_column, "source date column")
    return f"SELECT * FROM `{table_name}` WHERE `{column_name}` >= %s AND `{column_name}` <= %s ORDER BY `{column_name}`"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Read-only seven-day HSDS export.")
    parser.add_argument("--output", required=True)
    parser.add_argument("--table", default="planning_hsds_root_word_daily_source")
    parser.add_argument("--date-column", default="stat_date")
    parser.add_argument("--window-end", default=date.today().isoformat())
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        import pymysql
    except ModuleNotFoundError as exc:
        raise RuntimeError("pymysql is required for the legacy HSDS export") from exc
    end = date.fromisoformat(args.window_end)
    start = end - timedelta(days=6)
    required = {
        "host": os.getenv("HSDS_MYSQL_HOST", ""),
        "user": os.getenv("HSDS_MYSQL_USER", ""),
        "password": os.getenv("HSDS_MYSQL_PASSWORD", ""),
        "database": os.getenv("HSDS_MYSQL_DATABASE", ""),
    }
    missing = [key for key, value in required.items() if not value]
    if missing:
        raise RuntimeError("Missing HSDS MySQL settings: " + ", ".join(missing))
    connection = pymysql.connect(
        host=required["host"],
        port=int(os.getenv("HSDS_MYSQL_PORT", "3306")),
        user=required["user"],
        password=required["password"],
        database=required["database"],
        charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor,
        read_timeout=30,
        write_timeout=30,
        autocommit=False,
    )
    try:
        with connection.cursor() as cursor:
            cursor.execute(build_query(args.table, args.date_column), (start, end))
            rows = cursor.fetchall()
    finally:
        connection.rollback()
        connection.close()
    Path(args.output).write_text(
        json.dumps(rows, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({"read_only": True, "row_count": len(rows), "window_start": start.isoformat(), "window_end": end.isoformat()}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
