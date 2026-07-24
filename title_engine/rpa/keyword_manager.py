# -*- coding: utf-8 -*-
"""
Keyword manager for handling keyword data.

Provides keyword querying, sorting, filtering, and caching.
"""

import logging
from typing import List, Dict, Any, Optional, Tuple
from datetime import datetime, timedelta
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

KEYWORD_SCORE_VERSION = "keyword_score_40_30_30_v1"
KEYWORD_SCORE_TYPE = "keyword_composite"
KEYWORD_SCORE_NORMALIZATION = "candidate_set_min_max"
DEFAULT_KEYWORD_SCORE_WEIGHTS = {
    "search_heat": 0.4,
    "inverse_competition": 0.3,
    "product_fit": 0.3,
}


def _optional_float(value: Any) -> Optional[float]:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _clamp_unit(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


@dataclass
class KeywordScoreBreakdown:
    """Normalized 40/30/30 keyword score."""

    search_heat: Optional[float] = None
    inverse_competition: Optional[float] = None
    product_fit: Optional[float] = None
    total_score: Optional[float] = None
    status: str = "incomplete"
    missing_metrics: List[str] = field(default_factory=list)
    score_version: str = KEYWORD_SCORE_VERSION
    weights: Dict[str, float] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "search_heat": self.search_heat,
            "inverse_competition": self.inverse_competition,
            "product_fit": self.product_fit,
            "total_score": self.total_score,
            "status": self.status,
            "missing_metrics": list(self.missing_metrics),
            "score_version": self.score_version,
            "weights": dict(self.weights),
        }


@dataclass
class KeywordData:
    """Keyword data structure."""
    word: str
    category: str
    search_popularity: Optional[float] = None
    transaction_index: Optional[float] = None
    competition: Optional[float] = None
    relevance: Optional[float] = None
    product_fit: Optional[float] = None
    source: str = ""
    source_stat_date: Any = None
    source_record_key: str = ""
    source_role: str = "keyword_candidate"
    freshness_status: str = "unknown"
    source_metadata: Dict[str, Any] = field(default_factory=dict)
    last_updated: Optional[datetime] = None
    keyword_score: Optional[float] = None
    score_status: str = "not_scored"
    score_breakdown: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            "word": self.word,
            "category": self.category,
            "search_popularity": self.search_popularity,
            "transaction_index": self.transaction_index,
            "competition": self.competition,
            "relevance": self.relevance,
            "product_fit": self.product_fit,
            "source": self.source,
            "source_record_key": self.source_record_key,
            "source_role": self.source_role,
            "freshness_status": self.freshness_status,
            "source_metadata": dict(self.source_metadata),
            "source_stat_date": (
                self.source_stat_date.isoformat()
                if hasattr(self.source_stat_date, "isoformat")
                else self.source_stat_date
            ),
            "last_updated": self.last_updated.isoformat() if self.last_updated else None,
            "keyword_score": self.keyword_score,
            "score_type": KEYWORD_SCORE_TYPE,
            "score_version": self.score_breakdown.get("score_version", KEYWORD_SCORE_VERSION),
            "score_status": self.score_status,
            "score_breakdown": dict(self.score_breakdown),
        }


