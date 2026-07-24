# -*- coding: utf-8 -*-
"""Read-only access to the formal 1688 keyword evidence tables."""

from __future__ import annotations

import copy
import os
import threading
import time
from typing import Any, Callable, Dict, List

from config.settings import get_settings
from database.db_manager import DatabaseManager


def _category_search_term(category: str) -> str:
    text = str(category or "").strip()
    if text.endswith("类") and len(text) > 1:
        text = text[:-1]
    return text


class KeywordSourceRepository:
    """Query live crawler snapshots without writing or changing source data."""

    def __init__(
        self,
        db_manager: DatabaseManager | None = None,
        *,
        cache_ttl_seconds: float | None = None,
        clock: Callable[[], float] = time.monotonic,
    ):
        self._db_manager = db_manager or DatabaseManager()
        schema = get_settings().get_database_config().get("schema", "app")
        self._schema = DatabaseManager._quote_identifier(schema)
        configured_ttl = (
            cache_ttl_seconds
            if cache_ttl_seconds is not None
            else float(os.getenv("TITLE_ENGINE_SOURCE_CACHE_TTL_SECONDS", "300"))
        )
        self._cache_ttl_seconds = max(0.0, float(configured_ttl))
        self._clock = clock
        self._cache: dict[tuple[str, tuple[Any, ...]], tuple[float, List[Dict[str, Any]]]] = {}
        self._cache_lock = threading.RLock()

    def _query(self, sql: str, params: tuple[Any, ...]) -> List[Dict[str, Any]]:
        cache_key = (" ".join(sql.split()), tuple(params))
        now = self._clock()
        if self._cache_ttl_seconds > 0:
            with self._cache_lock:
                cached = self._cache.get(cache_key)
                if cached and now - cached[0] <= self._cache_ttl_seconds:
                    return copy.deepcopy(cached[1])

        strict_query = getattr(self._db_manager, "execute_query_strict", None)
        if callable(strict_query):
            rows = strict_query(sql, params)
        else:
            rows = self._db_manager.execute_query(sql, params)

        if self._cache_ttl_seconds > 0:
            with self._cache_lock:
                self._cache[cache_key] = (self._clock(), copy.deepcopy(rows))
        return rows

    def market_rank_keywords(self, limit: int = 500) -> List[Dict[str, Any]]:
        sql = """
            WITH latest AS (
                SELECT board_type, MAX(stat_month) AS stat_month
                FROM {schema}.[sycm_market_rank_monthly]
                WHERE board_type IN (N'keyword_hot', N'keyword_rising', N'keyword_blue_ocean')
                GROUP BY board_type
            )
            SELECT TOP (?)
                source.stat_month AS source_stat_date,
                source.board_type,
                source.category_path,
                source.rank_no,
                COALESCE(NULLIF(source.keyword_text, N''), source.primary_name) AS word,
                source.metric_summary,
                source.business_key_hash AS source_record_key,
                CONVERT(NVARCHAR(50), source.capture_time, 127) AS capture_time
            FROM {schema}.[sycm_market_rank_monthly] AS source
            INNER JOIN latest
                ON latest.board_type = source.board_type
               AND latest.stat_month = source.stat_month
            WHERE COALESCE(NULLIF(source.keyword_text, N''), source.primary_name) IS NOT NULL
            ORDER BY source.stat_month DESC, source.board_type, source.rank_no
        """.format(schema=self._schema)
        return self._query(sql, (max(1, min(int(limit), 2000)),))

    def distribution_product_evidence(
        self,
        category: str,
        limit: int = 500,
    ) -> List[Dict[str, Any]]:
        term = _category_search_term(category)
        sql = """
            SELECT TOP (?)
                source.stat_week_start AS source_stat_date,
                source.board_type,
                source.category_path,
                source.product_name,
                source.search_heat_num,
                source.trade_index_num,
                source.business_key_hash AS source_record_key,
                CONVERT(NVARCHAR(50), source.capture_time, 127) AS capture_time
            FROM {schema}.[distribution_selection_rank_weekly] AS source
            WHERE source.stat_week_start = (
                SELECT MAX(stat_week_start)
                FROM {schema}.[distribution_selection_rank_weekly]
            )
              AND (? = N'' OR source.category_path LIKE ?)
            ORDER BY source.search_heat_num DESC, source.rank_no
        """.format(schema=self._schema)
        return self._query(
            sql,
            (max(1, min(int(limit), 2000)), term, f"%{term}%" if term else ""),
        )

    def market_opportunity_items(
        self,
        category: str,
        limit: int = 500,
    ) -> List[Dict[str, Any]]:
        term = _category_search_term(category)
        sql = """
            SELECT TOP (?)
                source.stat_week_start AS source_stat_date,
                source.board_type,
                source.category_path,
                source.title_text,
                source.buyer_heat_num,
                source.purchase_count_num,
                source.business_key_hash AS source_record_key,
                CONVERT(NVARCHAR(50), source.capture_time, 127) AS capture_time
            FROM {schema}.[market_opportunity_item_weekly] AS source
            WHERE source.stat_week_start = (
                SELECT MAX(stat_week_start)
                FROM {schema}.[market_opportunity_item_weekly]
            )
              AND (? = N'' OR source.category_path LIKE ? OR source.title_text LIKE ?)
            ORDER BY source.buyer_heat_num DESC, source.purchase_count_num DESC
        """.format(schema=self._schema)
        wildcard = f"%{term}%" if term else ""
        return self._query(
            sql,
            (max(1, min(int(limit), 2000)), term, wildcard, wildcard),
        )

    def market_opportunity_signals(
        self,
        category: str,
        limit: int = 500,
    ) -> List[Dict[str, Any]]:
        term = _category_search_term(category)
        sql = """
            SELECT TOP (?)
                source.stat_week_start AS source_stat_date,
                source.board_type,
                source.category_path,
                source.signal_section,
                source.signal_title,
                source.signal_text,
                source.business_key_hash AS source_record_key,
                CONVERT(NVARCHAR(50), source.capture_time, 127) AS capture_time
            FROM {schema}.[market_opportunity_signal_detail_weekly] AS source
            WHERE source.stat_week_start = (
                SELECT MAX(stat_week_start)
                FROM {schema}.[market_opportunity_signal_detail_weekly]
            )
              AND (? = N'' OR source.category_path LIKE ? OR source.signal_text LIKE ?)
            ORDER BY source.row_index, source.line_index
        """.format(schema=self._schema)
        wildcard = f"%{term}%" if term else ""
        return self._query(
            sql,
            (max(1, min(int(limit), 2000)), term, wildcard, wildcard),
        )

    def hsds_root_words(
        self,
        category: str = "",
        limit: int = 200,
    ) -> List[Dict[str, Any]]:
        """Read the migrated HSDS seven-day root-word snapshot."""
        term = _category_search_term(category)
        sql = """
            SELECT TOP (?)
                source.stat_date AS source_stat_date,
                source.window_start,
                source.window_end,
                source.category_level_1,
                source.category_level_2,
                source.category_level_3,
                source.root_word,
                source.search_people,
                source.search_people_grow,
                source.source_record_key,
                source.source_hash,
                source.loaded_at
            FROM {schema}.[ali1688_title_hsds_root_word_snapshot] AS source
            WHERE source.window_end >= DATEADD(DAY, -7, CAST(GETDATE() AS date))
              AND (? = N'' OR source.category_level_1 LIKE ?
                   OR source.category_level_2 LIKE ?
                   OR source.category_level_3 LIKE ?)
            ORDER BY source.stat_date DESC, source.search_people DESC, source.root_word
        """.format(schema=self._schema)
        wildcard = f"%{term}%" if term else ""
        return self._query(
            sql,
            (
                max(1, min(int(limit), 1000)),
                term,
                wildcard,
                wildcard,
                wildcard,
            ),
        )
