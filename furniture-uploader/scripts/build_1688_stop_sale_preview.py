from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any, Iterable, Sequence


PROJECT_ROOT = Path(__file__).resolve().parents[1]
RPA_ROOT = PROJECT_ROOT / "rpa"
if str(RPA_ROOT) not in sys.path:
    sys.path.insert(0, str(RPA_ROOT))

from stop_sale_audit import hydrate_source_database_credentials
from stop_sale_audit import hydrate_dingtalk_credentials
from stop_sale_audit import resolve_stop_sale_app_config
from dingtalk import post_dingtalk_text_message
from sku_offline_tasks import (
    COMBINATION_SKU_REASON,
    COMBINATION_SKU_REASON_CODE,
    is_manual_combination_replacement,
)


STORE_NAME = "店铺名称"
PLATFORM = "平台"
METRIC_DATE = "指标日期"
HANDLING = "处理说明"
PRODUCT_ID = "商品ID"
PLATFORM_STORE_ITEM_CODE = "平台店铺商品编码"
ONLINE_SKU = "线上商品编码"
ONLINE_STOCK = "线上库存"
REPLACEMENT_SKU = "可替换商品编码（新）"
LEGACY_REPLACEMENT_SKU = "可替换商品编码"
CHANGE_IMAGE = "是否换图"

DEFAULT_PLATFORM = "Alibaba"
DEFAULT_HANDLING = "全渠道下架"
REPLACEMENT_HANDLING = "全渠道替换"
DEFAULT_TABLE = "dbo.op_stop_sale"
DEFAULT_DATABASE = "JSDataMiddlePlatform"
DEFAULT_OUTPUT_DIR = Path("logs/sku_offline/db_previews")
DEFAULT_REPLACE_OUTPUT_DIR = Path("logs/sku_replace/db_previews")

SKU_CODE_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+/#:-]*$")
REJECTED_ROW_NUMBER = "源数据行号"
REJECTED_REASON_CODE = "拒绝原因编码"
REJECTED_REASON = "拒绝原因"
BUSINESS_SKIP_REASON_CODE = "跳过原因编码"
BUSINESS_SKIP_REASON = "异常原因"

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

REJECTED_COLUMNS = [
    *REPORT_COLUMNS,
    REJECTED_ROW_NUMBER,
    REJECTED_REASON_CODE,
    REJECTED_REASON,
]