class KeywordManager:
    """Manager for keyword operations with caching."""

    def __init__(self, db_manager=None):
        """
        Initialize keyword manager.

        Args:
            db_manager: Database manager instance for data access
        """
        self._db_manager = db_manager
        self._cache: Dict[str, List[KeywordData]] = {}
        self._cache_expiry: Dict[str, datetime] = {}
        self._cache_size = 1000
        self._cache_ttl = 3600  # seconds

        logger.info("KeywordManager initialized")

    def get_keywords(
        self,
        category: str,
        limit: int = 100,
        use_cache: bool = True
    ) -> List[KeywordData]:
        """
        Get keywords for a category.

        Args:
            category: Category name
            limit: Maximum number of keywords to return
            use_cache: Whether to use cached data

        Returns:
            List of KeywordData objects
        """
        cache_key = f"category:{category}"

        # Check cache
        if use_cache and cache_key in self._cache:
            if self._is_cache_valid(cache_key):
                logger.debug("Cache hit for category: %s", category)
                return self._cache[cache_key][:limit]

        # Fetch from database
        keywords = self._fetch_keywords_from_db(category, limit)

        # Update cache
        if use_cache and keywords:
            self._update_cache(cache_key, keywords)

        return keywords

    def search_keywords(
        self,
        query: str,
        category: Optional[str] = None,
        limit: int = 50
    ) -> List[KeywordData]:
        """
        Search keywords by query.

        Args:
            query: Search query
            category: Optional category filter
            limit: Maximum results

        Returns:
            List of matching KeywordData objects
        """
        all_keywords = self._fetch_keywords_from_db(category, limit * 10)

        # Filter by query
        matching = [
            kw for kw in all_keywords
            if query.lower() in kw.word.lower()
        ]

        return matching[:limit]

    def get_top_keywords(
        self,
        category: str,
        metric: str = "search_popularity",
        limit: int = 10
    ) -> List[KeywordData]:
        """
        Get top keywords by metric.

        Args:
            category: Category name
            metric: Metric to sort by
            limit: Number of results

        Returns:
            List of top KeywordData objects
        """
        keywords = self.get_keywords(category, limit=1000)

        # Sort by specified metric
        sorted_keywords = sorted(
            keywords,
            key=lambda x: _optional_float(getattr(x, metric, None)) or 0.0,
            reverse=True
        )

        return sorted_keywords[:limit]

    def calculate_composite_score(self, keyword: KeywordData) -> float:
        """
        Calculate composite score for a keyword.

        Args:
            keyword: KeywordData object

        Returns:
            Composite score
        """
        breakdown = self._score_normalized_keyword(
            search_heat=_optional_float(keyword.search_popularity),
            normalized_competition=_optional_float(keyword.competition),
            product_fit=self._resolve_product_fit(keyword),
        )
        return breakdown.total_score or 0.0

    def rank_keywords(
        self,
        keywords: List[KeywordData],
        limit: int = 10
    ) -> List[Tuple[KeywordData, float]]:
        """
        Rank keywords by composite score.

        Args:
            keywords: List of KeywordData objects
            limit: Number of results

        Returns:
            List of (KeywordData, score) tuples
        """
        raw_search_heat = [_optional_float(kw.search_popularity) for kw in keywords]
        raw_competition = [_optional_float(kw.competition) for kw in keywords]
        raw_product_fit = [self._resolve_product_fit(kw) for kw in keywords]
        eligible = [
            keyword.freshness_status not in {"stale", "future", "missing"}
            and raw_search_heat[index] is not None
            and raw_competition[index] is not None
            and raw_product_fit[index] is not None
            for index, keyword in enumerate(keywords)
        ]
        search_heat = self._normalize_metric([
            value if eligible[index] else None
            for index, value in enumerate(raw_search_heat)
        ])
        competition = self._normalize_metric([
            value if eligible[index] else None
            for index, value in enumerate(raw_competition)
        ])
        product_fit = self._normalize_metric([
            value if eligible[index] else None
            for index, value in enumerate(raw_product_fit)
        ])

        scored: List[Tuple[KeywordData, float]] = []
        for index, keyword in enumerate(keywords):
            if keyword.freshness_status in {"stale", "future", "missing"}:
                breakdown = KeywordScoreBreakdown(
                    missing_metrics=["freshness"],
                    weights=self._get_score_weights(),
                )
            elif not eligible[index]:
                missing = []
                if raw_search_heat[index] is None:
                    missing.append("search_heat")
                if raw_competition[index] is None:
                    missing.append("competition")
                if raw_product_fit[index] is None:
                    missing.append("product_fit")
                breakdown = KeywordScoreBreakdown(
                    missing_metrics=missing,
                    weights=self._get_score_weights(),
                )
            else:
                breakdown = self._score_normalized_keyword(
                    search_heat=search_heat[index],
                    normalized_competition=competition[index],
                    product_fit=product_fit[index],
                )
            keyword.keyword_score = breakdown.total_score
            keyword.score_status = breakdown.status
            keyword.score_breakdown = breakdown.to_dict()
            scored.append((keyword, breakdown.total_score if breakdown.total_score is not None else -1.0))

        # Python's sort is stable, so exact ties retain source order.
        scored.sort(
            key=lambda item: (
                item[0].score_status == "complete",
                item[1],
            ),
            reverse=True,
        )
        return scored[:limit]

    def get_score_metadata(self) -> Dict[str, Any]:
        """Return the public keyword score contract."""
        return {
            "score_type": KEYWORD_SCORE_TYPE,
            "score_version": KEYWORD_SCORE_VERSION,
            "normalization": KEYWORD_SCORE_NORMALIZATION,
            "normalization_scope": "fresh_complete_candidates_only",
            "weights": self._get_score_weights(),
            "required_metrics": ["search_heat", "competition", "product_fit"],
            "incomplete_policy": "rank_last_without_total_score",
            "product_fit_fallback": "relevance",
            "hsds_policy": "independent_candidate_and_heat_evidence_only",
        }

    @staticmethod
    def _normalize_metric(values: List[Optional[float]]) -> List[Optional[float]]:
        parsed = [_optional_float(value) for value in values]
        present = [value for value in parsed if value is not None]
        if not present:
            return [None for _ in parsed]

        minimum = min(present)
        maximum = max(present)
        if maximum == minimum:
            return [1.0 if value is not None else None for value in parsed]

        span = maximum - minimum
        return [
            (value - minimum) / span if value is not None else None
            for value in parsed
        ]

    @staticmethod
    def _resolve_product_fit(keyword: KeywordData) -> Optional[float]:
        explicit = _optional_float(keyword.product_fit)
        if explicit is not None:
            return explicit
        return _optional_float(keyword.relevance)

    def _score_normalized_keyword(
        self,
        *,
        search_heat: Optional[float],
        normalized_competition: Optional[float],
        product_fit: Optional[float],
    ) -> KeywordScoreBreakdown:
        missing = []
        if search_heat is None:
            missing.append("search_heat")
        if normalized_competition is None:
            missing.append("competition")
        if product_fit is None:
            missing.append("product_fit")
        weights = self._get_score_weights()
        if missing:
            return KeywordScoreBreakdown(missing_metrics=missing, weights=weights)

        normalized_search_heat = _clamp_unit(search_heat)
        inverse_competition = 1.0 - _clamp_unit(normalized_competition)
        normalized_product_fit = _clamp_unit(product_fit)
        total_score = (
            normalized_search_heat * weights["search_heat"]
            + inverse_competition * weights["inverse_competition"]
            + normalized_product_fit * weights["product_fit"]
        )
        return KeywordScoreBreakdown(
            search_heat=normalized_search_heat,
            inverse_competition=inverse_competition,
            product_fit=normalized_product_fit,
            total_score=total_score,
            status="complete",
            weights=weights,
        )

    @staticmethod
    def _get_score_weights() -> Dict[str, float]:
        from config.settings import get_settings

        configured = (
            get_settings()
            .get_seo_rules()
            .get("word_rules", {})
            .get("priority_weights", DEFAULT_KEYWORD_SCORE_WEIGHTS)
        )
        weights = {}
        for key, default in DEFAULT_KEYWORD_SCORE_WEIGHTS.items():
            try:
                weights[key] = max(0.0, float(configured.get(key, default)))
            except (TypeError, ValueError):
                weights[key] = default
        total_weight = sum(weights.values())
        if total_weight <= 0:
            return DEFAULT_KEYWORD_SCORE_WEIGHTS.copy()
        return {key: value / total_weight for key, value in weights.items()}

    def filter_keywords(
        self,
        keywords: List[KeywordData],
        min_search_popularity: float = 0,
        min_transaction_index: float = 0,
        max_competition: float = 1.0
    ) -> List[KeywordData]:
        """
        Filter keywords by criteria.

        Args:
            keywords: List of KeywordData objects
            min_search_popularity: Minimum search popularity
            min_transaction_index: Minimum transaction index
            max_competition: Maximum competition

        Returns:
            Filtered list of KeywordData objects
        """
        filtered = []

        for kw in keywords:
            search_popularity = _optional_float(kw.search_popularity)
            transaction_index = _optional_float(kw.transaction_index)
            competition = _optional_float(kw.competition)
            if (search_popularity is not None and
                transaction_index is not None and
                competition is not None and
                search_popularity >= min_search_popularity and
                transaction_index >= min_transaction_index and
                competition <= max_competition):
                filtered.append(kw)

        return filtered

    def _fetch_keywords_from_db(
        self,
        category: Optional[str],
        limit: int
    ) -> List[KeywordData]:
        """
        Fetch keywords from database.

        Args:
            category: Category filter
            limit: Maximum results

        Returns:
            List of KeywordData objects
        """
        if self._db_manager is None:
            logger.warning("No database manager, returning empty keywords")
            return []

        try:
            if category:
                rows = self._db_manager.get_keywords_by_category(category, limit)
            else:
                rows = self._db_manager.search_keywords("", None, limit)
            return [self._row_to_keyword(row) for row in rows]
        except Exception as e:
            logger.error("Failed to fetch keywords: %s", e)
            return []

    def _row_to_keyword(self, row: Dict[str, Any]) -> KeywordData:
        """Convert a database row to KeywordData."""
        raw_updated = row.get("last_updated") or row.get("updated_at")
        last_updated = raw_updated if isinstance(raw_updated, datetime) else None
        return KeywordData(
            word=str(row.get("word") or row.get("keyword") or ""),
            category=str(row.get("category") or ""),
            search_popularity=_optional_float(row.get("search_popularity")),
            transaction_index=_optional_float(row.get("transaction_index")),
            competition=_optional_float(row.get("competition")),
            relevance=_optional_float(row.get("relevance")),
            product_fit=_optional_float(row.get("product_fit")),
            source=str(row.get("source") or ""),
            source_stat_date=row.get("source_stat_date"),
            source_record_key=str(row.get("source_record_key") or ""),
            source_role=str(row.get("source_role") or "keyword_candidate"),
            freshness_status=str(row.get("freshness_status") or "unknown"),
            source_metadata=dict(row.get("source_metadata") or {}),
            last_updated=last_updated,
        )

    def _is_cache_valid(self, key: str) -> bool:
        """Check if cache entry is still valid."""
        if key not in self._cache_expiry:
            return False

        return datetime.now() < self._cache_expiry[key]

    def _update_cache(self, key: str, data: List[KeywordData]) -> None:
        """Update cache with new data."""
        # Remove oldest entries if cache is full
        if len(self._cache) >= self._cache_size:
            oldest_key = min(
                self._cache_expiry.keys(),
                key=lambda k: self._cache_expiry[k]
            )
            del self._cache[oldest_key]
            del self._cache_expiry[oldest_key]

        self._cache[key] = data
        self._cache_expiry[key] = datetime.now() + timedelta(seconds=self._cache_ttl)

    def clear_cache(self) -> None:
        """Clear all cached data."""
        self._cache.clear()
        self._cache_expiry.clear()
        logger.info("Keyword cache cleared")

    def get_cache_stats(self) -> Dict[str, Any]:
        """
        Get cache statistics.

        Returns:
            Dictionary with cache stats
        """
        return {
            "size": len(self._cache),
            "max_size": self._cache_size,
            "ttl": self._cache_ttl,
        }
