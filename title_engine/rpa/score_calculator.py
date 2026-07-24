# -*- coding: utf-8 -*-
"""
Score calculator for title evaluation.

Provides 4-dimension scoring: length, distribution, repetition, quality.
"""

import logging
from typing import Dict, Any, List, Optional, Tuple
from dataclasses import dataclass

logger = logging.getLogger(__name__)

TITLE_SCORE_TYPE = "title_structure_quality"
TITLE_SCORE_VERSION = "title_structure_quality_v1"


@dataclass
class ScoreBreakdown:
    """Score breakdown result."""
    length_score: float = 0.0
    distribution_score: float = 0.0
    repetition_score: float = 0.0
    quality_score: float = 0.0
    total_score: float = 0.0

    def to_dict(self) -> Dict[str, float]:
        """Convert to dictionary."""
        return {
            "length_score": self.length_score,
            "distribution_score": self.distribution_score,
            "repetition_score": self.repetition_score,
            "quality_score": self.quality_score,
            "total_score": self.total_score,
        }


class ScoreCalculator:
    """Calculator for title scoring across 4 dimensions."""

    # Default weights for each dimension
    DEFAULT_WEIGHTS = {
        "length": 0.25,
        "distribution": 0.30,
        "repetition": 0.25,
        "quality": 0.20,
    }

    def __init__(self, weights: Optional[Dict[str, float]] = None):
        """
        Initialize score calculator.

        Args:
            weights: Optional custom weights for scoring dimensions
        """
        self._weights = weights or self.DEFAULT_WEIGHTS.copy()
        self._normalize_weights()
        logger.info("ScoreCalculator initialized")

    def _normalize_weights(self) -> None:
        """Normalize weights to sum to 1.0."""
        total = sum(self._weights.values())
        if total > 0:
            self._weights = {k: v / total for k, v in self._weights.items()}

    def get_score_metadata(self) -> Dict[str, Any]:
        """Return the public title structure score contract."""
        return {
            "score_type": TITLE_SCORE_TYPE,
            "score_version": TITLE_SCORE_VERSION,
            "weights": dict(self._weights),
            "dimensions": ["length", "distribution", "repetition", "quality"],
        }

    def calculate_score(
        self,
        title: str,
        core_word: Optional[str] = None,
        category: Optional[str] = None
    ) -> ScoreBreakdown:
        """
        Calculate total score for a title.

        Args:
            title: Title to score
            core_word: Core keyword in the title
            category: Product category

        Returns:
            ScoreBreakdown with all dimension scores
        """
        breakdown = ScoreBreakdown()

        # Calculate each dimension
        breakdown.length_score = self._calculate_length_score(title)
        breakdown.distribution_score = self._calculate_distribution_score(title, core_word)
        breakdown.repetition_score = self._calculate_repetition_score(title)
        breakdown.quality_score = self._calculate_quality_score(title, category)

        # Calculate total score
        breakdown.total_score = (
            breakdown.length_score * self._weights["length"] +
            breakdown.distribution_score * self._weights["distribution"] +
            breakdown.repetition_score * self._weights["repetition"] +
            breakdown.quality_score * self._weights["quality"]
        )

        return breakdown

    def _calculate_length_score(self, title: str) -> float:
        """
        Calculate score based on title length.

        Args:
            title: Title to evaluate

        Returns:
            Length score between 0 and 1
        """
        from config.settings import get_settings

        settings = get_settings()
        title_config = settings.get_title_engine_config()

        max_length = title_config.get("max_title_length", 30)

        length = len(title)

        if length == 0:
            return 0.0

        if length == max_length:
            return 1.0

        if length < max_length:
            # Gradual score decrease for shorter titles
            return length / max_length

        # Penalize titles that are too long
        over_length = length - max_length
        penalty = min(over_length * 0.1, 0.5)
        return max(0.5 - penalty, 0.0)

    def _calculate_distribution_score(
        self,
        title: str,
        core_word: Optional[str]
    ) -> float:
        """
        Calculate score based on keyword distribution.

        Args:
            title: Title to evaluate
            core_word: Core keyword

        Returns:
            Distribution score between 0 and 1
        """
        if not core_word or core_word not in title:
            return 0.5  # Neutral score if no core word

        from config.settings import get_settings

        settings = get_settings()
        title_config = settings.get_title_engine_config()

        # Find all positions of core word
        positions = []
        start = 0
        while True:
            pos = title.find(core_word, start)
            if pos == -1:
                break
            positions.append(pos)
            start = pos + 1

        if not positions:
            return 0.0

        # Check first occurrence position
        first_min = title_config.get("core_word_min_pos", 0)
        first_max = title_config.get("core_word_max_pos", 10)

        first_score = 0.0
        if first_min <= positions[0] <= first_max:
            first_score = 1.0
        elif positions[0] < first_min:
            first_score = 0.7
        else:
            first_score = max(0, 1.0 - (positions[0] - first_max) * 0.1)

        # Check second occurrence position
        second_min = title_config.get("second_core_word_min_pos", 20)
        second_max = title_config.get("second_core_word_max_pos", 25)

        second_score = 0.0
        if len(positions) > 1:
            if second_min <= positions[1] <= second_max:
                second_score = 1.0
            elif second_min <= positions[1]:
                second_score = 0.8
            else:
                second_score = 0.3
        else:
            # No second occurrence
            second_score = 0.3

        # Combine scores
        return (first_score * 0.6 + second_score * 0.4)

    def _calculate_repetition_score(self, title: str) -> float:
        """
        Calculate score based on word repetition.

        Args:
            title: Title to evaluate

        Returns:
            Repetition score between 0 and 1
        """
        from rpa.text_analyzer import TextAnalyzer

        analyzer = TextAnalyzer()
        words = analyzer.segment(title)
        duplicates = analyzer.detect_duplicates(words)

        if not duplicates:
            return 1.0  # No duplicates is perfect

        # Calculate penalty based on duplicate count
        total_duplicates = sum(duplicates.values())
        word_count = len(words)

        if word_count == 0:
            return 0.0

        duplicate_ratio = total_duplicates / word_count

        # Score decreases as duplicate ratio increases
        return max(1.0 - duplicate_ratio * 2, 0.0)

    def _calculate_quality_score(
        self,
        title: str,
        category: Optional[str]
    ) -> float:
        """
        Calculate score based on overall quality.

        Args:
            title: Title to evaluate
            category: Product category

        Returns:
            Quality score between 0 and 1
        """
        from rpa.text_analyzer import TextAnalyzer
        from rpa.seo_rules import SEORules

        analyzer = TextAnalyzer()
        seo_rules = SEORules()

        score = 0.0
        factors = 0

        # Factor 1: Contains meaningful words
        words = analyzer.segment(title)
        meaningful_words = [w for w in words if not analyzer.is_stop_word(w) and len(w) > 1]

        if meaningful_words:
            score += min(len(meaningful_words) / 5, 1.0)
        factors += 1

        # Factor 2: No forbidden words
        has_forbidden = any(seo_rules.is_forbidden_word(w) for w in words)
        if not has_forbidden:
            score += 1.0
        factors += 1

        # Factor 3: Category-appropriate (if category provided)
        if category:
            category_rule = seo_rules.get_category_rule(category)

            # Check for preferred prefixes
            has_preferred = any(
                title.startswith(prefix)
                for prefix in category_rule.preferred_prefixes
            )
            if has_preferred:
                score += 0.5

            # Check for forbidden suffixes
            has_forbidden_suffix = any(
                title.endswith(suffix)
                for suffix in category_rule.forbidden_suffixes
            )
            if not has_forbidden_suffix:
                score += 0.5

            factors += 1

        # Factor 4: Readability (no consecutive same characters)
        readability = 1.0
        for i in range(len(title) - 1):
            if title[i] == title[i + 1]:
                readability -= 0.1

        score += max(readability, 0.0)
        factors += 1

        # Calculate average
        return score / factors if factors > 0 else 0.0

    def get_score_interpretation(self, score: float) -> str:
        """
        Get human-readable interpretation of score.

        Args:
            score: Score between 0 and 1

        Returns:
            Score interpretation
        """
        if score >= 0.9:
            return "优秀 - 完全符合SEO标准"
        elif score >= 0.8:
            return "良好 - 基本符合SEO标准，有小幅优化空间"
        elif score >= 0.7:
            return "中等 - 需要优化，但基本可用"
        elif score >= 0.6:
            return "及格 - 需要较多优化"
        elif score >= 0.5:
            return "较差 - 需要大幅优化"
        else:
            return "不合格 - 建议重新生成"

    def compare_titles(
        self,
        title1: str,
        title2: str,
        core_word: Optional[str] = None,
        category: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Compare two titles and return analysis.

        Args:
            title1: First title
            title2: Second title
            core_word: Core keyword
            category: Product category

        Returns:
            Comparison result dictionary
        """
        score1 = self.calculate_score(title1, core_word, category)
        score2 = self.calculate_score(title2, core_word, category)

        return {
            "title1": title1,
            "title2": title2,
            "score1": score1.to_dict(),
            "score2": score2.to_dict(),
            "winner": "title1" if score1.total_score >= score2.total_score else "title2",
            "difference": abs(score1.total_score - score2.total_score),
        }
