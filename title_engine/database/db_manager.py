# -*- coding: utf-8 -*-
"""
Database manager for SQL Server operations.

Provides database connectivity, querying, and data management.
"""

import logging
from typing import List, Dict, Any, Optional, Tuple
from datetime import datetime
from contextlib import contextmanager

from config.database_credentials import load_external_credential

logger = logging.getLogger(__name__)

# Try to import pyodbc for SQL Server connectivity
try:
    import pyodbc
    PYODBC_AVAILABLE = True
except ImportError:
    PYODBC_AVAILABLE = False
    logger.warning("pyodbc not available, database operations will be limited")


class DatabaseManager:
    """Manager for SQL Server database operations."""

    _instance = None
    _initialized = False

    def __new__(cls):
        """Singleton pattern for DatabaseManager."""
        if cls._instance is None:
            cls._instance = super(DatabaseManager, cls).__new__(cls)
        return cls._instance

    def __init__(self):
        """Initialize database manager."""
        if self._initialized:
            return

        self._initialized = True
        self._connection = None
        self._database_config: Dict[str, Any] = {}
        self._schema = "app"
        self._keywords_table = ""
        self._history_table = ""

        self._init_database_config()
        logger.info("DatabaseManager initialized")

    def _init_database_config(self) -> None:
        """Initialize database metadata without resolving external secrets."""
        from config.settings import get_settings

        settings = get_settings()
        db_config = settings.get_database_config()

        self._schema = self._quote_identifier(db_config.get("schema", "app"))
        self._keywords_table = f"{self._schema}.[ali1688_title_keyword]"
        self._history_table = f"{self._schema}.[ali1688_title_generation_history]"
        self._database_config = dict(db_config)

    def _build_connection_string(self, *, username: str, password: str) -> str:
        db_config = self._database_config
        driver = db_config.get("driver", "SQL Server Native Client 10.0")
        trust_server_certificate = "yes" if db_config.get("trust_server_certificate", True) else "no"
        encrypt = "yes" if db_config.get("encrypt", True) else "no"
        return (
            f"DRIVER={{{driver}}};"
            f"SERVER={db_config.get('server', '')},{db_config.get('port', 1433)};"
            f"DATABASE={db_config.get('database', 'JSReportReplica')};"
            f"UID={username};"
            f"PWD={password};"
            f"Encrypt={encrypt};"
            f"TrustServerCertificate={trust_server_certificate}"
        )

    def _resolved_connection_string(self) -> str:
        username = str(self._database_config.get("username") or "")
        password = str(self._database_config.get("password") or "")
        credential_ref = str(self._database_config.get("credential_ref") or "").strip()
        if not username and credential_ref:
            username, password = load_external_credential(credential_ref)
        if not username or not password:
            raise RuntimeError("Title engine database credentials are not configured.")
        return self._build_connection_string(username=username, password=password)

    @staticmethod
    def _quote_identifier(value: str) -> str:
        text = str(value or "").strip()
        if not text:
            raise ValueError("SQL identifier cannot be empty")
        if not text.replace("_", "").isalnum() or text[0].isdigit():
            raise ValueError(f"Unsupported SQL identifier: {value}")
        return "[" + text.replace("]", "]]") + "]"

    @contextmanager
    def get_connection(self):
        """
        Get database connection context manager.

        Yields:
            Database connection
        """
        if not PYODBC_AVAILABLE:
            raise RuntimeError("pyodbc is not installed")

        connection = None
        try:
            connection = pyodbc.connect(
                self._resolved_connection_string(),
                timeout=int(self._database_config.get("connect_timeout") or 15),
            )
            connection.timeout = int(self._database_config.get("query_timeout") or 45)
            yield connection
        except Exception as e:
            logger.error("Database connection error: %s", e)
            raise
        finally:
            if connection:
                connection.close()

    def execute_query(
        self,
        query: str,
        params: Optional[Tuple] = None
    ) -> List[Dict[str, Any]]:
        """
        Execute a query and return results.

        Args:
            query: SQL query
            params: Query parameters

        Returns:
            List of result dictionaries
        """
        try:
            with self.get_connection() as conn:
                cursor = conn.cursor()
                if params:
                    cursor.execute(query, params)
                else:
                    cursor.execute(query)

                columns = [column[0] for column in cursor.description] if cursor.description else []
                results = []

                for row in cursor.fetchall():
                    results.append(dict(zip(columns, row)))

                return results
        except Exception as e:
            logger.error("Query execution error: %s", e)
            return []

    def execute_query_strict(
        self,
        query: str,
        params: Optional[Tuple] = None,
    ) -> List[Dict[str, Any]]:
        """Execute a read query and propagate failures to the caller."""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            if params:
                cursor.execute(query, params)
            else:
                cursor.execute(query)
            columns = [column[0] for column in cursor.description] if cursor.description else []
            return [dict(zip(columns, row)) for row in cursor.fetchall()]

    def execute_non_query(
        self,
        query: str,
        params: Optional[Tuple] = None
    ) -> int:
        """
        Execute a non-query command.

        Args:
            query: SQL command
            params: Command parameters

        Returns:
            Number of affected rows
        """
        try:
            with self.get_connection() as conn:
                cursor = conn.cursor()
                if params:
                    cursor.execute(query, params)
                else:
                    cursor.execute(query)
                conn.commit()
                return cursor.rowcount
        except Exception as e:
            logger.error("Non-query execution error: %s", e)
            return 0

    def get_keywords_by_category(
        self,
        category: str,
        limit: int = 100
    ) -> List[Dict[str, Any]]:
        """
        Get keywords by category.

        Args:
            category: Category name
            limit: Maximum results

        Returns:
            List of keyword dictionaries
        """
        query = """
            SELECT TOP (?)
                keyword AS word, category, search_popularity,
                transaction_index, competition, relevance, product_fit,
                source, source_stat_date, source_record_key, updated_at
            FROM {keywords_table}
            WHERE category = ?
            ORDER BY search_popularity DESC
        """.format(keywords_table=self._keywords_table)

        return self.execute_query(query, (limit, category))

    def search_keywords(
        self,
        query: str,
        category: Optional[str] = None,
        limit: int = 50
    ) -> List[Dict[str, Any]]:
        """
        Search keywords by query.

        Args:
            query: Search query
            category: Optional category filter
            limit: Maximum results

        Returns:
            List of keyword dictionaries
        """
        if category:
            sql = """
                SELECT TOP (?)
                    keyword AS word, category, search_popularity,
                    transaction_index, competition, relevance, product_fit,
                    source, source_stat_date, source_record_key, updated_at
                FROM {keywords_table}
                WHERE keyword LIKE ? AND category = ?
                ORDER BY search_popularity DESC
            """.format(keywords_table=self._keywords_table)
            params = (limit, f"%{query}%", category)
        else:
            sql = """
                SELECT TOP (?)
                    keyword AS word, category, search_popularity,
                    transaction_index, competition, relevance, product_fit,
                    source, source_stat_date, source_record_key, updated_at
                FROM {keywords_table}
                WHERE keyword LIKE ?
                ORDER BY search_popularity DESC
            """.format(keywords_table=self._keywords_table)
            params = (limit, f"%{query}%")

        return self.execute_query(sql, params)

    def save_title_history(
        self,
        title: str,
        category: str,
        score: float,
        generated_by: str = "system"
    ) -> bool:
        """
        Save generated title to history.

        Args:
            title: Generated title
            category: Product category
            score: Title score
            generated_by: Generator identifier

        Returns:
            True if successful, False otherwise
        """
        query = """
            INSERT INTO {history_table} (generated_title, category, score, generated_by, created_at)
            VALUES (?, ?, ?, ?, ?)
        """.format(history_table=self._history_table)

        try:
            affected = self.execute_non_query(
                query,
                (title, category, score, generated_by, datetime.now())
            )
            return affected > 0
        except Exception as e:
            logger.error("Failed to save title history: %s", e)
            return False

    def upsert_keyword(self, keyword_data: Dict[str, Any], updated_by: str = "manual_import") -> bool:
        """
        Insert or update one keyword row in the formal keyword table.

        Args:
            keyword_data: Normalized keyword payload
            updated_by: Operator or import source

        Returns:
            True if the upsert command affected a row, False otherwise
        """
        query = """
            MERGE {keywords_table} WITH (HOLDLOCK) AS target
            USING (
                SELECT
                    ? AS keyword,
                    ? AS category,
                    ? AS category_alias,
                    ? AS source,
                    ? AS search_popularity,
                    ? AS transaction_index,
                    ? AS competition,
                    ? AS relevance,
                    ? AS product_fit,
                    ? AS source_stat_date,
                    ? AS source_record_key,
                    ? AS raw_payload,
                    ? AS created_by
            ) AS source
            ON target.category = source.category
               AND target.keyword = source.keyword
               AND target.source = source.source
            WHEN MATCHED THEN
                UPDATE SET
                    category_alias = source.category_alias,
                    search_popularity = source.search_popularity,
                    transaction_index = source.transaction_index,
                    competition = source.competition,
                    relevance = source.relevance,
                    product_fit = source.product_fit,
                    source_stat_date = source.source_stat_date,
                    source_record_key = source.source_record_key,
                    raw_payload = source.raw_payload,
                    updated_at = SYSUTCDATETIME()
            WHEN NOT MATCHED THEN
                INSERT (
                    keyword, category, category_alias, source,
                    search_popularity, transaction_index, competition, relevance,
                    product_fit, source_stat_date, source_record_key,
                    raw_payload, created_by, created_at, updated_at
                )
                VALUES (
                    source.keyword, source.category, source.category_alias, source.source,
                    source.search_popularity, source.transaction_index, source.competition, source.relevance,
                    source.product_fit, source.source_stat_date, source.source_record_key,
                    source.raw_payload, source.created_by, SYSUTCDATETIME(), SYSUTCDATETIME()
                );
        """.format(keywords_table=self._keywords_table)
        params = (
            keyword_data.get("keyword", ""),
            keyword_data.get("category", ""),
            keyword_data.get("category_alias", ""),
            keyword_data.get("source", "manual"),
            keyword_data.get("search_popularity"),
            keyword_data.get("transaction_index", 0),
            keyword_data.get("competition"),
            keyword_data.get("relevance"),
            keyword_data.get("product_fit", keyword_data.get("relevance")),
            keyword_data.get("source_stat_date") or None,
            keyword_data.get("source_record_key", ""),
            keyword_data.get("raw_payload", ""),
            updated_by,
        )
        return self.execute_non_query(query, params) > 0

    def audit_keyword_score_inputs(self) -> List[Dict[str, Any]]:
        """Return read-only null/zero counts for legacy score input review."""
        query = """
            SELECT
                source,
                COUNT_BIG(*) AS total_rows,
                SUM(CASE WHEN search_popularity IS NULL THEN 1 ELSE 0 END) AS null_search_heat_rows,
                SUM(CASE WHEN search_popularity = 0 THEN 1 ELSE 0 END) AS zero_search_heat_rows,
                SUM(CASE WHEN competition IS NULL THEN 1 ELSE 0 END) AS null_competition_rows,
                SUM(CASE WHEN competition = 0 THEN 1 ELSE 0 END) AS zero_competition_rows,
                SUM(CASE WHEN product_fit IS NULL AND relevance IS NULL THEN 1 ELSE 0 END) AS null_product_fit_rows,
                SUM(CASE
                    WHEN COALESCE(product_fit, relevance) = 0 THEN 1 ELSE 0
                END) AS zero_product_fit_rows,
                SUM(CASE
                    WHEN search_popularity = 0
                      OR competition = 0
                      OR COALESCE(product_fit, relevance) = 0
                    THEN 1 ELSE 0
                END) AS ambiguous_zero_rows,
                MIN(source_stat_date) AS min_source_stat_date,
                MAX(source_stat_date) AS max_source_stat_date,
                MAX(updated_at) AS latest_updated_at
            FROM {keywords_table}
            GROUP BY source
            ORDER BY source
        """.format(keywords_table=self._keywords_table)
        return self.execute_query_strict(query)

    def get_title_history(
        self,
        category: Optional[str] = None,
        limit: int = 50
    ) -> List[Dict[str, Any]]:
        """
        Get title generation history.

        Args:
            category: Optional category filter
            limit: Maximum results

        Returns:
            List of history records
        """
        if category:
            query = """
                SELECT TOP (?)
                    id, generated_title AS title, category, score, generated_by, created_at
                FROM {history_table}
                WHERE category = ?
                ORDER BY created_at DESC
            """.format(history_table=self._history_table)
            return self.execute_query(query, (limit, category))
        else:
            query = """
                SELECT TOP (?)
                    id, generated_title AS title, category, score, generated_by, created_at
                FROM {history_table}
                ORDER BY created_at DESC
            """.format(history_table=self._history_table)
            return self.execute_query(query, (limit,))

    def create_tables(self) -> bool:
        """
        Refuse runtime DDL.

        Returns:
            True if successful, False otherwise
        """
        raise RuntimeError(
            "Runtime DDL is disabled. Apply the JSReportReplica/app SQL package before using title storage."
        )

    def test_connection(self) -> bool:
        """
        Test database connection.

        Returns:
            True if connection successful, False otherwise
        """
        try:
            with self.get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT 1")
                return True
        except Exception as e:
            logger.error("Connection test failed: %s", e)
            return False
