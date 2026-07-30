# -*- coding: utf-8 -*-
"""
Tests for manual keyword import helpers.
"""

from __future__ import annotations

import csv
import os
import sys
import tempfile
import unittest
from pathlib import Path


sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts.import_keywords import load_keyword_rows, normalize_keyword_row


class TestImportKeywords(unittest.TestCase):
    def test_normalize_keyword_row(self):
        row = {
            "关键词": "实木沙发",
            "类目": "家具",
            "搜索人气": "1234",
            "交易指数": "88.5",
            "竞争度": "0.25",
            "相关性": "0.93",
            "来源": "manual",
        }

        normalized = normalize_keyword_row(row)

        self.assertEqual(normalized["keyword"], "实木沙发")
        self.assertEqual(normalized["category"], "家具")
        self.assertAlmostEqual(normalized["search_popularity"], 1234.0)
        self.assertAlmostEqual(normalized["transaction_index"], 88.5)
        self.assertAlmostEqual(normalized["competition"], 0.25)
        self.assertAlmostEqual(normalized["relevance"], 0.93)
        self.assertAlmostEqual(normalized["product_fit"], 0.93)

    def test_explicit_product_fit_takes_priority(self):
        normalized = normalize_keyword_row({
            "关键词": "现代餐边柜",
            "类目": "柜类",
            "搜索人气": "120",
            "竞争指数": "30",
            "相关性": "0.4",
            "产品适配度": "0.9",
        })

        self.assertAlmostEqual(normalized["product_fit"], 0.9)

    def test_missing_score_metrics_remain_missing(self):
        normalized = normalize_keyword_row({
            "关键词": "现代餐边柜",
            "类目": "柜类",
        })

        self.assertIsNone(normalized["search_popularity"])
        self.assertIsNone(normalized["competition"])
        self.assertIsNone(normalized["relevance"])
        self.assertIsNone(normalized["product_fit"])

    def test_load_keyword_rows_from_csv(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "keywords.csv"
            with path.open("w", encoding="utf-8-sig", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=["关键词", "类目", "搜索人气", "交易指数", "竞争度", "相关性"])
                writer.writeheader()
                writer.writerow({
                    "关键词": "布艺沙发",
                    "类目": "家具",
                    "搜索人气": "100",
                    "交易指数": "50",
                    "竞争度": "0.3",
                    "相关性": "0.8",
                })

            rows = load_keyword_rows(path)

            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["keyword"], "布艺沙发")
            self.assertEqual(rows[0]["category"], "家具")


if __name__ == "__main__":
    unittest.main()
