from __future__ import annotations

import hashlib
import json
import os
import pyodbc
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
from types import SimpleNamespace


PROJECT_ROOT = Path(__file__).resolve().parents[1]
RPA_ROOT = PROJECT_ROOT / "rpa"
if str(RPA_ROOT) not in sys.path:
    sys.path.insert(0, str(RPA_ROOT))

from stop_sale_audit import (
    StopSaleAuditRepository,
    hydrate_dingtalk_credentials,
    load_jsonl_records,
    resolve_stop_sale_app_config,
    stop_sale_offline_key,
    stop_sale_task_key,
)


class StopSaleAuditTests(unittest.TestCase):
    def test_task_key_matches_jushuitan_four_field_identity(self) -> None:
        values = ("阿里巴巴-常州工莱家具", "1001", " SKU-A ", "CODE-A")
        expected = hashlib.sha256(
            "阿里巴巴-常州工莱家具|1001|sku-a|code-a".encode("utf-8")
        ).hexdigest()

        self.assertEqual(stop_sale_task_key(*values), expected)
        self.assertNotEqual(stop_sale_offline_key(*values[:3]), expected)

    def test_environment_app_config_safe_dict_excludes_credentials(self) -> None:
        with patch.dict(
            os.environ,
            {
                "STOP_SALE_APP_SQLSERVER_USER": "app_writer",
                "STOP_SALE_APP_SQLSERVER_PASSWORD": "secret-value",
            },
            clear=True,
        ):
            config = resolve_stop_sale_app_config("D:/not-used")

        safe = config.safe_dict()
        self.assertEqual(safe["database"], "JSReportReplica")
        self.assertEqual(safe["schema"], "app")
        self.assertNotIn("user", safe)
        self.assertNotIn("password", safe)
        self.assertNotIn("secret-value", repr(config))

    def test_jsonl_loader_ignores_invalid_and_non_object_rows(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "result.jsonl"
            path.write_text(
                "\n".join((json.dumps({"status": "success"}), "not-json", "[]")),
                encoding="utf-8",
            )

            records = load_jsonl_records(path)

        self.assertEqual(records, [{"status": "success"}])

    def test_dingtalk_credentials_can_be_hydrated_without_returning_values(self) -> None:
        values = {
            "YYDD/1688/notification/dingtalk/webhook": "https://example.invalid/webhook",
            "YYDD/1688/notification/dingtalk/secret": "signing-secret",
        }
        with patch.dict(os.environ, {}, clear=True):
            with patch(
                "stop_sale_audit._load_runtime_secret",
                side_effect=lambda _root, ref: values[ref],
            ):
                result = hydrate_dingtalk_credentials("D:/runtime")

            self.assertEqual(result, {"DINGTALK_WEBHOOK": True, "DINGTALK_SECRET": True})
            self.assertNotIn("example.invalid", repr(result))
            self.assertNotIn("signing-secret", repr(result))

    def test_heartbeat_updates_only_an_active_run(self) -> None:
        cursor = SimpleNamespace(rowcount=1)
        cursor.execute = unittest.mock.Mock(return_value=cursor)
        connection = unittest.mock.MagicMock()
        connection.__enter__.return_value = connection
        connection.cursor.return_value = cursor
        config = SimpleNamespace(schema="app")

        with patch("stop_sale_audit.connect_app_database", return_value=connection):
            StopSaleAuditRepository(config).heartbeat_run("run-1")

        statement = cursor.execute.call_args.args[0]
        self.assertIn("status = 'running'", statement)
        self.assertIn("finished_at IS NULL", statement)
        self.assertEqual(cursor.execute.call_args.args[1], ("run-1",))
        connection.commit.assert_called_once_with()

    def test_heartbeat_rejects_a_run_that_is_no_longer_active(self) -> None:
        inactive_result = SimpleNamespace(fetchone=lambda: ("success", "finished"))
        cursor = SimpleNamespace(rowcount=0)
        cursor.execute = unittest.mock.Mock(side_effect=[cursor, inactive_result])
        connection = unittest.mock.MagicMock()
        connection.__enter__.return_value = connection
        connection.cursor.return_value = cursor
        config = SimpleNamespace(schema="app")

        with patch("stop_sale_audit.connect_app_database", return_value=connection):
            with self.assertRaisesRegex(RuntimeError, "no longer active"):
                StopSaleAuditRepository(config).heartbeat_run("run-1")

    def test_heartbeat_accepts_unknown_rowcount_when_run_is_still_active(self) -> None:
        active_result = SimpleNamespace(fetchone=lambda: ("running", None))
        cursor = SimpleNamespace(rowcount=-1)
        cursor.execute = unittest.mock.Mock(side_effect=[cursor, active_result])
        connection = unittest.mock.MagicMock()
        connection.__enter__.return_value = connection
        connection.cursor.return_value = cursor
        config = SimpleNamespace(schema="app")

        with patch("stop_sale_audit.connect_app_database", return_value=connection):
            StopSaleAuditRepository(config).heartbeat_run("run-1")

        self.assertEqual(cursor.execute.call_count, 2)
        connection.commit.assert_called_once_with()

    def test_heartbeat_retries_a_transient_database_error(self) -> None:
        cursor = SimpleNamespace(rowcount=1)
        cursor.execute = unittest.mock.Mock(return_value=cursor)
        connection = unittest.mock.MagicMock()
        connection.__enter__.return_value = connection
        connection.cursor.return_value = cursor
        config = SimpleNamespace(schema="app")

        with (
            patch(
                "stop_sale_audit.connect_app_database",
                side_effect=[pyodbc.OperationalError("temporary"), connection],
            ) as connect,
            patch("stop_sale_audit.time.sleep") as sleep,
        ):
            StopSaleAuditRepository(config).heartbeat_run("run-1")

        self.assertEqual(connect.call_count, 2)
        sleep.assert_called_once_with(2.0)
        connection.commit.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
