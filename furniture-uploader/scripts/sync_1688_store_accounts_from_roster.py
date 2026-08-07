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
    identity_policy: Mapping[str, Any] | None = None,
    jushuitan_store_name: str = "",
) -> dict[str, Any]:
    identity_policy = identity_policy or {}
    aliases: list[str] = []
    for raw in [*(account.get("shop_names") or []), task_shop_name]:
        alias = str(raw or "").strip()
        if not alias or alias == source_store_name or alias in aliases:
            continue
        aliases.append(alias)
    binding = {
        "store_name": source_store_name,
        "store_aliases": aliases,
        "account_key": account_key,
        "browser_type": "edge",
        "browser_profile_dir": str(account.get("browser_profile_dir") or "")
        .strip()
        .replace("\\", "/"),
    }
    expected_member = member_id(account)
    if expected_member:
        binding["expected_member_id"] = expected_member
    login_name = str(
        identity_policy.get("login_name") or account.get("login_name") or ""
    ).strip()
    if login_name:
        binding["login_name"] = login_name
    superseded = [
        str(item).strip()
        for item in identity_policy.get("superseded_account_keys", []) or []
        if str(item).strip()
    ]
    if superseded:
        binding["superseded_account_keys"] = superseded
    exact_jushuitan_name = str(jushuitan_store_name or "").strip()
    if exact_jushuitan_name and exact_jushuitan_name != source_store_name:
        binding["jushuitan_store_name"] = exact_jushuitan_name
    return binding


def validate_disabled_account_policy(
    accounts: Mapping[str, Mapping[str, Any]],
    roster: Mapping[str, Mapping[str, Any]],
    enabled_keys: set[str],
    policy: Mapping[str, Any],
) -> dict[str, dict[str, Any]]:
    """Validate explicit superseded accounts without scheduling them.

    Disabled technical accounts are intentionally outside the enabled/preview
    set. Keeping their state in the policy makes an accidental re-enable or
    duplicate ERP binding fail closed instead of silently creating a second
    browser owner.
    """
    disabled_policy = policy.get("disabled_accounts") or {}
    if not isinstance(disabled_policy, Mapping):
        raise StoreAccountSyncError("disabled_accounts policy must be an object")

    validated: dict[str, dict[str, Any]] = {}
    for key, raw_rule in disabled_policy.items():
        rule = raw_rule if isinstance(raw_rule, Mapping) else {}
        account = accounts.get(str(key))
        roster_row = roster.get(str(key))
        if account is None or roster_row is None:
            raise StoreAccountSyncError(f"disabled account is missing from authoritative inputs: {key}")
        if str(key) in enabled_keys or account.get("enabled") is not False:
            raise StoreAccountSyncError(f"disabled account is enabled: {key}")
        if str(roster_row.get("migration_group") or "").strip() != "disabled":
            raise StoreAccountSyncError(f"disabled account roster group is not disabled: {key}")

        successor = str(rule.get("superseded_by_account_key") or "").strip()
        if not successor or successor not in enabled_keys:
            raise StoreAccountSyncError(f"disabled account successor is not enabled: {key}")
        roster_successor = str(roster_row.get("superseded_by_account_key") or "").strip()
        account_successor = str(account.get("superseded_by_account_key") or "").strip()
        if roster_successor != successor or account_successor != successor:
            raise StoreAccountSyncError(f"disabled account successor mismatch: {key}")

        expected_member = str(rule.get("expected_member_id") or "").strip()
        if expected_member and member_id(account) != expected_member:
            raise StoreAccountSyncError(f"disabled account member mismatch: {key}")
        expected_shop = str(rule.get("expected_shop_name") or "").strip()
        if expected_shop and str(roster_row.get("expected_shop_name") or "").strip() != expected_shop:
            raise StoreAccountSyncError(f"disabled account shop mismatch: {key}")
        validated[str(key)] = {
            "account_key": str(key),
            "enabled": False,
            "reason_code": str(rule.get("reason_code") or "superseded_identity"),
            "superseded_by_account_key": successor,
            "expected_member_id": expected_member or member_id(account),
            "expected_shop_name": expected_shop
            or str(roster_row.get("expected_shop_name") or "").strip(),
        }
    return validated


