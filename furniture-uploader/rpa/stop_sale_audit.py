from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import re
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping


DEFAULT_APP_SERVER = "218.93.9.21"
DEFAULT_APP_PORT = 1433
DEFAULT_APP_DATABASE = "JSReportReplica"
DEFAULT_APP_SCHEMA = "app"
DEFAULT_APP_DRIVER = "ODBC Driver 18 for SQL Server"
DEFAULT_CREDENTIAL_REF = "YYDD/1688/database/app-writer"
DINGTALK_WEBHOOK_CREDENTIAL_REF = "YYDD/1688/notification/dingtalk/webhook"
DINGTALK_SECRET_CREDENTIAL_REF = "YYDD/1688/notification/dingtalk/secret"


@dataclass(frozen=True)
class StopSaleAppConfig:
    server: str
    port: int
    database: str
    schema: str
    user: str
    password: str = field(repr=False)
    driver: str = DEFAULT_APP_DRIVER
    encrypt: bool = True
    trust_server_certificate: bool = True
    timeout_seconds: int = 15
    credential_ref: str = ""

    def safe_dict(self) -> dict[str, Any]:
        return {
            "database": self.database,
            "schema": self.schema,
            "driver": self.driver,
            "credential_ref": self.credential_ref or "environment",
        }


def _env_bool(value: str | None, default: bool) -> bool:
    text = str(value or "").strip().lower()
    if not text:
        return default
    return text in {"1", "true", "yes", "on"}


def _validate_identifier(value: str, label: str) -> str:
    text = str(value or "").strip()
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", text):
        raise ValueError(f"Invalid {label}: {text}")
    return text


def _external_config_root() -> Path:
    configured = str(os.getenv("YYDD_1688_CONFIG_ROOT", "")).strip()
    if configured:
        return Path(configured)
    program_data = str(os.getenv("PROGRAMDATA", r"C:\ProgramData")).strip()
    return Path(program_data) / "YYDD" / "1688-crawler" / "config"


def _load_secret_provider_module(shared_runtime_root: Path):
    module_path = shared_runtime_root / "src" / "security" / "secret_provider.py"
    if not module_path.exists():
        raise FileNotFoundError(f"1688 secret provider module not found: {module_path}")
    module_name = "_stop_sale_1688_secret_provider"
    existing = sys.modules.get(module_name)
    if existing is not None:
        return existing
    spec = importlib.util.spec_from_file_location(module_name, module_path)
    if spec is None or spec.loader is None:
        raise RuntimeError("Unable to load the 1688 secret provider module.")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def _load_runtime_secret(shared_runtime_root: str | Path, credential_ref: str) -> str:
    provider_module = _load_secret_provider_module(Path(shared_runtime_root).resolve())
    record = provider_module.get_secret_provider().get(credential_ref)
    value = str(record.secret or "")
    if not value:
        raise RuntimeError(f"Credential has no secret: {credential_ref}")
    return value


def hydrate_dingtalk_credentials(shared_runtime_root: str | Path) -> dict[str, bool]:
    references = {
        "DINGTALK_WEBHOOK": DINGTALK_WEBHOOK_CREDENTIAL_REF,
        "DINGTALK_SECRET": DINGTALK_SECRET_CREDENTIAL_REF,
    }
    result: dict[str, bool] = {}
    for env_name, credential_ref in references.items():
        configured = bool(str(os.getenv(env_name, "")).strip())
        if not configured:
            try:
                os.environ[env_name] = _load_runtime_secret(shared_runtime_root, credential_ref)
            except Exception:
                result[env_name] = False
                continue
            configured = True
        result[env_name] = configured
    return result


def _load_app_config_from_1688_runtime(shared_runtime_root: Path) -> StopSaleAppConfig:
    database_path = _external_config_root() / "database.json"
    if not database_path.exists():
        raise FileNotFoundError(f"1688 external database config not found: {database_path}")
    payload = json.loads(database_path.read_text(encoding="utf-8"))
    target = dict(payload.get("write_target", {}))
    credential_ref = str(target.get("credential_ref") or DEFAULT_CREDENTIAL_REF).strip()
    if not credential_ref:
        raise RuntimeError("1688 write_target.credential_ref is not configured.")

    provider_module = _load_secret_provider_module(shared_runtime_root)
    record = provider_module.get_secret_provider().get(credential_ref)
    if not str(record.secret or ""):
        raise RuntimeError(f"1688 app-writer credential has no secret: {credential_ref}")
    if not str(record.username or ""):
        raise RuntimeError(f"1688 app-writer credential has no username: {credential_ref}")

    return StopSaleAppConfig(
        server=str(target.get("server") or DEFAULT_APP_SERVER).strip(),
        port=int(target.get("port") or DEFAULT_APP_PORT),
        database=_validate_identifier(target.get("database") or DEFAULT_APP_DATABASE, "database"),
        schema=_validate_identifier(target.get("schema") or DEFAULT_APP_SCHEMA, "schema"),
        user=str(record.username).strip(),
        password=str(record.secret),
        driver=str(target.get("driver") or DEFAULT_APP_DRIVER).strip(),
        encrypt=bool(target.get("encrypt", True)),
        trust_server_certificate=bool(target.get("trust_server_certificate", True)),
        timeout_seconds=max(1, int(target.get("connect_timeout") or 15)),
        credential_ref=credential_ref,
    )


