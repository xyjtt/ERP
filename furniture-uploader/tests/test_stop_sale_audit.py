from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch


PROJECT_ROOT = Path(__file__).resolve().parents[1]
RPA_ROOT = PROJECT_ROOT / "rpa"
if str(RPA_ROOT) not in sys.path:
    sys.path.insert(0, str(RPA_ROOT))

from stop_sale_audit import (
    load_jsonl_records,
    resolve_stop_sale_app_config,
    stop_sale_offline_key,
    stop_sale_task_key,
)


class StopSaleAuditTests(unittest.TestCase):
    def test_task_key_matches_jushuitan_four_field_identity(self) -> None:
        values = ("阿里巴巴-常州工莱家具", "1001", " SKU-A ", "CODE-A")
        expected = hashlib.sha256(
            "阿里巴巴-常州工莱家具|1001|sku-a|code-a".encode("utf-8")
        ).hexdigest()

        self.assertEqual(stop_sale_task_key(*values), expected)
        self.assertNotEqual(stop_sale_offline_key(*values[:3]), expected)

    def test_environment_app_config_safe_dict_excludes_credentials(self) -> None:
        with patch.dict(
            os.environ,
            {
                "STOP_SALE_APP_SQLSERVER_USER": "app_writer",
                "STOP_SALE_APP_SQLSERVER_PASSWORD": "secret-value",
            },
            clear=True,
        ):
            config = resolve_stop_sale_app_config("D:/not-used")

        safe = config.safe_dict()
        self.assertEqual(safe["database"], "JSReportReplica")
        self.assertEqual(safe["schema"], "app")
        self.assertNotIn("user", safe)
        self.assertNotIn("password", safe)
        self.assertNotIn("secret-value", repr(config))

    def test_jsonl_loader_ignores_invalid_and_non_object_rows(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "result.jsonl"
            path.write_text(
                "\n".join((json.dumps({"status": "success"}), "not-json", "[]")),
                encoding="utf-8",
            )

            records = load_jsonl_records(path)

        self.assertEqual(records, [{"status": "success"}])


if __name__ == "__main__":
    unittest.main()
