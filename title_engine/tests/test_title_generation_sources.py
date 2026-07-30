from __future__ import annotations

import unittest
from unittest.mock import patch

from api.title_api import create_app
from rpa.keyword_manager import KeywordData
from rpa.keyword_sources import KeywordSourceService, SourceSummary


class TitleGenerationSourceTests(unittest.TestCase):
    def test_generate_uses_ranked_live_keywords_when_requested(self) -> None:
        candidates = [
            KeywordData(
                word=word,
                category="柜类",
                search_popularity=1000 - index * 10,
                competition=100 + index,
                product_fit=0.9 - index * 0.01,
                source="sycm_market_rank:search",
                source_role="keyword_candidate",
                freshness_status="fresh",
            )
            for index, word in enumerate(
                [
                    "卧室床边柜",
                    "现代简约床头柜",
                    "实木床头柜",
                    "小户型收纳柜",
                    "卧室储物柜",
                    "床边置物柜",
                    "家用床头柜",
                    "轻奢床头柜",
                    "北欧床头柜",
                    "简约收纳柜",
                    "卧室小柜子",
                    "床边小柜",
                ]
            )
        ]
        candidates.insert(
            0,
            KeywordData(
                word="沙发",
                category="家装建材",
                search_popularity=999999,
                competition=1,
                product_fit=0.0,
                source="sycm_market_rank:search",
                source_role="keyword_candidate",
                freshness_status="fresh",
            ),
        )
        summaries = [
            SourceSummary(
                source="sycm_market_rank",
                role="keyword_candidate",
                row_count=len(candidates),
                candidate_count=len(candidates),
                latest_stat_date="2026-07-21",
                freshness_status="fresh",
            )
        ]
        with patch.object(
            KeywordSourceService,
            "preview",
            return_value=(candidates, summaries, {"version": "product_fit_v1"}),
        ):
            client = create_app({"TESTING": True}).test_client()
            response = client.post(
                "/api/title/generate",
                json={
                    "category": "柜类",
                    "product_name": "北美黑胡桃木床头柜",
                    "core_keywords": ["床头柜"],
                    "attributes": {"材质": "黑胡桃木", "风格": "现代简约"},
                    "selling_points": ["卧室收纳", "小户型适用"],
                    "num_titles": 30,
                    "use_source_keywords": True,
                },
            )

        payload = response.get_json()["data"]
        self.assertEqual(response.status_code, 200)
        self.assertEqual(payload["generation_status"], "complete")
        self.assertEqual(payload["count"], 30)
        self.assertEqual(payload["requested_count"], 30)
        self.assertEqual(payload["source_keywords"][0]["score_status"], "complete")
        self.assertEqual(payload["source_summaries"][0]["source"], "sycm_market_rank")
        self.assertEqual(payload["source_keyword_min_fit"], 0.2)
        self.assertNotIn("沙发", payload["selected_source_keywords"])
        self.assertTrue(all(item["title"].startswith("床头柜") for item in payload["titles"]))


if __name__ == "__main__":
    unittest.main()
