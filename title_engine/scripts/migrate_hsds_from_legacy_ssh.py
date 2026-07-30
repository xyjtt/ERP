"""Audit or migrate the legacy HSDS MySQL source through an SSH boundary."""

from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import sys
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from config.database_credentials import load_external_credential
from database.db_manager import DatabaseManager
from scripts.migrate_hsds_root_words import apply_plan, build_plan


DEFAULT_CREDENTIAL_REF = "YYDD/1688/database/hsds-legacy-ssh"


def checked_identifier(value: str, label: str) -> str:
    text = str(value or "").strip()
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", text):
        raise ValueError(f"Invalid {label}: {text}")
    return text


def build_remote_node_script(table: str, *, include_rows: bool) -> str:
    source_table = checked_identifier(table, "source table")
    include_rows_js = "true" if include_rows else "false"
    return f"""
require('dotenv').config();
const mysql = require('mysql2/promise');
const env = process.env;
const first = (...names) => names.map((name) => env[name]).find((value) => value !== undefined && value !== '');
const config = {{
  host: first('DB_HOST', 'MYSQL_HOST'),
  port: Number(first('DB_PORT', 'MYSQL_PORT') || 3306),
  user: first('DB_USER', 'DB_USERNAME', 'MYSQL_USER'),
  password: first('DB_PASSWORD', 'MYSQL_PASSWORD'),
  database: first('DB_NAME', 'DB_DATABASE', 'MYSQL_DATABASE'),
  charset: 'utf8mb4',
}};
const missing = Object.entries(config)
  .filter(([key, value]) => ['host', 'user', 'password', 'database'].includes(key) && !value)
  .map(([key]) => key);
if (missing.length) throw new Error('Missing remote database settings: ' + missing.join(', '));
(async () => {{
  const connection = await mysql.createConnection(config);
  try {{
    const [columns] = await connection.query('SHOW COLUMNS FROM `{source_table}`');
    const columnNames = new Set(columns.map((column) => String(column.Field)));
    const orderColumns = ['stat_date', 'id', 'source_record_key', 'root_word']
      .filter((name) => columnNames.has(name));
    const orderClause = orderColumns.length
      ? ' ORDER BY ' + orderColumns.map((name) => '`' + name + '`').join(', ')
      : '';
    const [statsRows] = await connection.query(
      'SELECT COUNT(*) AS row_count, MIN(`stat_date`) AS min_stat_date, MAX(`stat_date`) AS max_stat_date, '
      + 'SUM(CASE WHEN `root_word` IS NULL OR TRIM(`root_word`) = "" THEN 1 ELSE 0 END) AS empty_root_count, '
      + 'SUM(COALESCE(`search_people`, 0)) AS search_people_sum FROM `{source_table}`'
    );
    const [dailyRows] = await connection.query(
      'SELECT `stat_date`, COUNT(*) AS row_count, SUM(COALESCE(`search_people`, 0)) AS search_people_sum '
      + 'FROM `{source_table}` GROUP BY `stat_date` ORDER BY `stat_date`'
    );
    const rowQuery = {include_rows_js}
      ? 'SELECT * FROM `{source_table}`' + orderClause
      : 'SELECT NULL WHERE 1=0';
    const [rows] = await connection.query(rowQuery);
    process.stdout.write(JSON.stringify({{
      source_table: '{source_table}',
      columns,
      stats: statsRows[0] || {{}},
      daily: dailyRows,
      rows,
    }}));
  }} finally {{
    await connection.end();
  }}
}})().catch((error) => {{
  process.stderr.write(JSON.stringify({{ error: error.message, error_type: error.name }}));
  process.exit(1);
}});
""".strip()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Audit or migrate HSDS through the legacy SSH host.")
    parser.add_argument("--host", default="ltai5tdb2ogyj59fquyedh.yydd.ai")
    parser.add_argument("--port", type=int, default=60022)
    parser.add_argument("--credential-ref", default=DEFAULT_CREDENTIAL_REF)
    parser.add_argument("--remote-root", default="/www/wwwroot/shujuku2/backend")
    parser.add_argument("--source-table", default="planning_hsds_root_word_daily_source")
    parser.add_argument("--output", default="")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--ack-production-write", action="store_true")
    return parser.parse_args()


