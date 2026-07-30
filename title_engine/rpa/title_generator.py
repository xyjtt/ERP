# -*- coding: utf-8 -*-
"""
Title generator engine for 1688 e-commerce.

Generates SEO-optimized titles based on category, keywords, and product attributes.
"""

import logging
import hashlib
import json
import random
import re
from typing import List, Dict, Any, Optional, Set, Tuple
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class ProductInfo:
    """Product information for title generation."""
    category: str
    core_keywords: List[str]
    attributes: Dict[str, str] = field(default_factory=dict)
    selling_points: List[str] = field(default_factory=list)
    brand: Optional[str] = None
    model: Optional[str] = None
    product_name: Optional[str] = None
    keyword_candidates: List[str] = field(default_factory=list)


@dataclass
class GeneratedTitle:
    """Generated title result."""
    title: str
    score: float
    core_word_used: str
    word_count: int
    breakdown: Dict[str, float] = field(default_factory=dict)


class TitleGenerator:
    """Engine for generating SEO-optimized product titles."""

    def __init__(self):
        """Initialize title generator."""
        from rpa.text_analyzer import TextAnalyzer
        from rpa.seo_rules import SEORules
        from rpa.score_calculator import ScoreCalculator
        from rpa.keyword_manager import KeywordManager

        self._analyzer = TextAnalyzer()
        self._seo_rules = SEORules()
        self._score_calculator = ScoreCalculator()
        self._keyword_manager = KeywordManager()

        logger.info("TitleGenerator initialized")

    def generate(
        self,
        product_info: ProductInfo,
        num_titles: int = 5
    ) -> List[GeneratedTitle]:
        """
        Generate multiple title candidates.

        Args:
            product_info: Product information
            num_titles: Number of titles to generate

        Returns:
            List of GeneratedTitle objects sorted by score
        """
        gen_params = self._seo_rules.get_generation_params()
        max_iterations = max(
            int(gen_params.get("max_iterations", 100)),
            max(1, int(num_titles)) * 8,
        )
        seed_payload = json.dumps(
            {
                "category": product_info.category,
                "core_keywords": product_info.core_keywords,
                "keyword_candidates": product_info.keyword_candidates,
                "attributes": product_info.attributes,
                "selling_points": product_info.selling_points,
                "brand": product_info.brand,
                "model": product_info.model,
                "product_name": product_info.product_name,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
        seed = int(hashlib.sha256(seed_payload.encode("utf-8")).hexdigest()[:16], 16)
        rng = random.Random(seed)
        candidates: List[GeneratedTitle] = []

        for iteration in range(max_iterations):
            title = self._generate_single_title(product_info, rng=rng, iteration=iteration)
            if title:
                core_word = product_info.core_keywords[0] if product_info.core_keywords else ""
                score_result = self._score_calculator.calculate_score(
                    title,
                    core_word or None,
                    product_info.category
                )
                candidate = GeneratedTitle(
                    title=title,
                    score=score_result.total_score,
                    core_word_used=core_word,
                    word_count=len(title),
                    breakdown=score_result.to_dict()
                )
                candidates.append(candidate)

        seen_titles: Set[str] = set()
        unique_candidates: List[GeneratedTitle] = []
        for candidate in candidates:
            if candidate.title not in seen_titles:
                seen_titles.add(candidate.title)
                unique_candidates.append(candidate)

        unique_candidates.sort(
            key=lambda item: (item.score, len(item.title), item.title),
            reverse=True,
        )
        return unique_candidates[:num_titles]

    def generate_single(
        self,
        product_info: ProductInfo
    ) -> Optional[GeneratedTitle]:
        """
        Generate a single title.

        Args:
            product_info: Product information

        Returns:
            GeneratedTitle or None if generation fails
        """
        titles = self.generate(product_info, num_titles=1)
        return titles[0] if titles else None

    def _generate_single_title(
        self,
        product_info: ProductInfo,
        *,
        rng: random.Random | None = None,
        iteration: int = 0,
    ) -> Optional[str]:
        """
        Generate a single title candidate.

        Args:
            product_info: Product information

        Returns:
            Generated title string or None
        """
        from config.settings import get_settings

        settings = get_settings()
        title_config = settings.get_title_engine_config()
        max_length = title_config.get("max_title_length", 30)

        core_word = str(product_info.core_keywords[0] if product_info.core_keywords else "").strip()
        if not core_word:
            return None

        required = self._unique_fragments(
            [
                *product_info.core_keywords[1:3],
                *product_info.keyword_candidates[:3],
            ],
            exclude={core_word},
        )
        product_name_residual = self._residual_product_name(
            product_info.product_name,
            [
                core_word,
                *product_info.core_keywords[1:],
                *product_info.keyword_candidates,
                *product_info.attributes.values(),
                *product_info.selling_points,
                product_info.brand,
                product_info.model,
            ],
        )
        factual_fragments = self._expand_factual_fragments(
            [
                product_info.product_name,
                product_name_residual,
                *product_info.attributes.values(),
                *product_info.selling_points,
                product_info.brand,
                product_info.model,
            ]
        )
        optional = self._unique_fragments(
            [*product_info.keyword_candidates[3:20], *factual_fragments],
            exclude={core_word, *required},
        )
        local_rng = rng or random.Random(iteration)
        if required:
            offset = iteration % len(required)
            required = required[offset:] + required[:offset]
        local_rng.shuffle(optional)
        if len(optional) > 1:
            omitted_count = (iteration // len(optional)) % len(optional)
            optional = optional[: len(optional) - omitted_count]

        title_parts = [core_word]
        for fragment in [*required, *optional]:
            current = "".join(title_parts)
            if fragment in current:
                continue
            if len(current) + len(fragment) <= max_length:
                title_parts.append(fragment)

        title = "".join(title_parts)

        # Validate title
        if not self._validate_title(title, product_info):
            return None

        return title

    @staticmethod
    def _residual_product_name(value: Any, known_fragments: List[Any]) -> str:
        text = "".join(str(value or "").split())
        if not text:
            return ""
        fragments = {
            "".join(str(fragment or "").split())
            for fragment in known_fragments
            if "".join(str(fragment or "").split())
        }
        for fragment in sorted(fragments, key=len, reverse=True):
            text = text.replace(fragment, "")
        return text.strip("-_/+|,，;；")

    @staticmethod
    def _unique_fragments(
        values: List[Any],
        *,
        exclude: Set[str] | None = None,
    ) -> List[str]:
        excluded = {str(value).strip() for value in (exclude or set()) if str(value).strip()}
        seen = set(excluded)
        result = []
        for value in values:
            text = "".join(str(value or "").split())
            if not text or text in seen:
                continue
            seen.add(text)
            result.append(text)
        return result

    @staticmethod
    def _expand_factual_fragments(values: List[Any]) -> List[str]:
        result: List[str] = []
        seen: Set[str] = set()
        dimension_pattern = re.compile(
            r"\d+(?:\.\d+)?(?:\s*[/xX×*]\s*\d+(?:\.\d+)?){1,3}"
        )

        def add(value: Any) -> None:
            text = "".join(str(value or "").split()).strip("-_/+|,，;；")
            if not text or text in seen:
                return
            seen.add(text)
            result.append(text)

        for value in values:
            raw = str(value or "").strip()
            if not raw:
                continue
            dimensions = dimension_pattern.findall(raw)
            for dimension in dimensions:
                add(re.sub(r"\s*[/xX×*]\s*", "x", dimension))

            without_dimensions = dimension_pattern.sub(" ", raw)
            chunks = [
                chunk
                for chunk in re.split(r"[\s+＋;；,，|]+", without_dimensions)
                if chunk.strip()
            ]
            if len(chunks) <= 1:
                add(without_dimensions)
            else:
                for chunk in chunks:
                    add(chunk)

        return result

    def _find_optimal_insert_position(
        self,
        parts: List[str],
        word: str,
        max_length: int
    ) -> Optional[int]:
        """
        Find optimal position to insert a word.

        Args:
            parts: Current title parts
            word: Word to insert
            max_length: Maximum title length

        Returns:
            Optimal insertion index or None
        """
        from config.settings import get_settings

        settings = get_settings()
        title_config = settings.get_title_engine_config()

        second_min = title_config.get("second_core_word_min_pos", 20)
        second_max = title_config.get("second_core_word_max_pos", 25)

        # Calculate current positions
        current_text = ''.join(parts)

        # Try each position
        for i in range(len(parts) + 1):
            new_parts = parts[:i] + [word] + parts[i:]
            new_text = ''.join(new_parts)

            if len(new_text) > max_length:
                continue

            # Find position of inserted word
            pos = len(''.join(parts[:i]))

            if second_min <= pos <= second_max:
                return i

        # If no optimal position found, try to append
        current_length = len(''.join(parts))
        if current_length + len(word) <= max_length:
            return len(parts)

        return None

    def _validate_title(self, title: str, product_info: ProductInfo) -> bool:
        """
        Validate generated title.

        Args:
            title: Generated title
            product_info: Product information

        Returns:
            True if valid, False otherwise
        """
        # Check length
        if not self._seo_rules.validate_title_length(title):
            return False

        # Check for forbidden words
        words = self._analyzer.segment(title)
        for word in words:
            if self._seo_rules.is_forbidden_word(word) and (len(word) > 1 or title == word):
                return False

        # Check duplicate roots
        roots = self._analyzer.extract_roots(words)
        duplicates = self._seo_rules.check_duplicate_roots(list(roots))
        if duplicates:
            return False

        # Check core word position
        if product_info.core_keywords:
            core_word = product_info.core_keywords[0]
            if core_word in title:
                if not self._seo_rules.validate_core_word_position(title, core_word, "first"):
                    return False

        return True

    def optimize_title(
        self,
        title: str,
        product_info: ProductInfo
    ) -> Optional[str]:
        """
        Optimize an existing title.

        Args:
            title: Existing title to optimize
            product_info: Product information

        Returns:
            Optimized title or None
        """
        # Analyze current title
        words = self._analyzer.segment(title)
        duplicates = self._analyzer.detect_duplicates(words)

        # Remove duplicates
        optimized_words: List[str] = []
        seen: Set[str] = set()

        for word in words:
            if word not in seen or not self._seo_rules.word_rule.no_duplicate_roots:
                optimized_words.append(word)
                seen.add(word)

        optimized_title = ''.join(optimized_words)

        # Adjust length
        from config.settings import get_settings

        settings = get_settings()
        title_config = settings.get_title_engine_config()
        max_length = title_config.get("max_title_length", 30)

        if len(optimized_title) > max_length:
            optimized_title = optimized_title[:max_length]

        # Ensure core word is present
        if product_info.core_keywords:
            core_word = product_info.core_keywords[0]
            if core_word not in optimized_title:
                # Insert core word at beginning
                optimized_title = core_word + optimized_title
                if len(optimized_title) > max_length:
                    optimized_title = optimized_title[:max_length]

        return optimized_title if optimized_title != title else None
