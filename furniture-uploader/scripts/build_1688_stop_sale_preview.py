from __future__ import annotations

import argparse
import csv
import json
import os
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any, Iterable, Sequence


STORE_NAME = "店铺名称"
PLATFORM = "平台"
METRIC_DATE = "指标日期"
HANDLING = "处理说明"
PRODUCT_ID = "商品ID"
PLATFORM_STORE_ITEM_CODE = "平台店铺商品编码"
ONLINE_SKU = "线上商品编码"
ONLINE_STOCK = "线上库存"
REPLACEMENT_SKU = "可替换商品编码"
CHANGE_IMAGE = "是否换图"

DEFAULT_PLATFORM = "Alibaba"
DEFAULT_HANDLING = "全渠道下架"
DEFAULT_TABLE = "dbo.op_stop_sale"
DEFAULT_DATABASE = "JSDataMiddlePlatform"
DEFAULT_OUTPUT_DIR = Path("logs/sku_offline/db_previews")

SOURCE_ENV_FALLBACKS = {
    "STOP_SALE_SOURCE_SQLSERVER_HOST": "STOP_SALE_SQLSERVER_HOST",
    "STOP_SALE_SOURCE_SQLSERVER_PORT": "STOP_SALE_SQLSERVER_PORT",
    "STOP_SALE_SOURCE_SQLSERVER_USER": "STOP_SALE_SQLSERVER_USER",
    "STOP_SALE_SOURCE_SQLSERVER_PASSWORD": "STOP_SALE_SQLSERVER_PASSWORD",
    "STOP_SALE_SOURCE_SQLSERVER_DRIVER": "STOP_SALE_SQLSERVER_DRIVER",
}

DEFAULT_TARGET_STORES = [
    "阿里巴巴-广州淘淘家居有限公司",
    "阿里巴巴-广州沃来贸易有限公司",
    "阿里巴巴-常州工莱家具",
    "阿里巴巴-常州乐畅家居有限公司",
]

OUTPUT_COLUMNS = [
    STORE_NAME,
    PLATFORM,
    PRODUCT_ID,
    PLATFORM_STORE_ITEM_CODE,
    ONLINE_SKU,
    HANDLING,
    REPLACEMENT_SKU,
    CHANGE_IMAGE,
    METRIC_DATE,
]

REPORT_COLUMNS = [
    STORE_NAME,
    PLATFORM,
    METRIC_DATE,
    HANDLING,
    PRODUCT_ID,
    PLATFORM_STORE_ITEM_CODE,
    ONLINE_SKU,
    ONLINE_STOCK,
    REPLACEMENT_SKU,
    CHANGE_IMAGE,
]

DB_FIELD_MAP = {
    STORE_NAME: "dpmc",
    PLATFORM: "pt",
    METRIC_DATE: "zbrq",
    HANDLING: "clsm",
    PRODUCT_ID: "spi",
    PLATFORM_STORE_ITEM_CODE: "ptdpspbm",
    ONLINE_SKU: "xsspbm",
    ONLINE_STOCK: "xskc",
    REPLACEMENT_SKU: "kthspbmx",
    CHANGE_IMAGE: "sfhtx",
}

REQUIRED_OUTPUT_COLUMNS = [
    STORE_NAME,
    PLATFORM,
    METRIC_DATE,
    HANDLING,
    PRODUCT_ID,
    ONLINE_SKU,
]


@dataclass(frozen=True)
class SqlServerConfig:
    server: str
    port: int
    database: str
    user: str
    password: str
    driver: str
    trust_server_certificate: bool = True
    timeout_seconds: int = 60

    def safe_dict(self) -> dict[str, Any]:
        return {
            "server": mask_network_endpoint(self.server),
            "port": "***",
            "database": self.database,
            "user": mask_identifier(self.user),
            "driver": self.driver,
            "trust_server_certificate": self.trust_server_certificate,
            "timeout_seconds": self.timeout_seconds,
        }


def mask_identifier(value: str) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    if len(text) <= 2:
        return "***"
    return f"{text[:2]}***"


