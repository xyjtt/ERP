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
        ("existing", "member-existing", "阿里巴巴-现有店", "ready"),
        ("eligible", "member-eligible", "阿里巴巴_新增店", "waiting_auth"),
        ("pingcan", "member-shared", "阿里巴巴-平灿家居有限公司", "waiting_auth"),
        ("pingcan_rpa", "member-shared", "阿里巴巴-常州平灿家居有限公司", "waiting_auth"),
        ("xinbaiguang_shanzhu", "member-xin", "新佰广1688", "ready"),
    ]
    accounts = {
        "config_revision": "test-1",
        "target_hostname": "EXECUTOR",
        "accounts": [
            {
                "account_key": key,
                "enabled": True,
                "shop_id": f"1688-member:{member}",
                "expected_member_id": member,
                "profile_key": key,
                "browser_profile_dir": f"C:\\profiles\\{key}",
                "shop_names": [task_name],
            }
            for key, member, task_name, _ in rows
        ],
    }
    accounts["config_hash"] = canonical_accounts_config_hash(accounts)
    roster = {
        "accounts": [
            {
                "account_key": key,
                "migration_order": index,
                "migration_group": "normal",
                "expected_shop_name": task_name,
            }
            for index, (key, _, task_name, _) in enumerate(rows, 1)
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
    for key, member, task_name, auth_status in rows:
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
                    in {"pingcan", "pingcan_rpa"}
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
        "expected_enabled_count": 5,
        "expected_final_configured_count": 2,
        "blocked_accounts": {
            "pingcan": {"reason_code": "member_identity_collision"},
            "pingcan_rpa": {"reason_code": "member_identity_collision"},
            "xinbaiguang_shanzhu": {
                "reason_code": "jushuitan_store_mapping_missing"
            },
        },
    }
    return accounts, roster, preview, config, policy


class StoreAccountRosterSyncTests(unittest.TestCase):
    def test_derives_only_unique_authoritative_binding(self) -> None:
        accounts, roster, preview, config, policy = fixture_payloads()
        result, report = derive_store_account_plan(
            accounts, roster, preview, config, policy
        )

        self.assertEqual(report["added_account_keys"], ["eligible"])
        self.assertEqual(report["configured_after_count"], 2)
        self.assertEqual(
            {item["account_key"]: item["reason_code"] for item in report["blocked_accounts"]},
            {
                "pingcan": "member_identity_collision",
                "pingcan_rpa": "member_identity_collision",
                "xinbaiguang_shanzhu": "jushuitan_store_mapping_missing",
            },
        )
        added = result["execution"]["store_accounts"][1]
        self.assertEqual(added["store_name"], "阿里巴巴-新增店")
        self.assertEqual(added["store_aliases"], ["阿里巴巴_新增店"])
        self.assertNotIn("jushuitan_store_name", added)

    def test_is_idempotent_after_binding_exists(self) -> None:
        accounts, roster, preview, config, policy = fixture_payloads()
        result, _ = derive_store_account_plan(accounts, roster, preview, config, policy)
        second, report = derive_store_account_plan(
            accounts, roster, preview, result, policy
        )
        self.assertEqual(report["status"], "up_to_date")
        self.assertEqual(report["added_count"], 0)
        self.assertEqual(second, result)

    def test_task_shop_name_mismatch_fails_closed(self) -> None:
        accounts, roster, preview, config, policy = fixture_payloads()
        preview["shops"][1]["task_shop_name"] = "WRONG"
        with self.assertRaisesRegex(StoreAccountSyncError, "task.shop_name/roster mismatch"):
            derive_store_account_plan(accounts, roster, preview, config, policy)

    def test_unapproved_blocker_change_fails_closed(self) -> None:
        accounts, roster, preview, config, policy = fixture_payloads()
        preview["shops"][-1]["jushuitan_mapping"]["status"] = "code_contract_valid"
        with self.assertRaisesRegex(StoreAccountSyncError, "blocker set changed"):
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
        self.assertEqual(len(keys), 17)
        self.assertEqual(
            keys & set(policy["blocked_accounts"]),
            set(),
        )
        self.assertEqual(
            set(policy["blocked_accounts"]),
            {"pingcan", "pingcan_rpa", "xinbaiguang_shanzhu"},
        )


if __name__ == "__main__":
    unittest.main()