def resolve_verified_jushuitan_store_name(
    account_key: str,
    *,
    task_shop_name: str,
    source_store_name: str,
    preview_row: Mapping[str, Any],
    mapping_policy: Mapping[str, Any],
) -> str:
    rule = mapping_policy.get(account_key) or {}
    if rule:
        if not isinstance(rule, Mapping):
            raise StoreAccountSyncError(f"Jushuitan mapping policy is invalid: {account_key}")
        if rule.get("verified") is not True:
            raise StoreAccountSyncError(f"Jushuitan mapping is not verified: {account_key}")
        if str(rule.get("evidence_type") or "") != "live_jushuitan_store_selector_and_row_query":
            raise StoreAccountSyncError(f"Jushuitan mapping evidence type is invalid: {account_key}")
        if str(rule.get("business_store_name") or "").strip() != task_shop_name:
            raise StoreAccountSyncError(f"Jushuitan business store differs from task shop: {account_key}")
        if str(rule.get("jushuitan_row_store_name") or "").strip() != task_shop_name:
            raise StoreAccountSyncError(f"Jushuitan row store differs from task shop: {account_key}")
        exact_name = str(rule.get("jushuitan_store_name") or "").strip()
        if not exact_name.startswith("阿里巴巴-"):
            raise StoreAccountSyncError(f"Jushuitan exact store name is invalid: {account_key}")
        evidence_run_id = str(rule.get("evidence_run_id") or "").strip()
        declared_hash = str(rule.get("evidence_sha256") or "").strip().lower()
        evidence_file = str(rule.get("evidence_file") or "").strip()
        if not evidence_run_id or not re.fullmatch(r"[0-9a-f]{64}", declared_hash):
            raise StoreAccountSyncError(f"Jushuitan mapping evidence metadata is incomplete: {account_key}")
        evidence_path = (PROJECT_ROOT / evidence_file).resolve()
        try:
            evidence_path.relative_to(PROJECT_ROOT.resolve())
        except ValueError as exc:
            raise StoreAccountSyncError(
                f"Jushuitan mapping evidence path escapes project root: {account_key}"
            ) from exc
        if not evidence_path.is_file() or not hmac.compare_digest(
            file_sha256(evidence_path), declared_hash
        ):
            raise StoreAccountSyncError(f"Jushuitan mapping evidence hash mismatch: {account_key}")
        evidence = load_json_object(evidence_path)
        expected_evidence = {
            "status": "accepted_mapping_evidence",
            "account_key": account_key,
            "business_store_name": task_shop_name,
            "jushuitan_store_name": exact_name,
            "jushuitan_row_store_name": task_shop_name,
        }
        for field, expected in expected_evidence.items():
            if evidence.get(field) != expected:
                raise StoreAccountSyncError(
                    f"Jushuitan mapping evidence field mismatch: {account_key}/{field}"
                )
        if str((evidence.get("probe") or {}).get("run_id") or "") != evidence_run_id:
            raise StoreAccountSyncError(f"Jushuitan mapping evidence run mismatch: {account_key}")
        if not all(
            bool((evidence.get("identity_contract") or {}).get(field))
            for field in (
                "task_store_name_unchanged",
                "saga_and_operation_key_use_business_store_name",
                "jushuitan_selector_uses_jushuitan_store_name",
                "jushuitan_row_matching_uses_business_store_name",
            )
        ):
            raise StoreAccountSyncError(f"Jushuitan mapping evidence contract is incomplete: {account_key}")
        return exact_name

    jushuitan_status = str(
        (preview_row.get("jushuitan_mapping") or {}).get("status") or ""
    ).strip()
    if jushuitan_status == "code_contract_valid" and source_store_name.startswith("阿里巴巴-"):
        return source_store_name
    return ""


