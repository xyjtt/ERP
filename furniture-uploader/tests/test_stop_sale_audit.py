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
    hydrate_source_database_credentials,
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
            "YYDD/1688/notification/dingtalk/webhook": (
                "https://oapi.dingtalk.com/robot/send?access_token=" + "a" * 64
            ),
            "YYDD/1688/notification/dingtalk/secret": "SEC" + "b" * 64,
        }
        with patch.dict(os.environ, {}, clear=True):
            with patch(
                "stop_sale_audit._load_runtime_secret",
                side_effect=lambda _root, ref: values[ref],
            ):
                result = hydrate_dingtalk_credentials("D:/runtime")

            self.assertEqual(result, {"DINGTALK_WEBHOOK": True, "DINGTALK_SECRET": True})
            self.assertNotIn("access_token", repr(result))
            self.assertNotIn(values["YYDD/1688/notification/dingtalk/secret"], repr(result))

    def test_invalid_environment_pair_falls_back_to_valid_runtime_credentials(self) -> None:
        values = {
            "YYDD/1688/notification/dingtalk/webhook": (
                "https://oapi.dingtalk.com/robot/send?access_token=" + "c" * 64
            ),
            "YYDD/1688/notification/dingtalk/secret": "SEC" + "d" * 64,
        }
        with patch.dict(
            os.environ,
            {
                "DINGTALK_WEBHOOK": "https://example.invalid/placeholder",
                "DINGTALK_SECRET": "placeholder-secret",
            },
            clear=True,
        ):
            with patch(
                "stop_sale_audit._load_runtime_secret",
                side_effect=lambda _root, ref: values[ref],
            ):
                result = hydrate_dingtalk_credentials("D:/runtime")

            self.assertEqual(result, {"DINGTALK_WEBHOOK": True, "DINGTALK_SECRET": True})
            self.assertEqual(os.environ["DINGTALK_WEBHOOK"], values[
                "YYDD/1688/notification/dingtalk/webhook"
            ])
            self.assertEqual(os.environ["DINGTALK_SECRET"], values[
                "YYDD/1688/notification/dingtalk/secret"
            ])

    def test_source_credentials_hydrate_without_returning_values(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            with patch(
                "stop_sale_audit._load_runtime_credential",
                return_value=("source-reader", "source-password"),
            ):
                result = hydrate_source_database_credentials("D:/runtime")

            self.assertEqual(
                result,
                {
                    "STOP_SALE_SOURCE_SQLSERVER_USER": True,
                    "STOP_SALE_SOURCE_SQLSERVER_PASSWORD": True,
                },
            )
            self.assertNotIn("source-reader", repr(result))
            self.assertNotIn("source-password", repr(result))
            self.assertEqual(os.environ["STOP_SALE_SOURCE_SQLSERVER_USER"], "source-reader")
            self.assertEqual(
                os.environ["STOP_SALE_SOURCE_SQLSERVER_PASSWORD"],
                "source-password",
            )

    def test_heartbeat_updates_only_an_active_run(self) -> None:
        active_result = SimpleNamespace(fetchone=lambda: ("running", None))
        cursor = SimpleNamespace()
        cursor.execute = unittest.mock.Mock(return_value=active_result)
        connection = unittest.mock.MagicMock()
        connection.__enter__.return_value = connection
        connection.cursor.return_value = cursor
        config = SimpleNamespace(schema="app")

        with patch("stop_sale_audit.connect_app_database", return_value=connection):
            StopSaleAuditRepository(config).heartbeat_run("run-1")

        statement = cursor.execute.call_args.args[0]
        self.assertIn("OUTPUT inserted.status, inserted.finished_at", statement)
        self.assertIn("status = 'running'", statement)
        self.assertIn("finished_at IS NULL", statement)
        self.assertEqual(cursor.execute.call_args.args[1], ("run-1",))
        self.assertEqual(connection.timeout, 15)
        connection.commit.assert_called_once_with()

    def test_heartbeat_rejects_a_run_that_is_no_longer_active(self) -> None:
        update_result = SimpleNamespace(fetchone=lambda: None)
        update_cursor = SimpleNamespace()
        update_cursor.execute = unittest.mock.Mock(return_value=update_result)
        update_connection = unittest.mock.MagicMock()
        update_connection.__enter__.return_value = update_connection
        update_connection.cursor.return_value = update_cursor
        verification_result = SimpleNamespace(fetchone=lambda: None)
        verification_cursor = SimpleNamespace()
        verification_cursor.execute = unittest.mock.Mock(return_value=verification_result)
        verification_connection = unittest.mock.MagicMock()
        verification_connection.__enter__.return_value = verification_connection
        verification_connection.cursor.return_value = verification_cursor
        config = SimpleNamespace(schema="app")

        with patch(
            "stop_sale_audit.connect_app_database",
            side_effect=[update_connection, verification_connection],
        ):
            with self.assertRaisesRegex(RuntimeError, "no longer active"):
                StopSaleAuditRepository(config).heartbeat_run("run-1")

    def test_heartbeat_verifies_empty_output_with_a_fresh_connection(self) -> None:
        update_result = SimpleNamespace(fetchone=lambda: None)
        update_cursor = SimpleNamespace()
        update_cursor.execute = unittest.mock.Mock(return_value=update_result)
        update_connection = unittest.mock.MagicMock()
        update_connection.__enter__.return_value = update_connection
        update_connection.cursor.return_value = update_cursor
        verification_result = SimpleNamespace(fetchone=lambda: ("running", None))
        verification_cursor = SimpleNamespace()
        verification_cursor.execute = unittest.mock.Mock(
            side_effect=[verification_result, verification_cursor]
        )
        verification_connection = unittest.mock.MagicMock()
        verification_connection.__enter__.return_value = verification_connection
        verification_connection.cursor.return_value = verification_cursor
        config = SimpleNamespace(schema="app")

        with patch(
            "stop_sale_audit.connect_app_database",
            side_effect=[update_connection, verification_connection],
        ):
            StopSaleAuditRepository(config).heartbeat_run("run-1")

        self.assertEqual(verification_cursor.execute.call_count, 2)
        self.assertEqual(update_connection.timeout, 15)
        self.assertEqual(verification_connection.timeout, 15)
        verification_connection.commit.assert_called_once_with()

    def test_heartbeat_retries_a_transient_database_error(self) -> None:
        active_result = SimpleNamespace(fetchone=lambda: ("running", None))
        cursor = SimpleNamespace()
        cursor.execute = unittest.mock.Mock(return_value=active_result)
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

    def test_active_crawler_query_is_scoped_to_account_key(self) -> None:
        object_result = SimpleNamespace(fetchone=lambda: (1,))
        count_result = SimpleNamespace(fetchone=lambda: (2,))
        cursor = SimpleNamespace()
        cursor.execute = unittest.mock.Mock(side_effect=[object_result, count_result])
        connection = unittest.mock.MagicMock()
        connection.__enter__.return_value = connection
        connection.cursor.return_value = cursor
        config = SimpleNamespace(schema="app")

        with patch("stop_sale_audit.connect_app_database", return_value=connection):
            count = StopSaleAuditRepository(config).count_active_crawler_tasks("gonglai")

        self.assertEqual(count, 2)
        statement, params = cursor.execute.call_args_list[1].args
        self.assertIn("AND account_key = ?", statement)
        self.assertEqual(params, ("gonglai",))

    def test_recent_stop_sale_query_can_filter_current_store_only(self) -> None:
        count_result = SimpleNamespace(fetchone=lambda: (1,))
        cursor = SimpleNamespace()
        cursor.execute = unittest.mock.Mock(return_value=count_result)
        connection = unittest.mock.MagicMock()
        connection.__enter__.return_value = connection
        connection.cursor.return_value = cursor
        config = SimpleNamespace(schema="app")

        with patch("stop_sale_audit.connect_app_database", return_value=connection):
            count = StopSaleAuditRepository(config).count_recent_active_stop_sale_runs(
                240,
                ["STORE-A"],
            )

        self.assertEqual(count, 1)
        statement, params = cursor.execute.call_args.args
        self.assertIn("JOIN [app].[ali1688_stop_sale_item]", statement)
        self.assertIn("item.store_name IN (?)", statement)
        self.assertEqual(params, (-240, "STORE-A"))


if __name__ == "__main__":
    unittest.main()
