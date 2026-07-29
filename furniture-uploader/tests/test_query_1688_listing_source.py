from __future__ import annotations

import sys
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_ROOT = PROJECT_ROOT / "scripts"
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

from query_1688_listing_source import load_source_rows, source_gate_passed


class FakeCursor:
    def __init__(self, row: tuple[object, ...]) -> None:
        self.description = []
        self.row = row
        self.calls: list[tuple[str, tuple[object, ...]]] = []

    def execute(self, sql: str, *params: object):
        self.calls.append((sql, params))
        names = [
            "autoid",
            "i_id",
            "name",
            "category",
            "modified",
            "sku_id",
            "enabled",
            "stock_disabled",
            "other_5",
            "item_type",
        ]
        self.description = [(name,) for name in names]
        return self

    def fetchall(self):
        return [self.row]


class FakeConnection:
    def __init__(self, row: tuple[object, ...]) -> None:
        self.cursor_obj = FakeCursor(row)

    def cursor(self) -> FakeCursor:
        return self.cursor_obj


class ListingSourceQueryTests(unittest.TestCase):
    def test_query_is_parameterized_and_current_row_passes_gate(self) -> None:
        connection = FakeConnection(
            (611035, "CTG0286", "款式一", "住宅家具", "2026-06-30", "SKU-1", 1, 0, "销售", "成品")
        )

        rows, columns = load_source_rows(connection, "SKU-1")

        sql, params = connection.cursor_obj.calls[-1]
        self.assertIn("WHERE [sku_id] = ?", sql)
        self.assertEqual(params, ("SKU-1",))
        self.assertTrue(source_gate_passed(rows, columns))

    def test_gate_rejects_non_sales_lifecycle(self) -> None:
        connection = FakeConnection(
            (611035, "CTG0286", "款式一", "住宅家具", "2026-06-30", "SKU-1", 1, 0, "下架", "成品")
        )

        rows, columns = load_source_rows(connection, "SKU-1")

        self.assertFalse(source_gate_passed(rows, columns))


if __name__ == "__main__":
    unittest.main()
