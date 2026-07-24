# -*- coding: utf-8 -*-
"""
Title optimizer for improving existing titles.

Analyzes and provides optimization suggestions for product titles.
"""

import logging
from typing import List, Dict, Any, Optional, Tuple
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class OptimizationSuggestion:
    """Optimization suggestion for a title."""
    type: str  # "length", "position", "duplicate", "quality"
    priority: str  # "high", "medium", "low"
    description: str
    current_value: Any
    suggested_value: Any
    impact_score: float  # 0-1


@dataclass
class OptimizationResult:
    """Result of title optimization analysis."""
    original_title: str
    optimized_title: str
    original_score: float
    optimized_score: float
    suggestions: List[OptimizationSuggestion]
    improvements: Dict[str, float]


class TitleOptimizer:
    """Optimizer for product titles."""

    def __init__(self):
        """Initialize title optimizer."""
        from rpa.text_analyzer import TextAnalyzer
        from rpa.seo_rules import SEORules
        from rpa.score_calculator import ScoreCalculator

        self._analyzer = TextAnalyzer()
        self._seo_rules = SEORules()
        self._score_calculator = ScoreCalculator()

        logger.info("TitleOptimizer initialized")

    def analyze(self, title: str, category: Optional[str] = None) -> Dict[str, Any]:
        """
        Analyze a title and return analysis results.

        Args:
            title: Title to analyze
            category: Product category

        Returns:
            Analysis results dictionary
        """
        analysis = {
            "title": title,
            "length": len(title),
            "word_count": 0,
            "keywords": [],
            "duplicates": {},
            "score_breakdown": {},
            "issues": [],
        }

        # Segment text
        words = self._analyzer.segment(title)
        analysis["word_count"] = len(words)

        # Get unique keywords
        unique_words = list(set(words))
        analysis["keywords"] = unique_words

        # Check duplicates
        duplicates = self._analyzer.detect_duplicates(words)
        analysis["duplicates"] = duplicates

        if duplicates:
            analysis["issues"].append({
                "type": "duplicate",
                "description": f"发现重复词汇: {', '.join(duplicates.keys())}",
                "severity": "high",
            })

        # Check length
        from config.settings import get_settings
        settings = get_settings()
        title_config = settings.get_title_engine_config()
        max_length = title_config.get("max_title_length", 30)

        if len(title) > max_length:
            analysis["issues"].append({
                "type": "length",
                "description": f"标题长度 {len(title)} 超过限制 {max_length}",
                "severity": "high",
            })
        elif len(title) < title_config.get("min_length", 10):
            analysis["issues"].append({
                "type": "length",
                "description": f"标题长度 {len(title)} 过短",
                "severity": "medium",
            })

        # Calculate score
        score = self._score_calculator.calculate_score(title, None, category)
        analysis["score_breakdown"] = score.to_dict()
        analysis["score_metadata"] = self._score_calculator.get_score_metadata()

        # Check core word position
        if words:
            # Find potential core words (longer, meaningful words)
            core_candidates = [w for w in unique_words if len(w) >= 2]
            if core_candidates:
                core_word = core_candidates[0]
                if not self._seo_rules.validate_core_word_position(title, core_word, "first"):
                    analysis["issues"].append({
                        "type": "position",
                        "description": f"核心词 '{core_word}' 不在最佳位置",
                        "severity": "medium",
                    })

        return analysis

    def optimize(
        self,
        title: str,
        category: Optional[str] = None,
        core_keywords: Optional[List[str]] = None
    ) -> OptimizationResult:
        """
        Optimize a title and return suggestions.

        Args:
            title: Title to optimize
            category: Product category
            core_keywords: List of core keywords

        Returns:
            OptimizationResult with suggestions
        """
        # Get original score
        original_score = self._score_calculator.calculate_score(
            title,
            core_keywords[0] if core_keywords else None,
            category
        )

        # Generate suggestions
        suggestions = self._generate_suggestions(title, category, core_keywords)

        # Apply optimizations
        optimized_title = self._apply_optimizations(title, suggestions, core_keywords)

        # Get optimized score
        optimized_score = self._score_calculator.calculate_score(
            optimized_title,
            core_keywords[0] if core_keywords else None,
            category
        )

        # Calculate improvements
        improvements = {
            "total_score": optimized_score.total_score - original_score.total_score,
            "length_score": optimized_score.length_score - original_score.length_score,
            "distribution_score": optimized_score.distribution_score - original_score.distribution_score,
            "repetition_score": optimized_score.repetition_score - original_score.repetition_score,
            "quality_score": optimized_score.quality_score - original_score.quality_score,
        }

        return OptimizationResult(
            original_title=title,
            optimized_title=optimized_title,
            original_score=original_score.total_score,
            optimized_score=optimized_score.total_score,
            suggestions=suggestions,
            improvements=improvements,
        )

    def _generate_suggestions(
        self,
        title: str,
        category: Optional[str],
        core_keywords: Optional[List[str]]
    ) -> List[OptimizationSuggestion]:
        """
        Generate optimization suggestions.

        Args:
            title: Title to optimize
            category: Product category
            core_keywords: Core keywords

        Returns:
            List of OptimizationSuggestion objects
        """
        suggestions: List[OptimizationSuggestion] = []

        # Check length
        length_suggestion = self._check_length(title)
        if length_suggestion:
            suggestions.append(length_suggestion)

        # Check duplicates
        duplicate_suggestion = self._check_duplicates(title)
        if duplicate_suggestion:
            suggestions.append(duplicate_suggestion)

        # Check core word position
        if core_keywords:
            position_suggestion = self._check_core_word_position(title, core_keywords[0])
            if position_suggestion:
                suggestions.append(position_suggestion)

        # Check quality
        quality_suggestion = self._check_quality(title, category)
        if quality_suggestion:
            suggestions.append(quality_suggestion)

        # Sort by impact score
        suggestions.sort(key=lambda x: x.impact_score, reverse=True)

        return suggestions

    def _check_length(self, title: str) -> Optional[OptimizationSuggestion]:
        """Check title length and generate suggestion."""
        from config.settings import get_settings

        settings = get_settings()
        title_config = settings.get_title_engine_config()
        max_length = title_config.get("max_title_length", 30)
        min_length = title_config.get("min_length", 10)

        current_length = len(title)

        if current_length > max_length:
            return OptimizationSuggestion(
                type="length",
                priority="high",
                description=f"标题长度 {current_length} 超过限制 {max_length}，建议截断",
                current_value=current_length,
                suggested_value=max_length,
                impact_score=0.8,
            )
        elif current_length < min_length:
            return OptimizationSuggestion(
                type="length",
                priority="medium",
                description=f"标题长度 {current_length} 过短，建议补充内容",
                current_value=current_length,
                suggested_value=min_length,
                impact_score=0.5,
            )

        return None

    def _check_duplicates(self, title: str) -> Optional[OptimizationSuggestion]:
        """Check for duplicate words and generate suggestion."""
        words = self._analyzer.segment(title)
        duplicates = self._analyzer.detect_duplicates(words)

        if duplicates:
            duplicate_words = list(duplicates.keys())
            return OptimizationSuggestion(
                type="duplicate",
                priority="high",
                description=f"发现重复词汇: {', '.join(duplicate_words)}，建议删除或替换",
                current_value=duplicates,
                suggested_value="去重",
                impact_score=0.7,
            )

        return None

    def _check_core_word_position(self, title: str, core_word: str) -> Optional[OptimizationSuggestion]:
        """Check core word position and generate suggestion."""
        if core_word not in title:
            return OptimizationSuggestion(
                type="position",
                priority="high",
                description=f"核心词 '{core_word}' 不在标题中，建议添加",
                current_value="缺失",
                suggested_value="前10字",
                impact_score=0.9,
            )

        if not self._seo_rules.validate_core_word_position(title, core_word, "first"):
            # Find current position
            pos = title.find(core_word)
            return OptimizationSuggestion(
                type="position",
                priority="medium",
                description=f"核心词 '{core_word}' 在位置 {pos}，建议移至前10字",
                current_value=f"位置{pos}",
                suggested_value="前10字",
                impact_score=0.6,
            )

        return None

    def _check_quality(self, title: str, category: Optional[str]) -> Optional[OptimizationSuggestion]:
        """Check title quality and generate suggestion."""
        words = self._analyzer.segment(title)

        # Check for stop words
        stop_words = [w for w in words if self._analyzer.is_stop_word(w)]
        if len(stop_words) > 2:
            return OptimizationSuggestion(
                type="quality",
                priority="low",
                description=f"包含过多停用词: {', '.join(stop_words[:3])}",
                current_value=len(stop_words),
                suggested_value="0-1个",
                impact_score=0.3,
            )

        # Check for forbidden words
        forbidden = [w for w in words if self._seo_rules.is_forbidden_word(w)]
        if forbidden:
            return OptimizationSuggestion(
                type="quality",
                priority="medium",
                description=f"包含禁止词汇: {', '.join(forbidden)}",
                current_value=forbidden,
                suggested_value="删除",
                impact_score=0.5,
            )

        return None

    def _apply_optimizations(
        self,
        title: str,
        suggestions: List[OptimizationSuggestion],
        core_keywords: Optional[List[str]]
    ) -> str:
        """
        Apply optimization suggestions to title.

        Args:
            title: Original title
            suggestions: List of suggestions
            core_keywords: Core keywords

        Returns:
            Optimized title
        """
        optimized = title

        for suggestion in suggestions:
            if suggestion.type == "length":
                from config.settings import get_settings
                settings = get_settings()
                title_config = settings.get_title_engine_config()
                max_length = title_config.get("max_title_length", 30)
                optimized = optimized[:max_length]

            elif suggestion.type == "duplicate":
                words = self._analyzer.segment(optimized)
                seen: set = set()
                unique_words: List[str] = []
                for word in words:
                    if word not in seen:
                        seen.add(word)
                        unique_words.append(word)
                optimized = ''.join(unique_words)

            elif suggestion.type == "position" and core_keywords:
                core_word = core_keywords[0]
                if core_word not in optimized:
                    optimized = core_word + optimized
                    from config.settings import get_settings
                    settings = get_settings()
                    title_config = settings.get_title_engine_config()
                    max_length = title_config.get("max_title_length", 30)
                    optimized = optimized[:max_length]

            elif suggestion.type == "quality":
                words = self._analyzer.segment(optimized)
                filtered = [w for w in words if not self._seo_rules.is_forbidden_word(w)]
                optimized = ''.join(filtered)

        return optimized

    def batch_optimize(
        self,
        titles: List[str],
        category: Optional[str] = None,
        core_keywords: Optional[List[str]] = None
    ) -> List[OptimizationResult]:
        """
        Optimize multiple titles.

        Args:
            titles: List of titles to optimize
            category: Product category
            core_keywords: Core keywords

        Returns:
            List of OptimizationResult objects
        """
        results: List[OptimizationResult] = []

        for title in titles:
            result = self.optimize(title, category, core_keywords)
            results.append(result)

        return results

    def get_improvement_report(self, results: List[OptimizationResult]) -> Dict[str, Any]:
        """
        Generate improvement report from optimization results.

        Args:
            results: List of optimization results

        Returns:
            Improvement report dictionary
        """
        if not results:
            return {"total_titles": 0}

        total_improvement = sum(r.optimized_score - r.original_score for r in results)
        avg_improvement = total_improvement / len(results)

        suggestion_counts: Dict[str, int] = {}
        for result in results:
            for suggestion in result.suggestions:
                suggestion_counts[suggestion.type] = suggestion_counts.get(suggestion.type, 0) + 1

        return {
            "total_titles": len(results),
            "total_improvement": total_improvement,
            "average_improvement": avg_improvement,
            "suggestion_counts": suggestion_counts,
            "titles_improved": sum(1 for r in results if r.optimized_score > r.original_score),
        }