BUSINESS_SKIPPED_COLUMNS = [
    *REPORT_COLUMNS,
    REJECTED_ROW_NUMBER,
    BUSINESS_SKIP_REASON_CODE,
    BUSINESS_SKIP_REASON,
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

REQUIRED_STOP_SALE_COLUMNS = [
    *REQUIRED_OUTPUT_COLUMNS,
    PLATFORM_STORE_ITEM_CODE,
]

REQUIRED_REPLACEMENT_COLUMNS = [
    *REQUIRED_OUTPUT_COLUMNS,
    REPLACEMENT_SKU,
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
        description="Read the BI SKU action source without writes and generate offline/replace preview CSV files."
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
    parser.add_argument(
        "--shared-runtime-root",
        default=os.getenv("SCRIPT_1688_ROOT", "D:/script_1688"),
        help="1688 runtime root used to load the source credential from Credential Manager.",
    )
    parser.add_argument("--timeout", type=int, default=60, help="ODBC query timeout seconds.")
    parser.add_argument(
        "--no-notify",
        action="store_true",
        help="Do not send DingTalk alerts when replacement source rows are rejected.",
    )
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


def hydrate_preview_database_credentials(args: argparse.Namespace) -> dict[str, Any]:
    schema, _ = parse_table_name(args.table)
    if (
        normalize_value(args.database).casefold() == "jsreportreplica"
        and schema.casefold() == "app"
    ):
        try:
            app_config = resolve_stop_sale_app_config(args.shared_runtime_root)
        except Exception:
            app_config = None
        if app_config is not None and app_config.database.casefold() == "jsreportreplica":
            os.environ[args.server_env] = app_config.server
            os.environ[args.port_env] = str(app_config.port)
            os.environ[args.user_env] = app_config.user
            os.environ[args.password_env] = app_config.password
            return {
                "configured": True,
                "source": "app_writer_credential",
                "credential_ref": app_config.credential_ref,
            }

    configured = hydrate_source_database_credentials(args.shared_runtime_root)
    return {
        "configured": all(configured.values()),
        "source": "stop_sale_source_credential",
        "credential_ref": "YYDD/1688/database/stop-sale-source",
    }


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


# Stop-sale fans one browser action into per-link cleanup; replacement is one SKU mapping.
def task_key(row: dict[str, str]) -> tuple[str, ...]:
    base_key = (
        normalize_value(row.get(STORE_NAME)),
        normalize_value(row.get(PRODUCT_ID)),
        normalize_value(row.get(ONLINE_SKU)),
    )
    if normalize_value(row.get(HANDLING)) == REPLACEMENT_HANDLING:
        return (*base_key, normalize_value(row.get(REPLACEMENT_SKU)))
    return (*base_key, normalize_value(row.get(PLATFORM_STORE_ITEM_CODE)))


def dedupe_sort_key(row: dict[str, str]) -> tuple[str, ...]:
    return (
        *task_key(row),
        *(normalize_value(row.get(column)).casefold() for column in REPORT_COLUMNS),
    )


def dedupe_rows(rows: Iterable[dict[str, str]]) -> tuple[list[dict[str, str]], list[dict[str, Any]]]:
    selected: list[dict[str, str]] = []
    duplicates: list[dict[str, Any]] = []
    seen: set[tuple[str, ...]] = set()
    for row in sorted(rows, key=dedupe_sort_key):
        key = task_key(row)
        if key in seen:
            duplicate = dict(row)
            duplicate["dedupe_key"] = {
                STORE_NAME: key[0],
                PRODUCT_ID: key[1],
                ONLINE_SKU: key[2],
            }
            identity_field = (
                REPLACEMENT_SKU
                if normalize_value(row.get(HANDLING)) == REPLACEMENT_HANDLING
                else PLATFORM_STORE_ITEM_CODE
            )
            duplicate["dedupe_key"][identity_field] = key[3]
            duplicates.append(duplicate)
            continue
        seen.add(key)
        selected.append(row)
    return selected, duplicates


def count_required_nulls(
    rows: Iterable[dict[str, str]],
    *,
    handling: str = DEFAULT_HANDLING,
) -> dict[str, int]:
    materialized = list(rows)
    required_columns = (
        REQUIRED_REPLACEMENT_COLUMNS
        if normalize_value(handling) == REPLACEMENT_HANDLING
        else REQUIRED_STOP_SALE_COLUMNS
    )
    return {
        column: sum(1 for row in materialized if not normalize_value(row.get(column)))
        for column in required_columns
    }


def is_valid_sku_code(value: Any) -> bool:
    return bool(SKU_CODE_PATTERN.fullmatch(normalize_value(value)))


def _reject_row(
    rejected: dict[int, dict[str, Any]],
    *,
    index: int,
    row: dict[str, str],
    reason_code: str,
    reason: str,
) -> None:
    current = rejected.get(index)
    if current is None:
        current = {
            **row,
            REJECTED_ROW_NUMBER: index,
            REJECTED_REASON_CODE: reason_code,
            REJECTED_REASON: reason,
        }
        rejected[index] = current
        return
    codes = [item for item in str(current[REJECTED_REASON_CODE]).split(";") if item]
    if reason_code not in codes:
        current[REJECTED_REASON_CODE] = ";".join([*codes, reason_code])
        current[REJECTED_REASON] = f"{current[REJECTED_REASON]}; {reason}"


def partition_replacement_rows(
    rows: Iterable[dict[str, str]],
) -> tuple[list[dict[str, str]], list[dict[str, Any]], list[dict[str, Any]]]:
    candidates: dict[int, dict[str, str]] = {}
    business_skipped: dict[int, dict[str, Any]] = {}
    rejected: dict[int, dict[str, Any]] = {}
    for index, source_row in enumerate(rows, start=1):
        row = dict(source_row)
        store = normalize_value(row.get(STORE_NAME))
        product_id = normalize_value(row.get(PRODUCT_ID))
        old_sku = normalize_value(row.get(ONLINE_SKU))
        new_sku = normalize_value(
            row.get(REPLACEMENT_SKU) or row.get(LEGACY_REPLACEMENT_SKU)
        )
        row[REPLACEMENT_SKU] = new_sku
        row[STORE_NAME] = store
        row[PRODUCT_ID] = product_id
        row[ONLINE_SKU] = old_sku
        missing = [
            label
            for label, value in (
                (STORE_NAME, store),
                (PRODUCT_ID, product_id),
                (ONLINE_SKU, old_sku),
                (REPLACEMENT_SKU, new_sku),
            )
            if not value
        ]
        if missing:
            _reject_row(
                rejected,
                index=index,
                row=row,
                reason_code="missing_required_field",
                reason=f"缺少必填字段：{', '.join(missing)}",
            )
            continue
        if not is_valid_sku_code(old_sku):
            _reject_row(
                rejected,
                index=index,
                row=row,
                reason_code="invalid_source_sku_format",
                reason=f"线上商品编码不是可执行的 SKU 格式：{old_sku}",
            )
            continue
        if is_manual_combination_replacement(new_sku):
            business_skipped[index] = {
                **row,
                REJECTED_ROW_NUMBER: index,
                BUSINESS_SKIP_REASON_CODE: COMBINATION_SKU_REASON_CODE,
                BUSINESS_SKIP_REASON: COMBINATION_SKU_REASON,
            }
            continue
        if not is_valid_sku_code(new_sku):
            _reject_row(
                rejected,
                index=index,
                row=row,
                reason_code="invalid_replacement_sku_format",
                reason=f"可替换商品编码（新）不是可执行的 SKU 格式：{new_sku}",
            )
            continue
        if old_sku.casefold() == new_sku.casefold():
            _reject_row(
                rejected,
                index=index,
                row=row,
                reason_code="replacement_same_as_source",
                reason=f"线上商品编码与替换后编码相同：{old_sku}",
            )
            continue
        candidates[index] = row

    source_groups: dict[tuple[str, str, str], list[int]] = defaultdict(list)
    target_groups: dict[tuple[str, str, str], list[int]] = defaultdict(list)
    for index, row in candidates.items():
        store = normalize_value(row.get(STORE_NAME))
        product_id = normalize_value(row.get(PRODUCT_ID))
        old_sku = normalize_value(row.get(ONLINE_SKU))
        new_sku = normalize_value(row.get(REPLACEMENT_SKU))
        source_key = (store.casefold(), product_id.casefold(), old_sku.casefold())
        target_key = (store.casefold(), product_id.casefold(), new_sku.casefold())
        source_groups[source_key].append(index)
        target_groups[target_key].append(index)

    for indexes in source_groups.values():
        targets = {normalize_value(candidates[index][REPLACEMENT_SKU]).casefold() for index in indexes}
        if len(targets) <= 1:
            continue
        target_text = ", ".join(sorted({normalize_value(candidates[index][REPLACEMENT_SKU]) for index in indexes}))
        for index in indexes:
            old_sku = normalize_value(candidates[index][ONLINE_SKU])
            _reject_row(
                rejected,
                index=index,
                row=candidates[index],
                reason_code="source_mapping_conflict",
                reason=f"同一线上商品编码 {old_sku} 映射到多个目标编码：{target_text}",
            )

    for indexes in target_groups.values():
        sources = {normalize_value(candidates[index][ONLINE_SKU]).casefold() for index in indexes}
        if len(sources) <= 1:
            continue
        source_text = ", ".join(sorted({normalize_value(candidates[index][ONLINE_SKU]) for index in indexes}))
        for index in indexes:
            new_sku = normalize_value(candidates[index][REPLACEMENT_SKU])
            _reject_row(
                rejected,
                index=index,
                row=candidates[index],
                reason_code="replacement_target_conflict",
                reason=f"目标编码 {new_sku} 被多个线上商品编码使用：{source_text}",
            )

    remaining = {
        index: row for index, row in candidates.items() if index not in rejected
    }
    product_sources: dict[tuple[str, str], set[str]] = defaultdict(set)
    for row in remaining.values():
        product_key = (
            normalize_value(row[STORE_NAME]).casefold(),
            normalize_value(row[PRODUCT_ID]).casefold(),
        )
        product_sources[product_key].add(normalize_value(row[ONLINE_SKU]).casefold())
    for index, row in remaining.items():
        product_key = (
            normalize_value(row[STORE_NAME]).casefold(),
            normalize_value(row[PRODUCT_ID]).casefold(),
        )
        new_sku = normalize_value(row[REPLACEMENT_SKU])
        if new_sku.casefold() in product_sources[product_key]:
            _reject_row(
                rejected,
                index=index,
                row=row,
                reason_code="chained_or_swap_mapping",
                reason=f"目标编码 {new_sku} 同时是该商品的源编码，链式或交换映射不具备幂等性",
            )

    accepted = [row for index, row in candidates.items() if index not in rejected]
    business_skipped_rows = [business_skipped[index] for index in sorted(business_skipped)]
    rejected_rows = [rejected[index] for index in sorted(rejected)]
    return accepted, business_skipped_rows, rejected_rows


def validate_replacement_rows(rows: Iterable[dict[str, str]]) -> None:
    _, _, rejected_rows = partition_replacement_rows(rows)
    if rejected_rows:
        errors = [
            f"row {row[REJECTED_ROW_NUMBER]}: {row[REJECTED_REASON]}"
            for row in rejected_rows
        ]
        raise ValueError("Invalid 1688 SKU replacement source rows: " + "; ".join(errors[:20]))


def output_prefix_for_handling(handling: str) -> str:
    return "1688_sku_replace" if normalize_value(handling) == REPLACEMENT_HANDLING else "1688_stop_sale"


def group_counts(rows: Iterable[dict[str, str]]) -> dict[str, int]:
    counts = Counter(normalize_value(row.get(STORE_NAME)) or "UNKNOWN_STORE" for row in rows)
    return dict(sorted(counts.items()))


def count_rejected_reasons(rows: Iterable[dict[str, Any]]) -> dict[str, int]:
    counts: Counter[str] = Counter()
    for row in rows:
        codes = [
            item.strip()
            for item in str(row.get(REJECTED_REASON_CODE) or "unknown").split(";")
            if item.strip()
        ]
        counts.update(codes or ["unknown"])
    return dict(sorted(counts.items()))


def write_csv(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=OUTPUT_COLUMNS)
        writer.writeheader()
        for row in rows:
            writer.writerow({column: row.get(column, "") for column in OUTPUT_COLUMNS})


def write_rejected_outputs(
    csv_path: Path,
    json_path: Path,
    rows: list[dict[str, Any]],
) -> None:
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    with csv_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=REJECTED_COLUMNS)
        writer.writeheader()
        for row in rows:
            writer.writerow({column: row.get(column, "") for column in REJECTED_COLUMNS})
    json_path.write_text(
        json.dumps(rows, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )


def write_business_skipped_outputs(
    csv_path: Path,
    json_path: Path,
    rows: list[dict[str, Any]],
) -> None:
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    with csv_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=BUSINESS_SKIPPED_COLUMNS)
        writer.writeheader()
        for row in rows:
            writer.writerow({column: row.get(column, "") for column in BUSINESS_SKIPPED_COLUMNS})
    json_path.write_text(
        json.dumps(rows, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )


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
        "ORDER BY [dpmc], [spi], [xsspbm], [kthspbmx], [ptdpspbm], [xskc], [sfhtx]"
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
    handling: str = DEFAULT_HANDLING,
) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    rejected_rows: list[dict[str, Any]] = []
    business_skipped_rows: list[dict[str, Any]] = []
    accepted_rows = rows
    if normalize_value(handling) == REPLACEMENT_HANDLING:
        accepted_rows, business_skipped_rows, rejected_rows = partition_replacement_rows(rows)
    selected_rows, duplicate_rows = dedupe_rows(accepted_rows)
    deduped_count = len(selected_rows)
    if limit > 0:
        selected_rows = selected_rows[:limit]

    prefix = output_prefix_for_handling(handling)
    preview_csv = output_dir / f"{prefix}_preview_{metric_date:%Y%m%d}_{timestamp}.csv"
    write_csv(preview_csv, selected_rows)

    rejected_csv = output_dir / f"{prefix}_rejected_{metric_date:%Y%m%d}_{timestamp}.csv"
    rejected_json = output_dir / f"{prefix}_rejected_{metric_date:%Y%m%d}_{timestamp}.json"
    if normalize_value(handling) == REPLACEMENT_HANDLING:
        write_rejected_outputs(rejected_csv, rejected_json, rejected_rows)
    business_skipped_csv = output_dir / f"{prefix}_business_skipped_{metric_date:%Y%m%d}_{timestamp}.csv"
    business_skipped_json = output_dir / f"{prefix}_business_skipped_{metric_date:%Y%m%d}_{timestamp}.json"
    if normalize_value(handling) == REPLACEMENT_HANDLING:
        write_business_skipped_outputs(
            business_skipped_csv,
            business_skipped_json,
            business_skipped_rows,
        )

    per_store_files: list[dict[str, Any]] = []
    for store, store_rows in rows_by_store(selected_rows).items():
        store_path = output_dir / f"{prefix}_preview_{metric_date:%Y%m%d}_{safe_filename(store)}_{timestamp}.csv"
        write_csv(store_path, store_rows)
        per_store_files.append({"store_name": store, "count": len(store_rows), "path": str(store_path)})

    return {
        "preview_csv": str(preview_csv),
        "per_store_preview_csv": per_store_files,
        "loaded_count": len(rows),
        "accepted_count": len(accepted_rows),
        "deduped_count": deduped_count,
        "selected_count": len(selected_rows),
        "rejected_count": len(rejected_rows),
        "rejected_reason_counts": count_rejected_reasons(rejected_rows),
        "rejected_store_counts": group_counts(rejected_rows),
        "rejected_csv": str(rejected_csv) if normalize_value(handling) == REPLACEMENT_HANDLING else "",
        "rejected_json": str(rejected_json) if normalize_value(handling) == REPLACEMENT_HANDLING else "",
        "rejected_rows": rejected_rows,
        "business_skipped_count": len(business_skipped_rows),
        "business_skipped_reason_counts": {
            COMBINATION_SKU_REASON_CODE: len(business_skipped_rows)
        } if business_skipped_rows else {},
        "business_skipped_store_counts": group_counts(business_skipped_rows),
        "business_skipped_csv": str(business_skipped_csv)
        if normalize_value(handling) == REPLACEMENT_HANDLING
        else "",
        "business_skipped_json": str(business_skipped_json)
        if normalize_value(handling) == REPLACEMENT_HANDLING
        else "",
        "business_skipped_rows": business_skipped_rows,
        "duplicate_count": len(duplicate_rows),
        "duplicates": duplicate_rows,
        "loaded_store_counts": group_counts(rows),
        "selected_store_counts": group_counts(selected_rows),
        "required_null_counts": count_required_nulls(selected_rows, handling=handling),
        "operation": "replace" if normalize_value(handling) == REPLACEMENT_HANDLING else "offline",
        "limit": limit,
    }


def write_report(path: Path, report: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")


def send_replacement_rejection_notification(
    report: dict[str, Any],
    *,
    shared_runtime_root: str | Path,
    disabled: bool,
) -> dict[str, Any]:
    rejected_count = int(report.get("rejected_count") or 0)
    if rejected_count <= 0:
        return {"attempted": False, "sent": False, "reason": "no_rejected_rows"}
    if disabled:
        return {"attempted": False, "sent": False, "reason": "disabled"}
    credentials = hydrate_dingtalk_credentials(shared_runtime_root)
    if not all(credentials.values()):
        return {"attempted": True, "sent": False, "reason": "credentials_unavailable"}
    reason_counts = "、".join(
        f"{reason}={count}"
        for reason, count in dict(report.get("rejected_reason_counts") or {}).items()
    )
    content = (
        "【1688 SKU替换数据异常】\n"
        f"指标日期：{report.get('filters', {}).get(METRIC_DATE, '')}\n"
        f"读取：{report.get('loaded_count', 0)}\n"
        f"可执行：{report.get('selected_count', 0)}\n"
        f"拒绝：{rejected_count}\n"
        f"原因：{reason_counts or 'unknown'}\n"
        f"拒绝明细：{report.get('rejected_csv', '')}\n"
        "处理：合法任务已继续生成；拒绝项不会进入1688或聚水潭执行。"
    )
    try:
        sent = post_dingtalk_text_message(
            {
                "notifications": {
                    "dingtalk": {
                        "enabled": True,
                        "webhook_env": "DINGTALK_WEBHOOK",
                        "secret_env": "DINGTALK_SECRET",
                    }
                }
            },
            content,
        )
    except Exception as exc:
        return {
            "attempted": True,
            "sent": False,
            "reason": f"{type(exc).__name__}: {exc}",
        }
    return {"attempted": True, "sent": bool(sent), "reason": "sent" if sent else "api_rejected"}


def run(args: argparse.Namespace) -> dict[str, Any]:
    metric_date = date.fromisoformat(str(args.date))
    stores = [str(item).strip() for item in (args.stores or DEFAULT_TARGET_STORES) if str(item).strip()]
    credential_source = hydrate_preview_database_credentials(args)
    config = config_from_env(args)
    output_dir = Path(args.output_dir)
    if (
        normalize_value(args.handling) == REPLACEMENT_HANDLING
        and output_dir == DEFAULT_OUTPUT_DIR
    ):
        output_dir = DEFAULT_REPLACE_OUTPUT_DIR

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

    outputs = build_preview_outputs(
        rows=rows,
        output_dir=output_dir,
        metric_date=metric_date,
        limit=int(args.limit),
        handling=str(args.handling),
    )
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    prefix = output_prefix_for_handling(str(args.handling))
    report_path = output_dir / f"{prefix}_db_preview_report_{metric_date:%Y%m%d}_{timestamp}.json"
    report = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "mode": "preview_only",
        "connection": config.safe_dict(),
        "credential_source": {
            "configured": bool(credential_source.get("configured")),
            "source": credential_source.get("source", ""),
            "credential_ref": credential_source.get("credential_ref", ""),
        },
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
    report["rejection_notification"] = send_replacement_rejection_notification(
        report,
        shared_runtime_root=args.shared_runtime_root,
        disabled=bool(args.no_notify),
    )
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
        "rejected_count": report["rejected_count"],
        "rejected_reason_counts": report["rejected_reason_counts"],
        "rejected_csv": report["rejected_csv"],
        "rejected_json": report["rejected_json"],
        "business_skipped_count": report["business_skipped_count"],
        "business_skipped_reason_counts": report["business_skipped_reason_counts"],
        "business_skipped_csv": report["business_skipped_csv"],
        "business_skipped_json": report["business_skipped_json"],
        "rejection_notification": report["rejection_notification"],
        "duplicate_count": report["duplicate_count"],
        "selected_store_counts": report["selected_store_counts"],
        "required_null_counts": report["required_null_counts"],
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
