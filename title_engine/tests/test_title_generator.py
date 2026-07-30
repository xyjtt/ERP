# -*- coding: utf-8 -*-
"""
Tests for title generator module.
"""

import unittest
import sys
import os

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from rpa.title_generator import TitleGenerator, ProductInfo, GeneratedTitle
from rpa.text_analyzer import TextAnalyzer
from rpa.seo_rules import SEORules
from rpa.score_calculator import ScoreCalculator


class TestTitleGenerator(unittest.TestCase):
    """Test cases for TitleGenerator."""

    def setUp(self):
        """Set up test fixtures."""
        self.generator = TitleGenerator()
        self.analyzer = TextAnalyzer()
        self.seo_rules = SEORules()

    def test_generate_single_title(self):
        """Test generating a single title."""
        product_info = ProductInfo(
            category="家具",
            core_keywords=["实木", "沙发"],
            attributes={"材质": "实木", "风格": "现代"},
            selling_points=["舒适", "耐用"],
            brand="家居优选",
        )

        result = self.generator.generate_single(product_info)

        self.assertIsNotNone(result)
        self.assertIsInstance(result, GeneratedTitle)
        self.assertGreater(len(result.title), 0)
        self.assertLessEqual(len(result.title), 30)
        self.assertGreaterEqual(result.score, 0)
        self.assertLessEqual(result.score, 1)

    def test_generate_multiple_titles(self):
        """Test generating multiple titles."""
        product_info = ProductInfo(
            category="电子产品",
            core_keywords=["蓝牙", "耳机"],
            attributes={"类型": "无线"},
            selling_points=["降噪", "长续航"],
        )

        results = self.generator.generate(product_info, num_titles=3)

        self.assertIsInstance(results, list)
        self.assertLessEqual(len(results), 3)

        for result in results:
            self.assertIsInstance(result, GeneratedTitle)
            self.assertGreater(len(result.title), 0)
            self.assertLessEqual(len(result.title), 30)

    def test_title_length_constraint(self):
        """Test that generated titles respect length constraint."""
        product_info = ProductInfo(
            category="服装",
            core_keywords=["连衣裙"],
            attributes={"季节": "夏季"},
            selling_points=["透气", "时尚", "新款"],
        )

        results = self.generator.generate(product_info, num_titles=10)

        for result in results:
            self.assertLessEqual(len(result.title), 30)

    def test_core_word_in_title(self):
        """Test that core word appears in generated title."""
        product_info = ProductInfo(
            category="家居",
            core_keywords=["床", "双人床"],
            attributes={"尺寸": "1.8米"},
            selling_points=["舒适"],
        )

        results = self.generator.generate(product_info, num_titles=5)

        # At least one title should contain the core word
        has_core_word = any(
            "床" in result.title
            for result in results
        )
        self.assertTrue(has_core_word)

    def test_furniture_prefix_requires_declared_product_evidence(self):
        product_info = ProductInfo(
            category="柜类",
            core_keywords=["餐边柜"],
            attributes={"颜色": "经典北美黑胡桃色", "尺寸": "50/29.8/80.4"},
            selling_points=["客厅收纳"],
        )

        results = self.generator.generate(product_info, num_titles=20)
        invented_prefixes = {"实木", "布艺", "皮艺", "铁艺", "竹艺"}

        self.assertTrue(results)
        self.assertTrue(all("餐边柜" in result.title for result in results))
        self.assertTrue(
            all(not any(prefix in result.title for prefix in invented_prefixes) for result in results)
        )

    def test_ranked_keyword_pool_can_generate_thirty_unique_candidates(self):
        product_info = ProductInfo(
            category="柜类",
            core_keywords=["床头柜"],
            product_name="北美黑胡桃木床头柜",
            keyword_candidates=[
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
            ],
            attributes={"材质": "黑胡桃木", "风格": "现代简约", "颜色": "胡桃色"},
            selling_points=["卧室收纳", "小户型适用"],
        )

        first = self.generator.generate(product_info, num_titles=30)
        second = self.generator.generate(product_info, num_titles=30)

        self.assertEqual(len(first), 30)
        self.assertEqual([item.title for item in first], [item.title for item in second])
        self.assertEqual(len({item.title for item in first}), 30)
        self.assertTrue(all(item.title.startswith("床头柜") for item in first))
        self.assertTrue(all(10 <= len(item.title) <= 30 for item in first))
        self.assertTrue(all(item.word_count == len(item.title) for item in first))

    def test_narrow_factual_pool_can_generate_thirty_unique_candidates(self):
        product_info = ProductInfo(
            category="柜类",
            core_keywords=["床头柜"],
            product_name="款式一 50/40/49 黑色台面+胡桃",
            keyword_candidates=["床头柜"],
            attributes={"颜色": "黑色台面+胡桃", "尺寸": "50/40/49"},
            selling_points=["卧室收纳"],
        )

        results = self.generator.generate(product_info, num_titles=30)

        self.assertEqual(len(results), 30)
        self.assertEqual(len({item.title for item in results}), 30)
        self.assertTrue(all(item.title.startswith("床头柜") for item in results))
        self.assertTrue(all("沙发" not in item.title and "桌子" not in item.title for item in results))

    def test_real_shoe_cabinet_factual_pools_generate_thirty_candidates(self):
        products = [
            ProductInfo(
                category="住宅家具 - 鞋柜",
                core_keywords=["鞋柜"],
                product_name="款式二 91/34/96.5 莫兰橡木色+大象灰色抽面+大象灰色门板",
                keyword_candidates=["鞋柜"],
                attributes={
                    "规格": "莫兰橡木色+大象灰色抽面+大象灰色门板;91/34/96.5",
                    "尺寸": "91x34x96.5",
                },
            ),
            ProductInfo(
                category="住宅家具 - 鞋柜",
                core_keywords=["鞋柜"],
                product_name=(
                    "款式五 118.6/29.8/84 屿山雪松木色+云溪雪松白色抽面+"
                    "云溪雪松白色柜门+原木色小圆拉手+原木色PVC木腿"
                ),
                keyword_candidates=["鞋柜"],
                attributes={
                    "规格": (
                        "屿山雪松木色+云溪雪松白色抽面+云溪雪松白色柜门+"
                        "原木色小圆拉手+原木色PVC木腿"
                    ),
                    "尺寸": "118.6x29.8x84",
                },
                brand="亿家达",
            ),
            ProductInfo(
                category="住宅家具 - 鞋柜",
                core_keywords=["鞋柜"],
                product_name=(
                    "款式一 118.8/30/98 屿山雪松木色＋云溪雪松白色抽面+"
                    "云溪雪松白色柜门"
                ),
                keyword_candidates=["鞋柜"],
                attributes={
                    "规格": "屿山雪松木色+云溪雪松白色抽面+云溪雪松白色柜门",
                    "尺寸": "118.8x30x98",
                },
            ),
        ]

        for product in products:
            with self.subTest(product_name=product.product_name):
                results = self.generator.generate(product, num_titles=30)
                self.assertEqual(len(results), 30)
                self.assertEqual(len({item.title for item in results}), 30)
                self.assertTrue(all(item.title.startswith("鞋柜") for item in results))
                self.assertTrue(all(10 <= len(item.title) <= 30 for item in results))
                self.assertTrue(
                    all("普通大货" not in item.title for item in results)
                )

    def test_optimize_title(self):
        """Test title optimization."""
        original_title = "这是我的沙发很好用的沙发"
        product_info = ProductInfo(
            category="家具",
            core_keywords=["沙发"],
        )

        optimized = self.generator.optimize_title(original_title, product_info)

        self.assertIsNotNone(optimized)
        self.assertLessEqual(len(optimized), 30)

    def test_empty_product_info(self):
        """Test handling of empty product info."""
        product_info = ProductInfo(
            category="",
            core_keywords=[],
        )

        # Should not crash
        results = self.generator.generate(product_info, num_titles=1)
        self.assertIsInstance(results, list)


