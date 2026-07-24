# -*- coding: utf-8 -*-
"""Transparent lexical product-fit scoring for keyword candidates."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Set

from rpa.text_analyzer import TextAnalyzer


PRODUCT_FIT_VERSION = "product_fit_lexical_v1"
GENERIC_TERMS = {"家具", "家装", "建材", "商品", "产品", "用品", "类"}


@dataclass
class ProductContext:
    category: str
    product_name: str = ""
    core_keywords: List[str] = field(default_factory=list)
    attributes: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ProductFitResult:
    score: Optional[float]
    status: str
    breakdown: Dict[str, float]
    missing_context: List[str]
    version: str = PRODUCT_FIT_VERSION

    def to_dict(self) -> Dict[str, Any]:
        return {
            "score": self.score,
            "status": self.status,
            "breakdown": dict(self.breakdown),
            "missing_context": list(self.missing_context),
            "version": self.version,
        }


class ProductFitEvaluator:
    """Score lexical overlap against explicit product context."""

    WEIGHTS = {
        "core_keywords": 0.50,
        "product_name": 0.25,
        "category": 0.15,
        "attributes": 0.10,
    }

    def __init__(self, analyzer: TextAnalyzer | None = None):
        self._analyzer = analyzer or TextAnalyzer()

    def evaluate(self, candidate: str, context: ProductContext) -> ProductFitResult:
        candidate_text = self._normalize(candidate)
        missing = []
        if not candidate_text:
            return ProductFitResult(None, "incomplete", {}, ["candidate"])

        core_texts = [self._normalize(value) for value in context.core_keywords if self._normalize(value)]
        name_texts = [self._normalize(context.product_name)] if self._normalize(context.product_name) else []
        category_texts = [self._normalize(context.category)] if self._normalize(context.category) else []
        attribute_texts = [
            self._normalize(value)
            for value in context.attributes.values()
            if self._normalize(value)
        ]
        groups = {
            "core_keywords": core_texts,
            "product_name": name_texts,
            "category": category_texts,
            "attributes": attribute_texts,
        }
        for key, texts in groups.items():
            if not texts:
                missing.append(key)

        if all(not texts for texts in groups.values()):
            return ProductFitResult(None, "incomplete", {}, missing)

        breakdown = {
            key: self._best_similarity(candidate_text, texts)
            for key, texts in groups.items()
        }
        total = sum(breakdown[key] * self.WEIGHTS[key] for key in self.WEIGHTS)
        return ProductFitResult(
            score=max(0.0, min(1.0, total)),
            status="complete",
            breakdown=breakdown,
            missing_context=missing,
        )

    def metadata(self) -> Dict[str, Any]:
        return {
            "version": PRODUCT_FIT_VERSION,
            "method": "lexical_overlap",
            "weights": dict(self.WEIGHTS),
            "automatic_submit_allowed": False,
        }

    def _best_similarity(self, candidate: str, values: Iterable[str]) -> float:
        scores = [self._similarity(candidate, value) for value in values if value]
        return max(scores, default=0.0)

    def _similarity(self, left: str, right: str) -> float:
        if not left or not right:
            return 0.0
        if left == right:
            return 1.0
        if len(left) >= 2 and left in right:
            return 1.0
        if len(right) >= 2 and right in left:
            return 0.9
        left_terms = self._terms(left)
        right_terms = self._terms(right)
        if not left_terms or not right_terms:
            return 0.0
        overlap = len(left_terms & right_terms)
        return (2.0 * overlap) / (len(left_terms) + len(right_terms))

    def _terms(self, text: str) -> Set[str]:
        words = self._analyzer.segment(text)
        terms = self._analyzer.extract_roots(words)
        terms.update(re.findall(r"[a-z0-9]+", text))
        chinese = "".join(re.findall(r"[\u4e00-\u9fff]", text))
        terms.update(chinese[index:index + 2] for index in range(max(0, len(chinese) - 1)))
        return {term for term in terms if term and term not in GENERIC_TERMS}

    @staticmethod
    def _normalize(value: Any) -> str:
        text = str(value or "").strip().lower()
        return re.sub(r"[^\u4e00-\u9fffa-z0-9]+", "", text)
