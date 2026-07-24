# -*- coding: utf-8 -*-
"""API contract tests for keyword and title score metadata."""

import os
import sys
import unittest
from unittest.mock import patch


sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api.title_api import create_app
from config.settings import Settings
from database.db_manager import DatabaseManager
from database.keyword_source_repository import KeywordSourceRepository


class TestTitleApiScoring(unittest.TestCase):
    def setUp(self):
        Settings._instance = None
        Settings._initialized = False
        DatabaseManager._instance = None
        DatabaseManager._initialized = False

    def test_keywords_endpoint_returns_ranked_scores_and_metadata(self):
        rows = [
            {
                "word": "高竞争柜",
                "category": "柜类",
                "search_popularity": 100,
                "competition": 90,
                "product_fit": 80,
            },
            {
                "word": "低竞争柜",
                "category": "柜类",
                "search_popularity": 100,
                "competition": 10,
                "product_fit": 80,
            },
        ]
        with patch.object(DatabaseManager, "get_keywords_by_category", return_value=rows):
            client = create_app({"TESTING": True}).test_client()
            response = client.get("/api/title/keywords?category=柜类&limit=10")

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()["data"]
        self.assertEqual([item["word"] for item in payload["keywords"]], ["低竞争柜", "高竞争柜"])
        self.assertEqual(payload["keywords"][0]["score_status"], "complete")
        self.assertEqual(payload["score_metadata"]["score_version"], "keyword_score_40_30_30_v1")
        self.assertEqual(payload["score_metadata"]["weights"]["search_heat"], 0.4)

    def test_health_distinguishes_keyword_and_title_scores(self):
        client = create_app({"TESTING": True}).test_client()

        response = client.get("/api/health")

        self.assertEqual(response.status_code, 200)
        scores = response.get_json()["data"]["scores"]
        self.assertEqual(scores["keyword"]["score_type"], "keyword_composite")
        self.assertEqual(scores["title_structure"]["score_type"], "title_structure_quality")

    def test_live_source_preview_is_read_only_and_exposes_source_roles(self):
        market_rows = [{
            "source_stat_date": "2026-07-16",
            "board_type": "keyword_hot",
            "category_path": "家装建材",
            "rank_no": 1,
            "word": "床头柜",
            "metric_summary": '{"seSpvIndex": 100, "seMaxRescnt": 20}',
            "source_record_key": "market-row",
        }]
        distribution_rows = [{
            "source_stat_date": "2026-07-15",
            "board_type": "hot_search",
            "category_path": "家装建材 > 卧室家具 > 斗柜",
            "product_name": "现代斗柜",
            "search_heat_num": 60,
            "trade_index_num": 10,
            "source_record_key": "distribution-row",
        }]
        with patch.object(KeywordSourceRepository, "market_rank_keywords", return_value=market_rows), \
             patch.object(KeywordSourceRepository, "distribution_product_evidence", return_value=distribution_rows), \
             patch.object(KeywordSourceRepository, "market_opportunity_items", return_value=[]), \
             patch.object(KeywordSourceRepository, "market_opportunity_signals", return_value=[]):
            client = create_app({"TESTING": True}).test_client()
            response = client.post("/api/title/keywords/preview", json={
                "category": "柜类",
                "product_name": "现代实木床头柜",
                "core_keywords": ["床头柜"],
                "attributes": {"材质": "实木"},
                "limit": 20,
            })

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()["data"]
        self.assertTrue(payload["read_only"])
        by_word = {item["word"]: item for item in payload["keywords"]}
        self.assertEqual(by_word["床头柜"]["score_status"], "complete")
        self.assertEqual(by_word["斗柜"]["source_role"], "product_evidence")
        self.assertEqual(by_word["斗柜"]["score_status"], "incomplete")
        self.assertFalse(payload["product_fit_metadata"]["automatic_submit_allowed"])

    def test_live_source_preview_query_filters_candidates(self):
        market_rows = [
            {
                "source_stat_date": "2026-07-16",
                "board_type": "keyword_hot",
                "category_path": "家装建材",
                "rank_no": 1,
                "word": "床头柜",
                "metric_summary": '{"seSpvIndex": 100, "seMaxRescnt": 20}',
            },
            {
                "source_stat_date": "2026-07-16",
                "board_type": "keyword_hot",
                "category_path": "家装建材",
                "rank_no": 2,
                "word": "鞋柜",
                "metric_summary": '{"seSpvIndex": 80, "seMaxRescnt": 10}',
            },
        ]
        with patch.object(KeywordSourceRepository, "market_rank_keywords", return_value=market_rows), \
             patch.object(KeywordSourceRepository, "distribution_product_evidence", return_value=[]), \
             patch.object(KeywordSourceRepository, "market_opportunity_items", return_value=[]), \
             patch.object(KeywordSourceRepository, "market_opportunity_signals", return_value=[]):
            client = create_app({"TESTING": True}).test_client()
            response = client.post("/api/title/keywords/preview", json={
                "category": "柜类",
                "core_keywords": ["床头柜"],
                "query": "床头",
            })

        words = [item["word"] for item in response.get_json()["data"]["keywords"]]
        self.assertEqual(words, ["床头柜"])


if __name__ == "__main__":
    unittest.main()