class TestProductInfo(unittest.TestCase):
    """Test cases for ProductInfo dataclass."""

    def test_create_product_info(self):
        """Test creating ProductInfo."""
        info = ProductInfo(
            category="测试类别",
            core_keywords=["测试词"],
        )

        self.assertEqual(info.category, "测试类别")
        self.assertEqual(info.core_keywords, ["测试词"])
        self.assertEqual(info.attributes, {})
        self.assertEqual(info.selling_points, [])

    def test_product_info_with_attributes(self):
        """Test ProductInfo with attributes."""
        info = ProductInfo(
            category="测试",
            core_keywords=["词1", "词2"],
            attributes={"颜色": "红色", "尺寸": "大"},
            selling_points=["卖点1"],
            brand="品牌",
            model="型号",
        )

        self.assertEqual(len(info.attributes), 2)
        self.assertEqual(info.brand, "品牌")
        self.assertEqual(info.model, "型号")


class TestGeneratedTitle(unittest.TestCase):
    """Test cases for GeneratedTitle dataclass."""

    def test_create_generated_title(self):
        """Test creating GeneratedTitle."""
        title = GeneratedTitle(
            title="测试标题",
            score=0.85,
            core_word_used="测试",
            word_count=2,
            breakdown={"length_score": 0.9},
        )

        self.assertEqual(title.title, "测试标题")
        self.assertEqual(title.score, 0.85)
        self.assertEqual(title.core_word_used, "测试")
        self.assertEqual(title.word_count, 2)
        self.assertIn("length_score", title.breakdown)


if __name__ == "__main__":
    unittest.main()
