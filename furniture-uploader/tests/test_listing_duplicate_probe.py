from __future__ import annotations

import sys
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
RPA_ROOT = PROJECT_ROOT / "rpa"
if str(RPA_ROOT) not in sys.path:
    sys.path.insert(0, str(RPA_ROOT))

from listing_duplicate_probe import (
    evaluate_duplicate_gate,
    parse_draft_count,
    summarize_search_result,
)


class ListingDuplicateProbeTests(unittest.TestCase):
    def test_parse_draft_count(self) -> None:
        self.assertEqual(parse_draft_count("草稿箱 (3)"), 3)
        self.assertEqual(parse_draft_count("草稿（19）"), 19)
        self.assertIsNone(parse_draft_count("全部商品"))

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
