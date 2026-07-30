# -*- coding: utf-8 -*-
"""Tests for live crawler keyword source adapters."""

import json
import os
import sys
import unittest
from datetime import date


sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from rpa.keyword_manager import KeywordManager
from rpa.keyword_sources import KeywordSourceService
from rpa.product_fit import ProductContext


class FakeSourceRepository:
    def market_rank_keywords(self, _limit):
        return [
            {
                "source_stat_date": date(2026, 7, 16),
                "board_type": "keyword_hot",
                "category_path": "家装建材",
                "rank_no": 4,
                "word": "床头柜",
                "metric_summary": json.dumps({"seSpvIndex": 15000, "seMaxRescnt": 19000}),
                "source_record_key": "market-fresh",
            },
            {
                "source_stat_date": date(2026, 5, 1),
                "board_type": "keyword_rising",
                "category_path": "家装建材",
                "rank_no": 2,
                "word": "出租屋床头柜",
                "metric_summary": json.dumps({"seSpvIndex": 2500, "seMaxRescnt": 300}),
                "source_record_key": "market-stale",
            },
        ]

    def distribution_product_evidence(self, _category, _limit):
        return [{
            "source_stat_date": date(2026, 7, 15),
            "board_type": "hot_search",
            "category_path": "家装建材 > 卧室家具 > 床头柜",
            "product_name": "实木床头柜",
            "search_heat_num": 800,
            "trade_index_num": 70,
            "source_record_key": "distribution",
        }]

    def market_opportunity_items(self, _category, _limit):
        return [{
            "source_stat_date": date(2026, 7, 21),
            "board_type": "trend_new",
            "category_path": "家装建材 > 卧室家具 > 斗柜",
            "title_text": "现代实木斗柜",
            "buyer_heat_num": 300,
            "purchase_count_num": 20,
            "source_record_key": "opportunity",
        }]

    def market_opportunity_signals(self, _category, _limit):
        return []

    def hsds_root_words(self, _category, _limit):
        return [{
            "source_stat_date": date(2026, 7, 21),
            "window_start": date(2026, 7, 15),
            "window_end": date(2026, 7, 21),
            "category_level_3": "鏌滅被",
            "root_word": "鏌滃瓙",
            "search_people": 1200,
            "search_people_grow": 0.2,
            "source_record_key": "hsds-1",
            "source_hash": "hash",
        }]


class TestKeywordSourceService(unittest.TestCase):
    def setUp(self):
        self.context = ProductContext(
            category="柜类",
            product_name="现代实木床头柜",
            core_keywords=["床头柜"],
            attributes={"材质": "实木"},
        )
        self.service = KeywordSourceService(
            repository=FakeSourceRepository(),
            today=date(2026, 7, 21),
        )

    def test_preview_keeps_true_keyword_and_evidence_roles_separate(self):
        candidates, summaries, metadata = self.service.preview(self.context)
        by_word = {candidate.word: candidate for candidate in candidates}

        self.assertEqual(by_word["床头柜"].source_role, "keyword_candidate")
        self.assertIsNotNone(by_word["床头柜"].competition)
        self.assertEqual(by_word["斗柜"].source_role, "product_evidence")
        self.assertIsNone(by_word["斗柜"].competition)
        self.assertEqual(metadata["version"], "product_fit_lexical_v1")
        self.assertEqual({item.source for item in summaries}, {
            "sycm_market_rank",
            "distribution_selection",
            "market_opportunity_item",
            "market_opportunity_signal",
            "hsds_demand_root",
        })
        hsds = by_word["鏌滃瓙"]
        self.assertEqual(hsds.source_role, "independent_candidate")
        self.assertIsNone(hsds.competition)

    def test_stale_market_candidate_does_not_receive_total_score(self):
        candidates, _summaries, _metadata = self.service.preview(self.context)
        KeywordManager().rank_keywords(candidates, limit=20)
        stale = next(item for item in candidates if item.word == "出租屋床头柜")

        self.assertEqual(stale.freshness_status, "stale")
        self.assertIsNone(stale.keyword_score)
        self.assertEqual(stale.score_breakdown["missing_metrics"], ["freshness"])

    def test_market_source_summary_reports_mixed_freshness(self):
        _candidates, summaries, _metadata = self.service.preview(self.context)
        market = next(item for item in summaries if item.source == "sycm_market_rank")

        self.assertEqual(market.freshness_status, "mixed")

    def test_evidence_title_does_not_inflate_target_product_fit(self):
        candidates, _summaries, _metadata = self.service.preview(self.context)
        drawer_cabinet = next(item for item in candidates if item.word == "斗柜")

        self.assertLess(drawer_cabinet.product_fit, 0.5)


if __name__ == "__main__":
    unittest.main()
