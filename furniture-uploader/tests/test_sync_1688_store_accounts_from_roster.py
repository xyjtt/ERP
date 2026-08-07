from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_ROOT = PROJECT_ROOT / "scripts"
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

from sync_1688_store_accounts_from_roster import (  # noqa: E402
    StoreAccountSyncError,
    canonical_accounts_config_hash,
    derive_store_account_plan,
)


def fixture_payloads() -> tuple[dict, dict, dict, dict, dict]:
    rows = [
        ("existing", "member-existing", "阿里巴巴-现有店", "ready", True, "normal", ""),
        ("eligible", "member-eligible", "阿里巴巴_新增店", "waiting_auth", True, "normal", ""),
        (
            "pingcan",
            "b2b-2221733989984a3731",
            "阿里巴巴-常州平灿家居有限公司",
            "ready",
            True,
            "normal",
            "",
        ),
        (
            "pingcan_rpa",
            "b2b-2221733989984a3731",
            "阿里巴巴-常州平灿家居有限公司",
            "disabled",
            False,
            "disabled",
            "pingcan",
        ),
        ("xinbaiguang_shanzhu", "member-xin", "新佰广1688", "ready", True, "normal", ""),
    ]
    accounts = {
        "config_revision": "test-1",
        "target_hostname": "EXECUTOR",
        "accounts": [
            {
                "account_key": key,
                "enabled": enabled,
                "shop_id": f"1688-member:{member}",
                "expected_member_id": member,
                "profile_key": key,
                "browser_profile_dir": f"C:\\profiles\\{key}",
                "shop_names": [task_name],
            }
            for key, member, task_name, _, enabled, _, superseded_by in rows
        ],
    }
    for account, row in zip(accounts["accounts"], rows):
        if row[6]:
            account["superseded_by_account_key"] = row[6]
        if row[0] == "pingcan":
            account["shop_names"] = [
                "阿里巴巴-常州平灿家居有限公司",
                "平灿家居",
                "阿里巴巴-平灿家居有限公司",
                "平灿家居有限公司",
                "常州平灿家居有限公司",
                "平灿家居:RPA",
            ]
            account["login_name"] = "平灿家居"
    accounts["config_hash"] = canonical_accounts_config_hash(accounts)
    roster = {
        "accounts": [
            {
                "account_key": key,
                "migration_order": index,
                "migration_group": migration_group,
                "expected_shop_name": task_name,
                **(
                    {"superseded_by_account_key": superseded_by}
                    if superseded_by
                    else {}
                ),
            }
            for index, (key, _, task_name, _, _, migration_group, superseded_by) in enumerate(rows, 1)
        ]
    }
    source_names = {
        "existing": "阿里巴巴-现有店",
        "eligible": "阿里巴巴-新增店",
        "pingcan": "阿里巴巴-常州平灿家居有限公司",
        "pingcan_rpa": "阿里巴巴-常州平灿家居有限公司",
        "xinbaiguang_shanzhu": "新佰广1688",
    }
    preview = {
        "mode": "read_only_preview",
        "write_executed": False,
        "tasks_created": False,
        "browser_started": False,
        "generated_at": "2026-08-07T00:00:00+08:00",
        "executor": {
            "config_revision": accounts["config_revision"],
            "config_hash": accounts["config_hash"],
            "accounts_file_sha256": "",
        },
        "shops": [],
    }
    for key, member, task_name, auth_status, enabled, _, _ in rows:
        if not enabled:
            continue
        source = source_names[key]
        preview["shops"].append(
            {
                "account_key": key,
                "task_shop_name": task_name,
                "expected_member_id": member,
                "auth_status": auth_status,
                "profile_evidence": {
                    "browser_profile_dir": f"C:\\profiles\\{key}",
                    "profile_exists": True,
                    "profile_default_exists": True,
                    "storage_state_exists": True,
                },
                "source_mapping": {
                    "status": "mapped",
                    "source_store_names": [source],
                    "selected_source_store_name": source,
                },
                "identity_warnings": {
                    "duplicate_shop_id_or_member_id_with_other_enabled_account": key
                    in set()
                },
                "jushuitan_mapping": {
                    "status": (
                        "requires_explicit_mapping_field"
                        if key == "xinbaiguang_shanzhu"
                        else "code_contract_valid"
                    )
                },
            }
        )
    config = {
        "execution": {
            "store_accounts": [
                {
                    "store_name": "阿里巴巴-现有店",
                    "store_aliases": [],
                    "account_key": "existing",
                    "browser_type": "edge",
                    "browser_profile_dir": "C:/profiles/existing",
                }
            ]
        }
    }
    policy = {
        "expected_enabled_count": 4,
        "expected_final_configured_count": 4,
        "canonical_accounts": {
            "pingcan": {
                "expected_shop_name": "阿里巴巴-常州平灿家居有限公司",
                "source_store_name": "阿里巴巴-常州平灿家居有限公司",
                "login_name": "平灿家居",
                "expected_member_id": "b2b-2221733989984a3731",
                "superseded_account_keys": ["pingcan_rpa"],
            }
        },
        "disabled_accounts": {
            "pingcan_rpa": {
                "reason_code": "superseded_identity",
                "superseded_by_account_key": "pingcan",
                "expected_shop_name": "阿里巴巴-常州平灿家居有限公司",
                "expected_member_id": "b2b-2221733989984a3731",
                "required_state": "disabled",
            }
        },
        "jushuitan_store_mappings": {
            "xinbaiguang_shanzhu": {
                "business_store_name": "新佰广1688",
                "jushuitan_store_name": "阿里巴巴-新佰广",
                "jushuitan_row_store_name": "新佰广1688",
                "verified": True,
                "evidence_type": "live_jushuitan_store_selector_and_row_query",
                "evidence_run_id": "xinbaiguang-jst-store-discovery-20260808",
                "evidence_file": "docs/operations/1688_XINBAIGUANG_JUSHUITAN_IDENTITY_EVIDENCE_2026-08-08.json",
                "evidence_sha256": "484835ef897f83bf65959d06ac16771b005bc0b14199fd3d4e6ec931d7d6f23d",
            }
        },
        "blocked_accounts": {},
    }
    return accounts, roster, preview, config, policy


