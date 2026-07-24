"""External database metadata and credential resolution for the title engine."""

from __future__ import annotations

import importlib.util
import json
import os
import sys
import threading
from pathlib import Path
from typing import Any


DEFAULT_SERVER = "218.93.9.21"
DEFAULT_PORT = 1433
DEFAULT_DATABASE = "JSReportReplica"
DEFAULT_SCHEMA = "app"
DEFAULT_DRIVER = "SQL Server Native Client 10.0"
DEFAULT_CREDENTIAL_REF = "YYDD/1688/database/app-writer"
_SECRET_PROVIDER_MODULE_NAME = "_title_engine_1688_secret_provider"
_SECRET_PROVIDER_MODULE_LOCK = threading.Lock()


def _env_bool(value: str | None, default: bool) -> bool:
    text = str(value or "").strip().lower()
    if not text:
        return default
    return text in {"1", "true", "yes", "on"}


def _external_config_root() -> Path:
    configured = str(os.getenv("YYDD_1688_CONFIG_ROOT", "")).strip()
    if configured:
        return Path(configured)
    program_data = str(os.getenv("PROGRAMDATA", r"C:\ProgramData")).strip()
    return Path(program_data) / "YYDD" / "1688-crawler" / "config"


def _environment_value(name: str, fallback: str, default: str = "") -> str:
    return str(os.getenv(name, os.getenv(fallback, default))).strip()


def resolve_database_settings() -> dict[str, Any]:
    """Resolve non-secret settings and optional environment credentials."""
    username = _environment_value("TITLE_ENGINE_SQLSERVER_USERNAME", "SQLSERVER_USERNAME")
    password = str(
        os.getenv(
            "TITLE_ENGINE_SQLSERVER_PASSWORD",
            os.getenv("SQLSERVER_PASSWORD", ""),
        )
    )
    if bool(username) != bool(password):
        raise RuntimeError("Title engine SQL Server username and password must both be configured.")

    target: dict[str, Any] = {}
    config_path = _external_config_root() / "database.json"
    if config_path.exists():
        payload = json.loads(config_path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError(f"External database configuration must be an object: {config_path}")
        target = dict(payload.get("write_target") or {})

    return {
        "server": _environment_value(
            "TITLE_ENGINE_SQLSERVER_HOST",
            "SQLSERVER_HOST",
            str(target.get("server") or DEFAULT_SERVER),
        ),
        "port": int(
            _environment_value(
                "TITLE_ENGINE_SQLSERVER_PORT",
                "SQLSERVER_PORT",
                str(target.get("port") or DEFAULT_PORT),
            )
        ),
        "database": _environment_value(
            "TITLE_ENGINE_SQLSERVER_DATABASE",
            "SQLSERVER_DATABASE",
            str(target.get("database") or DEFAULT_DATABASE),
        ),
        "schema": _environment_value(
            "TITLE_ENGINE_SQLSERVER_SCHEMA",
            "SQLSERVER_SCHEMA",
            str(target.get("schema") or DEFAULT_SCHEMA),
        ),
        "username": username,
        "password": password,
        "credential_ref": "" if username else str(
            target.get("credential_ref") or DEFAULT_CREDENTIAL_REF
        ).strip(),
        "driver": _environment_value(
            "TITLE_ENGINE_SQLSERVER_DRIVER",
            "SQLSERVER_DRIVER",
            str(target.get("driver") or DEFAULT_DRIVER),
        ),
        "encrypt": _env_bool(
            os.getenv("TITLE_ENGINE_SQLSERVER_ENCRYPT", os.getenv("SQLSERVER_ENCRYPT")),
            bool(target.get("encrypt", True)),
        ),
        "trust_server_certificate": _env_bool(
            os.getenv(
                "TITLE_ENGINE_SQLSERVER_TRUST_SERVER_CERTIFICATE",
                os.getenv("SQLSERVER_TRUST_SERVER_CERTIFICATE"),
            ),
            bool(target.get("trust_server_certificate", True)),
        ),
        "connect_timeout": max(1, int(target.get("connect_timeout") or 15)),
        "query_timeout": max(
            1,
            int(
                _environment_value(
                    "TITLE_ENGINE_SQLSERVER_QUERY_TIMEOUT",
                    "SQLSERVER_QUERY_TIMEOUT",
                    str(target.get("query_timeout") or 45),
                )
            ),
        ),
    }


def load_external_credential(
    credential_ref: str,
    *,
    shared_runtime_root: str | Path | None = None,
) -> tuple[str, str]:
    """Load one credential from the shared Windows credential provider."""
    runtime_root = Path(
        shared_runtime_root
        or os.getenv("YYDD_1688_RUNTIME_ROOT", "D:/script_1688")
    ).resolve()
    module_path = runtime_root / "src" / "security" / "secret_provider.py"
    if not module_path.exists():
        raise FileNotFoundError(f"1688 secret provider module not found: {module_path}")

    with _SECRET_PROVIDER_MODULE_LOCK:
        module = sys.modules.get(_SECRET_PROVIDER_MODULE_NAME)
        if module is None or not callable(getattr(module, "get_secret_provider", None)):
            spec = importlib.util.spec_from_file_location(
                _SECRET_PROVIDER_MODULE_NAME,
                module_path,
            )
            if spec is None or spec.loader is None:
                raise RuntimeError("Unable to load the 1688 secret provider module.")
            module = importlib.util.module_from_spec(spec)
            sys.modules[_SECRET_PROVIDER_MODULE_NAME] = module
            try:
                spec.loader.exec_module(module)
            except Exception:
                if sys.modules.get(_SECRET_PROVIDER_MODULE_NAME) is module:
                    sys.modules.pop(_SECRET_PROVIDER_MODULE_NAME, None)
                raise

    record = module.get_secret_provider().get(credential_ref)
    username = str(record.username or "").strip()
    password = str(record.secret or "")
    if not username or not password:
        raise RuntimeError(f"Database credential is incomplete: {credential_ref}")
    return username, password
