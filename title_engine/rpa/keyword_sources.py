# -*- coding: utf-8 -*-
"""Adapters from crawler snapshots to title-engine keyword candidates."""

from __future__ import annotations

import json
import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Dict, Iterable, List, Optional, Tuple

from database.keyword_source_repository import KeywordSourceRepository
from rpa.keyword_manager import KeywordData, _optional_float
from rpa.product_fit import ProductContext, ProductFitEvaluator


MONTHLY_MAX_AGE_DAYS = 45
WEEKLY_MAX_AGE_DAYS = 14
HSDS_MAX_AGE_DAYS = 7


@dataclass
class SourceSummary:
    source: str
    role: str
    row_count: int
    candidate_count: int
    latest_stat_date: Optional[str]
    freshness_status: str
    note: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "source": self.source,
            "role": self.role,
            "row_count": self.row_count,
            "candidate_count": self.candidate_count,
            "latest_stat_date": self.latest_stat_date,
            "freshness_status": self.freshness_status,
            "note": self.note,
        }


class KeywordSourceService:
    """Build score-ready and evidence-only keyword candidates."""

    def __init__(
        self,
        repository: KeywordSourceRepository | None = None,
        fit_evaluator: ProductFitEvaluator | None = None,
        today: date | None = None,
    ):
        self._repository = repository or KeywordSourceRepository()
        self._fit_evaluator = fit_evaluator or ProductFitEvaluator()
        self._today = today or date.today()

    def metadata(self) -> Dict[str, Any]:
        return {
            "sources": {
                "sycm_market_rank": "keyword_candidate",
                "distribution_selection": "product_evidence",
                "market_opportunity_item": "product_evidence",
                "market_opportunity_signal": "product_evidence",
                "hsds_demand_root": "independent_candidate",
            },
            "freshness_days": {
                "monthly": MONTHLY_MAX_AGE_DAYS,
                "weekly": WEEKLY_MAX_AGE_DAYS,
            },
            "product_fit": self._fit_evaluator.metadata(),
        }

    def preview(
        self,
        context: ProductContext,
        limit_per_source: int = 500,
    ) -> Tuple[List[KeywordData], List[SourceSummary], Dict[str, Any]]:
        hsds_reader = getattr(self._repository, "hsds_root_words", None)
        with ThreadPoolExecutor(max_workers=5, thread_name_prefix="title-source") as executor:
            jobs = {
                "market": executor.submit(self._repository.market_rank_keywords, limit_per_source),
                "distribution": executor.submit(
                    self._repository.distribution_product_evidence,
                    context.category,
                    limit_per_source,
                ),
                "opportunity": executor.submit(
                    self._repository.market_opportunity_items,
                    context.category,
                    limit_per_source,
                ),
                "signal": executor.submit(
                    self._repository.market_opportunity_signals,
                    context.category,
                    limit_per_source,
                ),
            }
            if callable(hsds_reader):
                jobs["hsds"] = executor.submit(
                    hsds_reader,
                    context.category,
                    min(limit_per_source, 1000),
                )

            market_rows = jobs["market"].result()
            distribution_rows = jobs["distribution"].result()
            opportunity_rows = jobs["opportunity"].result()
            signal_rows = jobs["signal"].result()
            hsds_rows: List[Dict[str, Any]] = []
            hsds_error = ""
            if "hsds" in jobs:
                try:
                    hsds_rows = jobs["hsds"].result()
                except Exception as exc:
                    hsds_error = self._source_error_note(exc)

        market_candidates = [
            candidate
            for row in market_rows
            if (candidate := self._from_market_rank(row, context)) is not None
        ]
        distribution_candidates = [
            candidate
            for row in distribution_rows
            if (candidate := self._from_distribution(row, context)) is not None
        ]
        opportunity_candidates = [
            candidate
            for row in opportunity_rows
            if (candidate := self._from_opportunity(row, context)) is not None
        ]
        signal_candidates = [
            candidate
            for row in signal_rows
            if (candidate := self._from_signal(row, context)) is not None
        ]
        hsds_candidates = [
            candidate
            for row in hsds_rows
            if (candidate := self._from_hsds(row, context)) is not None
        ]
        candidates = self._deduplicate(
            market_candidates
            + distribution_candidates
            + opportunity_candidates
            + signal_candidates
            + hsds_candidates
        )
        summaries = [
            self._summary(
                "sycm_market_rank",
                "keyword_candidate",
                market_rows,
                market_candidates,
                MONTHLY_MAX_AGE_DAYS,
            ),
            self._summary(
                "distribution_selection",
                "product_evidence",
                distribution_rows,
                distribution_candidates,
                WEEKLY_MAX_AGE_DAYS,
                "Current rows are opportunity products, not opportunity words.",
            ),
            self._summary(
                "market_opportunity_item",
                "product_evidence",
                opportunity_rows,
                opportunity_candidates,
                WEEKLY_MAX_AGE_DAYS,
                "No reliable competition metric; evidence-derived candidates remain incomplete.",
            ),
            self._summary(
                "market_opportunity_signal",
                "product_evidence",
                signal_rows,
                signal_candidates,
                WEEKLY_MAX_AGE_DAYS,
                "Signals support fit evidence only and do not supply competition.",
            ),
        ]
        if callable(hsds_reader):
            summaries.append(
                self._summary(
                    "hsds_demand_root",
                    "independent_candidate",
                    hsds_rows,
                    hsds_candidates,
                    HSDS_MAX_AGE_DAYS,
                    hsds_error or "HSDS candidates are independent heat evidence and do not enter the composite score.",
                )
            )
        return candidates, summaries, self._fit_evaluator.metadata()

    def _from_market_rank(
        self,
        row: Dict[str, Any],
        context: ProductContext,
    ) -> Optional[KeywordData]:
        word = str(row.get("word") or "").strip()
        if not word:
            return None
        metrics = self._parse_json_object(row.get("metric_summary"))
        fit = self._fit_evaluator.evaluate(word, context)
        stat_date = self._date_value(row.get("source_stat_date"))
        freshness = self._freshness(stat_date, MONTHLY_MAX_AGE_DAYS)
        board_type = str(row.get("board_type") or "unknown")
        return KeywordData(
            word=word,
            category=str(row.get("category_path") or context.category),
            search_popularity=_optional_float(metrics.get("seSpvIndex")),
            transaction_index=_optional_float(metrics.get("tradeIndex")),
            competition=_optional_float(metrics.get("seMaxRescnt")),
            product_fit=fit.score,
            source=f"sycm_market_rank:{board_type}",
            source_stat_date=stat_date,
            source_record_key=str(row.get("source_record_key") or ""),
            source_role="keyword_candidate",
            freshness_status=freshness,
            source_metadata={
                "board_type": board_type,
                "rank_no": row.get("rank_no"),
                "metric_keys": sorted(metrics.keys()),
                "product_fit": fit.to_dict(),
                "evidence_sources": [f"sycm_market_rank:{board_type}"],
            },
        )

    def _from_distribution(
        self,
        row: Dict[str, Any],
        context: ProductContext,
    ) -> Optional[KeywordData]:
        word = self._category_leaf(row.get("category_path"))
        return self._evidence_candidate(
            word=word,
            context=context,
            row=row,
            source="distribution_selection",
            search_heat=row.get("search_heat_num"),
            transaction_index=row.get("trade_index_num"),
            evidence_text=row.get("product_name"),
        )

    def _from_opportunity(
        self,
        row: Dict[str, Any],
        context: ProductContext,
    ) -> Optional[KeywordData]:
        word = self._category_leaf(row.get("category_path"))
        return self._evidence_candidate(
            word=word,
            context=context,
            row=row,
            source="market_opportunity_item",
            search_heat=row.get("buyer_heat_num"),
            transaction_index=row.get("purchase_count_num"),
            evidence_text=row.get("title_text"),
        )

    def _from_signal(
        self,
        row: Dict[str, Any],
        context: ProductContext,
    ) -> Optional[KeywordData]:
        word = self._category_leaf(row.get("category_path"))
        return self._evidence_candidate(
            word=word,
            context=context,
            row=row,
            source="market_opportunity_signal",
            search_heat=None,
            transaction_index=None,
            evidence_text=" ".join(
                str(row.get(key) or "")
                for key in ("signal_section", "signal_title", "signal_text")
            ).strip(),
        )

    def _from_hsds(
        self,
        row: Dict[str, Any],
        context: ProductContext,
    ) -> Optional[KeywordData]:
        word = str(row.get("root_word") or "").strip()
        if not word:
            return None
        fit = self._fit_evaluator.evaluate(word, context)
        stat_date = self._date_value(row.get("source_stat_date"))
        category = " > ".join(
            str(row.get(key) or "").strip()
            for key in ("category_level_1", "category_level_2", "category_level_3")
            if str(row.get(key) or "").strip()
        ) or context.category
        return KeywordData(
            word=word,
            category=category,
            search_popularity=_optional_float(row.get("search_people")),
            transaction_index=_optional_float(row.get("search_people_grow")),
            competition=None,
            product_fit=fit.score,
            source="hsds_demand_root",
            source_stat_date=stat_date,
            source_record_key=str(row.get("source_record_key") or ""),
            source_role="independent_candidate",
            freshness_status=self._freshness(stat_date, HSDS_MAX_AGE_DAYS),
            source_metadata={
                "source_type": "hsds_demand_root",
                "window_start": str(row.get("window_start") or ""),
                "window_end": str(row.get("window_end") or ""),
                "search_people_grow": row.get("search_people_grow"),
                "source_hash": str(row.get("source_hash") or ""),
                "product_fit": fit.to_dict(),
                "evidence_sources": ["hsds_demand_root"],
            },
        )

    def _evidence_candidate(
        self,
        *,
        word: str,
        context: ProductContext,
        row: Dict[str, Any],
        source: str,
        search_heat: Any,
        transaction_index: Any,
        evidence_text: Any,
    ) -> Optional[KeywordData]:
        if not word:
            return None
        fit = self._fit_evaluator.evaluate(word, context)
        stat_date = self._date_value(row.get("source_stat_date"))
        return KeywordData(
            word=word,
            category=str(row.get("category_path") or context.category),
            search_popularity=_optional_float(search_heat),
            transaction_index=_optional_float(transaction_index),
            competition=None,
            product_fit=fit.score,
            source=source,
            source_stat_date=stat_date,
            source_record_key=str(row.get("source_record_key") or ""),
            source_role="product_evidence",
            freshness_status=self._freshness(stat_date, WEEKLY_MAX_AGE_DAYS),
            source_metadata={
                "board_type": str(row.get("board_type") or ""),
                "product_fit": fit.to_dict(),
                "evidence_sources": [source],
                "evidence_text": str(evidence_text or "")[:200],
            },
        )

    def _deduplicate(self, candidates: Iterable[KeywordData]) -> List[KeywordData]:
        selected: Dict[str, KeywordData] = {}
        for candidate in candidates:
            key = re.sub(r"\s+", "", candidate.word).lower()
            existing = selected.get(key)
            if existing is None:
                selected[key] = candidate
                continue
            evidence = set(existing.source_metadata.get("evidence_sources", []))
            evidence.update(candidate.source_metadata.get("evidence_sources", []))
            if self._candidate_priority(candidate) > self._candidate_priority(existing):
                candidate.source_metadata["evidence_sources"] = sorted(evidence)
                selected[key] = candidate
            else:
                existing.source_metadata["evidence_sources"] = sorted(evidence)
        return list(selected.values())

    @staticmethod
    def _candidate_priority(candidate: KeywordData) -> Tuple[int, int, str, float]:
        complete = all(
            value is not None
            for value in (candidate.search_popularity, candidate.competition, candidate.product_fit)
        )
        freshness = 1 if candidate.freshness_status == "fresh" else 0
        stat_date = str(candidate.source_stat_date or "")
        search_heat = _optional_float(candidate.search_popularity) or 0.0
        return (freshness, 1 if complete else 0, stat_date, search_heat)

    def _summary(
        self,
        source: str,
        role: str,
        rows: List[Dict[str, Any]],
        candidates: List[KeywordData],
        max_age_days: int,
        note: str = "",
    ) -> SourceSummary:
        dates = [self._date_value(row.get("source_stat_date")) for row in rows]
        dates = [value for value in dates if value is not None]
        latest = max(dates, default=None)
        freshness_values = {self._freshness(value, max_age_days) for value in dates}
        if not dates:
            freshness_status = "missing"
        elif len(freshness_values) == 1:
            freshness_status = next(iter(freshness_values))
        else:
            freshness_status = "mixed"
        return SourceSummary(
            source=source,
            role=role,
            row_count=len(rows),
            candidate_count=len(candidates),
            latest_stat_date=latest.isoformat() if latest else None,
            freshness_status=freshness_status,
            note=note,
        )

    def _freshness(self, value: Optional[date], max_age_days: int) -> str:
        if value is None:
            return "missing"
        age_days = (self._today - value).days
        if age_days < 0:
            return "future"
        return "fresh" if age_days <= max_age_days else "stale"

    @staticmethod
    def _source_error_note(error: Exception) -> str:
        text = str(error).lower()
        if "invalid object name" in text or "42s02" in text or "does not exist" in text:
            return "HSDS snapshot table is not deployed or SELECT permission is missing."
        return "HSDS snapshot read failed; existing keyword sources remain available."

    @staticmethod
    def _parse_json_object(value: Any) -> Dict[str, Any]:
        if isinstance(value, dict):
            return dict(value)
        try:
            parsed = json.loads(str(value or "{}"))
        except (TypeError, ValueError, json.JSONDecodeError):
            return {}
        return parsed if isinstance(parsed, dict) else {}

    @staticmethod
    def _category_leaf(value: Any) -> str:
        parts = [part.strip() for part in re.split(r"[>/|]", str(value or "")) if part.strip()]
        return parts[-1] if parts else ""

    @staticmethod
    def _date_value(value: Any) -> Optional[date]:
        if isinstance(value, datetime):
            return value.date()
        if isinstance(value, date):
            return value
        text = str(value or "").strip()
        if not text:
            return None
        try:
            return date.fromisoformat(text[:10])
        except ValueError:
            return None
