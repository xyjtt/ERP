from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

from selenium.common.exceptions import StaleElementReferenceException


PROJECT_ROOT = Path(__file__).resolve().parents[1]
RPA_ROOT = PROJECT_ROOT / "rpa"
if str(RPA_ROOT) not in sys.path:
    sys.path.insert(0, str(RPA_ROOT))

from listing_duplicate_probe import (
    LiveListingDuplicateProbe,
    evaluate_duplicate_gate,
    extract_draft_identifiers,
    parse_draft_count,
    parse_pagination_total,
    summarize_draft_identity_evidence,
    summarize_search_result,
)


class ListingDuplicateProbeTests(unittest.TestCase):
    def test_parse_draft_count(self) -> None:
        self.assertEqual(parse_draft_count("草稿箱 (3)"), 3)
        self.assertEqual(parse_draft_count("草稿（19）"), 19)
        self.assertIsNone(parse_draft_count("全部商品"))

    def test_parse_pagination_total(self) -> None:
        self.assertEqual(parse_pagination_total("共 17 条"), 17)
        self.assertEqual(parse_pagination_total("总计3条"), 3)
        self.assertIsNone(parse_pagination_total("每页 20 条"))

    def test_extract_draft_identifiers_from_links_and_data_attributes(self) -> None:
        result = extract_draft_identifiers(
            {
                "links": [
                    {
                        "href": (
                            "https://offer.1688.com/offer/post/fillProductInfo.htm?"
                            "operator=draft2offer&offerDraftId=6a635fcee4b0eda6ebbdf340"
                        ),
                        "data_attributes": {
                            "data-draft-id": "6a6976c1e4b09c827f2edbc9",
                        },
                    }
                ],
                "draft_id": "6a635fcee4b0eda6ebbdf340",
            }
        )

        self.assertEqual(
            result["draft_ids"],
            ["6a635fcee4b0eda6ebbdf340", "6a6976c1e4b09c827f2edbc9"],
        )
        self.assertEqual(
            result["named_draft_ids"],
            {
                "offerDraftId": ["6a635fcee4b0eda6ebbdf340"],
                "draft_id": ["6a635fcee4b0eda6ebbdf340"],
            },
        )

    def test_draft_page_evidence_annotates_rows_and_page_links(self) -> None:
        class FakeDriver:
            def execute_script(self, _script: str):
                return {
                    "rows": [
                        {
                            "index": 0,
                            "text": "床头柜草稿",
                            "data_attributes": {
                                "data-offer-draft-id": "6a635fcee4b0eda6ebbdf340",
                            },
                            "links": [
                                {
                                    "text": "编辑",
                                    "href": "https://example.test/edit?draftId=6a635fcee4b0eda6ebbdf340",
                                }
                            ],
                        }
                    ],
                    "links": [
                        {
                            "text": "编辑",
                            "href": "https://example.test/edit?draftId=6a635fcee4b0eda6ebbdf340",
                        }
                    ],
                }

        probe = LiveListingDuplicateProbe.__new__(LiveListingDuplicateProbe)
        probe.browser = SimpleNamespace(driver=FakeDriver())

        result = probe._draft_page_evidence()

        self.assertEqual(result["draft_ids"], ["6a635fcee4b0eda6ebbdf340"])
        self.assertEqual(result["rows"][0]["text"], "床头柜草稿")
        self.assertEqual(
            result["rows"][0]["links"][0]["href"],
            "https://example.test/edit?draftId=6a635fcee4b0eda6ebbdf340",
        )
        self.assertEqual(
            result["links"][0]["named_draft_ids"],
            {"draftId": ["6a635fcee4b0eda6ebbdf340"]},
        )

    def test_draft_identity_evidence_reports_found_links_and_missing_ids(self) -> None:
        result = summarize_draft_identity_evidence(
            ["6a635fcee4b0eda6ebbdf340", "6a6976c1e4b09c827f2edbc9"],
            {
                "draft_ids": ["6a635fcee4b0eda6ebbdf340"],
                "rows": [
                    {
                        "links": [
                            {
                                "text": "修改",
                                "href": "https://example.test/edit?offerDraftId=6a635fcee4b0eda6ebbdf340",
                            }
                        ]
                    }
                ],
                "links": [],
            },
        )

        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["found_draft_ids"], ["6a635fcee4b0eda6ebbdf340"])
        self.assertEqual(result["missing_draft_ids"], ["6a6976c1e4b09c827f2edbc9"])
        self.assertEqual(
            result["link_matches"]["6a635fcee4b0eda6ebbdf340"][0]["href"],
            "https://example.test/edit?offerDraftId=6a635fcee4b0eda6ebbdf340",
        )

    def test_shop_identity_ignores_stale_name_element_and_uses_body(self) -> None:
        class StaleName:
            @property
            def text(self):
                raise StaleElementReferenceException("rerendered")

        class Body:
            text = "1688 商家工作台 木刻理想"

        class FakeDriver:
            def find_element(self, _by, _value):
                return Body()

            def find_elements(self, _by, _value):
                return [StaleName()]

        browser = SimpleNamespace(
            driver=FakeDriver(),
            browser_config={"explicit_wait_seconds": 1},
            _resolve_selector=lambda selector, _context: selector,
            _selector_is_configured=lambda selector: bool(selector.get("value")),
        )
        probe = LiveListingDuplicateProbe(
            browser,
            {
                "workflow": {
                    "selectors": {
                        "current_store_name": {"by": "css", "value": ".store-name"},
                    }
                }
            },
            expected_shop="木刻理想",
        )

        result = probe._shop_identity()

        self.assertTrue(result["matched"])
        self.assertEqual(result["visible_names"], [])

    def test_search_result_requires_rows_or_explicit_no_data(self) -> None:
        found = summarize_search_result("XG0143", ["XG014317 商品"], "")
        clear = summarize_search_result("XG0143", [], "暂无数据")
        unknown = summarize_search_result("XG0143", [], "加载完成")
        stale = summarize_search_result("XG0143", ["其他商品"], "")

        self.assertEqual(found["status"], "found")
        self.assertEqual(found["exact_text_match_count"], 1)
        self.assertEqual(clear["status"], "clear")
        self.assertEqual(unknown["status"], "inconclusive")
        self.assertEqual(stale["status"], "inconclusive")

    def test_clear_spu_is_new_spu(self) -> None:
        result = evaluate_duplicate_gate(
            sku_search={"status": "clear", "result_count": 0},
            spu_search={"status": "clear", "result_count": 0},
            draft_count=3,
            draft_limit=20,
        )

        self.assertEqual(result["status"], "clear")
        self.assertEqual(result["novelty_type"], "new_spu")
        self.assertEqual(result["draft_remaining"], 17)

    def test_existing_spu_with_clear_sku_is_new_sku(self) -> None:
        result = evaluate_duplicate_gate(
            sku_search={"status": "clear", "result_count": 0},
            spu_search={"status": "found", "result_count": 2},
            draft_count=5,
            draft_limit=20,
        )

        self.assertEqual(result["status"], "clear")
        self.assertEqual(result["novelty_type"], "new_sku")

    def test_unknown_or_full_draft_capacity_blocks(self) -> None:
        clear = {"status": "clear", "result_count": 0}
        self.assertEqual(
            evaluate_duplicate_gate(
                sku_search=clear,
                spu_search=clear,
                draft_count=None,
                draft_limit=20,
            )["status"],
            "blocked",
        )
        self.assertEqual(
            evaluate_duplicate_gate(
                sku_search=clear,
                spu_search=clear,
                draft_count=20,
                draft_limit=20,
            )["status"],
            "blocked",
        )


if __name__ == "__main__":
    unittest.main()
