from __future__ import annotations

import hashlib
from pathlib import Path
import sys
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
RPA_ROOT = PROJECT_ROOT / "rpa"
if str(RPA_ROOT) not in sys.path:
    sys.path.insert(0, str(RPA_ROOT))

from sku_replace_audit import SkuReplaceAuditRepository, sku_replace_task_key


class SkuReplaceAuditTests(unittest.TestCase):
    def test_task_key_matches_jushuitan_sync_identity(self) -> None:
        expected = hashlib.sha256(
            "阿里巴巴-常州工莱家具|1001|old-a|new-a".encode("utf-8")
        ).hexdigest()

        self.assertEqual(
            sku_replace_task_key(
                "阿里巴巴-常州工莱家具",
                "1001",
                " OLD-A ",
                "NEW-A",
            ),
            expected,
        )

    def test_audit_contract_uses_dedicated_replace_tables(self) -> None:
        self.assertEqual(
            SkuReplaceAuditRepository.REQUIRED_TABLES,
            ("ali1688_sku_replace_run", "ali1688_sku_replace_item"),
        )

    def test_ddl_contains_replacement_and_jushuitan_fields(self) -> None:
        ddl = (PROJECT_ROOT / "sql" / "361_ali1688_sku_replace_audit.sql").read_text(
            encoding="utf-8"
        )

        self.assertIn("replacement_sku", ddl)
        self.assertIn("replace_status", ddl)
        self.assertIn("jushuitan_status", ddl)
        self.assertIn("JSReportReplica", ddl)


if __name__ == "__main__":
    unittest.main()