def mask_network_endpoint(value: str) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    if re.match(r"^\d{1,3}(?:\.\d{1,3}){3}$", text):
        parts = text.split(".")
        return f"{parts[0]}.{parts[1]}.***.***"
    if "." in text:
        head = text.split(".", 1)[0]
        return f"{head[:2]}***"
    return mask_identifier(text)


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Read the legacy BI stop-sale source without writes and generate preview CSV files."
    )
    parser.add_argument("--date", default=date.today().isoformat(), help="Metric date in YYYY-MM-DD format.")
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR), help="Output directory for CSV/JSON files.")
    parser.add_argument("--table", default=DEFAULT_TABLE, help="Source table name, defaults to dbo.op_stop_sale.")
    parser.add_argument(
        "--database",
        default=(
            os.getenv("STOP_SALE_SOURCE_SQLSERVER_DATABASE")
            or os.getenv("STOP_SALE_SQLSERVER_DATABASE")
            or DEFAULT_DATABASE
        ),
    )
    parser.add_argument("--platform", default=DEFAULT_PLATFORM)
    parser.add_argument("--handling", default=DEFAULT_HANDLING)
    parser.add_argument(
        "--store",
        action="append",
        dest="stores",
        help="Target store name. Repeat to override the default four stores.",
    )
    parser.add_argument("--limit", type=int, default=0, help="Limit deduped selected rows for controlled previews.")
    parser.add_argument(
        "--driver",
        default=(
            os.getenv("STOP_SALE_SOURCE_SQLSERVER_DRIVER")
            or os.getenv("STOP_SALE_SQLSERVER_DRIVER")
            or "SQL Server Native Client 10.0"
        ),
    )
    parser.add_argument("--server-env", default="STOP_SALE_SOURCE_SQLSERVER_HOST")
    parser.add_argument("--port-env", default="STOP_SALE_SOURCE_SQLSERVER_PORT")
    parser.add_argument("--user-env", default="STOP_SALE_SOURCE_SQLSERVER_USER")
    parser.add_argument("--password-env", default="STOP_SALE_SOURCE_SQLSERVER_PASSWORD")
    parser.add_argument("--timeout", type=int, default=60, help="ODBC query timeout seconds.")
    return parser.parse_args(argv)


def config_from_env(args: argparse.Namespace) -> SqlServerConfig:
    def read_runtime_env(name: str, default: str = "") -> str:
        primary = str(os.getenv(name, "")).strip()
        if primary:
            return primary
        fallback = SOURCE_ENV_FALLBACKS.get(name, "")
        return str(os.getenv(fallback, default) if fallback else default).strip()

    server = read_runtime_env(args.server_env)
    user = read_runtime_env(args.user_env)
    password = read_runtime_env(args.password_env)
    missing = [
        name
        for name, value in (
            (args.server_env, server),
            (args.user_env, user),
            (args.password_env, password),
        )
        if not value
    ]
    if missing:
        raise RuntimeError(
            "Missing SQL Server environment variables: "
            + ", ".join(missing)
            + ". Do not pass secrets on the command line."
        )

    raw_port = read_runtime_env(args.port_env, "1433") or "1433"
    try:
        port = int(raw_port)
    except ValueError as exc:
        raise RuntimeError(f"Invalid SQL Server port in {args.port_env}: {raw_port}") from exc

    return SqlServerConfig(
        server=server,
        port=port,
        database=str(args.database).strip() or DEFAULT_DATABASE,
        user=user,
        password=password,
        driver=str(args.driver).strip() or "SQL Server Native Client 10.0",
        timeout_seconds=max(1, int(args.timeout)),
    )


def build_connection_string(config: SqlServerConfig) -> str:
    trust = "yes" if config.trust_server_certificate else "no"
    return (
        f"DRIVER={{{config.driver}}};"
        f"SERVER={config.server},{config.port};"
        f"DATABASE={config.database};"
        f"UID={config.user};"
        f"PWD={config.password};"
        f"TrustServerCertificate={trust};"
        "Connection Timeout=10;"
    )


def connect_sqlserver(config: SqlServerConfig) -> Any:
    import pyodbc

    return pyodbc.connect(build_connection_string(config), timeout=config.timeout_seconds)


def parse_table_name(value: str) -> tuple[str, str]:
    parts = [item.strip() for item in str(value or "").split(".") if item.strip()]
    if len(parts) == 1:
        return "dbo", parts[0]
    if len(parts) == 2:
        return parts[0], parts[1]
    raise ValueError(f"Unsupported table name: {value}")


def quote_identifier(value: str) -> str:
    return "[" + str(value).replace("]", "]]") + "]"


def fetchall_dict(cursor: Any) -> list[dict[str, Any]]:
    columns = [item[0] for item in cursor.description]
    return [dict(zip(columns, row)) for row in cursor.fetchall()]