class StoreAccountRosterSyncTests(unittest.TestCase):
    def test_derives_only_unique_authoritative_binding(self) -> None:
        accounts, roster, preview, config, policy = fixture_payloads()
        result, report = derive_store_account_plan(
            accounts, roster, preview, config, policy
        )

        self.assertEqual(
            report["added_account_keys"],
            ["eligible", "pingcan", "xinbaiguang_shanzhu"],
        )
        self.assertEqual(report["configured_after_count"], 4)
        self.assertEqual(report["blocked_accounts"], [])
        self.assertEqual(
            report["disabled_accounts"],
            [
                {
                    "account_key": "pingcan_rpa",
                    "enabled": False,
                    "reason_code": "superseded_identity",
                    "superseded_by_account_key": "pingcan",
                    "expected_member_id": "b2b-2221733989984a3731",
                    "expected_shop_name": "阿里巴巴-常州平灿家居有限公司",
                }
            ],
        )
        added = result["execution"]["store_accounts"][1]
        self.assertEqual(added["store_name"], "阿里巴巴-新增店")
        self.assertEqual(added["store_aliases"], ["阿里巴巴_新增店"])
        self.assertNotIn("jushuitan_store_name", added)
        pingcan = next(
            item for item in result["execution"]["store_accounts"] if item["account_key"] == "pingcan"
        )
        self.assertEqual(pingcan["store_name"], "阿里巴巴-常州平灿家居有限公司")
        self.assertEqual(pingcan["login_name"], "平灿家居")
        self.assertEqual(pingcan["expected_member_id"], "b2b-2221733989984a3731")
        self.assertEqual(pingcan["superseded_account_keys"], ["pingcan_rpa"])
        self.assertNotIn("pingcan_rpa", {item["account_key"] for item in result["execution"]["store_accounts"]})
        xinbaiguang = next(
            item
            for item in result["execution"]["store_accounts"]
            if item["account_key"] == "xinbaiguang_shanzhu"
        )
        self.assertEqual(xinbaiguang["store_name"], "新佰广1688")
        self.assertEqual(xinbaiguang["jushuitan_store_name"], "阿里巴巴-新佰广")

    def test_is_idempotent_after_binding_exists(self) -> None:
        accounts, roster, preview, config, policy = fixture_payloads()
        result, _ = derive_store_account_plan(accounts, roster, preview, config, policy)
        second, report = derive_store_account_plan(
            accounts, roster, preview, result, policy
        )
        self.assertEqual(report["status"], "up_to_date")
        self.assertEqual(report["added_count"], 0)
        self.assertEqual(second, result)

    def test_targeted_sync_validates_only_selected_identity_and_preserves_others(self) -> None:
        accounts, roster, preview, config, policy = fixture_payloads()
        preview["shops"][0]["task_shop_name"] = "NON_TARGET_DRIFT"

        result, report = derive_store_account_plan(
            accounts,
            roster,
            preview,
            config,
            policy,
            account_keys={"pingcan", "xinbaiguang_shanzhu"},
        )

        self.assertEqual(report["sync_scope"], "targeted")
        self.assertFalse(report["full_identity_validation_executed"])
        self.assertEqual(
            report["full_sync_gate_status"],
            "not_evaluated_due_to_targeted_scope",
        )
        self.assertEqual(
            report["added_account_keys"],
            ["pingcan", "xinbaiguang_shanzhu"],
        )
        self.assertIn("existing", report["untouched_account_keys"])
        self.assertEqual(
            next(
                row for row in result["execution"]["store_accounts"]
                if row["account_key"] == "existing"
            ),
            config["execution"]["store_accounts"][0],
        )

    def test_targeted_sync_still_fails_for_selected_identity_drift(self) -> None:
        accounts, roster, preview, config, policy = fixture_payloads()
        preview["shops"][-1]["task_shop_name"] = "WRONG"
        with self.assertRaisesRegex(StoreAccountSyncError, "task.shop_name/roster mismatch"):
            derive_store_account_plan(
                accounts,
                roster,
                preview,
                config,
                policy,
                account_keys={"xinbaiguang_shanzhu"},
            )

    def test_targeted_sync_rejects_disabled_account(self) -> None:
        accounts, roster, preview, config, policy = fixture_payloads()
        with self.assertRaisesRegex(StoreAccountSyncError, "missing or disabled"):
            derive_store_account_plan(
                accounts,
                roster,
                preview,
                config,
                policy,
                account_keys={"pingcan_rpa"},
            )

    def test_task_shop_name_mismatch_fails_closed(self) -> None:
        accounts, roster, preview, config, policy = fixture_payloads()
        preview["shops"][1]["task_shop_name"] = "WRONG"
        with self.assertRaisesRegex(StoreAccountSyncError, "task.shop_name/roster mismatch"):
            derive_store_account_plan(accounts, roster, preview, config, policy)

    def test_unapproved_blocker_change_fails_closed(self) -> None:
        accounts, roster, preview, config, policy = fixture_payloads()
        policy["jushuitan_store_mappings"].pop("xinbaiguang_shanzhu")
        with self.assertRaisesRegex(StoreAccountSyncError, "blocker set changed"):
            derive_store_account_plan(accounts, roster, preview, config, policy)

    def test_jushuitan_evidence_hash_tampering_fails_closed(self) -> None:
        accounts, roster, preview, config, policy = fixture_payloads()
        policy["jushuitan_store_mappings"]["xinbaiguang_shanzhu"]["evidence_sha256"] = "0" * 64
        with self.assertRaisesRegex(StoreAccountSyncError, "evidence hash mismatch"):
            derive_store_account_plan(accounts, roster, preview, config, policy)

    def test_superseded_account_cannot_be_reenabled(self) -> None:
        accounts, roster, preview, config, policy = fixture_payloads()
        accounts["accounts"][3]["enabled"] = True
        accounts["config_hash"] = canonical_accounts_config_hash(accounts)
        with self.assertRaisesRegex(StoreAccountSyncError, "disabled account is enabled"):
            derive_store_account_plan(accounts, roster, preview, config, policy)

    def test_canonical_login_name_and_member_are_required(self) -> None:
        accounts, roster, preview, config, policy = fixture_payloads()
        accounts["accounts"][2]["shop_names"] = ["阿里巴巴-常州平灿家居有限公司"]
        accounts["config_hash"] = canonical_accounts_config_hash(accounts)
        preview["executor"]["config_hash"] = accounts["config_hash"]
        with self.assertRaisesRegex(StoreAccountSyncError, "canonical login name"):
            derive_store_account_plan(accounts, roster, preview, config, policy)

    def test_production_config_excludes_unresolved_accounts(self) -> None:
        config = json.loads(
            (PROJECT_ROOT / "config" / "systems" / "1688_sku_offline.json").read_text(
                encoding="utf-8"
            )
        )
        policy = json.loads(
            (
                PROJECT_ROOT
                / "config"
                / "systems"
                / "1688_store_account_roster_policy.json"
            ).read_text(encoding="utf-8")
        )
        keys = {
            row["account_key"] for row in config["execution"]["store_accounts"]
        }
        self.assertEqual(len(keys), 19)
        self.assertEqual(
            keys & set(policy["blocked_accounts"]),
            set(),
        )
        self.assertEqual(
            set(policy["blocked_accounts"]),
            set(),
        )
        self.assertIn("pingcan", keys)
        self.assertIn("xinbaiguang_shanzhu", keys)
        self.assertNotIn("pingcan_rpa", keys)
        xinbaiguang = next(
            item
            for item in config["execution"]["store_accounts"]
            if item["account_key"] == "xinbaiguang_shanzhu"
        )
        self.assertEqual(xinbaiguang["store_name"], "新佰广1688")
        self.assertEqual(xinbaiguang["jushuitan_store_name"], "阿里巴巴-新佰广")


if __name__ == "__main__":
    unittest.main()
