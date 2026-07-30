# -*- coding: utf-8 -*-
"""Tests for the read-only legacy score-input audit."""

import os
import sys
import unittest


sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database.db_manager import DatabaseManager
from scripts.audit_keyword_score_inputs import (
    build_audit_report,
    build_error_report,
    run_audit,
)


class TestKeywordScoreAudit(unittest.TestCase):
    def test_report_summarizes_ambiguous_zero_rows(self):
        report = build_audit_report([
            {"source": "manual", "total_rows": 10, "ambiguous_zero_rows": 3},
            {"source": "sycm", "total_rows": 20, "ambiguous_zero_rows": 0},
        ])

        self.assertTrue(report["read_only"])
        self.assertEqual(report["totals"]["total_rows"], 30)
        self.assertEqual(report["totals"]["ambiguous_zero_rows"], 3)
        self.assertTrue(report["warnings"])

    def test_empty_report_explains_missing_table_or_permission(self):
        report = build_audit_report([])

        self.assertEqual(report["totals"]["source_count"], 0)
        self.assertIn("SELECT permission", report["warnings"][0])

    def test_database_audit_query_is_select_only(self):
        manager = object.__new__(DatabaseManager)
        manager._keywords_table = "[app].[ali1688_title_keyword]"
        captured = {}

        def execute_query_strict(sql, params=None):
            captured["sql"] = sql
            captured["params"] = params
            return []

        manager.execute_query_strict = execute_query_strict
        manager.audit_keyword_score_inputs()
        normalized = " ".join(captured["sql"].lower().split())

        self.assertTrue(normalized.startswith("select"))
        self.assertNotIn(" update ", normalized)
        self.assertNotIn(" delete ", normalized)
        self.assertNotIn(" merge ", normalized)

    def test_missing_table_is_a_structured_redacted_error(self):
        report = build_error_report(
            RuntimeError("42S02: Invalid object name 'app.ali1688_title_keyword'")
        )

        self.assertTrue(report["read_only"])
        self.assertEqual(report["error"]["code"], "SOURCE_TABLE_UNAVAILABLE")
        self.assertNotIn("42S02", report["error"]["message"])

    def test_query_failure_returns_nonzero_exit_code(self):
        class FailingManager:
            def audit_keyword_score_inputs(self):
                raise RuntimeError("connection failed for secret-value")

        report, exit_code = run_audit(FailingManager())

        self.assertEqual(exit_code, 4)
        self.assertEqual(report["error"]["code"], "AUDIT_QUERY_FAILED")
        self.assertNotIn("secret-value", str(report))


if __name__ == "__main__":
    unittest.main()
