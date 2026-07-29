from __future__ import annotations

import hashlib
from pathlib import Path
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch


PROJECT_ROOT = Path(__file__).resolve().parents[1]
RPA_ROOT = PROJECT_ROOT / "rpa"
if str(RPA_ROOT) not in sys.path:
    sys.path.insert(0, str(RPA_ROOT))

from sku_replace_audit import SkuReplaceAuditRepository, sku_replace_task_key


class SkuReplaceAuditTests(unittest.TestCase):
    def test_task_key_matches_jushuitan_sync_identity(self) -> None:
        expected = hashlib.sha256(
            "阿里巴巴-常州工莱家具|1001|old-a|new-a".encode("utf-8")
        ).hexdigest()

        self.assertEqual(
            sku_replace_task_key(
                "阿里巴巴-常州工莱家具",
                "1001",
                " OLD-A ",
                "NEW-A",
            ),
            expected,
        )

    def test_audit_contract_uses_dedicated_replace_tables(self) -> None:
        self.assertEqual(
            SkuReplaceAuditRepository.REQUIRED_TABLES,
            ("ali1688_sku_replace_run", "ali1688_sku_replace_item"),
        )

    def test_ddl_contains_replacement_and_jushuitan_fields(self) -> None:
        ddl = (PROJECT_ROOT / "sql" / "361_ali1688_sku_replace_audit.sql").read_text(
            encoding="utf-8"
        )

        self.assertIn("replacement_sku", ddl)
        self.assertIn("replace_status", ddl)
        self.assertIn("jushuitan_status", ddl)
        self.assertIn("JSReportReplica", ddl)

    def test_recent_active_run_check_can_be_scoped_to_store(self) -> None:
        cursor = MagicMock()
        cursor.execute.return_value.fetchone.return_value = (1,)
        connection = MagicMock()
        connection.__enter__.return_value = connection
        connection.cursor.return_value = cursor

        with patch("sku_replace_audit.connect_app_database", return_value=connection):
            count = SkuReplaceAuditRepository(SimpleNamespace(schema="app")).count_recent_active_runs(
                240,
                store_name="STORE-A",
            )

        statement, parameters = cursor.execute.call_args.args
        self.assertEqual(count, 1)
        self.assertIn("ali1688_sku_replace_item", statement)
        self.assertIn("item.store_name = ?", statement)
        self.assertEqual(parameters, (-240, "STORE-A"))

    def test_heartbeat_sets_a_bounded_query_timeout(self) -> None:
        cursor = MagicMock()
        cursor.execute.return_value.fetchone.return_value = ("running", None)
        connection = MagicMock()
        connection.__enter__.return_value = connection
        connection.cursor.return_value = cursor
        config = SimpleNamespace(schema="app", timeout_seconds=9)

        with patch("sku_replace_audit.connect_app_database", return_value=connection):
            SkuReplaceAuditRepository(config).heartbeat_run("run-1")

        self.assertEqual(connection.timeout, 9)
        connection.commit.assert_called_once_with()

    def test_heartbeat_verifies_empty_output_with_a_fresh_connection(self) -> None:
        update_cursor = MagicMock()
        update_cursor.execute.return_value.fetchone.return_value = None
        update_connection = MagicMock()
        update_connection.__enter__.return_value = update_connection
        update_connection.cursor.return_value = update_cursor

        verification_cursor = MagicMock()
        verification_cursor.execute.side_effect = [
            MagicMock(fetchone=MagicMock(return_value=("running", None))),
            MagicMock(),
        ]
        verification_connection = MagicMock()
        verification_connection.__enter__.return_value = verification_connection
        verification_connection.cursor.return_value = verification_cursor
        config = SimpleNamespace(schema="app", timeout_seconds=9)

        with patch(
            "sku_replace_audit.connect_app_database",
            side_effect=[update_connection, verification_connection],
        ):
            SkuReplaceAuditRepository(config).heartbeat_run("run-1")

        update_connection.commit.assert_called_once_with()
        verification_connection.commit.assert_called_once_with()
        self.assertEqual(verification_cursor.execute.call_count, 2)

    def test_heartbeat_rejects_a_terminal_run_after_empty_output(self) -> None:
        update_cursor = MagicMock()
        update_cursor.execute.return_value.fetchone.return_value = None
        update_connection = MagicMock()
        update_connection.__enter__.return_value = update_connection
        update_connection.cursor.return_value = update_cursor

        verification_cursor = MagicMock()
        verification_cursor.execute.return_value.fetchone.return_value = (
            "partial",
            "2026-07-29T00:00:00",
        )
        verification_connection = MagicMock()
        verification_connection.__enter__.return_value = verification_connection
        verification_connection.cursor.return_value = verification_cursor
        config = SimpleNamespace(schema="app", timeout_seconds=9)

        with (
            patch(
                "sku_replace_audit.connect_app_database",
                side_effect=[update_connection, verification_connection],
            ),
            self.assertRaisesRegex(RuntimeError, "no longer active"),
        ):
            SkuReplaceAuditRepository(config).heartbeat_run("run-1")


if __name__ == "__main__":
    unittest.main()
