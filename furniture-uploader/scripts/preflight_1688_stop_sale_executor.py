from __future__ import annotations

import argparse
import importlib.util
import json
import os
from pathlib import Path
import platform
import re
import shutil
import sys
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
RPA_ROOT = PROJECT_ROOT / "rpa"
if str(RPA_ROOT) not in sys.path:
    sys.path.insert(0, str(RPA_ROOT))

from config_loader import load_json_with_local_override
from stop_sale_audit import (
    StopSaleAuditRepository,
    hydrate_dingtalk_credentials,
    hydrate_source_database_credentials,
    resolve_stop_sale_app_config,
)


EXECUTION_SECRET_ENV_NAMES = (
    "JST_USERNAME",
    "JST_PASSWORD",
    "DINGTALK_WEBHOOK",
    "DINGTALK_SECRET",
)
REQUIRED_PYTHON_MODULES = (
    "selenium",
    "pandas",
    "openpyxl",
    "webdriver_manager",
    "pyodbc",
)
MEMBER_ID_PATTERN = re.compile(r"^[A-Za-z0-9_.:@-]{2,160}$")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Preflight the Windows 1688 stop-sale executor.")
    parser.add_argument("--script-1688-root", default=os.getenv("SCRIPT_1688_ROOT", "D:/script_1688"))
    parser.add_argument("--config-dir", default=str(PROJECT_ROOT / "config"))
    parser.add_argument("--jushuitan-root", default=str(PROJECT_ROOT.parent / "jushuitan-sku-offline-batch"))
    parser.add_argument(
        "--existing-input",
        action="store_true",
        help="Preflight execution from an existing CSV/JSONL without requiring the legacy source database.",
    )
    return parser.parse_args()


def check_plaintext_env_file(path: Path) -> list[str]:
    if not path.exists():
        return []
    forbidden: list[str] = []
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        text = line.strip()
        if not text or text.startswith("#") or "=" not in text:
            continue
        key, value = text.split("=", 1)
        if key.strip() in {
            "STOP_SALE_SOURCE_SQLSERVER_PASSWORD",
            "STOP_SALE_SQLSERVER_PASSWORD",
            "STOP_SALE_APP_SQLSERVER_PASSWORD",
            "JST_USERNAME",
            "JST_PASSWORD",
            "DINGTALK_WEBHOOK",
            "DINGTALK_SECRET",
        } and value.strip():
            forbidden.append(key.strip())
    return forbidden


def check_jushuitan_storage_state(path: Path) -> bool:
    if not path.is_file():
        return False
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return False
    if not isinstance(payload, dict):
        return False
    cookies = payload.get("cookies")
    origins = payload.get("origins")
    return bool(isinstance(cookies, list) and cookies) or bool(
        isinstance(origins, list) and origins
    )


def check_python_runtime_dependencies(
    module_names: tuple[str, ...] = REQUIRED_PYTHON_MODULES,
) -> dict[str, bool]:
    return {name: importlib.util.find_spec(name) is not None for name in module_names}


