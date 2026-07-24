# -*- coding: utf-8 -*-
"""
Tests for title optimizer module.
"""

import unittest
import sys
import os

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from rpa.title_optimizer import TitleOptimizer, OptimizationSuggestion, OptimizationResult
from rpa.text_analyzer import TextAnalyzer
from rpa.seo_rules import SEORules
from rpa.score_calculator import ScoreCalculator


class TestTitleOptimizer(unittest.TestCase):
    """Test cases for TitleOptimizer."""

    def setUp(self):
        """Set up test fixtures."""
        self.optimizer = TitleOptimizer()
        self.analyzer = TextAnalyzer()

    def test_analyze_title(self):
        """Test title analysis."""
        title = "实木沙发现代简约客厅家具"

        analysis = self.optimizer.analyze(title, "家具")

        self.assertIn("title", analysis)
        self.assertIn("length", analysis)
        self.assertIn("word_count", analysis)
        self.assertIn("duplicates", analysis)
        self.assertIn("score_breakdown", analysis)
        self.assertIn("issues", analysis)

    def test_optimize_title(self):
        """Test title optimization."""
        title = "这是我的沙发很好用的沙发"

        result = self.optimizer.optimize(title, "家具", ["沙发"])

        self.assertIsInstance(result, OptimizationResult)
        self.assertEqual(result.original_title, title)
        self.assertGreater(len(result.optimized_title), 0)
        self.assertGreaterEqual(result.optimized_score, 0)
        self.assertLessEqual(result.optimized_score, 1)

    def test_optimization_suggestions(self):
        """Test that optimization provides suggestions."""
        title = "沙发"

        result = self.optimizer.optimize(title, "家具", ["沙发"])

        self.assertIsInstance(result.suggestions, list)
        # Should have suggestions for short title
        self.assertGreater(len(result.suggestions), 0)

    def test_detect_duplicates(self):
        """Test duplicate detection."""
        title = "沙发沙发舒适沙发"

        analysis = self.optimizer.analyze(title)

        # Should detect duplicates
        self.assertGreater(len(analysis["duplicates"]), 0)

    def test_batch_optimize(self):
        """Test batch optimization."""
        titles = [
            "实木沙发",
            "现代简约家具",
            "舒适座椅",
        ]

        results = self.optimizer.batch_optimize(titles, "家具", ["家具"])

        self.assertEqual(len(results), 3)
        for result in results:
            self.assertIsInstance(result, OptimizationResult)

    def test_improvement_report(self):
        """Test improvement report generation."""
        titles = ["沙发", "家具", "椅子"]

        results = self.optimizer.batch_optimize(titles, "家具")
        report = self.optimizer.get_improvement_report(results)

        self.assertIn("total_titles", report)
        self.assertIn("average_improvement", report)
        self.assertEqual(report["total_titles"], 3)

    def test_empty_title(self):
        """Test handling of empty title."""
        analysis = self.optimizer.analyze("")

        self.assertEqual(analysis["length"], 0)
        self.assertEqual(analysis["word_count"], 0)

    def test_long_title(self):
        """Test handling of long title."""
        # Create a title longer than 30 characters
        title = "这是一测试标题" * 5  # 35 characters

        result = self.optimizer.optimize(title, "测试", ["测试"])

        # Optimized title should be shorter
        self.assertLessEqual(len(result.optimized_title), 30)


class TestOptimizationSuggestion(unittest.TestCase):
    """Test cases for OptimizationSuggestion dataclass."""

    def test_create_suggestion(self):
        """Test creating OptimizationSuggestion."""
        suggestion = OptimizationSuggestion(
            type="length",
            priority="high",
            description="标题过长",
            current_value=35,
            suggested_value=30,
            impact_score=0.8,
        )

        self.assertEqual(suggestion.type, "length")
        self.assertEqual(suggestion.priority, "high")
        self.assertEqual(suggestion.impact_score, 0.8)


class TestOptimizationResult(unittest.TestCase):
    """Test cases for OptimizationResult dataclass."""

    def test_create_result(self):
        """Test creating OptimizationResult."""
        result = OptimizationResult(
            original_title="原标题",
            optimized_title="优化后标题",
            original_score=0.5,
            optimized_score=0.8,
            suggestions=[],
            improvements={"total_score": 0.3},
        )

        self.assertEqual(result.original_title, "原标题")
        self.assertEqual(result.optimized_title, "优化后标题")
        self.assertEqual(result.original_score, 0.5)
        self.assertEqual(result.optimized_score, 0.8)