def resolve_stop_sale_app_config(shared_runtime_root: str | Path) -> StopSaleAppConfig:
    password = str(os.getenv("STOP_SALE_APP_SQLSERVER_PASSWORD", ""))
    user = str(os.getenv("STOP_SALE_APP_SQLSERVER_USER", "")).strip()
    if password or user:
        if not password or not user:
            raise RuntimeError(
                "STOP_SALE_APP_SQLSERVER_USER and STOP_SALE_APP_SQLSERVER_PASSWORD must both be configured."
            )
        return StopSaleAppConfig(
            server=str(os.getenv("STOP_SALE_APP_SQLSERVER_HOST", DEFAULT_APP_SERVER)).strip(),
            port=int(os.getenv("STOP_SALE_APP_SQLSERVER_PORT", str(DEFAULT_APP_PORT))),
            database=_validate_identifier(
                os.getenv("STOP_SALE_APP_SQLSERVER_DATABASE", DEFAULT_APP_DATABASE),
                "database",
            ),
            schema=_validate_identifier(
                os.getenv("STOP_SALE_APP_SQLSERVER_SCHEMA", DEFAULT_APP_SCHEMA),
                "schema",
            ),
            user=user,
            password=password,
            driver=str(os.getenv("STOP_SALE_APP_SQLSERVER_DRIVER", DEFAULT_APP_DRIVER)).strip(),
            encrypt=_env_bool(os.getenv("STOP_SALE_APP_SQLSERVER_ENCRYPT"), True),
            trust_server_certificate=_env_bool(
                os.getenv("STOP_SALE_APP_SQLSERVER_TRUST_SERVER_CERTIFICATE"),
                True,
            ),
            timeout_seconds=max(1, int(os.getenv("STOP_SALE_APP_SQLSERVER_TIMEOUT", "15"))),
        )
    return _load_app_config_from_1688_runtime(Path(shared_runtime_root).resolve())


def build_connection_string(config: StopSaleAppConfig) -> str:
    return (
        f"DRIVER={{{config.driver}}};"
        f"SERVER={config.server},{config.port};"
        f"DATABASE={config.database};"
        f"UID={config.user};"
        f"PWD={config.password};"
        f"Encrypt={'yes' if config.encrypt else 'no'};"
        f"TrustServerCertificate={'yes' if config.trust_server_certificate else 'no'};"
        f"Connection Timeout={config.timeout_seconds};"
    )


def connect_app_database(config: StopSaleAppConfig):
    import pyodbc

    return pyodbc.connect(build_connection_string(config), timeout=config.timeout_seconds)


def normalized_identity_part(value: Any) -> str:
    return "".join(str(value or "").split()).lower()


def stop_sale_task_key(
    store_name: Any,
    product_id: Any,
    online_sku: Any,
    platform_store_item_code: Any,
) -> str:
    identity = "|".join(
        normalized_identity_part(value)
        for value in (store_name, product_id, online_sku, platform_store_item_code)
    )
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()


def stop_sale_offline_key(store_name: Any, product_id: Any, online_sku: Any) -> str:
    identity = "|".join(
        normalized_identity_part(value)
        for value in (store_name, product_id, online_sku)
    )
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()


def load_jsonl_records(path: str | Path) -> list[dict[str, Any]]:
    source = Path(path)
    if not source.exists():
        return []
    records: list[dict[str, Any]] = []
    for line in source.read_text(encoding="utf-8").splitlines():
        text = line.strip()
        if not text:
            continue
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            records.append(payload)
    return records


def _task_value(task: Any, name: str, default: str = "") -> str:
    return str(getattr(task, name, default) or "").strip()


def _task_metric_date(task: Any) -> str | None:
    raw = getattr(task, "raw", {})
    value = str(raw.get("指标日期", "") if isinstance(raw, Mapping) else "").strip()
    return value[:10] if value else None


def _clean_error(value: Any, limit: int = 2000) -> str:
    return str(value or "").strip()[:limit]


