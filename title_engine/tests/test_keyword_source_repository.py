# -*- coding: utf-8 -*-
"""Query-contract tests for read-only keyword source access."""

import os
import sys
import unittest


sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database.keyword_source_repository import KeywordSourceRepository


class FakeDatabaseManager:
    def __init__(self):
        self.calls = []

    def execute_query(self, sql, params=None):
        self.calls.append((sql, params))
        return []


class FailingStrictDatabaseManager(FakeDatabaseManager):
    def execute_query_strict(self, sql, params=None):
        raise RuntimeError("database unavailable")


class TestKeywordSourceRepository(unittest.TestCase):
    def setUp(self):
        self.db = FakeDatabaseManager()
        self.repository = KeywordSourceRepository(self.db)

    def test_market_rank_query_uses_board_specific_latest_windows(self):
        self.repository.market_rank_keywords(100)
        sql, params = self.db.calls[-1]

        self.assertIn("GROUP BY board_type", sql)
        self.assertIn("metric_summary", sql)
        self.assertEqual(params, (100,))

    def test_category_filter_removes_generic_class_suffix(self):
        self.repository.distribution_product_evidence("柜类", 50)
        sql, params = self.db.calls[-1]

        self.assertIn("category_path LIKE ?", sql)
        self.assertEqual(params, (50, "柜", "%柜%"))

    def test_all_source_queries_are_select_only(self):
        self.repository.market_rank_keywords()
        self.repository.distribution_product_evidence("柜类")
        self.repository.market_opportunity_items("柜类")
        self.repository.market_opportunity_signals("柜类")

        for sql, _params in self.db.calls:
            normalized = " ".join(sql.lower().split())
            self.assertTrue(normalized.startswith("with") or normalized.startswith("select"))
            for forbidden in ("insert ", "update ", "delete ", "merge ", "alter ", "drop "):
                self.assertNotIn(forbidden, normalized)

    def test_capture_time_is_converted_for_legacy_odbc_drivers(self):
        self.repository.market_rank_keywords()
        self.repository.distribution_product_evidence("柜类")
        self.repository.market_opportunity_items("柜类")
        self.repository.market_opportunity_signals("柜类")

        for sql, _params in self.db.calls:
            self.assertIn(
                "CONVERT(NVARCHAR(50), source.capture_time, 127) AS capture_time",
                sql,
            )

    def test_live_source_failure_is_not_silently_converted_to_empty_rows(self):
        repository = KeywordSourceRepository(FailingStrictDatabaseManager())

        with self.assertRaisesRegex(RuntimeError, "database unavailable"):
            repository.market_rank_keywords()

    def test_hsds_query_is_select_only_and_uses_recent_window(self):
        self.repository.hsds_root_words("鏌滅被", 10)
        sql, params = self.db.calls[-1]
        normalized = " ".join(sql.lower().split())

        self.assertTrue(normalized.startswith("select"))
        self.assertIn("window_end >= dateadd(day, -7", normalized)
        self.assertEqual(params[0], 10)
        self.assertNotIn(" update ", normalized)
        self.assertNotIn(" delete ", normalized)

    def test_successful_source_query_is_cached_by_sql_and_parameters(self):
        repository = KeywordSourceRepository(self.db, cache_ttl_seconds=300)

        first = repository.market_rank_keywords(10)
        first.append({"word": "mutated by caller"})
        second = repository.market_rank_keywords(10)

        self.assertEqual(len(self.db.calls), 1)
        self.assertEqual(second, [])

    def test_zero_cache_ttl_disables_source_cache(self):
        repository = KeywordSourceRepository(self.db, cache_ttl_seconds=0)

        repository.market_rank_keywords(10)
        repository.market_rank_keywords(10)

        self.assertEqual(len(self.db.calls), 2)


if __name__ == "__main__":
    unittest.main()
