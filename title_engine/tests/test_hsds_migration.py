# -*- coding: utf-8 -*-
"""Tests for the dry-run HSDS migration plan builder."""

import os
import sys
import unittest
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts.export_hsds_source import build_query, checked_identifier
from scripts.migrate_hsds_root_words import build_plan, normalize_hsds_row


class TestHsdsMigration(unittest.TestCase):
    def test_normalization_requires_a_root_word_and_hashes_payload(self):
        self.assertIsNone(normalize_hsds_row({}, window_start=date(2026, 7, 15), window_end=date(2026, 7, 21)))
        row = normalize_hsds_row(
            {
                "root_word": "鏌滃瓙",
                "search_people": "12.5",
                "category_level_3": "鏌滅被",
                "stat_date": "2026-07-18",
                "demand_primary_name": "bedroom",
                "is_new_root": 1,
            },
            window_start=date(2026, 7, 15),
            window_end=date(2026, 7, 21),
        )
        self.assertEqual(row["root_word"], "鏌滃瓙")
        self.assertEqual(str(row["search_people"]), "12.5")
        self.assertEqual(row["window_start"], date(2026, 7, 12))
        self.assertEqual(row["window_end"], date(2026, 7, 18))
        self.assertEqual(row["demand_primary_name"], "bedroom")
        self.assertIs(row["is_new_root"], True)
        self.assertEqual(row["source_type"], "hsds_demand_root")
        self.assertEqual(len(row["source_hash"]), 64)

    def test_plan_is_read_only_and_counts_rejected_rows(self):
        plan = build_plan(
            [{"root_word": "鏌滃瓙"}, {"root_word": ""}],
            today=date(2026, 7, 21),
        )
        self.assertTrue(plan["read_only"])
        self.assertEqual(plan["window_start"], "2026-07-15")
        self.assertEqual(plan["window_end"], "2026-07-21")
        self.assertEqual(plan["extracted_count"], 1)
        self.assertEqual(plan["rejected_count"], 1)
        self.assertEqual(plan["upserted_count"], 0)

    def test_source_export_query_is_read_only_and_identifiers_are_validated(self):
        query = build_query("planning_hsds_root_word_daily_source", "stat_date")
        self.assertTrue(query.lower().startswith("select"))
        self.assertNotIn("delete", query.lower())
        with self.assertRaises(ValueError):
            checked_identifier("table; drop table x", "table")


if __name__ == "__main__":
    unittest.main()