class TestScoreCalculator(unittest.TestCase):
    """Test cases for ScoreCalculator."""

    def setUp(self):
        """Set up test fixtures."""
        self.calculator = ScoreCalculator()

    def test_calculate_score(self):
        """Test score calculation."""
        title = "实木沙发现代简约客厅家具"

        score = self.calculator.calculate_score(title, "沙发", "家具")

        self.assertGreaterEqual(score.total_score, 0)
        self.assertLessEqual(score.total_score, 1)
        self.assertGreaterEqual(score.length_score, 0)
        self.assertGreaterEqual(score.distribution_score, 0)
        self.assertGreaterEqual(score.repetition_score, 0)
        self.assertGreaterEqual(score.quality_score, 0)

    def test_score_interpretation(self):
        """Test score interpretation."""
        interpretation = self.calculator.get_score_interpretation(0.85)

        self.assertIsInstance(interpretation, str)
        self.assertGreater(len(interpretation), 0)

    def test_compare_titles(self):
        """Test title comparison."""
        title1 = "实木沙发"
        title2 = "舒适沙发座椅"

        comparison = self.calculator.compare_titles(title1, title2)

        self.assertIn("title1", comparison)
        self.assertIn("title2", comparison)
        self.assertIn("winner", comparison)
        self.assertIn("difference", comparison)


class TestTextAnalyzer(unittest.TestCase):
    """Test cases for TextAnalyzer."""

    def setUp(self):
        """Set up test fixtures."""
        self.analyzer = TextAnalyzer()

    def test_segment_chinese(self):
        """Test Chinese text segmentation."""
        text = "实木沙发"

        words = self.analyzer.segment(text)

        self.assertIsInstance(words, list)
        self.assertGreater(len(words), 0)

    def test_extract_roots(self):
        """Test root extraction."""
        words = ["实木", "沙发", "的"]

        roots = self.analyzer.extract_roots(words)

        self.assertIsInstance(roots, set)
        # Should not include stop words
        self.assertNotIn("的", roots)

    def test_detect_duplicates(self):
        """Test duplicate detection."""
        words = ["沙发", "舒适", "沙发", "沙发"]

        duplicates = self.analyzer.detect_duplicates(words)

        self.assertIn("沙发", duplicates)
        self.assertEqual(duplicates["沙发"], 3)

    def test_is_stop_word(self):
        """Test stop word detection."""
        self.assertTrue(self.analyzer.is_stop_word("的"))
        self.assertTrue(self.analyzer.is_stop_word("了"))
        self.assertFalse(self.analyzer.is_stop_word("沙发"))

    def test_get_text_statistics(self):
        """Test text statistics."""
        text = "测试标题123"

        stats = self.analyzer.get_text_statistics(text)

        self.assertIn("total_length", stats)
        self.assertIn("chinese_length", stats)
        self.assertIn("word_count", stats)


class TestSEORules(unittest.TestCase):
    """Test cases for SEORules."""

    def setUp(self):
        """Set up test fixtures."""
        self.rules = SEORules()

    def test_validate_title_length(self):
        """Test title length validation."""
        self.assertTrue(self.rules.validate_title_length("测试标题"))
        self.assertTrue(self.rules.validate_title_length("x" * 30))
        self.assertFalse(self.rules.validate_title_length("短"))
        self.assertFalse(self.rules.validate_title_length("x" * 31))

    def test_check_duplicate_roots(self):
        """Test duplicate root checking."""
        roots = ["沙发", "沙发", "舒适"]

        duplicates = self.rules.check_duplicate_roots(roots)

        self.assertIn("沙发", duplicates)

    def test_get_category_rule(self):
        """Test category rule retrieval."""
        rule = self.rules.get_category_rule("家具")

        self.assertIsNotNone(rule)
        self.assertIn("furniture", rule.name.lower() if hasattr(rule, 'name') else "")

    def test_is_forbidden_word(self):
        """Test forbidden word checking."""
        self.assertTrue(self.rules.is_forbidden_word("的"))
        self.assertFalse(self.rules.is_forbidden_word("沙发"))


if __name__ == "__main__":
    unittest.main()