def load_account_identity_flags(accounts_path: Path) -> dict[str, bool]:
    if not accounts_path.is_file():
        return {}
    try:
        payload = json.loads(accounts_path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return {}
    rows = payload.get("accounts") if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        return {}
    result: dict[str, bool] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        account_key = str(row.get("account_key") or "").strip()
        member_id = str(row.get("expected_member_id") or "").strip()
        if not member_id:
            shop_id = str(row.get("shop_id") or "").strip()
            if shop_id.startswith("1688-member:"):
                member_id = shop_id.removeprefix("1688-member:").strip()
        if account_key:
            result[account_key] = bool(MEMBER_ID_PATTERN.fullmatch(member_id))
    return result


def build_report(args: argparse.Namespace) -> dict[str, Any]:
    script_1688_root = Path(args.script_1688_root).resolve()
    config_dir = Path(args.config_dir).resolve()
    jushuitan_root = Path(args.jushuitan_root).resolve()
    system_config = load_json_with_local_override(config_dir / "systems" / "1688_sku_offline.json")
    store_accounts = list(system_config.get("execution", {}).get("store_accounts", []))
    external_config_root = Path(
        os.getenv(
            "YYDD_1688_CONFIG_ROOT",
            str(Path(os.getenv("PROGRAMDATA", r"C:\ProgramData")) / "YYDD" / "1688-crawler" / "config"),
        )
    ).resolve()
    accounts_path = external_config_root / "accounts.json"
    identity_flags = load_account_identity_flags(accounts_path)
    profile_checks = []
    for binding in store_accounts:
        profile_dir = Path(str(binding.get("browser_profile_dir", ""))).resolve()
        profile_checks.append(
            {
                "account_key": str(binding.get("account_key", "")),
                "store_name": str(binding.get("store_name", "")),
                "profile_exists": profile_dir.exists(),
                "default_profile_exists": (profile_dir / "Default").exists(),
                "expected_member_id_configured": identity_flags.get(
                    str(binding.get("account_key", "")).strip(),
                    False,
                ),
            }
        )

    lock_module = script_1688_root / "src" / "runtime" / "global_lock.py"
    lock_path = script_1688_root / "artifacts" / "locks" / "ali1688_full_cycle.lock"
    source_credentials = hydrate_source_database_credentials(script_1688_root)
    source_env = {
        "STOP_SALE_SOURCE_SQLSERVER_HOST": bool(
            str(os.getenv("STOP_SALE_SOURCE_SQLSERVER_HOST", "")).strip()
            or str(os.getenv("STOP_SALE_SQLSERVER_HOST", "")).strip()
        ),
        "STOP_SALE_SOURCE_SQLSERVER_USER": source_credentials[
            "STOP_SALE_SOURCE_SQLSERVER_USER"
        ],
        "STOP_SALE_SOURCE_SQLSERVER_PASSWORD": source_credentials[
            "STOP_SALE_SOURCE_SQLSERVER_PASSWORD"
        ],
    }
    dingtalk_credentials = hydrate_dingtalk_credentials(script_1688_root)
    execution_secret_env = {
        name: (
            dingtalk_credentials.get(name, False)
            if name in dingtalk_credentials
            else bool(str(os.getenv(name, "")).strip())
        )
        for name in EXECUTION_SECRET_ENV_NAMES
    }
    storage_state_value = str(os.getenv("STORAGE_STATE_PATH", "")).strip()
    storage_state_path = (
        Path(storage_state_value).expanduser()
        if storage_state_value
        else jushuitan_root / "storage" / "jushuitan.json"
    )
    if not storage_state_path.is_absolute():
        storage_state_path = jushuitan_root / storage_state_path
    storage_state_path = storage_state_path.resolve()
    jushuitan_credentials_configured = all(
        execution_secret_env[name] for name in ("JST_USERNAME", "JST_PASSWORD")
    )
    jushuitan_storage_state_available = check_jushuitan_storage_state(storage_state_path)
    jushuitan_auth_available = (
        jushuitan_credentials_configured or jushuitan_storage_state_available
    )
    dingtalk_configured = all(
        execution_secret_env[name]
        for name in ("DINGTALK_WEBHOOK", "DINGTALK_SECRET")
    )
    execution_auth_available = jushuitan_auth_available and dingtalk_configured
    source_read_available = all(source_env.values())
    source_read_ready = source_read_available or bool(args.existing_input)
    app_audit_config = None
    app_audit_contract = None
    app_audit_error = ""
    try:
        app_audit_config = resolve_stop_sale_app_config(script_1688_root)
        app_audit_contract = StopSaleAuditRepository(app_audit_config).check_contract()
    except Exception as exc:
        app_audit_error = type(exc).__name__
    plaintext_keys = check_plaintext_env_file(jushuitan_root / ".env")
    python_runtime_dependencies = check_python_runtime_dependencies()
    checks = {
        "windows": platform.system().lower() == "windows",
        "python": bool(sys.executable and Path(sys.executable).exists()),
        "python_runtime_dependencies": all(python_runtime_dependencies.values()),
        "node": shutil.which("node") is not None,
        "npm": shutil.which("npm.cmd") is not None or shutil.which("npm") is not None,
        "script_1688_root": script_1688_root.exists(),
        "shared_lock_module": lock_module.exists(),
        "jushuitan_package": (jushuitan_root / "package.json").exists(),
        "jushuitan_dependencies": (jushuitan_root / "node_modules").exists(),
        "source_read_configured": source_read_ready,
        "execution_secrets_configured": execution_auth_available,
        "app_audit_configured": app_audit_config is not None,
        "app_audit_tables_exist": bool(
            app_audit_contract and app_audit_contract.get("ready")
        ),
        "all_secret_env_set": (
            source_read_ready
            and execution_auth_available
            and app_audit_config is not None
        ),
        "all_profiles_exist": bool(profile_checks) and all(item["profile_exists"] for item in profile_checks),
        "all_account_identities_configured": bool(profile_checks)
        and all(item["expected_member_id_configured"] for item in profile_checks),
        "no_plaintext_jushuitan_secrets": not plaintext_keys,
    }
    return {
        "status": "ok" if all(checks.values()) else "failed",
        "checks": checks,
        "source_environment": source_env,
        "execution_secret_environment": execution_secret_env,
        "source_read": {
            "required": not bool(args.existing_input),
            "configured": source_read_available,
        },
        "jushuitan_auth": {
            "credentials_configured": jushuitan_credentials_configured,
            "storage_state_available": jushuitan_storage_state_available,
            "storage_state_path": str(storage_state_path),
        },
        "app_audit": {
            "configured": app_audit_config is not None,
            "target": app_audit_config.safe_dict() if app_audit_config is not None else None,
            "contract": app_audit_contract,
            "error_type": app_audit_error,
        },
        "profile_checks": profile_checks,
        "account_identity": {
            "accounts_path": str(accounts_path),
            "all_configured": bool(profile_checks)
            and all(item["expected_member_id_configured"] for item in profile_checks),
        },
        "shared_lock": {
            "path": str(lock_path),
            "currently_present": lock_path.exists(),
        },
        "plaintext_env_keys": plaintext_keys,
        "python_runtime": {
            "executable": str(Path(sys.executable).resolve()),
            "dependencies": python_runtime_dependencies,
        },
    }


def main() -> int:
    report = build_report(parse_args())
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["status"] == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
