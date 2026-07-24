from __future__ import annotations

import unittest

from scripts.migrate_hsds_from_legacy_ssh import (
    build_remote_node_script,
    checked_identifier,
)


class HsdsSshMigrationTests(unittest.TestCase):
    def test_remote_audit_script_is_read_only_and_does_not_embed_credentials(self) -> None:
        script = build_remote_node_script(
            "planning_hsds_root_word_daily_source",
            include_rows=False,
        )
        lowered = script.lower()

        self.assertIn("select count(*)", lowered)
        self.assertNotIn("delete from", lowered)
        self.assertNotIn("insert into", lowered)
        self.assertNotIn("update `", lowered)
        self.assertNotIn("credential_ref", lowered)

    def test_source_identifier_is_validated(self) -> None:
        with self.assertRaises(ValueError):
            checked_identifier("source; drop table x", "source table")

    def test_apply_query_only_orders_by_columns_present_in_source(self) -> None:
        script = build_remote_node_script(
            "planning_hsds_root_word_daily_source",
            include_rows=True,
        )

        self.assertIn("const columnNames = new Set", script)
        self.assertIn(".filter((name) => columnNames.has(name))", script)
        self.assertIn("'stat_date', 'id', 'source_record_key', 'root_word'", script)
        self.assertNotIn("ORDER BY `stat_date`, `id`", script)


if __name__ == "__main__":
    unittest.main()