class StopSaleAuditRepository:
    REQUIRED_TABLES = (
        "ali1688_stop_sale_run",
        "ali1688_stop_sale_item",
    )

    def __init__(self, config: StopSaleAppConfig) -> None:
        self.config = config
        self.schema = _validate_identifier(config.schema, "schema")

    def _table(self, name: str) -> str:
        return f"[{self.schema}].[{_validate_identifier(name, 'table')}]"

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
            raise RuntimeError(
                f"Stop-sale audit connected to unexpected database: {database_name}"
            )
        return {
            "database": database_name,
            "schema": self.schema,
            "missing_tables": missing,
            "ready": not missing,
        }

    def count_active_crawler_tasks(self) -> int:
        with connect_app_database(self.config) as connection:
            cursor = connection.cursor()
            if cursor.execute("SELECT OBJECT_ID('app.crawler_task', 'U')").fetchone()[0] is None:
                return 0
            row = cursor.execute(
                """
                SELECT COUNT(*)
                FROM app.crawler_task
                WHERE task_kind IN ('task', 'variant')
                  AND status IN ('claimed', 'preflight', 'running', 'persisted', 'validating')
                """
            ).fetchone()
            return int(row[0] or 0)

    def count_recent_active_stop_sale_runs(self, max_age_minutes: int = 240) -> int:
        age_minutes = max(1, int(max_age_minutes))
        run_table = self._table("ali1688_stop_sale_run")
        with connect_app_database(self.config) as connection:
            row = connection.cursor().execute(
                f"""
                SELECT COUNT(*)
                FROM {run_table}
                WHERE status = 'running'
                  AND finished_at IS NULL
                  AND COALESCE(updated_at, started_at) >= DATEADD(MINUTE, ?, SYSUTCDATETIME())
                """,
                (-age_minutes,),
            ).fetchone()
            return int(row[0] or 0)

    def heartbeat_run(
        self,
        run_id: str,
        *,
        max_attempts: int = 3,
        retry_seconds: float = 2.0,
    ) -> None:
        if max_attempts <= 0 or retry_seconds < 0:
            raise ValueError("Heartbeat retry settings are invalid.")

        import pyodbc

        run_table = self._table("ali1688_stop_sale_run")
        for attempt in range(max_attempts):
            try:
                with connect_app_database(self.config) as connection:
                    cursor = connection.cursor()
                    cursor.execute(
                        f"""
                        UPDATE {run_table}
                        SET updated_at = SYSUTCDATETIME()
                        WHERE run_id = ?
                          AND status = 'running'
                          AND finished_at IS NULL
                        """,
                        (str(run_id),),
                    )
                    if cursor.rowcount != 1:
                        active_row = cursor.execute(
                            f"""
                            SELECT status, finished_at
                            FROM {run_table}
                            WHERE run_id = ?
                            """,
                            (str(run_id),),
                        ).fetchone()
                        if (
                            active_row is None
                            or str(active_row[0] or "") != "running"
                            or active_row[1] is not None
                        ):
                            raise RuntimeError(
                                f"Stop-sale audit run is no longer active: {run_id}"
                            )
                    connection.commit()
                return
            except pyodbc.Error:
                if attempt + 1 >= max_attempts:
                    raise
                time.sleep(retry_seconds)

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
        offline_target_count = len(
            {
                stop_sale_offline_key(
                    _task_value(task, "store_name"),
                    _task_value(task, "product_id"),
                    _task_value(task, "online_sku"),
                )
                for task in materialized
            }
        )
        run_table = self._table("ali1688_stop_sale_run")
        item_table = self._table("ali1688_stop_sale_item")
        with connect_app_database(self.config) as connection:
            cursor = connection.cursor()
            cursor.execute(
                f"""
                INSERT INTO {run_table} (
                    run_id, mode, source_database, source_table, input_file,
                    status, total_count, offline_target_count, jushuitan_target_count,
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
                    offline_target_count,
                    len(materialized),
                ),
            )
            for task in materialized:
                task_key = stop_sale_task_key(
                    _task_value(task, "store_name"),
                    _task_value(task, "product_id"),
                    _task_value(task, "online_sku"),
                    _task_value(task, "platform_store_item_code"),
                )
                cursor.execute(
                    f"""
                    INSERT INTO {item_table} (
                        run_id, task_key, offline_task_key, store_name, platform, product_id,
                        platform_store_item_code, online_sku, handling, metric_date,
                        source_row_number, offline_status, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', SYSUTCDATETIME(), SYSUTCDATETIME())
                    """,
                    (
                        run_id,
                        task_key,
                        stop_sale_offline_key(
                            _task_value(task, "store_name"),
                            _task_value(task, "product_id"),
                            _task_value(task, "online_sku"),
                        ),
                        _task_value(task, "store_name"),
                        _task_value(task, "platform"),
                        _task_value(task, "product_id"),
                        _task_value(task, "platform_store_item_code"),
                        _task_value(task, "online_sku"),
                        _task_value(task, "handling"),
                        _task_metric_date(task),
                        int(getattr(task, "source_row_number", 0) or 0),
                    ),
                )
            connection.commit()

    def record_1688_results(self, run_id: str, records: Iterable[Mapping[str, Any]]) -> None:
        item_table = self._table("ali1688_stop_sale_item")
        with connect_app_database(self.config) as connection:
            cursor = connection.cursor()
            for record in records:
                cursor.execute(
                    f"""
                    UPDATE {item_table}
                    SET offline_status = ?, attempts = ?, error_category = ?, error_message = ?,
                        screenshot_path = ?, html_snapshot_path = ?, updated_at = SYSUTCDATETIME()
                    WHERE run_id = ? AND store_name = ? AND product_id = ? AND online_sku = ?
                    """,
                    (
                        str(record.get("status") or "failed").strip(),
                        int(record.get("attempts") or 0),
                        _clean_error(record.get("error_category"), 100),
                        _clean_error(record.get("error_message")),
                        _clean_error(record.get("screenshot_path"), 1000),
                        _clean_error(record.get("html_snapshot_path"), 1000),
                        run_id,
                        str(record.get("store_name") or "").strip(),
                        str(record.get("product_id") or "").strip(),
                        str(record.get("online_sku") or "").strip(),
                    ),
                )
            cursor.execute(
                f"""
                UPDATE {item_table}
                SET offline_status = 'not_attempted', updated_at = SYSUTCDATETIME()
                WHERE run_id = ? AND offline_status = 'pending'
                """,
                (run_id,),
            )
            connection.commit()

    def record_jushuitan_results(self, run_id: str, records: Iterable[Mapping[str, Any]]) -> None:
        item_table = self._table("ali1688_stop_sale_item")
        with connect_app_database(self.config) as connection:
            cursor = connection.cursor()
            for record in records:
                cursor.execute(
                    f"""
                    UPDATE {item_table}
                    SET jushuitan_status = ?, jushuitan_category = ?, jushuitan_message = ?,
                        jushuitan_evidence_path = ?, updated_at = SYSUTCDATETIME()
                    WHERE run_id = ? AND task_key = ?
                    """,
                    (
                        str(record.get("status") or "failed").strip(),
                        _clean_error(record.get("category"), 100),
                        _clean_error(record.get("message")),
                        _clean_error(record.get("evidence_path"), 1000),
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
        offline_report_path: str,
        jushuitan_report_path: str,
        summary_path: str,
        error_message: str = "",
    ) -> None:
        run_table = self._table("ali1688_stop_sale_run")
        item_table = self._table("ali1688_stop_sale_item")
        with connect_app_database(self.config) as connection:
            cursor = connection.cursor()
            counts = cursor.execute(
                f"""
                SELECT
                    COUNT(DISTINCT CASE WHEN offline_status = 'success' THEN offline_task_key END),
                    COUNT(DISTINCT CASE WHEN offline_status = 'already_offline' THEN offline_task_key END),
                    COUNT(DISTINCT CASE WHEN offline_status = 'failed' THEN offline_task_key END),
                    COUNT(DISTINCT CASE WHEN offline_status = 'not_attempted' THEN offline_task_key END),
                    SUM(CASE WHEN jushuitan_status = 'success' THEN 1 ELSE 0 END),
                    SUM(CASE WHEN jushuitan_status = 'already_cleared' THEN 1 ELSE 0 END),
                    SUM(CASE WHEN jushuitan_status = 'failed' THEN 1 ELSE 0 END)
                FROM {item_table}
                WHERE run_id = ?
                """,
                (run_id,),
            ).fetchone()
            values = [int(value or 0) for value in counts]
            cursor.execute(
                f"""
                UPDATE {run_table}
                SET status = ?, offline_success_count = ?, offline_already_count = ?,
                    offline_failed_count = ?, not_attempted_count = ?,
                    jushuitan_success_count = ?, jushuitan_already_count = ?,
                    jushuitan_failed_count = ?, offline_report_path = ?,
                    jushuitan_report_path = ?, summary_path = ?, error_message = ?,
                    finished_at = SYSUTCDATETIME(), updated_at = SYSUTCDATETIME()
                WHERE run_id = ?
                """,
                (
                    status,
                    *values,
                    offline_report_path,
                    jushuitan_report_path,
                    summary_path,
                    _clean_error(error_message),
                    run_id,
                ),
            )
            connection.commit()
