# -*- coding: utf-8 -*-
"""
Tests for storage and configuration contracts.
"""

import os
import sys
import unittest


sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config.settings import Settings
from database.db_manager import DatabaseManager


class TestStorageContracts(unittest.TestCase):
    def setUp(self):
        Settings._instance = None
        Settings._initialized = False
        DatabaseManager._instance = None
        DatabaseManager._initialized = False

    def test_title_engine_defaults_to_formal_database(self):
        settings = Settings()
        db_config = settings.get_database_config()

        self.assertEqual(db_config.get("database"), "JSReportReplica")
        self.assertEqual(db_config.get("schema"), "app")
        self.assertTrue(db_config.get("trust_server_certificate"))

    def test_runtime_ddl_is_disabled(self):
        manager = DatabaseManager()

        with self.assertRaises(RuntimeError):
            manager.create_tables()


if __name__ == "__main__":
    unittest.main()