def derive_store_account_plan(
    accounts_payload: Mapping[str, Any],
    roster_payload: Mapping[str, Any],
    preview_payload: Mapping[str, Any],
    system_config: Mapping[str, Any],
    policy: Mapping[str, Any],
    *,
    accounts_file_sha256: str = "",
    account_keys: set[str] | None = None,
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
    enabled_keys = set(enabled_accounts)
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

    disabled_accounts = validate_disabled_account_policy(
        accounts,
        roster,
        enabled_keys,
        policy,
    )
    canonical_policy = policy.get("canonical_accounts") or {}
    if not isinstance(canonical_policy, Mapping):
        raise StoreAccountSyncError("canonical_accounts policy must be an object")
    jushuitan_mapping_policy = policy.get("jushuitan_store_mappings") or {}
    if not isinstance(jushuitan_mapping_policy, Mapping):
        raise StoreAccountSyncError("jushuitan_store_mappings policy must be an object")

    expected_enabled = int(policy.get("expected_enabled_count") or 0)
    if len(enabled_accounts) != expected_enabled:
        raise StoreAccountSyncError(
            f"enabled account count mismatch: expected={expected_enabled} actual={len(enabled_accounts)}"
        )
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

    requested_keys = {
        str(key or "").strip() for key in (account_keys or set()) if str(key or "").strip()
    }
    targeted_sync = account_keys is not None
    if targeted_sync and not requested_keys:
        raise StoreAccountSyncError("targeted sync requires at least one account_key")
    unavailable_keys = requested_keys - enabled_keys
    if unavailable_keys:
        raise StoreAccountSyncError(
            f"target accounts are missing or disabled: {sorted(unavailable_keys)}"
        )
    validation_keys = requested_keys if targeted_sync else enabled_keys

    member_owners: dict[str, list[str]] = defaultdict(list)
    profile_owners: dict[str, list[str]] = defaultdict(list)
    source_owners: dict[str, list[str]] = defaultdict(list)
    for key in sorted(enabled_keys):
        account = enabled_accounts[key]
        preview_row = preview[key]
        source_mapping = preview_row.get("source_mapping") or {}
        member_owners[member_id(account)].append(key)
        profile_owners[
            normalize_windows_path(account.get("browser_profile_dir"))
        ].append(key)
        source_owners[
            normalize_store_name(source_mapping.get("selected_source_store_name"))
        ].append(key)

    validated: dict[str, dict[str, Any]] = {}
    for key in sorted(validation_keys):
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

        identity_rule = canonical_policy.get(key) or {}
        if not isinstance(identity_rule, Mapping):
            raise StoreAccountSyncError(f"canonical identity policy is invalid: {key}")
        rule_member = str(identity_rule.get("expected_member_id") or "").strip()
        if rule_member and rule_member != expected_member:
            raise StoreAccountSyncError(f"canonical member policy mismatch: {key}")
        rule_shop = str(identity_rule.get("expected_shop_name") or "").strip()
        if rule_shop and rule_shop != task_shop_name:
            raise StoreAccountSyncError(f"canonical task shop policy mismatch: {key}")
        rule_source = str(identity_rule.get("source_store_name") or "").strip()
        if rule_source and rule_source != source_store_name:
            raise StoreAccountSyncError(f"canonical source shop policy mismatch: {key}")
        login_name = str(identity_rule.get("login_name") or "").strip()
        if login_name and login_name not in account_shop_names:
            raise StoreAccountSyncError(f"canonical login name missing from authoritative aliases: {key}")

        jushuitan_store_name = resolve_verified_jushuitan_store_name(
            key,
            task_shop_name=task_shop_name,
            source_store_name=source_store_name,
            preview_row=preview_row,
            mapping_policy=jushuitan_mapping_policy,
        )

        validated[key] = {
            "account": account,
            "task_shop_name": task_shop_name,
            "source_store_name": source_store_name,
            "expected_member_id": expected_member,
            "jushuitan_store_name": jushuitan_store_name,
            "preview": preview_row,
        }

    blockers: dict[str, dict[str, Any]] = {}
    for key, item in validated.items():
        member_peers = sorted(member_owners[item["expected_member_id"]])
        profile_peers = sorted(
            profile_owners[normalize_windows_path(item["account"].get("browser_profile_dir"))]
        )
        source_peers = sorted(source_owners[normalize_store_name(item["source_store_name"])])
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
        elif not item["jushuitan_store_name"]:
            blockers[key] = {
                "reason_code": "jushuitan_store_mapping_missing",
                "source_store_name": item["source_store_name"],
                "required_field": "jushuitan_store_name",
                "observed_contract_status": str(
                    (item["preview"].get("jushuitan_mapping") or {}).get("status") or ""
                ),
            }

    policy_blockers = {
        str(key): str((value or {}).get("reason_code") or "")
        for key, value in (policy.get("blocked_accounts") or {}).items()
    }
    expected_blockers = (
        {
            key: policy_blockers[key]
            for key in sorted(validation_keys)
            if key in policy_blockers
        }
        if targeted_sync
        else policy_blockers
    )
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

    eligible_keys = validation_keys - set(blockers)
    expected_final = int(policy.get("expected_final_configured_count") or 0)
    if not targeted_sync and len(eligible_keys) != expected_final:
        raise StoreAccountSyncError(
            f"eligible account count mismatch: expected={expected_final} actual={len(eligible_keys)}"
        )

    result_config = copy.deepcopy(dict(system_config))
    execution = result_config.setdefault("execution", {})
    raw_bindings = execution.get("store_accounts") or []
    bindings = [copy.deepcopy(item) for item in raw_bindings if isinstance(item, dict)]
    configured = require_unique_rows(bindings, "account_key", label="ERP store_accounts")
    globally_allowed_keys = enabled_keys - set(policy_blockers)
    unknown_configured = set(configured) - globally_allowed_keys
    if unknown_configured:
        raise StoreAccountSyncError(
            f"ERP config contains blocked or unknown accounts: {sorted(unknown_configured)}"
        )

    configured_validation_keys = set(configured) & validation_keys
    for key in sorted(configured_validation_keys):
        binding = configured[key]
        expected = validated[key]
        if str(binding.get("store_name") or "").strip() != expected["source_store_name"]:
            raise StoreAccountSyncError(f"configured source store differs from preview: {key}")
        if normalize_windows_path(binding.get("browser_profile_dir")) != normalize_windows_path(
            expected["account"].get("browser_profile_dir")
        ):
            raise StoreAccountSyncError(f"configured browser profile differs from roster: {key}")
        configured_jushuitan_name = str(
            binding.get("jushuitan_store_name") or binding.get("store_name") or ""
        ).strip()
        if configured_jushuitan_name != expected["jushuitan_store_name"]:
            raise StoreAccountSyncError(f"configured Jushuitan store differs from evidence: {key}")
        identity_rule = canonical_policy.get(key) or {}
        if identity_rule:
            expected_member = str(identity_rule.get("expected_member_id") or "").strip()
            if str(binding.get("expected_member_id") or "").strip() != expected_member:
                raise StoreAccountSyncError(f"configured canonical member differs from roster: {key}")
            expected_login = str(identity_rule.get("login_name") or "").strip()
            if str(binding.get("login_name") or "").strip() != expected_login:
                raise StoreAccountSyncError(f"configured canonical login differs from roster: {key}")
            expected_successors = [
                str(item).strip()
                for item in identity_rule.get("superseded_account_keys", []) or []
                if str(item).strip()
            ]
            configured_successors = [
                str(item).strip()
                for item in binding.get("superseded_account_keys", []) or []
                if str(item).strip()
            ]
            if configured_successors != expected_successors:
                raise StoreAccountSyncError(f"configured canonical successor set differs from policy: {key}")

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
                canonical_policy.get(key),
                item["jushuitan_store_name"],
            )
        )
        added_keys.append(key)

    final_bindings = require_unique_rows(bindings, "account_key", label="final store_accounts")
    if not targeted_sync and set(final_bindings) != eligible_keys:
        raise StoreAccountSyncError(
            "final store_accounts do not exactly match eligible accounts: "
            f"missing={sorted(eligible_keys - set(final_bindings))} "
            f"extra={sorted(set(final_bindings) - eligible_keys)}"
        )
    if targeted_sync and not eligible_keys.issubset(final_bindings):
        raise StoreAccountSyncError(
            "target store_accounts were not fully configured: "
            f"missing={sorted(eligible_keys - set(final_bindings))}"
        )
    execution["store_accounts"] = bindings

    auth_counts = Counter(
        str(preview[key].get("auth_status") or "unknown")
        for key in final_bindings
        if key in preview
    )
    report = {
        "schema_version": 1,
        "sync_scope": "targeted" if targeted_sync else "full",
        "selected_account_keys": sorted(validation_keys),
        "full_identity_validation_executed": not targeted_sync,
        "full_sync_gate_status": (
            "not_evaluated_due_to_targeted_scope" if targeted_sync else "passed"
        ),
        "status": "ready_to_apply" if added_keys else "up_to_date",
        "write_executed": False,
        "enabled_count": len(enabled_keys),
        "configured_before_count": len(configured),
        "configured_after_count": len(final_bindings),
        "added_count": len(added_keys),
        "added_account_keys": added_keys,
        "configured_account_keys": sorted(final_bindings),
        "untouched_account_keys": sorted(set(final_bindings) - validation_keys),
        "expected_final_configured_count": expected_final,
        "configured_count_matches_policy": len(final_bindings) == expected_final,
        "blocked_count": len(blockers),
        "blocked_accounts": [
            {"account_key": key, **blockers[key]} for key in sorted(blockers)
        ],
        "disabled_accounts": [disabled_accounts[key] for key in sorted(disabled_accounts)],
        "canonical_accounts": {
            str(key): {
                "account_key": str(key),
                "expected_member_id": str((canonical_policy.get(key) or {}).get("expected_member_id") or ""),
                "login_name": str((canonical_policy.get(key) or {}).get("login_name") or ""),
                "superseded_account_keys": [
                    str(item).strip()
                    for item in (canonical_policy.get(key) or {}).get("superseded_account_keys", []) or []
                    if str(item).strip()
                ],
            }
            for key in sorted(canonical_policy)
            if key in enabled_keys
        },
        "jushuitan_store_mappings": {
            key: {
                "business_store_name": validated[key]["task_shop_name"],
                "jushuitan_store_name": validated[key]["jushuitan_store_name"],
                "mapping_source": (
                    "verified_policy"
                    if key in jushuitan_mapping_policy
                    else "source_store_name"
                ),
            }
            for key in sorted(eligible_keys)
        },
        "configured_auth_status_counts": dict(sorted(auth_counts.items())),
        "accounts_config_revision": str(accounts_payload.get("config_revision") or ""),
        "accounts_config_hash": str(accounts_payload.get("config_hash") or ""),
        "preview_generated_at": str(preview_payload.get("generated_at") or ""),
        "identity_rules": {
            "task_shop_name_from_roster": True,
            "source_store_name_from_read_only_preview": True,
            "expected_member_and_profile_from_accounts": True,
            "canonical_login_name_from_policy_and_accounts": True,
            "disabled_superseded_accounts_never_scheduled": True,
            "business_and_jushuitan_store_names_are_distinct": True,
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
    parser.add_argument(
        "--account-key",
        action="append",
        default=[],
        help=(
            "Validate and synchronize only this enabled account. May be repeated; "
            "global metadata/set gates remain enforced and non-target bindings are preserved."
        ),
    )
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
        account_keys=set(args.account_key) if args.account_key else None,
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
