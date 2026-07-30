# -*- coding: utf-8 -*-
"""
Configuration management for title engine.

Manages application settings, database configuration, and SEO rules.
"""

import os
import json
import logging
from typing import Dict, Any, Optional
from pathlib import Path

from config.database_credentials import resolve_database_settings

logger = logging.getLogger(__name__)


class Settings:
    """Application settings manager."""

    _instance = None
    _initialized = False

    def __new__(cls):
        """Singleton pattern for Settings."""
        if cls._instance is None:
            cls._instance = super(Settings, cls).__new__(cls)
        return cls._instance

    def __init__(self):
        """Initialize settings if not already done."""
        if self._initialized:
            return

        self._initialized = True
        self._settings: Dict[str, Any] = {}
        self._load_settings()
        logger.info("Settings initialized")

    def _load_settings(self) -> None:
        """Load settings from environment and config files."""
        # Database settings
        self._settings["database"] = resolve_database_settings()

        # Application settings
        self._settings["app"] = {
            "debug": os.getenv("APP_DEBUG", "False").lower() == "true",
            "log_level": os.getenv("LOG_LEVEL", "INFO"),
            "host": os.getenv("APP_HOST", "0.0.0.0"),
            "port": int(os.getenv("APP_PORT", "5000")),
        }

        # Title engine settings
        self._settings["title_engine"] = {
            "max_title_length": int(os.getenv("MAX_TITLE_LENGTH", "30")),
            "core_word_min_pos": int(os.getenv("CORE_WORD_MIN_POS", "0")),
            "core_word_max_pos": int(os.getenv("CORE_WORD_MAX_POS", "10")),
            "second_core_word_min_pos": int(os.getenv("SECOND_CORE_WORD_MIN_POS", "20")),
            "second_core_word_max_pos": int(os.getenv("SECOND_CORE_WORD_MAX_POS", "25")),
            "cache_size": int(os.getenv("CACHE_SIZE", "1000")),
            "cache_ttl": int(os.getenv("CACHE_TTL", "3600")),
        }

        # Load SEO rules
        self._load_seo_rules()

    def _load_seo_rules(self) -> None:
        """Load SEO rules from JSON config file."""
        config_dir = Path(__file__).parent
        rules_file = config_dir / "seo_rules.json"

        try:
            if rules_file.exists():
                with open(rules_file, "r", encoding="utf-8") as f:
                    self._settings["seo_rules"] = json.load(f)
                logger.info("Loaded SEO rules from %s", rules_file)
            else:
                logger.warning("SEO rules file not found: %s", rules_file)
                self._settings["seo_rules"] = {}
        except Exception as e:
            logger.error("Failed to load SEO rules: %s", e)
            self._settings["seo_rules"] = {}

    def get(self, key: str, default: Any = None) -> Any:
        """
        Get setting by key.

        Args:
            key: Setting key (supports dot notation, e.g., "database.server")
            default: Default value if key not found

        Returns:
            Setting value or default
        """
        keys = key.split(".")
        value = self._settings

        for k in keys:
            if isinstance(value, dict) and k in value:
                value = value[k]
            else:
                return default

        return value

    def set(self, key: str, value: Any) -> None:
        """
        Set setting by key.

        Args:
            key: Setting key (supports dot notation)
            value: Setting value
        """
        keys = key.split(".")
        current = self._settings

        for k in keys[:-1]:
            if k not in current:
                current[k] = {}
            current = current[k]

        current[keys[-1]] = value
        logger.debug("Setting updated: %s = %s", key, value)

    def get_database_config(self) -> Dict[str, Any]:
        """
        Get database configuration.

        Returns:
            Database configuration dictionary
        """
        return self._settings.get("database", {})

    def get_seo_rules(self) -> Dict[str, Any]:
        """
        Get SEO rules configuration.

        Returns:
            SEO rules dictionary
        """
        return self._settings.get("seo_rules", {})

    def get_title_engine_config(self) -> Dict[str, Any]:
        """
        Get title engine configuration.

        Returns:
            Title engine configuration dictionary
        """
        return self._settings.get("title_engine", {})

    def reload(self) -> None:
        """Reload all settings."""
        self._initialized = False
        self._load_settings()
        logger.info("Settings reloaded")


def get_settings() -> Settings:
    """
    Get settings instance.

    Returns:
        Settings singleton instance
    """
    return Settings()
