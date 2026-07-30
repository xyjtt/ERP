# -*- coding: utf-8 -*-
"""Tests for the 40/30/30 keyword candidate score."""

import os
import sys
import unittest


sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config.settings import Settings
from rpa.keyword_manager import KeywordData, KeywordManager


class TestKeywordScoring(unittest.TestCase):
    def setUp(self):
        Settings._instance = None
        Settings._initialized = False
        self.manager = KeywordManager()

    def test_exact_40_30_30_formula_after_candidate_normalization(self):
        keywords = [
            KeywordData("目标词", "柜类", search_popularity=30, competition=20, product_fit=10),
            KeywordData("边界低", "柜类", search_popularity=10, competition=10, product_fit=10),
            KeywordData("边界高", "柜类", search_popularity=30, competition=30, product_fit=30),
        ]

        self.manager.rank_keywords(keywords, limit=3)

        self.assertAlmostEqual(keywords[0].score_breakdown["search_heat"], 1.0)
        self.assertAlmostEqual(keywords[0].score_breakdown["inverse_competition"], 0.5)
        self.assertAlmostEqual(keywords[0].score_breakdown["product_fit"], 0.0)
        self.assertAlmostEqual(keywords[0].keyword_score, 0.55)
        self.assertEqual(keywords[0].score_breakdown["weights"], {
            "search_heat": 0.4,
            "inverse_competition": 0.3,
            "product_fit": 0.3,
        })

    def test_lower_competition_ranks_higher_when_other_metrics_match(self):
        high_competition = KeywordData("高竞争", "柜类", 100, competition=90, product_fit=80)
        low_competition = KeywordData("低竞争", "柜类", 100, competition=10, product_fit=80)

        ranked = self.manager.rank_keywords([high_competition, low_competition], limit=2)

        self.assertEqual([item.word for item, _score in ranked], ["低竞争", "高竞争"])

    def test_missing_metric_is_incomplete_and_ranks_last(self):
        incomplete = KeywordData("缺竞争度", "柜类", 1000, competition=None, product_fit=1)
        complete = KeywordData("完整词", "柜类", 10, competition=1, product_fit=1)

        ranked = self.manager.rank_keywords([incomplete, complete], limit=2)

        self.assertEqual([item.word for item, _score in ranked], ["完整词", "缺竞争度"])
        self.assertIsNone(incomplete.keyword_score)
        self.assertEqual(incomplete.score_status, "incomplete")
        self.assertEqual(incomplete.score_breakdown["missing_metrics"], ["competition"])

    def test_incomplete_extreme_values_do_not_distort_complete_scores(self):
        complete_low = KeywordData("完整低", "柜类", 10, competition=10, product_fit=10)
        complete_high = KeywordData("完整高", "柜类", 20, competition=5, product_fit=20)
        incomplete_extreme = KeywordData("不完整极值", "柜类", 999999, competition=None, product_fit=999999)

        self.manager.rank_keywords(
            [complete_low, complete_high, incomplete_extreme],
            limit=3,
        )

        self.assertAlmostEqual(complete_high.score_breakdown["search_heat"], 1.0)
        self.assertAlmostEqual(complete_high.score_breakdown["product_fit"], 1.0)
        self.assertIsNone(incomplete_extreme.keyword_score)

    def test_relevance_is_legacy_product_fit_fallback(self):
        keyword = KeywordData("兼容词", "柜类", 10, competition=2, relevance=0.8)

        self.manager.rank_keywords([keyword], limit=1)

        self.assertEqual(keyword.score_status, "complete")
        self.assertAlmostEqual(keyword.score_breakdown["product_fit"], 1.0)

    def test_exact_ties_keep_source_order(self):
        first = KeywordData("先输入", "柜类", 10, competition=2, product_fit=5)
        second = KeywordData("后输入", "柜类", 10, competition=2, product_fit=5)

        ranked = self.manager.rank_keywords([first, second], limit=2)

        self.assertEqual([item.word for item, _score in ranked], ["先输入", "后输入"])

    def test_configured_weights_are_normalized(self):
        Settings().set("seo_rules.word_rules.priority_weights", {
            "search_heat": 4,
            "inverse_competition": 3,
            "product_fit": 3,
        })

        metadata = self.manager.get_score_metadata()

        self.assertEqual(metadata["weights"], {
            "search_heat": 0.4,
            "inverse_competition": 0.3,
            "product_fit": 0.3,
        })


if __name__ == "__main__":
    unittest.main()
