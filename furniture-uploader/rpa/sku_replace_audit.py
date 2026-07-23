from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any, Iterable, Mapping

from stop_sale_audit import (
    StopSaleAppConfig,
    connect_app_database,
    normalized_identity_part,
)


def sku_replace_task_key(
    store_name: Any,
    product_id: Any,
    online_sku: Any,
    replacement_sku: Any,
) -> str:
    identity = "|".join(
        normalized_identity_part(value)
        for value in (store_name, product_id, online_sku, replacement_sku)
    )
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()


def _task_value(task: Any, name: str, default: str = "") -> str:
    return str(getattr(task, name, default) or "").strip()


def _task_metric_date(task: Any) -> str | None:
    raw = getattr(task, "raw", {})
    value = str(raw.get("指标日期", "") if isinstance(raw, Mapping) else "").strip()
    return value[:10] if value else None


def _clean(value: Any, limit: int = 2000) -> str:
    return str(value or "").strip()[:limit]


class SkuReplaceAuditRepository:
    REQUIRED_TABLES = (
        "ali1688_sku_replace_run",
        "ali1688_sku_replace_item",
    )

    def __init__(self, config: StopSaleAppConfig) -> None:
        self.config = config
        self.schema = str(config.schema).strip()
        if not self.schema.replace("_", "").isalnum():
            raise ValueError(f"Invalid audit schema: {self.schema!r}")

    def _table(self, name: str) -> str:
        if not name.replace("_", "").isalnum():
            raise ValueError(f"Invalid audit table: {name!r}")
        return f"[{self.schema}].[{name}]"

    def check_contract(self) -> dict[str, Any]:
        with connect_app_database(self.config) as connection:
            cursor = connection.cursor()
            database_name = str(cursor.execute("SELECT DB_NAME()").fetchone()[0])
            missing = []
            for table in self.REQUIRED_TABLES:
                object_name = f"{self.schema}.{table}"
                if cursor.execute("SELECT OBJECT_ID(?, 'U')", (object_name,)).fetchone()[0] is None:
                    missing.append(object_name)
        if database_name != self.config.database:
            raise RuntimeError(f"SKU replacement audit connected to unexpected database: {database_name}")
        return {
            "database": database_name,
            "schema": self.schema,
            "missing_tables": missing,
            "ready": not missing,
        }

    def count_recent_active_runs(self, max_age_minutes: int = 240) -> int:
        with connect_app_database(self.config) as connection:
            row = connection.cursor().execute(
                f"""
                SELECT COUNT(*) FROM {self._table('ali1688_sku_replace_run')}
                WHERE status = 'running' AND finished_at IS NULL
                  AND COALESCE(updated_at, started_at) >= DATEADD(MINUTE, ?, SYSUTCDATETIME())
                """,
                (-max(1, int(max_age_minutes)),),
            ).fetchone()
            return int(row[0] or 0)

    def heartbeat_run(self, run_id: str) -> None:
        with connect_app_database(self.config) as connection:
            cursor = connection.cursor()
            row = cursor.execute(
                f"""
                UPDATE {self._table('ali1688_sku_replace_run')}
                SET updated_at = SYSUTCDATETIME()
                OUTPUT inserted.status
                WHERE run_id = ? AND status = 'running' AND finished_at IS NULL
                """,
                (str(run_id),),
            ).fetchone()
            if row is None or str(row[0] or "") != "running":
                raise RuntimeError(f"SKU replacement audit run is no longer active: {run_id}")
            connection.commit()

    def start_run(
        self,
        *,
        run_id: str,
        mode: str,
        input_file: str | Path,
        tasks: Iterable[Any],
        source_database: str,
        source_table: str,
    ) -> None:
        materialized = list(tasks)
        with connect_app_database(self.config) as connection:
            cursor = connection.cursor()
            cursor.execute(
                f"""
                INSERT INTO {self._table('ali1688_sku_replace_run')} (
                    run_id, mode, source_database, source_table, input_file, status,
                    total_count, replace_target_count, jushuitan_target_count,
                    started_at, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, 'running', ?, ?, ?, SYSUTCDATETIME(), SYSUTCDATETIME(), SYSUTCDATETIME())
                """,
                (
                    run_id,
                    mode,
                    source_database,
                    source_table,
                    str(Path(input_file).resolve()),
                    len(materialized),
                    len(materialized),
                    len(materialized),
                ),
            )
            for task in materialized:
                task_key = sku_replace_task_key(
                    _task_value(task, "store_name"),
                    _task_value(task, "product_id"),
                    _task_value(task, "online_sku"),
                    _task_value(task, "replacement_sku"),
                )
                cursor.execute(
                    f"""
                    INSERT INTO {self._table('ali1688_sku_replace_item')} (
                        run_id, task_key, store_name, platform, product_id,
                        platform_store_item_code, online_sku, replacement_sku,
                        handling, metric_date, source_row_number, replace_status,
                        created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', SYSUTCDATETIME(), SYSUTCDATETIME())
                    """,
                    (
                        run_id,
                        task_key,
                        _task_value(task, "store_name"),
                        _task_value(task, "platform"),
                        _task_value(task, "product_id"),
                        _task_value(task, "platform_store_item_code"),
                        _task_value(task, "online_sku"),
                        _task_value(task, "replacement_sku"),
                        _task_value(task, "handling"),
                        _task_metric_date(task),
                        int(getattr(task, "source_row_number", 0) or 0),
                    ),
                )
            connection.commit()

    def record_1688_results(self, run_id: str, records: Iterable[Mapping[str, Any]]) -> None:
        with connect_app_database(self.config) as connection:
            cursor = connection.cursor()
            for record in records:
                cursor.execute(
                    f"""
                    UPDATE {self._table('ali1688_sku_replace_item')}
                    SET replace_status = ?, attempts = ?, error_category = ?, error_message = ?,
                        screenshot_path = ?, html_snapshot_path = ?, updated_at = SYSUTCDATETIME()
                    WHERE run_id = ? AND store_name = ? AND product_id = ?
                      AND online_sku = ? AND replacement_sku = ?
                    """,
                    (
                        str(record.get("status") or "failed").strip(),
                        int(record.get("attempts") or 0),
                        _clean(record.get("error_category"), 100),
                        _clean(record.get("error_message")),
                        _clean(record.get("screenshot_path"), 1000),
                        _clean(record.get("html_snapshot_path"), 1000),
                        run_id,
                        str(record.get("store_name") or "").strip(),
                        str(record.get("product_id") or "").strip(),
                        str(record.get("online_sku") or "").strip(),
                        str(record.get("replacement_sku") or "").strip(),
                    ),
                )
            cursor.execute(
                f"""
                UPDATE {self._table('ali1688_sku_replace_item')}
                SET replace_status = 'not_attempted', updated_at = SYSUTCDATETIME()
                WHERE run_id = ? AND replace_status = 'pending'
                """,
                (run_id,),
            )
            connection.commit()

    def record_jushuitan_results(self, run_id: str, records: Iterable[Mapping[str, Any]]) -> None:
        with connect_app_database(self.config) as connection:
            cursor = connection.cursor()
            for record in records:
                cursor.execute(
                    f"""
                    UPDATE {self._table('ali1688_sku_replace_item')}
                    SET jushuitan_status = ?, jushuitan_category = ?, jushuitan_message = ?,
                        jushuitan_evidence_path = ?, updated_at = SYSUTCDATETIME()
                    WHERE run_id = ? AND task_key = ?
                    """,
                    (
                        str(record.get("status") or "failed").strip(),
                        _clean(record.get("category"), 100),
                        _clean(record.get("message")),
                        _clean(record.get("evidence_path"), 1000),
                        run_id,
                        str(record.get("task_id") or "").strip(),
                    ),
                )
            connection.commit()

    def finish_run(
        self,
        *,
        run_id: str,
        status: str,
        replace_report_path: str,
        jushuitan_report_path: str,
        summary_path: str,
        error_message: str = "",
    ) -> None:
        with connect_app_database(self.config) as connection:
            cursor = connection.cursor()
            counts = cursor.execute(
                f"""
                SELECT
                    SUM(CASE WHEN replace_status = 'success' THEN 1 ELSE 0 END),
                    SUM(CASE WHEN replace_status = 'already_replaced' THEN 1 ELSE 0 END),
                    SUM(CASE WHEN replace_status = 'failed' THEN 1 ELSE 0 END),
                    SUM(CASE WHEN replace_status = 'not_attempted' THEN 1 ELSE 0 END),
                    SUM(CASE WHEN jushuitan_status = 'success' THEN 1 ELSE 0 END),
                    SUM(CASE WHEN jushuitan_status = 'already_synced' THEN 1 ELSE 0 END),
                    SUM(CASE WHEN jushuitan_status = 'failed' THEN 1 ELSE 0 END)
                FROM {self._table('ali1688_sku_replace_item')} WHERE run_id = ?
                """,
                (run_id,),
            ).fetchone()
            values = [int(value or 0) for value in counts]
            cursor.execute(
                f"""
                UPDATE {self._table('ali1688_sku_replace_run')}
                SET status = ?, replace_success_count = ?, replace_already_count = ?,
                    replace_failed_count = ?, not_attempted_count = ?,
                    jushuitan_success_count = ?, jushuitan_already_count = ?,
                    jushuitan_failed_count = ?, replace_report_path = ?,
                    jushuitan_report_path = ?, summary_path = ?, error_message = ?,
                    finished_at = SYSUTCDATETIME(), updated_at = SYSUTCDATETIME()
                WHERE run_id = ?
                """,
                (
                    status,
                    *values,
                    replace_report_path,
                    jushuitan_report_path,
                    summary_path,
                    _clean(error_message),
                    run_id,
                ),
            )
            connection.commit()
