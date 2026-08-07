from __future__ import annotations

"""Derive ERP store-account bindings from authoritative, non-secret inputs.

The command is dry-run by default. It never starts a browser or creates an ERP
task. ``--apply`` only updates the selected local JSON configuration after all
identity and policy checks pass.
"""

import argparse
import copy
import hashlib
import hmac
import json
import os
import re
import tempfile
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Mapping


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = PROJECT_ROOT / "config" / "systems" / "1688_sku_offline.json"
DEFAULT_POLICY = (
    PROJECT_ROOT / "config" / "systems" / "1688_store_account_roster_policy.json"
)
ACCOUNT_KEY_PATTERN = re.compile(r"[A-Za-z0-9_.-]{1,64}")
MEMBER_ID_PATTERN = re.compile(r"[A-Za-z0-9_.:-]{3,128}")


class StoreAccountSyncError(RuntimeError):
    pass


def load_json_object(path: str | Path) -> dict[str, Any]:
    value = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict):
        raise StoreAccountSyncError(f"JSON root must be an object: {path}")
    return value


def canonical_accounts_config_hash(payload: Mapping[str, Any]) -> str:
    canonical = dict(payload)
    canonical.pop("config_hash", None)
    serialized = json.dumps(
        canonical,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(serialized).hexdigest()


def file_sha256(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def normalize_store_name(value: Any) -> str:
    text = str(value or "").strip().casefold()
    for char in (" ", "-", "_", ":", "：", "/", "\\", "(", ")", "（", "）"):
        text = text.replace(char, "")
    return text


def normalize_windows_path(value: Any) -> str:
    return os.path.normcase(os.path.normpath(str(value or "").strip().replace("/", "\\")))


def require_unique_rows(
    rows: list[Any],
    key_name: str,
    *,
    label: str,
) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for raw in rows:
        if not isinstance(raw, dict):
            raise StoreAccountSyncError(f"{label} contains a non-object row")
        key = str(raw.get(key_name) or "").strip()
        if not key:
            raise StoreAccountSyncError(f"{label} contains an empty {key_name}")
        if key in result:
            raise StoreAccountSyncError(f"{label} contains duplicate {key_name}: {key}")
        result[key] = raw
    return result


def validate_accounts_metadata(payload: Mapping[str, Any]) -> None:
    revision = str(payload.get("config_revision") or "").strip()
    declared_hash = str(payload.get("config_hash") or "").strip().lower()
    hostname = str(payload.get("target_hostname") or payload.get("hostname") or "").strip()
    if not revision or not hostname or not re.fullmatch(r"[0-9a-f]{64}", declared_hash):
        raise StoreAccountSyncError("authoritative accounts metadata is incomplete")
    calculated_hash = canonical_accounts_config_hash(payload)
    if not hmac.compare_digest(declared_hash, calculated_hash):
        raise StoreAccountSyncError("authoritative accounts config_hash mismatch")


def member_id(account: Mapping[str, Any]) -> str:
    expected = str(account.get("expected_member_id") or "").strip()
    if not expected:
        shop_id = str(account.get("shop_id") or "").strip()
        if shop_id.startswith("1688-member:"):
            expected = shop_id.removeprefix("1688-member:").strip()
    return expected


def build_binding(
    account_key: str,
    account: Mapping[str, Any],
    task_shop_name: str,
    source_store_name: str,
) -> dict[str, Any]:
    aliases: list[str] = []
    for raw in [*(account.get("shop_names") or []), task_shop_name]:
        alias = str(raw or "").strip()
        if not alias or alias == source_store_name or alias in aliases:
            continue
        aliases.append(alias)
    return {
        "store_name": source_store_name,
        "store_aliases": aliases,
        "account_key": account_key,
        "browser_type": "edge",
        "browser_profile_dir": str(account.get("browser_profile_dir") or "")
        .strip()
        .replace("\\", "/"),
    }


def derive_store_account_plan(
    accounts_payload: Mapping[str, Any],
    roster_payload: Mapping[str, Any],
    preview_payload: Mapping[str, Any],
    system_config: Mapping[str, Any],
    policy: Mapping[str, Any],
    *,
    accounts_file_sha256: str = "",
) -> tuple[dict[str, Any], dict[str, Any]]:
    validate_accounts_metadata(accounts_payload)
    accounts = require_unique_rows(
        list(accounts_payload.get("accounts") or []),
        "account_key",
        label="authoritative accounts",
    )
    enabled_accounts = {
        key: row for key, row in accounts.items() if row.get("enabled") is True
    }
    roster = require_unique_rows(
        list(roster_payload.get("accounts") or []),
        "account_key",
        label="task roster",
    )
    preview = require_unique_rows(
        list(preview_payload.get("shops") or []),
        "account_key",
        label="read-only preview",
    )

    expected_enabled = int(policy.get("expected_enabled_count") or 0)
    if len(enabled_accounts) != expected_enabled:
        raise StoreAccountSyncError(
            f"enabled account count mismatch: expected={expected_enabled} actual={len(enabled_accounts)}"
        )
    enabled_keys = set(enabled_accounts)
    normal_roster_keys = {
        key
        for key, row in roster.items()
        if str(row.get("migration_group") or "normal").strip() != "disabled"
    }
    if normal_roster_keys != enabled_keys:
        raise StoreAccountSyncError(
            "enabled accounts and non-disabled task roster differ: "
            f"accounts_only={sorted(enabled_keys - normal_roster_keys)} "
            f"roster_only={sorted(normal_roster_keys - enabled_keys)}"
        )
    if set(preview) != enabled_keys:
        raise StoreAccountSyncError(
            "preview and enabled accounts differ: "
            f"accounts_only={sorted(enabled_keys - set(preview))} "
            f"preview_only={sorted(set(preview) - enabled_keys)}"
        )
    if str(preview_payload.get("mode") or "") != "read_only_preview":
        raise StoreAccountSyncError("preview mode is not read_only_preview")
    if any(
        bool(preview_payload.get(key))
        for key in ("write_executed", "tasks_created", "browser_started")
    ):
        raise StoreAccountSyncError("preview contains a write/task/browser execution flag")

    executor = preview_payload.get("executor") or {}
    if str(executor.get("config_revision") or "") != str(
        accounts_payload.get("config_revision") or ""
    ):
        raise StoreAccountSyncError("preview config_revision differs from accounts roster")
    if str(executor.get("config_hash") or "").lower() != str(
        accounts_payload.get("config_hash") or ""
    ).lower():
        raise StoreAccountSyncError("preview config_hash differs from accounts roster")
    expected_file_hash = str(executor.get("accounts_file_sha256") or "").lower()
    if accounts_file_sha256 and expected_file_hash != accounts_file_sha256.lower():
        raise StoreAccountSyncError("preview accounts_file_sha256 differs from supplied roster")

    member_owners: dict[str, list[str]] = defaultdict(list)
    profile_owners: dict[str, list[str]] = defaultdict(list)
    source_owners: dict[str, list[str]] = defaultdict(list)
    validated: dict[str, dict[str, Any]] = {}
    for key in sorted(enabled_keys):
        account = enabled_accounts[key]
        roster_row = roster[key]
        preview_row = preview[key]
        if not ACCOUNT_KEY_PATTERN.fullmatch(key):
            raise StoreAccountSyncError(f"invalid account_key: {key}")
        expected_member = member_id(account)
        if not MEMBER_ID_PATTERN.fullmatch(expected_member):
            raise StoreAccountSyncError(f"invalid expected_member_id: {key}")
        if str(account.get("shop_id") or "").strip() != f"1688-member:{expected_member}":
            raise StoreAccountSyncError(f"shop_id/member mismatch: {key}")
        if str(preview_row.get("expected_member_id") or "").strip() != expected_member:
            raise StoreAccountSyncError(f"preview/member mismatch: {key}")

        task_shop_name = str(roster_row.get("expected_shop_name") or "").strip()
        if not task_shop_name or str(preview_row.get("task_shop_name") or "").strip() != task_shop_name:
            raise StoreAccountSyncError(f"task.shop_name/roster mismatch: {key}")
        account_shop_names = [
            str(item or "").strip() for item in account.get("shop_names") or []
        ]
        if task_shop_name not in account_shop_names:
            raise StoreAccountSyncError(f"task.shop_name missing from authoritative aliases: {key}")

        profile_key = str(account.get("profile_key") or "").strip()
        profile_dir = str(account.get("browser_profile_dir") or "").strip()
        profile_evidence = preview_row.get("profile_evidence") or {}
        if profile_key != key or not profile_dir:
            raise StoreAccountSyncError(f"profile identity is incomplete: {key}")
        if normalize_windows_path(profile_evidence.get("browser_profile_dir")) != normalize_windows_path(
            profile_dir
        ):
            raise StoreAccountSyncError(f"preview/profile mismatch: {key}")
        if not all(
            bool(profile_evidence.get(field))
            for field in ("profile_exists", "profile_default_exists", "storage_state_exists")
        ):
            raise StoreAccountSyncError(f"profile evidence is incomplete: {key}")

        source_mapping = preview_row.get("source_mapping") or {}
        source_store_name = str(source_mapping.get("selected_source_store_name") or "").strip()
        source_names = [
            str(item or "").strip() for item in source_mapping.get("source_store_names") or []
        ]
        if (
            source_mapping.get("status") != "mapped"
            or not source_store_name
            or source_names != [source_store_name]
        ):
            raise StoreAccountSyncError(f"source store mapping is not unique: {key}")

        member_owners[expected_member].append(key)
        profile_owners[normalize_windows_path(profile_dir)].append(key)
        source_owners[normalize_store_name(source_store_name)].append(key)
        validated[key] = {
            "account": account,
            "task_shop_name": task_shop_name,
            "source_store_name": source_store_name,
            "expected_member_id": expected_member,
            "preview": preview_row,
        }

    blockers: dict[str, dict[str, Any]] = {}
    for key, item in validated.items():
        member_peers = sorted(member_owners[item["expected_member_id"]])
        profile_peers = sorted(
            profile_owners[normalize_windows_path(item["account"].get("browser_profile_dir"))]
        )
        source_peers = sorted(source_owners[normalize_store_name(item["source_store_name"])])
        jushuitan_status = str(
            (item["preview"].get("jushuitan_mapping") or {}).get("status") or ""
        )
        if len(member_peers) > 1:
            blockers[key] = {
                "reason_code": "member_identity_collision",
                "expected_member_id": item["expected_member_id"],
                "conflicting_account_keys": member_peers,
                "source_store_name": item["source_store_name"],
            }
        elif len(profile_peers) > 1:
            blockers[key] = {
                "reason_code": "profile_identity_collision",
                "conflicting_account_keys": profile_peers,
            }
        elif len(source_peers) > 1 or bool(
            (item["preview"].get("identity_warnings") or {}).get(
                "duplicate_shop_id_or_member_id_with_other_enabled_account"
            )
        ):
            blockers[key] = {
                "reason_code": "source_identity_collision",
                "conflicting_account_keys": source_peers,
                "source_store_name": item["source_store_name"],
            }
        elif jushuitan_status != "code_contract_valid":
            blockers[key] = {
                "reason_code": "jushuitan_store_mapping_missing",
                "source_store_name": item["source_store_name"],
                "required_field": "jushuitan_store_name",
                "observed_contract_status": jushuitan_status,
            }

    expected_blockers = {
        str(key): str((value or {}).get("reason_code") or "")
        for key, value in (policy.get("blocked_accounts") or {}).items()
    }
    actual_blockers = {key: value["reason_code"] for key, value in blockers.items()}
    if actual_blockers != expected_blockers:
        raise StoreAccountSyncError(
            f"fail-closed blocker set changed: expected={expected_blockers} actual={actual_blockers}"
        )
    for key, item in blockers.items():
        item["required_resolution"] = str(
            ((policy.get("blocked_accounts") or {}).get(key) or {}).get(
                "required_resolution"
            )
            or ""
        )

    eligible_keys = enabled_keys - set(blockers)
    expected_final = int(policy.get("expected_final_configured_count") or 0)
    if len(eligible_keys) != expected_final:
        raise StoreAccountSyncError(
            f"eligible account count mismatch: expected={expected_final} actual={len(eligible_keys)}"
        )

    result_config = copy.deepcopy(dict(system_config))
    execution = result_config.setdefault("execution", {})
    raw_bindings = execution.get("store_accounts") or []
    bindings = [copy.deepcopy(item) for item in raw_bindings if isinstance(item, dict)]
    configured = require_unique_rows(bindings, "account_key", label="ERP store_accounts")
    unknown_configured = set(configured) - eligible_keys
    if unknown_configured:
        raise StoreAccountSyncError(
            f"ERP config contains blocked or unknown accounts: {sorted(unknown_configured)}"
        )

    for key, binding in configured.items():
        expected = validated[key]
        if str(binding.get("store_name") or "").strip() != expected["source_store_name"]:
            raise StoreAccountSyncError(f"configured source store differs from preview: {key}")
        if normalize_windows_path(binding.get("browser_profile_dir")) != normalize_windows_path(
            expected["account"].get("browser_profile_dir")
        ):
            raise StoreAccountSyncError(f"configured browser profile differs from roster: {key}")

    roster_order = {
        key: int(row.get("migration_order") or 10**9) for key, row in roster.items()
    }
    added_keys: list[str] = []
    for key in sorted(eligible_keys - set(configured), key=lambda item: (roster_order[item], item)):
        item = validated[key]
        bindings.append(
            build_binding(
                key,
                item["account"],
                item["task_shop_name"],
                item["source_store_name"],
            )
        )
        added_keys.append(key)

    final_bindings = require_unique_rows(bindings, "account_key", label="final store_accounts")
    if set(final_bindings) != eligible_keys:
        raise StoreAccountSyncError(
            "final store_accounts do not exactly match eligible accounts: "
            f"missing={sorted(eligible_keys - set(final_bindings))} "
            f"extra={sorted(set(final_bindings) - eligible_keys)}"
        )
    execution["store_accounts"] = bindings

    auth_counts = Counter(
        str(validated[key]["preview"].get("auth_status") or "unknown")
        for key in eligible_keys
    )
    report = {
        "schema_version": 1,
        "status": "ready_to_apply" if added_keys else "up_to_date",
        "write_executed": False,
        "enabled_count": len(enabled_keys),
        "configured_before_count": len(configured),
        "configured_after_count": len(final_bindings),
        "added_count": len(added_keys),
        "added_account_keys": added_keys,
        "configured_account_keys": sorted(final_bindings),
        "blocked_count": len(blockers),
        "blocked_accounts": [
            {"account_key": key, **blockers[key]} for key in sorted(blockers)
        ],
        "configured_auth_status_counts": dict(sorted(auth_counts.items())),
        "accounts_config_revision": str(accounts_payload.get("config_revision") or ""),
        "accounts_config_hash": str(accounts_payload.get("config_hash") or ""),
        "preview_generated_at": str(preview_payload.get("generated_at") or ""),
        "identity_rules": {
            "task_shop_name_from_roster": True,
            "source_store_name_from_read_only_preview": True,
            "expected_member_and_profile_from_accounts": True,
            "jushuitan_store_name_guessed": False,
        },
    }
    return result_config, report


def write_json_atomic(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    content = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    descriptor, temp_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent)
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
    except BaseException:
        try:
            os.unlink(temp_name)
        except FileNotFoundError:
            pass
        raise


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Derive fail-closed ERP 1688 store bindings from authoritative inputs."
    )
    parser.add_argument("--accounts-config", required=True)
    parser.add_argument("--task-roster", required=True)
    parser.add_argument("--preview", required=True)
    parser.add_argument("--config", default=str(DEFAULT_CONFIG))
    parser.add_argument("--policy", default=str(DEFAULT_POLICY))
    parser.add_argument("--report")
    parser.add_argument("--apply", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    accounts_path = Path(args.accounts_config).resolve()
    roster_path = Path(args.task_roster).resolve()
    preview_path = Path(args.preview).resolve()
    config_path = Path(args.config).resolve()
    policy_path = Path(args.policy).resolve()
    result_config, report = derive_store_account_plan(
        load_json_object(accounts_path),
        load_json_object(roster_path),
        load_json_object(preview_path),
        load_json_object(config_path),
        load_json_object(policy_path),
        accounts_file_sha256=file_sha256(accounts_path),
    )
    report["input_sha256"] = {
        "accounts_config": file_sha256(accounts_path),
        "task_roster": file_sha256(roster_path),
        "preview": file_sha256(preview_path),
        "config_before": file_sha256(config_path),
        "policy": file_sha256(policy_path),
    }
    if args.apply:
        changed = report["added_count"] > 0
        if changed:
            write_json_atomic(config_path, result_config)
        report["write_executed"] = changed
        report["status"] = "applied" if changed else "up_to_date"
        report["config_after_sha256"] = file_sha256(config_path)
    if args.report:
        write_json_atomic(Path(args.report).resolve(), report)
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
