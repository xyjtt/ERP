from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import platform
import shutil
import sys
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
RPA_ROOT = PROJECT_ROOT / "rpa"
if str(RPA_ROOT) not in sys.path:
    sys.path.insert(0, str(RPA_ROOT))

from config_loader import load_json_with_local_override
from stop_sale_audit import StopSaleAuditRepository, resolve_stop_sale_app_config


SOURCE_ENV_NAMES = (
    "STOP_SALE_SOURCE_SQLSERVER_HOST",
    "STOP_SALE_SOURCE_SQLSERVER_USER",
    "STOP_SALE_SOURCE_SQLSERVER_PASSWORD",
)
SOURCE_ENV_FALLBACKS = {
    "STOP_SALE_SOURCE_SQLSERVER_HOST": "STOP_SALE_SQLSERVER_HOST",
    "STOP_SALE_SOURCE_SQLSERVER_USER": "STOP_SALE_SQLSERVER_USER",
    "STOP_SALE_SOURCE_SQLSERVER_PASSWORD": "STOP_SALE_SQLSERVER_PASSWORD",
}
EXECUTION_SECRET_ENV_NAMES = (
    "JST_USERNAME",
    "JST_PASSWORD",
    "DINGTALK_WEBHOOK",
    "DINGTALK_SECRET",
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Preflight the Windows 1688 stop-sale executor.")
    parser.add_argument("--script-1688-root", default=os.getenv("SCRIPT_1688_ROOT", "D:/script_1688"))
    parser.add_argument("--config-dir", default=str(PROJECT_ROOT / "config"))
    parser.add_argument("--jushuitan-root", default=str(PROJECT_ROOT.parent / "jushuitan-sku-offline-batch"))
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


def build_report(args: argparse.Namespace) -> dict[str, Any]:
    script_1688_root = Path(args.script_1688_root).resolve()
    config_dir = Path(args.config_dir).resolve()
    jushuitan_root = Path(args.jushuitan_root).resolve()
    system_config = load_json_with_local_override(config_dir / "systems" / "1688_sku_offline.json")
    store_accounts = list(system_config.get("execution", {}).get("store_accounts", []))
    profile_checks = []
    for binding in store_accounts:
        profile_dir = Path(str(binding.get("browser_profile_dir", ""))).resolve()
        profile_checks.append(
            {
                "account_key": str(binding.get("account_key", "")),
                "store_name": str(binding.get("store_name", "")),
                "profile_exists": profile_dir.exists(),
                "default_profile_exists": (profile_dir / "Default").exists(),
            }
        )

    lock_module = script_1688_root / "src" / "runtime" / "global_lock.py"
    lock_path = script_1688_root / "artifacts" / "locks" / "ali1688_full_cycle.lock"
    source_env = {
        name: bool(
            str(os.getenv(name, "")).strip()
            or str(os.getenv(SOURCE_ENV_FALLBACKS[name], "")).strip()
        )
        for name in SOURCE_ENV_NAMES
    }
    execution_secret_env = {
        name: bool(str(os.getenv(name, "")).strip())
        for name in EXECUTION_SECRET_ENV_NAMES
    }
    app_audit_config = None
    app_audit_contract = None
    app_audit_error = ""
    try:
        app_audit_config = resolve_stop_sale_app_config(script_1688_root)
        app_audit_contract = StopSaleAuditRepository(app_audit_config).check_contract()
    except Exception as exc:
        app_audit_error = type(exc).__name__
    plaintext_keys = check_plaintext_env_file(jushuitan_root / ".env")
    checks = {
        "windows": platform.system().lower() == "windows",
        "python": bool(sys.executable and Path(sys.executable).exists()),
        "node": shutil.which("node") is not None,
        "npm": shutil.which("npm.cmd") is not None or shutil.which("npm") is not None,
        "script_1688_root": script_1688_root.exists(),
        "shared_lock_module": lock_module.exists(),
        "jushuitan_package": (jushuitan_root / "package.json").exists(),
        "jushuitan_dependencies": (jushuitan_root / "node_modules").exists(),
        "source_read_configured": all(source_env.values()),
        "execution_secrets_configured": all(execution_secret_env.values()),
        "app_audit_configured": app_audit_config is not None,
        "app_audit_tables_exist": bool(
            app_audit_contract and app_audit_contract.get("ready")
        ),
        "all_secret_env_set": (
            all(source_env.values())
            and all(execution_secret_env.values())
            and app_audit_config is not None
        ),
        "all_profiles_exist": bool(profile_checks) and all(item["profile_exists"] for item in profile_checks),
        "no_plaintext_jushuitan_secrets": not plaintext_keys,
    }
    return {
        "status": "ok" if all(checks.values()) else "failed",
        "checks": checks,
        "source_environment": source_env,
        "execution_secret_environment": execution_secret_env,
        "app_audit": {
            "configured": app_audit_config is not None,
            "target": app_audit_config.safe_dict() if app_audit_config is not None else None,
            "contract": app_audit_contract,
            "error_type": app_audit_error,
        },
        "profile_checks": profile_checks,
        "shared_lock": {
            "path": str(lock_path),
            "currently_present": lock_path.exists(),
        },
        "plaintext_env_keys": plaintext_keys,
    }


def main() -> int:
    report = build_report(parse_args())
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["status"] == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
