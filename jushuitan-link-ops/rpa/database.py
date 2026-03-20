from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any


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
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        password = str(payload.get("password", "")).strip()
        password_env = str(payload.get("password_env", "")).strip()
        if not password and password_env:
            password = os.environ.get(password_env, "").strip()
        if not password:
            raise ValueError("Database password is missing")

        return cls(
            driver=str(payload.get("driver", "")).strip() or choose_preferred_sqlserver_driver(),
            host=str(payload.get("host", "")).strip(),
            port=int(payload.get("port", 1433)),
            database=str(payload.get("database", "")).strip(),
            username=str(payload.get("username", "")).strip(),
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

    def upsert_task(self, payload: dict[str, Any]) -> None:
        self._require_connection()
        columns = [
            "task_id",
            "source_type",
            "source_record_id",
            "platform",
            "shop_name",
            "old_online_sku_code",
            "new_online_sku_code",
            "operator_name",
            "remark",
            "status",
        ]
        assignments = ", ".join(
            f"target.{column} = source.{column}"
            for column in columns
            if column != "task_id"
        )
        sql = f"""
        MERGE dbo.jst_code_change_task AS target
        USING (
            SELECT
                ? AS task_id,
                ? AS source_type,
                ? AS source_record_id,
                ? AS platform,
                ? AS shop_name,
                ? AS old_online_sku_code,
                ? AS new_online_sku_code,
                ? AS operator_name,
                ? AS remark,
                ? AS status
        ) AS source
        ON target.task_id = source.task_id
        WHEN MATCHED THEN
            UPDATE SET
                {assignments},
                target.updated_at = GETDATE()
        WHEN NOT MATCHED THEN
            INSERT ({", ".join(columns)})
            VALUES ({", ".join(f"source.{column}" for column in columns)});
        """
        values = [payload.get(column) for column in columns]
        cursor = self.connection.cursor()
        cursor.execute(sql, values)
        self.connection.commit()

    def log_result(self, payload: dict[str, Any]) -> None:
        self._insert_dict(
            "dbo.jst_code_change_result",
            [
                "task_id",
                "platform",
                "shop_name",
                "old_online_sku_code",
                "new_online_sku_code",
                "matched_count_before",
                "remaining_count_after",
                "status",
                "operator_name",
            ],
            payload,
        )

    def log_error(self, payload: dict[str, Any]) -> None:
        self._insert_dict(
            "dbo.jst_code_change_error_log",
            [
                "task_id",
                "platform",
                "shop_name",
                "old_online_sku_code",
                "new_online_sku_code",
                "step_name",
                "error_type",
                "error_message",
                "screenshot_path",
                "html_snapshot_path",
                "current_url",
                "page_title",
            ],
            payload,
        )

    def _insert_dict(self, table_name: str, columns: list[str], payload: dict[str, Any]) -> None:
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
            raise RuntimeError("Database connection is not open")


def choose_preferred_sqlserver_driver() -> str:
    try:
        pyodbc = _require_pyodbc()
    except ModuleNotFoundError:
        return "ODBC Driver 17 for SQL Server"

    drivers = [str(driver).strip() for driver in pyodbc.drivers()]
    for candidate in (
        "ODBC Driver 18 for SQL Server",
        "ODBC Driver 17 for SQL Server",
        "ODBC Driver 13 for SQL Server",
        "SQL Server Native Client 11.0",
        "SQL Server",
    ):
        if candidate in drivers:
            return candidate
    return "ODBC Driver 17 for SQL Server"


def _require_pyodbc():
    try:
        import pyodbc  # type: ignore
    except ModuleNotFoundError as exc:
        raise ModuleNotFoundError("Missing dependency 'pyodbc'.") from exc
    return pyodbc
