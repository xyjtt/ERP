from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


PROJECT_ROOT = Path(__file__).resolve().parents[1]
RPA_ROOT = PROJECT_ROOT / "rpa"
if str(RPA_ROOT) not in sys.path:
    sys.path.insert(0, str(RPA_ROOT))

from database import DatabaseConfig, SQLServerLogger, choose_preferred_sqlserver_driver


class FakeCursor:
    def __init__(self, row=None) -> None:
        self.calls: list[tuple[str, list[object]]] = []
        self.row = row

    def execute(self, sql, *params):
        if len(params) == 1 and isinstance(params[0], list):
            params = params[0]
        self.calls.append((sql, list(params)))
        return self

    def fetchone(self):
        return self.row


class FakeConnection:
    def __init__(self, row=None) -> None:
        self.cursor_obj = FakeCursor(row=row)
        self.commits = 0

    def cursor(self):
        return self.cursor_obj

    def commit(self) -> None:
        self.commits += 1


class DatabaseSqlTests(unittest.TestCase):
    def build_logger(self, *, row=None) -> tuple[SQLServerLogger, FakeConnection]:
        logger = SQLServerLogger(
            DatabaseConfig(
                driver="ODBC Driver 17 for SQL Server",
                host="127.0.0.1",
                port=1433,
                database="JianSun",
                username="sa",
                password="x",
            )
        )
        connection = FakeConnection(row=row)
        logger.connection = connection
        return logger, connection

    def test_upsert_publish_task_uses_merge(self) -> None:
        logger, connection = self.build_logger()

        logger.upsert_publish_task(
            {
                "task_id": "TASK-001",
                "source_type": "excel",
                "source_record_id": "SRC-001",
                "channel": "1688",
                "store_name": "demo-store",
                "store_label": "阿里巴巴-demo-store",
                "outer_sku": "SKU-001",
                "title": "测试商品",
                "category_hint": "客厅家具",
                "brand": "品牌A",
                "material": "实木",
                "color": "原木色",
                "size": "120x80x75cm",
                "price": "1299",
                "quantity": "20",
                "main_image": "D:/images/main.jpg",
                "detail_images": "D:/images/1.jpg|D:/images/2.jpg",
                "description": "测试描述",
                "ship_from_template": "常州仓",
                "freight_template": "日常模板",
                "ship_time_template": "24小时发货",
                "length_cm": "120",
                "width_cm": "60",
                "height_cm": "75",
                "weight_g": "37600",
                "link_owner": "张三",
                "operator_name": "系统联调",
                "status": "running",
            }
        )

        sql, params = connection.cursor_obj.calls[0]
        self.assertIn("MERGE dbo.publish_task AS target", sql)
        self.assertEqual(len(params), 28)
        self.assertEqual(params[-1], "running")
        self.assertEqual(connection.commits, 1)

    def test_upsert_category_mapping_history_tracks_success_flags(self) -> None:
        logger, connection = self.build_logger()

        logger.upsert_category_mapping_history(
            {
                "channel": "1688",
                "store_name": "demo-store",
                "keyword_text": "客厅家具",
                "category_path": "家具 > 客厅家具",
                "success": False,
            }
        )

        sql, params = connection.cursor_obj.calls[0]
        self.assertIn("MERGE dbo.category_mapping_history AS target", sql)
        self.assertEqual(len(params), 12)
        self.assertEqual(params[4], 0)
        self.assertEqual(params[5], 1)
        self.assertEqual(connection.commits, 1)

    def test_upsert_store_default_template_uses_merge(self) -> None:
        logger, connection = self.build_logger()

        logger.upsert_store_default_template(
            {
                "channel": "1688",
                "store_name": "demo-store",
                "ship_from_template": "常州仓",
                "freight_template": "日常模板",
                "ship_time_template": "24小时发货",
                "default_length_cm": "120",
                "default_width_cm": "60",
                "default_height_cm": "75",
                "default_weight_g": "37600",
            }
        )

        sql, params = connection.cursor_obj.calls[0]
        self.assertIn("MERGE dbo.store_default_template AS target", sql)
        self.assertEqual(len(params), 9)
        self.assertEqual(params[0], "1688")
        self.assertEqual(connection.commits, 1)

    def test_get_category_mapping_prefers_store_specific_history(self) -> None:
        class Row:
            category_path = "家具 > 客厅家具"

            def __getitem__(self, index):
                return self.category_path

        logger, connection = self.build_logger(row=Row())

        result = logger.get_category_mapping(
            channel="1688",
            store_name="demo-store",
            keyword_text="客厅家具",
        )

        sql, params = connection.cursor_obj.calls[0]
        self.assertIn("SELECT TOP 1 category_path", sql)
        self.assertEqual(params, ["1688", "demo-store", "客厅家具", "demo-store"])
        self.assertEqual(result, "家具 > 客厅家具")

    def test_choose_preferred_sqlserver_driver_prefers_installed_known_driver(self) -> None:
        fake_pyodbc = mock.Mock()
        fake_pyodbc.drivers.return_value = ["SQL Server", "SQL Server Native Client 10.0"]

        with mock.patch("database._require_pyodbc", return_value=fake_pyodbc):
            driver = choose_preferred_sqlserver_driver()

        self.assertEqual(driver, "SQL Server Native Client 10.0")

    def test_database_config_from_json_falls_back_when_requested_driver_is_missing(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "database.local.json"
            config_path.write_text(
                """
                {
                  "driver": "ODBC Driver 17 for SQL Server",
                  "host": "192.168.151.76",
                  "port": 1433,
                  "database": "JianSun",
                  "username": "sa",
                  "password": "secret"
                }
                """.strip(),
                encoding="utf-8",
            )

            fake_pyodbc = mock.Mock()
            fake_pyodbc.drivers.return_value = ["SQL Server", "SQL Server Native Client 10.0"]

            with mock.patch("database._require_pyodbc", return_value=fake_pyodbc):
                config = DatabaseConfig.from_json(config_path)

        self.assertEqual(config.driver, "SQL Server Native Client 10.0")


if __name__ == "__main__":
    unittest.main()
