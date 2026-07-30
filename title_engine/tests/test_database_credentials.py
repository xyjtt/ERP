from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from config.database_credentials import load_external_credential, resolve_database_settings
from database.db_manager import DatabaseManager


class DatabaseCredentialTests(unittest.TestCase):
    def setUp(self) -> None:
        DatabaseManager._instance = None
        DatabaseManager._initialized = False

    def tearDown(self) -> None:
        DatabaseManager._instance = None
        DatabaseManager._initialized = False
        sys.modules.pop("_title_engine_1688_secret_provider", None)

    def test_external_config_loads_metadata_without_plaintext_credentials(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "database.json"
            config_path.write_text(
                json.dumps(
                    {
                        "write_target": {
                            "server": "db.internal",
                            "database": "JSReportReplica",
                            "schema": "app",
                            "driver": "SQL Server Native Client 10.0",
                            "credential_ref": "YYDD/1688/database/app-writer",
                            "query_timeout": 27,
                        }
                    }
                ),
                encoding="utf-8",
            )
            with patch.dict(
                os.environ,
                {"YYDD_1688_CONFIG_ROOT": temp_dir},
                clear=True,
            ):
                config = resolve_database_settings()

        self.assertEqual(config["server"], "db.internal")
        self.assertEqual(config["credential_ref"], "YYDD/1688/database/app-writer")
        self.assertEqual(config["username"], "")
        self.assertEqual(config["password"], "")
        self.assertEqual(config["query_timeout"], 27)

    def test_partial_environment_credentials_fail_closed(self) -> None:
        with patch.dict(
            os.environ,
            {"TITLE_ENGINE_SQLSERVER_USERNAME": "app_writer"},
            clear=True,
        ):
            with self.assertRaisesRegex(RuntimeError, "must both be configured"):
                resolve_database_settings()

    def test_connection_resolves_external_credential_lazily(self) -> None:
        manager = DatabaseManager()
        manager._database_config.update(
            {
                "username": "",
                "password": "",
                "credential_ref": "YYDD/1688/database/app-writer",
            }
        )
        with patch(
            "database.db_manager.load_external_credential",
            return_value=("writer", "test-secret"),
        ):
            connection_string = manager._resolved_connection_string()

        self.assertIn("UID=writer", connection_string)
        self.assertIn("PWD=test-secret", connection_string)

    def test_external_credential_module_load_is_thread_safe(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            runtime_root = Path(temp_dir)
            module_path = runtime_root / "src" / "security" / "secret_provider.py"
            module_path.parent.mkdir(parents=True)
            module_path.write_text(
                """
import time
from types import SimpleNamespace

time.sleep(0.05)

class Provider:
    def get(self, _credential_ref):
        return SimpleNamespace(username="writer", secret="test-secret")

def get_secret_provider():
    return Provider()
""".strip()
                + "\n",
                encoding="utf-8",
            )
            sys.modules.pop("_title_engine_1688_secret_provider", None)
            with ThreadPoolExecutor(max_workers=8) as executor:
                results = list(
                    executor.map(
                        lambda _index: load_external_credential(
                            "YYDD/1688/database/app-writer",
                            shared_runtime_root=runtime_root,
                        ),
                        range(16),
                    )
                )

        self.assertEqual(results, [("writer", "test-secret")] * 16)


if __name__ == "__main__":
    unittest.main()
