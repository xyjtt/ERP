# -*- coding: utf-8 -*-
"""
SEO rules engine for title optimization.

Manages and validates SEO rules for 1688 e-commerce titles.
"""

import json
import logging
from typing import Dict, Any, List, Optional, Set
from pathlib import Path
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class TitleRule:
    """Title generation rule."""
    max_length: int = 30
    min_length: int = 10
    absolute_min_length: int = 2
    core_word_first_min: int = 0
    core_word_first_max: int = 10
    core_word_second_min: int = 20
    core_word_second_max: int = 25


@dataclass
class WordRule:
    """Word processing rule."""
    no_duplicate_roots: bool = True
    max_duplicate_count: int = 1
    forbidden_words: Set[str] = field(default_factory=set)
    priority_weights: Dict[str, float] = field(default_factory=dict)


@dataclass
class CategoryRule:
    """Category-specific rule."""
    name: str
    forbidden_suffixes: List[str] = field(default_factory=list)
    preferred_prefixes: List[str] = field(default_factory=list)
    max_brand_length: int = 6


class SEORules:
    """SEO rules manager for title optimization."""

    _instance = None
    _initialized = False

    def __new__(cls):
        """Singleton pattern for SEORules."""
        if cls._instance is None:
            cls._instance = super(SEORules, cls).__new__(cls)
        return cls._instance

    def __init__(self):
        """Initialize SEO rules."""
        if self._initialized:
            return

        self._initialized = True
        self.title_rule = TitleRule()
        self.word_rule = WordRule()
        self.category_rules: Dict[str, CategoryRule] = {}
        self.category_aliases: Dict[str, Set[str]] = {}
        self.seo_weights: Dict[str, float] = {}
        self.generation_params: Dict[str, Any] = {}

        self._load_rules()
        logger.info("SEORules initialized")

    def _load_rules(self) -> None:
        """Load rules from configuration."""
        config_dir = Path(__file__).parent.parent / "config"
        rules_file = config_dir / "seo_rules.json"

        try:
            if rules_file.exists():
                with open(rules_file, "r", encoding="utf-8") as f:
                    rules = json.load(f)

                self._parse_title_rules(rules.get("title_rules", {}))
                self._parse_word_rules(rules.get("word_rules", {}))
                self._parse_category_rules(rules.get("category_rules", {}))
                self._parse_category_aliases(rules.get("category_aliases", {}))

                self.seo_weights = rules.get("seo_weights", {})
                self.generation_params = rules.get("generation_params", {})

                logger.info("Loaded SEO rules from %s", rules_file)
            else:
                logger.warning("SEO rules file not found: %s", rules_file)
                self._load_default_rules()
        except Exception as e:
            logger.error("Failed to load SEO rules: %s", e)
            self._load_default_rules()

    def _parse_title_rules(self, rules: Dict[str, Any]) -> None:
        """Parse title rules from config."""
        if not rules:
            return

        self.title_rule.max_length = rules.get("max_length", 30)
        self.title_rule.min_length = rules.get("min_length", 10)
        self.title_rule.absolute_min_length = rules.get("absolute_min_length", 2)

        core_positions = rules.get("core_word_positions", {})
        if "first" in core_positions:
            self.title_rule.core_word_first_min = core_positions["first"].get("min", 0)
            self.title_rule.core_word_first_max = core_positions["first"].get("max", 10)
        if "second" in core_positions:
            self.title_rule.core_word_second_min = core_positions["second"].get("min", 20)
            self.title_rule.core_word_second_max = core_positions["second"].get("max", 25)

    def _parse_word_rules(self, rules: Dict[str, Any]) -> None:
        """Parse word rules from config."""
        if not rules:
            return

        self.word_rule.no_duplicate_roots = rules.get("no_duplicate_roots", True)
        self.word_rule.max_duplicate_count = rules.get("max_duplicate_count", 1)
        self.word_rule.forbidden_words = set(rules.get("forbidden_words", []))
        self.word_rule.priority_weights = rules.get("priority_weights", {
            "search_heat": 0.4,
            "inverse_competition": 0.3,
            "product_fit": 0.3,
        })

    def _parse_category_rules(self, rules: Dict[str, Any]) -> None:
        """Parse category rules from config."""
        if not rules:
            return

        for category_name, category_data in rules.items():
            self.category_rules[category_name] = CategoryRule(
                name=category_name,
                forbidden_suffixes=category_data.get("forbidden_suffixes", []),
                preferred_prefixes=category_data.get("preferred_prefixes", []),
                max_brand_length=category_data.get("max_brand_length", 6),
            )

    def _parse_category_aliases(self, aliases: Dict[str, Any]) -> None:
        """Parse category aliases from config."""
        self.category_aliases = {}
        for category_name, alias_values in aliases.items():
            if not isinstance(alias_values, list):
                continue
            self.category_aliases[category_name] = {
                str(alias).strip().lower()
                for alias in alias_values
                if str(alias).strip()
            }

    def _load_default_rules(self) -> None:
        """Load default rules when config file is not available."""
        self.title_rule = TitleRule(
            max_length=30,
            min_length=10,
            absolute_min_length=2,
            core_word_first_min=0,
            core_word_first_max=10,
            core_word_second_min=20,
            core_word_second_max=25,
        )

        self.word_rule = WordRule(
            no_duplicate_roots=True,
            max_duplicate_count=1,
            forbidden_words={"的", "了", "在", "是", "我", "有", "和", "就", "不"},
            priority_weights={
                "search_heat": 0.4,
                "inverse_competition": 0.3,
                "product_fit": 0.3,
            },
        )

        self.category_rules = {
            "general": CategoryRule(
                name="general",
                forbidden_suffixes=[],
                preferred_prefixes=["热销", "爆款", "新款", "正品"],
                max_brand_length=6,
            ),
        }

        self.category_aliases = {
            "furniture": {"家具", "家居", "沙发", "桌椅", "柜类", "床"},
            "electronics": {"电子", "电器", "数码", "家电"},
            "clothing": {"服装", "女装", "男装", "童装", "衣服"},
        }

        self.seo_weights = {
            "keyword_density": 0.25,
            "position_score": 0.30,
            "length_score": 0.20,
            "readability_score": 0.15,
            "uniqueness_score": 0.10,
        }

        self.generation_params = {
            "beam_width": 3,
            "max_iterations": 100,
            "temperature": 0.7,
            "min_score_threshold": 0.6,
        }

    def validate_title_length(self, title: str) -> bool:
        """
        Validate title length.

        Args:
            title: Title to validate

        Returns:
            True if valid, False otherwise
        """
        length = len(title)
        return self.title_rule.absolute_min_length <= length <= self.title_rule.max_length

    def validate_core_word_position(
        self,
        title: str,
        core_word: str,
        position: str = "first"
    ) -> bool:
        """
        Validate core word position in title.

        Args:
            title: Title to validate
            core_word: Core word to check
            position: Position to check ("first" or "second")

        Returns:
            True if valid, False otherwise
        """
        positions = [i for i, char in enumerate(title) if title[i:i+len(core_word)] == core_word]

        if not positions:
            return False

        if position == "first":
            return any(
                self.title_rule.core_word_first_min <= pos <= self.title_rule.core_word_first_max
                for pos in positions
            )
        elif position == "second":
            return any(
                self.title_rule.core_word_second_min <= pos <= self.title_rule.core_word_second_max
                for pos in positions
            )

        return False

    def check_duplicate_roots(self, roots: List[str]) -> Dict[str, int]:
        """
        Check for duplicate roots in word list.

        Args:
            roots: List of word roots

        Returns:
            Dictionary of roots that exceed max duplicate count
        """
        from collections import Counter
        root_counts = Counter(roots)

        duplicates = {}
        for root, count in root_counts.items():
            if root in self.word_rule.forbidden_words:
                duplicates[root] = count
            elif count > self.word_rule.max_duplicate_count:
                duplicates[root] = count

        return duplicates

    def get_category_rule(self, category: str) -> CategoryRule:
        """
        Get rule for specific category.

        Args:
            category: Category name

        Returns:
            CategoryRule for the category
        """
        normalized_category = str(category or "").strip().lower()

        # Try exact match first
        if normalized_category in self.category_rules:
            return self.category_rules[normalized_category]

        # Try configured aliases before partial matching.
        for cat_name, aliases in self.category_aliases.items():
            if normalized_category in aliases and cat_name in self.category_rules:
                return self.category_rules[cat_name]

        # Try partial match
        for cat_name, rule in self.category_rules.items():
            normalized_cat_name = cat_name.lower()
            if normalized_cat_name in normalized_category or normalized_category in normalized_cat_name:
                return rule

        # Return general rule as fallback
        return self.category_rules.get(
            "general",
            CategoryRule(name="general")
        )

    def calculate_word_score(self, keyword_data: Dict[str, Any]) -> float:
        """
        Calculate score for a keyword based on SEO weights.

        Args:
            keyword_data: Dictionary containing keyword metrics

        Returns:
            Calculated score
        """
        search_heat = keyword_data.get("search_heat", keyword_data.get("search_popularity"))
        competition = keyword_data.get("competition")
        product_fit = keyword_data.get("product_fit", keyword_data.get("relevance"))
        if search_heat is None or competition is None or product_fit is None:
            return 0.0

        weights = self.word_rule.priority_weights
        total_weight = sum(float(value) for value in weights.values())
        if total_weight <= 0:
            return 0.0

        normalized_weights = {
            key: float(value) / total_weight
            for key, value in weights.items()
        }
        normalized_search_heat = max(0.0, min(1.0, float(search_heat)))
        inverse_competition = 1.0 - max(0.0, min(1.0, float(competition)))
        normalized_product_fit = max(0.0, min(1.0, float(product_fit)))
        return (
            normalized_search_heat * normalized_weights.get("search_heat", 0.0)
            + inverse_competition * normalized_weights.get("inverse_competition", 0.0)
            + normalized_product_fit * normalized_weights.get("product_fit", 0.0)
        )

    def get_generation_params(self) -> Dict[str, Any]:
        """
        Get title generation parameters.

        Returns:
            Dictionary of generation parameters
        """
        return self.generation_params.copy()

    def get_seo_weights(self) -> Dict[str, float]:
        """
        Get SEO scoring weights.

        Returns:
            Dictionary of SEO weights
        """
        return self.seo_weights.copy()

    def is_forbidden_word(self, word: str) -> bool:
        """
        Check if a word is forbidden.

        Args:
            word: Word to check

        Returns:
            True if forbidden, False otherwise
        """
        return word in self.word_rule.forbidden_words

    def get_preferred_prefixes(self, category: str) -> List[str]:
        """
        Get preferred prefixes for a category.

        Args:
            category: Category name

        Returns:
            List of preferred prefixes
        """
        rule = self.get_category_rule(category)
        return rule.preferred_prefixes.copy()

    def reload_rules(self) -> None:
        """Reload rules from configuration file."""
        self._initialized = False
        self.__init__()
        logger.info("SEO rules reloaded")