def fetchone_dict(cursor: Any) -> dict[str, Any] | None:
    columns = [item[0] for item in cursor.description]
    row = cursor.fetchone()
    return dict(zip(columns, row)) if row else None


def normalize_value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (datetime, date)):
        return value.isoformat()[:10]
    text = str(value).strip()
    if text.endswith(".0") and text[:-2].isdigit():
        return text[:-2]
    return text


def safe_filename(value: str) -> str:
    name = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff_-]+", "_", str(value or "")).strip("_")
    return name[:80] or "UNKNOWN_STORE"


def task_key(row: dict[str, str]) -> tuple[str, str, str]:
    return (
        normalize_value(row.get(STORE_NAME)),
        normalize_value(row.get(PRODUCT_ID)),
        normalize_value(row.get(ONLINE_SKU)),
    )


def dedupe_rows(rows: Iterable[dict[str, str]]) -> tuple[list[dict[str, str]], list[dict[str, Any]]]:
    selected: list[dict[str, str]] = []
    duplicates: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for row in rows:
        key = task_key(row)
        if key in seen:
            duplicate = dict(row)
            duplicate["dedupe_key"] = {
                STORE_NAME: key[0],
                PRODUCT_ID: key[1],
                ONLINE_SKU: key[2],
            }
            duplicates.append(duplicate)
            continue
        seen.add(key)
        selected.append(row)
    return selected, duplicates


def count_required_nulls(rows: Iterable[dict[str, str]]) -> dict[str, int]:
    materialized = list(rows)
    return {
        column: sum(1 for row in materialized if not normalize_value(row.get(column)))
        for column in REQUIRED_OUTPUT_COLUMNS
    }


def group_counts(rows: Iterable[dict[str, str]]) -> dict[str, int]:
    counts = Counter(normalize_value(row.get(STORE_NAME)) or "UNKNOWN_STORE" for row in rows)
    return dict(sorted(counts.items()))


def write_csv(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=OUTPUT_COLUMNS)
        writer.writeheader()
        for row in rows:
            writer.writerow({column: row.get(column, "") for column in OUTPUT_COLUMNS})


def rows_by_store(rows: Iterable[dict[str, str]]) -> dict[str, list[dict[str, str]]]:
    grouped: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in rows:
        grouped[normalize_value(row.get(STORE_NAME)) or "UNKNOWN_STORE"].append(row)
    return dict(sorted(grouped.items()))


def build_query(table: str, stores: Sequence[str]) -> tuple[str, list[str]]:
    schema, table_name = parse_table_name(table)
    full_table = f"{quote_identifier(schema)}.{quote_identifier(table_name)}"
    select_sql = ", ".join(
        f"{quote_identifier(db_field)} AS {quote_identifier(display_name)}"
        for display_name, db_field in DB_FIELD_MAP.items()
    )
    store_placeholders = ", ".join("?" for _ in stores)
    sql = (
        f"SELECT {select_sql} FROM {full_table} "
        "WHERE CAST([zbrq] AS date) = ? "
        "AND [pt] = ? "
        "AND [clsm] = ? "
        f"AND [dpmc] IN ({store_placeholders}) "
        "ORDER BY [dpmc], [spi], [xsspbm]"
    )
    return sql, list(stores)


def validate_source_columns(connection: Any, table: str) -> list[dict[str, Any]]:
    schema, table_name = parse_table_name(table)
    cursor = connection.cursor()
    cursor.execute(
        """
        SELECT c.column_id, c.name AS column_name, t.name AS data_type, c.max_length, c.precision, c.scale, c.is_nullable
        FROM sys.columns c
        JOIN sys.types t ON c.user_type_id = t.user_type_id
        WHERE c.object_id = OBJECT_ID(?)
        ORDER BY c.column_id
        """,
        (f"{schema}.{table_name}",),
    )
    columns = fetchall_dict(cursor)
    present = {str(item["column_name"]) for item in columns}
    missing = [field for field in DB_FIELD_MAP.values() if field not in present]
    if missing:
        raise RuntimeError(f"Source table {schema}.{table_name} is missing required DB fields: {missing}")
    return columns


def fetch_stop_sale_rows(
    connection: Any,
    *,
    table: str,
    metric_date: date,
    platform: str,
    handling: str,
    stores: Sequence[str],
) -> list[dict[str, str]]:
    if not stores:
        raise ValueError("At least one target store is required.")
    sql, store_params = build_query(table, stores)
    cursor = connection.cursor()
    cursor.execute(sql, (metric_date.isoformat(), platform, handling, *store_params))
    return [
        {column: normalize_value(row.get(column)) for column in REPORT_COLUMNS}
        for row in fetchall_dict(cursor)
    ]


