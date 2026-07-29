from __future__ import annotations

from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_ROOT = PROJECT_ROOT / "scripts"
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

from preflight_1688_stop_sale_executor import (
    build_account_lock_checks,
    check_jushuitan_storage_state,
    check_plaintext_env_file,
    check_python_runtime_dependencies,
    load_account_identity_flags,
)


class ExecutorPreflightTests(unittest.TestCase):
    def test_account_lock_checks_are_scoped_and_reject_unsafe_keys(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            checks = build_account_lock_checks(
                root,
                [
                    {"account_key": "lechang"},
                    {"account_key": "gonglai"},
                    {"account_key": "../shared"},
                ],
            )

        self.assertTrue(checks[0]["valid"])
        self.assertTrue(checks[1]["valid"])
        self.assertNotEqual(checks[0]["path"], checks[1]["path"])
        self.assertFalse(checks[2]["valid"])
        self.assertEqual(checks[2]["path"], "")

    def test_plaintext_secret_keys_are_detected_without_returning_values(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            env_path = Path(temp_dir) / ".env"
            env_path.write_text(
                f"{'JST_USERNAME'}=operator\n{'JST_PASSWORD'}=private-value\nHEADLESS=false\n",
                encoding="utf-8",
            )

            self.assertEqual(
                check_plaintext_env_file(env_path),
                ["JST_USERNAME", "JST_PASSWORD"],
            )

    def test_storage_state_requires_a_non_empty_cookie_or_origin_list(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            state_path = Path(temp_dir) / "jushuitan.json"
            state_path.write_text('{"cookies": [], "origins": []}', encoding="utf-8")
            self.assertFalse(check_jushuitan_storage_state(state_path))

            state_path.write_text(
                '{"cookies": [{"name": "session", "value": "masked"}], "origins": []}',
                encoding="utf-8",
            )
            self.assertTrue(check_jushuitan_storage_state(state_path))

    def test_runtime_dependency_check_reports_each_missing_module(self) -> None:
        with patch(
            "preflight_1688_stop_sale_executor.importlib.util.find_spec",
            side_effect=lambda name: object() if name == "selenium" else None,
        ):
            result = check_python_runtime_dependencies(("selenium", "pyodbc"))

        self.assertEqual(result, {"selenium": True, "pyodbc": False})

    def test_account_identity_flags_require_valid_expected_member_ids(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            accounts_path = Path(temp_dir) / "accounts.json"
            accounts_path.write_text(
                '{"accounts": ['
                '{"account_key": "matched", "expected_member_id": "b2b-member_1"},'
                '{"account_key": "missing", "expected_member_id": ""},'
                '{"account_key": "invalid", "expected_member_id": "contains space"},'
                '{"account_key": "legacy", "shop_id": "1688-member:legacy-member"}'
                "]}",
                encoding="utf-8",
            )

            flags = load_account_identity_flags(accounts_path)

        self.assertEqual(
            flags,
            {"matched": True, "missing": False, "invalid": False, "legacy": True},
        )


if __name__ == "__main__":
    unittest.main()
