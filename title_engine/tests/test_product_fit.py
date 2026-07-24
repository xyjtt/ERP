# -*- coding: utf-8 -*-
"""Tests for transparent product-fit scoring."""

import os
import sys
import unittest


sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from rpa.product_fit import ProductContext, ProductFitEvaluator


class TestProductFitEvaluator(unittest.TestCase):
    def setUp(self):
        self.evaluator = ProductFitEvaluator()
        self.context = ProductContext(
            category="柜类",
            product_name="现代实木床头柜 黑色台面 胡桃色",
            core_keywords=["床头柜"],
            attributes={"材质": "实木", "颜色": "胡桃色"},
        )

    def test_exact_core_keyword_scores_above_different_cabinet(self):
        exact = self.evaluator.evaluate("床头柜", self.context)
        mismatch = self.evaluator.evaluate("鞋柜", self.context)

        self.assertEqual(exact.status, "complete")
        self.assertGreater(exact.score, mismatch.score)
        self.assertEqual(exact.breakdown["core_keywords"], 1.0)

    def test_attribute_keyword_contributes_without_becoming_exact_product(self):
        result = self.evaluator.evaluate("胡桃色", self.context)

        self.assertGreater(result.breakdown["attributes"], 0.0)
        self.assertLess(result.score, 1.0)

    def test_missing_product_context_is_incomplete(self):
        result = self.evaluator.evaluate("床头柜", ProductContext(category=""))

        self.assertIsNone(result.score)
        self.assertEqual(result.status, "incomplete")

    def test_metadata_disallows_automatic_submit(self):
        metadata = self.evaluator.metadata()

        self.assertEqual(metadata["version"], "product_fit_lexical_v1")
        self.assertFalse(metadata["automatic_submit_allowed"])


if __name__ == "__main__":
    unittest.main()
