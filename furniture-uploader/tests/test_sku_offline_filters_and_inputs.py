from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
RPA_ROOT = PROJECT_ROOT / "rpa"
if str(RPA_ROOT) not in sys.path:
    sys.path.insert(0, str(RPA_ROOT))

from sku_offline_main import load_preview_for_files, resolve_input_files
from sku_offline_tasks import OfflineTask, filter_offline_tasks


class SkuOfflineFiltersAndInputsTests(unittest.TestCase):
    def build_task(self, *, store_name: str, product_id: str, online_sku: str) -> OfflineTask:
        return OfflineTask(
            source_file="demo.xlsx",
            source_sheet="Sheet1",
            source_row_number=2,
            store_name=store_name,
            platform="Alibaba",
            product_id=product_id,
            online_sku=online_sku,
            handling="全渠道下架",
            replacement_sku="",
            change_image="",
            platform_store_item_code="",
            raw={},
        )

    def test_filter_offline_tasks_respects_store_name(self) -> None:
        tasks = [
            self.build_task(
                store_name="阿里巴巴-常州速班达家居有限公司",
                product_id="1001",
                online_sku="SKU001",
            ),
            self.build_task(
                store_name="阿里巴巴-其他店铺",
                product_id="1002",
                online_sku="SKU002",
            ),
        ]

        filtered, skipped = filter_offline_tasks(
            tasks,
            {
                "store_name": "阿里巴巴-常州速班达家居有限公司",
                "platform": "Alibaba",
                "handling": "全渠道下架",
            },
        )

        self.assertEqual(len(filtered), 1)
        self.assertEqual(filtered[0].store_name, "阿里巴巴-常州速班达家居有限公司")
        self.assertEqual(len(skipped), 1)

    def test_filter_offline_tasks_matches_store_short_name_against_company_name(self) -> None:
        tasks = [
            self.build_task(
                store_name="速班达家居",
                product_id="1001",
                online_sku="SKU001",
            ),
            self.build_task(
                store_name="阿里巴巴-其他店铺",
                product_id="1002",
                online_sku="SKU002",
            ),
        ]

        filtered, skipped = filter_offline_tasks(
            tasks,
            {
                "store_name": "阿里巴巴-常州速班达家居有限公司",
                "platform": "Alibaba",
                "handling": "全渠道下架",
            },
        )

        self.assertEqual(len(filtered), 1)
        self.assertEqual(filtered[0].store_name, "速班达家居")
        self.assertEqual(len(skipped), 1)

    def test_load_preview_for_files_adds_filter_hint_when_store_does_not_match(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            csv_path = Path(temp_dir) / "offline.csv"
            csv_path.write_text(
                (
                    "店铺名称,平台,商品ID,平台店铺商品编码,线上商品编码,处理说明,可替换商品编码,是否换图\n"
                    "速班达家居,Alibaba,963374911361,ziluo02,SYT000601N11,全渠道下架,,否\n"
                ),
                encoding="utf-8",
            )
            system_config = {
                "input": {
                    "sheet_name": "停产下架通知-链接维度",
                    "filters": {
                        "store_name": "阿里巴巴-完全不匹配店铺",
                        "platform": "Alibaba",
                        "handling": "全渠道下架",
                    },
                    "columns": {
                        "store_name": "店铺名称",
                        "platform": "平台",
                        "product_id": "商品ID",
                        "platform_store_item_code": "平台店铺商品编码",
                        "online_sku": "线上商品编码",
                        "handling": "处理说明",
                        "replacement_sku": "可替换商品编码",
                        "change_image": "是否换图",
                    },
                }
            }

            preview = load_preview_for_files(system_config, [csv_path], limit=0)

            self.assertEqual(preview["selected_count"], 0)
            self.assertIn("No tasks matched the configured store filter", preview.get("filter_hint", ""))
            self.assertIn("速班达家居", preview.get("loaded_store_names", []))

    def test_resolve_input_files_from_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            directory = Path(temp_dir)
            csv_path = directory / "offline.csv"
            xlsx_path = directory / "offline.xlsx"
            txt_path = directory / "ignore.txt"
            csv_path.write_text("demo", encoding="utf-8")
            xlsx_path.write_text("demo", encoding="utf-8")
            txt_path.write_text("demo", encoding="utf-8")

            files = resolve_input_files(file_path="", dir_path=str(directory))

            self.assertEqual(files, [csv_path, xlsx_path])

    def test_resolve_input_files_rejects_file_and_directory_together(self) -> None:
        with self.assertRaises(ValueError):
            resolve_input_files(file_path="a.xlsx", dir_path="D:/demo")


if __name__ == "__main__":
    unittest.main()
