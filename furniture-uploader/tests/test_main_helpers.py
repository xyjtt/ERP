from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace


PROJECT_ROOT = Path(__file__).resolve().parents[1]
RPA_ROOT = PROJECT_ROOT / "rpa"
if str(RPA_ROOT) not in sys.path:
    sys.path.insert(0, str(RPA_ROOT))

from main import (
    apply_category_history,
    build_category_mapping_payload,
    build_publish_task_payload,
    build_store_default_template_payload,
    initialize_db_config_template,
    initialize_operator_local_config,
    resolve_category_keyword,
)


class MainHelperTests(unittest.TestCase):
    def build_product(self) -> SimpleNamespace:
        return SimpleNamespace(
            task_id="TASK-001",
            channel="1688",
            store_name="demo-store",
            store_label="阿里巴巴-demo-store",
            outer_sku="SKU-001",
            title="北欧餐桌",
            platform_category="living_room",
            price="1299",
            quantity="20",
            ship_from_template="常州仓",
            freight_template="日常模板",
            ship_time_template="24小时发货",
            length_cm="120",
            width_cm="60",
            height_cm="75",
            weight_g="37600",
            link_owner="张三",
            operator_name="系统联调",
            source_record_id="SRC-001",
            raw={
                "brand": "木作时光",
                "material": "白蜡木",
                "color": "原木色",
                "size": "120x80x75cm",
                "main_image": "D:/images/main.jpg",
                "detail_images": "D:/images/1.jpg|D:/images/2.jpg",
                "description": "测试描述",
                "category_hint": "客厅家具",
            },
        )

    def test_initialize_db_config_template_copies_example(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_dir = Path(temp_dir)
            example_path = config_dir / "database.example.json"
            example_path.write_text('{"host":"127.0.0.1"}', encoding="utf-8")

            target = initialize_db_config_template(config_dir)

            self.assertTrue((config_dir / "database.local.json").exists())
            self.assertIn('"host": "127.0.0.1"', Path(target).read_text(encoding="utf-8"))

    def test_initialize_operator_local_config_creates_private_template(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_dir = Path(temp_dir)

            target = initialize_operator_local_config(config_dir)
            content = Path(target).read_text(encoding="utf-8")

            self.assertTrue((config_dir / "operator_config.local.json").exists())
            self.assertIn('"1688"', content)
            self.assertIn('"username": ""', content)

    def test_apply_category_history_prefers_database_mapping(self) -> None:
        product = self.build_product()

        class FakeDb:
            def get_category_mapping(self, *, channel, store_name, keyword_text):
                self.last_lookup = (channel, store_name, keyword_text)
                return "数据库类目 > 客厅家具"

        db = FakeDb()
        result = apply_category_history([product], "1688", {}, db)

        self.assertEqual(db.last_lookup, ("1688", "demo-store", "客厅家具"))
        self.assertEqual(result["living_room"]["platform_categories"]["1688"], "数据库类目 > 客厅家具")

    def test_build_publish_task_payload_contains_status_and_dimensions(self) -> None:
        product = self.build_product()
        payload = build_publish_task_payload(product, "1688", status="running")

        self.assertEqual(payload["status"], "running")
        self.assertEqual(payload["weight_g"], "37600")
        self.assertEqual(payload["category_hint"], "living_room")

    def test_build_store_default_template_payload_uses_store_level_defaults(self) -> None:
        product = self.build_product()
        payload = build_store_default_template_payload(product, "1688")

        self.assertEqual(payload["channel"], "1688")
        self.assertEqual(payload["store_name"], "demo-store")
        self.assertEqual(payload["default_height_cm"], "75")

    def test_build_category_mapping_payload_uses_runtime_category_when_present(self) -> None:
        product = self.build_product()
        payload = build_category_mapping_payload(
            product,
            "1688",
            {"resolved_category_name": "家具 > 客厅家具"},
            success=True,
        )

        self.assertEqual(payload["category_path"], "家具 > 客厅家具")
        self.assertEqual(payload["keyword_text"], "客厅家具")
        self.assertTrue(payload["success"])

    def test_resolve_category_keyword_falls_back(self) -> None:
        product = self.build_product()
        product.raw["category_hint"] = ""

        self.assertEqual(resolve_category_keyword(product), "living_room")
        product.platform_category = ""
        self.assertEqual(resolve_category_keyword(product), "北欧餐桌")


if __name__ == "__main__":
    unittest.main()