def load_legacy_source(args: argparse.Namespace) -> dict[str, Any]:
    try:
        import paramiko
    except ImportError as exc:
        raise RuntimeError("paramiko is required for the legacy HSDS SSH migration") from exc

    username, password = load_external_credential(args.credential_ref)
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        client.connect(
            hostname=args.host,
            port=args.port,
            username=username,
            password=password,
            look_for_keys=False,
            allow_agent=False,
            timeout=15,
            banner_timeout=15,
            auth_timeout=15,
        )
        script = build_remote_node_script(args.source_table, include_rows=args.apply)
        command = f"cd {shlex.quote(args.remote_root)} && node -e {shlex.quote(script)}"
        _stdin, stdout, stderr = client.exec_command(command, timeout=300)
        output = stdout.read().decode("utf-8")
        error = stderr.read().decode("utf-8")
        status = stdout.channel.recv_exit_status()
        if status != 0:
            try:
                details = json.loads(error)
            except json.JSONDecodeError:
                details = {"error": "Legacy HSDS command failed without structured details."}
            raise RuntimeError(str(details.get("error") or "Legacy HSDS command failed."))
        payload = json.loads(output)
        if not isinstance(payload, dict):
            raise RuntimeError("Legacy HSDS response is not a JSON object.")
        return payload
    finally:
        client.close()


def target_summary(run_id: str) -> dict[str, Any]:
    manager = DatabaseManager()
    with manager.get_connection() as connection:
        cursor = connection.cursor()
        row = cursor.execute(
            """
            SELECT COUNT(*) AS row_count, MIN(stat_date) AS min_stat_date,
                   MAX(stat_date) AS max_stat_date,
                   SUM(COALESCE(search_people, 0)) AS search_people_sum
            FROM app.ali1688_title_hsds_root_word_snapshot
            WHERE migration_run_id = ?
            """,
            run_id,
        ).fetchone()
        return {
            "row_count": int(row[0] or 0),
            "min_stat_date": str(row[1] or ""),
            "max_stat_date": str(row[2] or ""),
            "search_people_sum": str(row[3] or 0),
        }


def main() -> int:
    args = parse_args()
    if args.apply:
        enabled = str(os.getenv("ENABLE_HSDS_MIGRATION_WRITE", "")).strip().lower()
        if not args.ack_production_write or enabled not in {"1", "true", "yes", "on"}:
            raise RuntimeError(
                "--apply requires --ack-production-write and ENABLE_HSDS_MIGRATION_WRITE=true"
            )

    source = load_legacy_source(args)
    report: dict[str, Any] = {
        "mode": "apply" if args.apply else "audit_only",
        "source_table": source.get("source_table"),
        "source_columns": source.get("columns") or [],
        "source_stats": source.get("stats") or {},
        "source_daily": source.get("daily") or [],
    }
    if args.apply:
        plan = build_plan(source.get("rows") or [])
        migrated = apply_plan(plan)
        target = target_summary(plan["run_id"])
        source_count = int((source.get("stats") or {}).get("row_count") or 0)
        source_sum = Decimal(str((source.get("stats") or {}).get("search_people_sum") or 0))
        target_sum = Decimal(str(target["search_people_sum"] or 0))
        quantum = Decimal("0.0001")
        sum_match = (
            source_sum.quantize(quantum, rounding=ROUND_HALF_UP)
            == target_sum.quantize(quantum, rounding=ROUND_HALF_UP)
        )
        report.update(
            {
                "run_id": plan["run_id"],
                "extracted_count": plan["extracted_count"],
                "rejected_count": plan["rejected_count"],
                "upserted_count": migrated["upserted_count"],
                "target": target,
                "count_match": source_count == target["row_count"] == plan["extracted_count"],
                "search_people_sum_match": sum_match,
            }
        )
        if not report["count_match"] or not sum_match or plan["rejected_count"]:
            raise RuntimeError("HSDS migration count reconciliation failed.")

    rendered = json.dumps(report, ensure_ascii=False, indent=2, default=str)
    if args.output:
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
