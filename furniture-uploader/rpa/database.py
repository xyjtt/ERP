from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import pyodbc


@dataclass
class DatabaseConfig:
    driver: str
    host: str
    port: int
    database: str
    username: str
    password: str
    encrypt: bool = False
    trust_server_certificate: bool = True

    @classmethod
    def from_json(cls, path: str | Path) -> "DatabaseConfig":
        config_path = Path(path)
        with config_path.open("r", encoding="utf-8") as file:
            payload = json.load(file)

        password = payload.get("password", "").strip()
        password_env = payload.get("password_env", "").strip()
        if not password and password_env:
            password = os.environ.get(password_env, "").strip()

        if not password:
            raise ValueError("Database password is missing. Set it in env or local config.")

        driver = str(payload.get("driver", "")).strip() or choose_preferred_sqlserver_driver()

        return cls(
            driver=driver,
            host=payload["host"],
            port=int(payload.get("port", 1433)),
            database=payload["database"],
            username=payload["username"],
            password=password,
            encrypt=bool(payload.get("encrypt", False)),
            trust_server_certificate=bool(payload.get("trust_server_certificate", True)),
        )

    def connection_string(self) -> str:
        return (
            f"DRIVER={{{self.driver}}};"
            f"SERVER={self.host},{self.port};"
            f"DATABASE={self.database};"
            f"UID={self.username};"
            f"PWD={self.password};"
            f"Encrypt={'yes' if self.encrypt else 'no'};"
            f"TrustServerCertificate={'yes' if self.trust_server_certificate else 'no'};"
        )


@dataclass
class StoreDefaultTemplate:
    channel: str
    store_name: str
    ship_from_template: str = ""
    freight_template: str = ""
    ship_time_template: str = ""
    default_length_cm: str = ""
    default_width_cm: str = ""
    default_height_cm: str = ""
    default_weight_g: str = ""