def count_base_rows(
    connection: Any,
    *,
    table: str,
    metric_date: date,
    platform: str,
    handling: str,
) -> int:
    schema, table_name = parse_table_name(table)
    full_table = f"{quote_identifier(schema)}.{quote_identifier(table_name)}"
    cursor = connection.cursor()
    cursor.execute(
        f"SELECT COUNT(1) AS cnt FROM {full_table} WHERE CAST([zbrq] AS date)=? AND [pt]=? AND [clsm]=?",
        (metric_date.isoformat(), platform, handling),
    )
    row = fetchone_dict(cursor)
    return int(row["cnt"]) if row else 0


def build_preview_outputs(
    *,
    rows: list[dict[str, str]],
    output_dir: Path,
    metric_date: date,
    limit: int = 0,
) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    selected_rows, duplicate_rows = dedupe_rows(rows)
    if limit > 0:
        selected_rows = selected_rows[:limit]

    preview_csv = output_dir / f"1688_stop_sale_preview_{metric_date:%Y%m%d}_{timestamp}.csv"
    write_csv(preview_csv, selected_rows)

    per_store_files: list[dict[str, Any]] = []
    for store, store_rows in rows_by_store(selected_rows).items():
        store_path = output_dir / f"1688_stop_sale_preview_{metric_date:%Y%m%d}_{safe_filename(store)}_{timestamp}.csv"
        write_csv(store_path, store_rows)
        per_store_files.append({"store_name": store, "count": len(store_rows), "path": str(store_path)})

    return {
        "preview_csv": str(preview_csv),
        "per_store_preview_csv": per_store_files,
        "loaded_count": len(rows),
        "selected_count": len(selected_rows),
        "duplicate_count": len(duplicate_rows),
        "duplicates": duplicate_rows,
        "loaded_store_counts": group_counts(rows),
        "selected_store_counts": group_counts(selected_rows),
        "required_null_counts": count_required_nulls(selected_rows),
        "limit": limit,
    }


def write_report(path: Path, report: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")


def run(args: argparse.Namespace) -> dict[str, Any]:
    metric_date = date.fromisoformat(str(args.date))
    stores = [str(item).strip() for item in (args.stores or DEFAULT_TARGET_STORES) if str(item).strip()]
    config = config_from_env(args)
    output_dir = Path(args.output_dir)

    connection = connect_sqlserver(config)
    try:
        columns = validate_source_columns(connection, args.table)
        base_count = count_base_rows(
            connection,
            table=args.table,
            metric_date=metric_date,
            platform=str(args.platform),
            handling=str(args.handling),
        )
        rows = fetch_stop_sale_rows(
            connection,
            table=args.table,
            metric_date=metric_date,
            platform=str(args.platform),
            handling=str(args.handling),
            stores=stores,
        )
    finally:
        connection.close()

    outputs = build_preview_outputs(rows=rows, output_dir=output_dir, metric_date=metric_date, limit=int(args.limit))
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    report_path = output_dir / f"1688_stop_sale_db_preview_report_{metric_date:%Y%m%d}_{timestamp}.json"
    report = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "mode": "preview_only",
        "connection": config.safe_dict(),
        "source": {
            "role": "read_only_business_source",
            "database": config.database,
            "table": args.table,
            "column_count": len(columns),
            "field_mapping": DB_FIELD_MAP,
        },
        "filters": {
            METRIC_DATE: metric_date.isoformat(),
            PLATFORM: str(args.platform),
            HANDLING: str(args.handling),
            STORE_NAME: stores,
        },
        "base_count_for_date_platform_handling": base_count,
        **outputs,
    }
    write_report(report_path, report)
    report["report_path"] = str(report_path)
    return report


def main(argv: Sequence[str] | None = None) -> int:
    report = run(parse_args(argv))
    summary = {
        "mode": report["mode"],
        "report_path": report["report_path"],
        "preview_csv": report["preview_csv"],
        "loaded_count": report["loaded_count"],
        "selected_count": report["selected_count"],
        "duplicate_count": report["duplicate_count"],
        "selected_store_counts": report["selected_store_counts"],
        "required_null_counts": report["required_null_counts"],
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
