"""Read the current listing lifecycle row from JSDataMiddlePlatform."""

from __future__ import annotations

import argparse
import json
import socket
import sys
from datetime import datetime
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
RPA_ROOT = PROJECT_ROOT / "rpa"
if str(RPA_ROOT) not in sys.path:
    sys.path.insert(0, str(RPA_ROOT))

from database import resolve_sqlserver_driver
from stop_sale_audit import (
    StopSaleAppConfig,
    _load_secret_provider_module,
    connect_app_database,
)


DEFAULT_SERVER = "218.93.191.16"
DEFAULT_PORT = 1433
DEFAULT_DATABASE = "JSDataMiddlePlatform"
DEFAULT_CREDENTIAL_REF = "YYDD/1688/database/stop-sale-source"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Read current 1688 listing source lifecycle fields.")
    parser.add_argument("--shared-runtime-root", required=True)
    parser.add_argument("--sku", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--server", default=DEFAULT_SERVER)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--database", default=DEFAULT_DATABASE)
    parser.add_argument("--credential-ref", default=DEFAULT_CREDENTIAL_REF)
    parser.add_argument("--driver", default="ODBC Driver 17 for SQL Server")
    return parser


def _flag_matches(value: object, expected: bool) -> bool:
    if isinstance(value, bool):
        return value is expected
    accepted = {"1", "true", "yes"} if expected else {"0", "false", "no"}
    return str(value).strip().lower() in accepted


def load_source_rows(connection: Any, sku: str) -> tuple[list[dict[str, Any]], dict[str, str]]:
    cursor = connection.cursor()
    cursor.execute("SELECT TOP 0 * FROM dbo.jst_sku")
    columns = {str(item[0]).lower(): str(item[0]) for item in cursor.description}
    required = ["sku_id", "enabled", "stock_disabled", "other_5", "item_type"]
    missing = [name for name in required if name not in columns]
    if missing:
        raise RuntimeError(f"dbo.jst_sku missing required columns: {','.join(missing)}")

    optional = [
        name
        for name in ("autoid", "i_id", "spu", "name", "category", "modified", "updated")
        if name in columns
    ]
    selected = optional + required
    select_list = ", ".join(f"[{columns[name]}]" for name in selected)
    order_by = f" ORDER BY [{columns['autoid']}] DESC" if "autoid" in columns else ""
    cursor.execute(
        f"SELECT TOP 5 {select_list} FROM dbo.jst_sku "
        f"WHERE [{columns['sku_id']}] = ?{order_by}",
        sku,
    )
    result_columns = [str(item[0]) for item in cursor.description]
    rows = [dict(zip(result_columns, row)) for row in cursor.fetchall()]
    return rows, columns


def source_gate_passed(rows: list[dict[str, Any]], columns: dict[str, str]) -> bool:
    if not rows:
        return False
    latest = rows[0]
    return (
        _flag_matches(latest.get(columns["enabled"]), True)
        and _flag_matches(latest.get(columns["stock_disabled"]), False)
        and str(latest.get(columns["other_5"]) or "").strip() == "销售"
        and str(latest.get(columns["item_type"]) or "").strip() == "成品"
    )


def main() -> int:
    args = build_parser().parse_args()
    runtime_root = Path(args.shared_runtime_root).resolve()
    provider_module = _load_secret_provider_module(runtime_root)
    record = provider_module.get_secret_provider().get(str(args.credential_ref).strip())
    driver = resolve_sqlserver_driver(args.driver)
    config = StopSaleAppConfig(
        server=str(args.server).strip(),
        port=int(args.port),
        database=str(args.database).strip(),
        schema="dbo",
        user=str(record.username),
        password=str(record.secret),
        driver=driver,
        encrypt=False,
        trust_server_certificate=True,
        timeout_seconds=15,
        credential_ref=str(args.credential_ref).strip(),
    )

    connection = connect_app_database(config)
    try:
        rows, columns = load_source_rows(connection, str(args.sku).strip())
    finally:
        connection.close()

    passed = source_gate_passed(rows, columns)
    result = {
        "checked_at": datetime.now().astimezone().isoformat(),
        "read_only": True,
        "observation_host": socket.gethostname(),
        "source_table": f"{config.database}.dbo.jst_sku",
        "sku": str(args.sku).strip(),
        "rows": rows,
        "gate": "passed" if passed else "failed",
        "required": {
            "enabled": 1,
            "stock_disabled": 0,
            "other_5": "销售",
            "item_type": "成品",
        },
        "connection": {
            "driver": driver,
            "credential_ref": config.credential_ref,
        },
    }
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({"gate": result["gate"], "row_count": len(rows)}, ensure_ascii=False))
    return 0 if passed else 2


if __name__ == "__main__":
    raise SystemExit(main())
