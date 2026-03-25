from __future__ import annotations

import sys
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
RPA_ROOT = PROJECT_ROOT / "rpa"
if str(RPA_ROOT) not in sys.path:
    sys.path.insert(0, str(RPA_ROOT))

from jst_attribute_mapper import (
    build_platform_attributes,
    build_style_name,
    build_variant_title,
    compute_price_value,
    infer_platform_category_key,
    parse_properties_value,
)


class JstAttributeMapperTests(unittest.TestCase):
    def build_row(self) -> dict[str, object]:
        return {
            "name": "款式五 120.6/29.8/92 经典北美黑胡桃色",
            "category": "住宅家具 - 鞋柜",
            "boardType": "正常板材",
            "properties_value": "经典北美黑胡桃色;120.6/29.8/92",
            "cost_price": "150.00",
            "brand": "大货",
        }

    def test_parse_properties_value_extracts_color_and_size(self) -> None:
        parsed = parse_properties_value("珍珠白色;120/24/80")
        self.assertEqual(parsed["color"], "珍珠白色")
        self.assertEqual(parsed["size"], "120/24/80")

    def test_infer_platform_category_key_uses_simple_heuristics(self) -> None:
        self.assertEqual(infer_platform_category_key("住宅家具 - 床头柜"), "bedside_table")
        self.assertEqual(infer_platform_category_key("住宅家具 - 鞋柜"), "shoe_cabinet")
        self.assertEqual(infer_platform_category_key("住宅家具 - 书柜"), "bookshelf")
        self.assertEqual(infer_platform_category_key("住宅家具 - 电脑桌"), "computer_desk")
        self.assertEqual(infer_platform_category_key("住宅家具 - 角几"), "corner_table")
        self.assertEqual(infer_platform_category_key("住宅家具 - 双人床"), "bedroom")

    def test_build_platform_attributes_merges_board_type_and_properties(self) -> None:
        payload = build_platform_attributes(self.build_row())
        self.assertEqual(payload["材质"], "人造板")
        self.assertEqual(payload["颜色"], "经典北美黑胡桃色")
        self.assertEqual(payload["尺寸"], "120.6/29.8/92")

    def test_compute_price_value_uses_cost_div_0_75_rule(self) -> None:
        self.assertEqual(compute_price_value(self.build_row()), "200")

    def test_build_titles_are_non_empty(self) -> None:
        row = self.build_row()
        self.assertTrue(build_variant_title(row))
        self.assertTrue(build_style_name(row))


if __name__ == "__main__":
    unittest.main()
