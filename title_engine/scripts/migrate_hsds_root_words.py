# -*- coding: utf-8 -*-
"""Dry-run-first HSDS root-word migration plan builder.

The command only builds a parameter-ready plan. It never writes to a source
or target database; deployment of the reviewed SQL and writer remains a
separate authorized operation.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import uuid
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any, Iterable


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from database.db_manager import DatabaseManager


def _optional_decimal(value: Any) -> Decimal | None:
    if value in (None, ""):
        return None
    try:
        return Decimal(str(value))
    except Exception:
        return None


def _optional_date(value: Any) -> date | None:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value).strip()[:10])
    except ValueError:
        return None


def _optional_bool(value: Any) -> bool | None:
    if value in (None, ""):
        return None
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "y"}:
        return True
    if text in {"0", "false", "no", "n"}:
        return False
    return None


def normalize_hsds_row(row: dict[str, Any], *, window_start: date, window_end: date) -> dict[str, Any] | None:
    root_word = str(row.get("root_word") or row.get("word") or "").strip()
    if not root_word:
        return None
    category = [
        str(row.get(key) or "").strip()
        for key in ("category_level_1", "category_level_2", "category_level_3")
    ]
    stat_date = _optional_date(row.get("stat_date") or row.get("source_stat_date")) or window_end
    row_window_end = _optional_date(row.get("window_end")) or stat_date
    row_window_start = _optional_date(row.get("window_start")) or (row_window_end - timedelta(days=6))
    source_key = str(
        row.get("source_record_key")
        or row.get("id")
        or "|".join(category + [stat_date.isoformat(), root_word])
    ).strip()
    source_hash = hashlib.sha256(
        json.dumps(
            {"category": category, "root_word": root_word, "raw": row},
            ensure_ascii=False,
            sort_keys=True,
            default=str,
        ).encode("utf-8")
    ).hexdigest()
    return {
        "stat_date": stat_date,
        "window_start": row_window_start,
        "window_end": row_window_end,
        "category_level_1": category[0] or None,
        "category_level_2": category[1] or None,
        "category_level_3": category[2] or None,
        "root_word": root_word,
        "search_people": _optional_decimal(row.get("search_people")),
        "search_people_grow": _optional_decimal(row.get("search_people_grow")),
        "demand_primary_name": str(row.get("demand_primary_name") or "").strip() or None,
        "is_new_root": _optional_bool(row.get("is_new_root")),
        "source_type": "hsds_demand_root",
        "source_record_key": source_key,
        "source_hash": source_hash,
        "raw_payload": json.dumps(row, ensure_ascii=False, default=str),
        "source_updated_at": row.get("updated_at") or row.get("source_updated_at"),
    }


def build_plan(rows: Iterable[dict[str, Any]], *, today: date | None = None) -> dict[str, Any]:
    raw_rows = list(rows)
    end = today or date.today()
    start = end - timedelta(days=6)
    normalized = [
        item
        for row in raw_rows
        if (item := normalize_hsds_row(row, window_start=start, window_end=end))
    ]
    plan_start = min((item["window_start"] for item in normalized), default=start)
    plan_end = max((item["window_end"] for item in normalized), default=end)
    return {
        "read_only": True,
        "run_id": str(uuid.uuid4()),
        "source_type": "hsds_demand_root",
        "window_start": plan_start.isoformat(),
        "window_end": plan_end.isoformat(),
        "extracted_count": len(normalized),
        "upserted_count": 0,
        "rejected_count": len(raw_rows) - len(normalized),
        "rows": normalized,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build a dry-run HSDS migration plan.")
    parser.add_argument("--input", required=True, help="JSON array exported from the HSDS source.")
    parser.add_argument("--output", default="", help="Optional plan JSON output path.")
    parser.add_argument("--apply", action="store_true", help="Apply the plan to the configured app schema.")
    parser.add_argument(
        "--ack-production-write",
        action="store_true",
        help="Required with --apply after deployment approval is recorded.",
    )
    return parser


def apply_plan(plan: dict[str, Any], db_manager: DatabaseManager | None = None) -> dict[str, Any]:
    manager = db_manager or DatabaseManager()
    merge_sql = """
        MERGE app.ali1688_title_hsds_root_word_snapshot AS target
        USING (SELECT ? AS stat_date, ? AS window_start, ? AS window_end,
                      ? AS category_level_1, ? AS category_level_2, ? AS category_level_3,
                      ? AS root_word, ? AS search_people, ? AS search_people_grow,
                      ? AS demand_primary_name, ? AS is_new_root, ? AS source_type,
                      ? AS source_record_key, ? AS source_hash, ? AS raw_payload,
                      ? AS source_updated_at, ? AS migration_run_id) AS source
        ON target.stat_date = source.stat_date
       AND ISNULL(target.category_level_1, N'') = ISNULL(source.category_level_1, N'')
       AND ISNULL(target.category_level_2, N'') = ISNULL(source.category_level_2, N'')
       AND ISNULL(target.category_level_3, N'') = ISNULL(source.category_level_3, N'')
       AND target.root_word = source.root_word
       AND target.source_record_key = source.source_record_key
        WHEN MATCHED THEN UPDATE SET
            target.window_start = source.window_start,
            target.window_end = source.window_end,
            target.search_people = source.search_people,
            target.search_people_grow = source.search_people_grow,
            target.demand_primary_name = source.demand_primary_name,
            target.is_new_root = source.is_new_root,
            target.source_type = source.source_type,
            target.source_hash = source.source_hash,
            target.raw_payload = source.raw_payload,
            target.source_updated_at = source.source_updated_at,
            target.extracted_at = SYSUTCDATETIME(),
            target.migration_run_id = source.migration_run_id,
            target.loaded_at = SYSUTCDATETIME()
        WHEN NOT MATCHED THEN INSERT (
            stat_date, window_start, window_end,
            category_level_1, category_level_2, category_level_3,
            root_word, search_people, search_people_grow,
            demand_primary_name, is_new_root, source_type,
            source_record_key, source_hash, raw_payload,
            source_updated_at, migration_run_id
        ) VALUES (
            source.stat_date, source.window_start, source.window_end,
            source.category_level_1, source.category_level_2, source.category_level_3,
            source.root_word, source.search_people, source.search_people_grow,
            source.demand_primary_name, source.is_new_root, source.source_type,
            source.source_record_key, source.source_hash, source.raw_payload,
            source.source_updated_at, source.migration_run_id
        );
    """
    run_sql = """
        INSERT INTO app.ali1688_title_source_sync_run (
            run_id, source_type, window_start, window_end, started_at, finished_at,
            status, extracted_count, upserted_count, rejected_count,
            source_max_date, target_max_date, error_summary
        ) VALUES (?, ?, ?, ?, SYSUTCDATETIME(), SYSUTCDATETIME(),
                  N'succeeded', ?, ?, ?, ?, ?, NULL)
    """
    upserted = 0
    with manager.get_connection() as connection:
        cursor = connection.cursor()
        try:
            for row in plan.get("rows", []):
                cursor.execute(
                    merge_sql,
                    row["stat_date"], row["window_start"], row["window_end"],
                    row.get("category_level_1"), row.get("category_level_2"), row.get("category_level_3"),
                    row["root_word"], row.get("search_people"), row.get("search_people_grow"),
                    row.get("demand_primary_name"), row.get("is_new_root"), row["source_type"],
                    row["source_record_key"], row["source_hash"], row.get("raw_payload"),
                    row.get("source_updated_at"), plan["run_id"],
                )
                upserted += 1
            cursor.execute(
                run_sql,
                plan["run_id"], plan["source_type"], plan["window_start"], plan["window_end"],
                plan["extracted_count"], upserted, plan["rejected_count"],
                plan["window_end"], plan["window_end"],
            )
            connection.commit()
        except Exception:
            connection.rollback()
            raise
    return {**plan, "read_only": False, "upserted_count": upserted, "rows": []}


def main() -> int:
    args = build_parser().parse_args()
    with open(args.input, "r", encoding="utf-8-sig") as file:
        rows = json.load(file)
    if not isinstance(rows, list):
        raise ValueError("HSDS input must be a JSON array.")
    plan = build_plan(rows)
    if args.apply:
        write_enabled = str(os.getenv("ENABLE_HSDS_MIGRATION_WRITE", "")).strip().lower() in {
            "1", "true", "yes", "on"
        }
        if not args.ack_production_write or not write_enabled:
            raise RuntimeError(
                "--apply requires --ack-production-write and ENABLE_HSDS_MIGRATION_WRITE=true"
            )
        plan = apply_plan(plan)
    rendered = json.dumps(plan, ensure_ascii=False, indent=2, default=str)
    if args.output:
        with open(args.output, "w", encoding="utf-8") as file:
            file.write(rendered + "\n")
    print(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
