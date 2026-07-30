from __future__ import annotations

import unittest

from scripts.validate_real_title_samples import normalized_brand, sample_attributes


class ValidateRealTitleSamplesTests(unittest.TestCase):
    def test_product_dimensions_take_priority_over_package_dimensions(self) -> None:
        attributes = sample_attributes(
            {
                "name": "款式五 118.6/29.8/84 雪松木色",
                "properties_value": "雪松木色",
                "l": "131.000",
                "w": "37.000",
                "h": "16.000",
            }
        )

        self.assertEqual(attributes["尺寸"], "118.6x29.8x84")

    def test_internal_brand_labels_are_normalized(self) -> None:
        self.assertEqual(normalized_brand("自有品牌-亿家达"), "亿家达")
        self.assertEqual(normalized_brand("代理品牌-顾家"), "顾家")
        self.assertEqual(normalized_brand("其他品牌-普通大货"), "")


if __name__ == "__main__":
    unittest.main()