class SQLServerLogger:
    def __init__(self, config: DatabaseConfig) -> None:
        self.config = config
        self.connection: Any | None = None

    def connect(self) -> None:
        pyodbc = _require_pyodbc()
        self.connection = pyodbc.connect(self.config.connection_string())

    def close(self) -> None:
        if self.connection:
            self.connection.close()
            self.connection = None

    def log_publish_result(self, payload: dict[str, Any]) -> None:
        self._require_connection()
        columns = [
            "task_id",
            "channel",
            "store_name",
            "outer_sku",
            "platform_link_id",
            "platform_link_url",
            "platform_item_title",
            "link_owner",
            "operator_name",
            "published_at",
            "source_type",
            "source_record_id",
            "match_source_id",
            "category_path",
            "status",
        ]
        self._insert_dict("dbo.publish_result", columns, payload)

    def upsert_publish_task(self, payload: dict[str, Any]) -> None:
        self._require_connection()
        columns = [
            "task_id",
            "source_type",
            "source_record_id",
            "channel",
            "store_name",
            "store_label",
            "outer_sku",
            "title",
            "category_hint",
            "brand",
            "material",
            "color",
            "size",
            "price",
            "quantity",
            "main_image",
            "detail_images",
            "description",
            "ship_from_template",
            "freight_template",
            "ship_time_template",
            "length_cm",
            "width_cm",
            "height_cm",
            "weight_g",
            "link_owner",
            "operator_name",
            "status",
        ]
        filtered = {column: payload.get(column) for column in columns}
        assignments = ", ".join(
            f"target.{column} = source.{column}"
            for column in columns
            if column not in {"task_id", "channel", "store_name", "outer_sku"}
        )
        sql = f"""
        MERGE dbo.publish_task AS target
        USING (
            SELECT
                ? AS task_id,
                ? AS source_type,
                ? AS source_record_id,
                ? AS channel,
                ? AS store_name,
                ? AS store_label,
                ? AS outer_sku,
                ? AS title,
                ? AS category_hint,
                ? AS brand,
                ? AS material,
                ? AS color,
                ? AS size,
                ? AS price,
                ? AS quantity,
                ? AS main_image,
                ? AS detail_images,
                ? AS description,
                ? AS ship_from_template,
                ? AS freight_template,
                ? AS ship_time_template,
                ? AS length_cm,
                ? AS width_cm,
                ? AS height_cm,
                ? AS weight_g,
                ? AS link_owner,
                ? AS operator_name,
                ? AS status
        ) AS source
        ON
            target.task_id = source.task_id
            AND target.channel = source.channel
            AND target.store_name = source.store_name
            AND target.outer_sku = source.outer_sku
        WHEN MATCHED THEN
            UPDATE SET
                {assignments},
                target.updated_at = GETDATE()
        WHEN NOT MATCHED THEN
            INSERT ({", ".join(filtered.keys())})
            VALUES ({", ".join(f"source.{column}" for column in filtered.keys())});
        """
        cursor = self.connection.cursor()
        cursor.execute(sql, list(filtered.values()))
        self.connection.commit()

    def log_publish_error(self, payload: dict[str, Any]) -> None:
        self._require_connection()
        columns = [
            "task_id",
            "channel",
            "store_name",
            "outer_sku",
            "step_name",
            "error_type",
            "error_message",
            "screenshot_path",
            "html_snapshot_path",
            "operator_name",
            "failed_at",
            "status",
        ]
        self._insert_dict("dbo.publish_error_log", columns, payload)

    def log_match_candidate(self, payload: dict[str, Any]) -> None:
        self._require_connection()
        columns = [
            "task_id",
            "outer_sku",
            "channel",
            "store_name",
            "candidate_source_id",
            "candidate_store_name",
            "candidate_title",
            "candidate_use_result",
            "candidate_error_message",
            "candidate_error_type",
            "candidate_attempted_at",
        ]
        self._insert_dict("dbo.match_candidate_history", columns, payload)

    def get_category_mapping(
        self,
        *,
        channel: str,
        store_name: str,
        keyword_text: str,
    ) -> str | None:
        self._require_connection()
        sql = """
        SELECT TOP 1 category_path
        FROM dbo.category_mapping_history
        WHERE channel = ?
          AND (store_name = ? OR store_name IS NULL)
          AND keyword_text = ?
        ORDER BY
          CASE WHEN store_name = ? THEN 0 ELSE 1 END,
          success_count DESC,
          last_success_at DESC,
          updated_at DESC
        """
        cursor = self.connection.cursor()
        row = cursor.execute(sql, channel, store_name, keyword_text, store_name).fetchone()
        if not row:
            return None
        return str(row.category_path or row[0] or "").strip() or None

    def upsert_category_mapping_history(self, payload: dict[str, Any]) -> None:
        self._require_connection()
        channel = payload.get("channel")
        store_name = payload.get("store_name")
        keyword_text = payload.get("keyword_text")
        category_path = payload.get("category_path")
        success = bool(payload.get("success", True))

        sql = """
        MERGE dbo.category_mapping_history AS target
        USING (
            SELECT
                ? AS channel,
                ? AS store_name,
                ? AS keyword_text,
                ? AS category_path
        ) AS source
        ON
            target.channel = source.channel
            AND ISNULL(target.store_name, '') = ISNULL(source.store_name, '')
            AND target.keyword_text = source.keyword_text
        WHEN MATCHED THEN
            UPDATE SET
                target.category_path = source.category_path,
                target.success_count = target.success_count + ?,
                target.failure_count = target.failure_count + ?,
                target.last_success_at = CASE WHEN ? = 1 THEN GETDATE() ELSE target.last_success_at END,
                target.last_failure_at = CASE WHEN ? = 1 THEN GETDATE() ELSE target.last_failure_at END,
                target.updated_at = GETDATE()
        WHEN NOT MATCHED THEN
            INSERT (
                channel,
                store_name,
                keyword_text,
                category_path,
                success_count,
                failure_count,
                last_success_at,
                last_failure_at
            )
            VALUES (
                source.channel,
                source.store_name,
                source.keyword_text,
                source.category_path,
                ?,
                ?,
                CASE WHEN ? = 1 THEN GETDATE() ELSE NULL END,
                CASE WHEN ? = 1 THEN GETDATE() ELSE NULL END
            );
        """
        success_increment = 1 if success else 0
        failure_increment = 0 if success else 1
        failure_flag = 0 if success else 1
        cursor = self.connection.cursor()
        cursor.execute(
            sql,
            [
                channel,
                store_name,
                keyword_text,
                category_path,
                success_increment,
                failure_increment,
                success_increment,
                failure_flag,
                success_increment,
                failure_increment,
                success_increment,
                failure_flag,
            ],
        )
        self.connection.commit()

    def upsert_store_default_template(self, payload: dict[str, Any]) -> None:
        self._require_connection()
        sql = """
        MERGE dbo.store_default_template AS target
        USING (
            SELECT
                ? AS channel,
                ? AS store_name,
                ? AS ship_from_template,
                ? AS freight_template,
                ? AS ship_time_template,
                ? AS default_length_cm,
                ? AS default_width_cm,
                ? AS default_height_cm,
                ? AS default_weight_g
        ) AS source
        ON target.channel = source.channel AND target.store_name = source.store_name
        WHEN MATCHED THEN
            UPDATE SET
                target.ship_from_template = CASE WHEN NULLIF(source.ship_from_template, '') IS NOT NULL THEN source.ship_from_template ELSE target.ship_from_template END,
                target.freight_template = CASE WHEN NULLIF(source.freight_template, '') IS NOT NULL THEN source.freight_template ELSE target.freight_template END,
                target.ship_time_template = CASE WHEN NULLIF(source.ship_time_template, '') IS NOT NULL THEN source.ship_time_template ELSE target.ship_time_template END,
                target.default_length_cm = COALESCE(NULLIF(source.default_length_cm, ''), target.default_length_cm),
                target.default_width_cm = COALESCE(NULLIF(source.default_width_cm, ''), target.default_width_cm),
                target.default_height_cm = COALESCE(NULLIF(source.default_height_cm, ''), target.default_height_cm),
                target.default_weight_g = COALESCE(NULLIF(source.default_weight_g, ''), target.default_weight_g),
                target.updated_at = GETDATE()
        WHEN NOT MATCHED THEN
            INSERT (
                channel,
                store_name,
                ship_from_template,
                freight_template,
                ship_time_template,
                default_length_cm,
                default_width_cm,
                default_height_cm,
                default_weight_g
            )
            VALUES (
                source.channel,
                source.store_name,
                NULLIF(source.ship_from_template, ''),
                NULLIF(source.freight_template, ''),
                NULLIF(source.ship_time_template, ''),
                NULLIF(source.default_length_cm, ''),
                NULLIF(source.default_width_cm, ''),
                NULLIF(source.default_height_cm, ''),
                NULLIF(source.default_weight_g, '')
            );
        """
        cursor = self.connection.cursor()
        cursor.execute(
            sql,
            [
                payload.get("channel"),
                payload.get("store_name"),
                payload.get("ship_from_template", ""),
                payload.get("freight_template", ""),
                payload.get("ship_time_template", ""),
                payload.get("default_length_cm", ""),
                payload.get("default_width_cm", ""),
                payload.get("default_height_cm", ""),
                payload.get("default_weight_g", ""),
            ],
        )
        self.connection.commit()

    def get_store_default_template(
        self,
        *,
        channel: str,
        store_name: str,
    ) -> StoreDefaultTemplate | None:
        self._require_connection()
        sql = """
        SELECT TOP 1
            channel,
            store_name,
            ship_from_template,
            freight_template,
            ship_time_template,
            default_length_cm,
            default_width_cm,
            default_height_cm,
            default_weight_g
        FROM dbo.store_default_template
        WHERE channel = ? AND store_name = ?
        """
        cursor = self.connection.cursor()
        row = cursor.execute(sql, channel, store_name).fetchone()
        if not row:
            return None

        return StoreDefaultTemplate(
            channel=str(row.channel or "").strip(),
            store_name=str(row.store_name or "").strip(),
            ship_from_template=str(row.ship_from_template or "").strip(),
            freight_template=str(row.freight_template or "").strip(),
            ship_time_template=str(row.ship_time_template or "").strip(),
            default_length_cm=str(row.default_length_cm or "").strip(),
            default_width_cm=str(row.default_width_cm or "").strip(),
            default_height_cm=str(row.default_height_cm or "").strip(),
            default_weight_g=str(row.default_weight_g or "").strip(),
        )

    def _insert_dict(
        self,
        table_name: str,
        columns: list[str],
        payload: dict[str, Any],
    ) -> None:
        self._require_connection()
        filtered = {column: payload.get(column) for column in columns}
        sql = (
            f"INSERT INTO {table_name} ({', '.join(filtered.keys())}) "
            f"VALUES ({', '.join('?' for _ in filtered)})"
        )
        cursor = self.connection.cursor()
        cursor.execute(sql, list(filtered.values()))
        self.connection.commit()

    def _require_connection(self) -> None:
        if not self.connection:
            raise RuntimeError("Database connection is not open.")


def _require_pyodbc():
    try:
        import pyodbc  # type: ignore
    except ModuleNotFoundError as exc:
        raise ModuleNotFoundError(
            "Missing dependency 'pyodbc'. Install requirements before using SQL Server logging."
        ) from exc
    return pyodbc


def choose_preferred_sqlserver_driver() -> str:
    try:
        pyodbc = _require_pyodbc()
    except ModuleNotFoundError:
        return "ODBC Driver 17 for SQL Server"

    drivers = [str(driver).strip() for driver in pyodbc.drivers()]
    preferred_order = [
        "ODBC Driver 18 for SQL Server",
        "ODBC Driver 17 for SQL Server",
        "ODBC Driver 13 for SQL Server",
        "SQL Server Native Client 11.0",
        "SQL Server Native Client 10.0",
        "SQL Server",
    ]
    for candidate in preferred_order:
        if candidate in drivers:
            return candidate

    for driver in drivers:
        if "SQL Server" in driver:
            return driver

    return "ODBC Driver 17 for SQL Server"
