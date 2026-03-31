from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parents[1]
RPA_ROOT = PROJECT_ROOT / "rpa"
if str(RPA_ROOT) not in sys.path:
    sys.path.insert(0, str(RPA_ROOT))

from sku_offline_tasks import (
    ProcessedFileRegistry,
    build_preview_payload,
    dedupe_offline_tasks,
    filter_offline_tasks,
    group_tasks_by_store,
    load_offline_tasks,
)


class OfflineTaskTests(unittest.TestCase):
    def build_input_config(self) -> dict:
        return {
            "sheet_name": "停产下架通知-链接维度",
            "filters": {
                "platform": "Alibaba",
                "handling": "全渠道下架"
            },
            "columns": {
                "store_name": "店铺名称",
                "platform": "平台",
                "product_id": "商品ID",
                "platform_store_item_code": "平台店铺商品编码",
                "online_sku": "线上商品编码",
                "handling": "处理说明",
                "replacement_sku": "可替换商品编码",
                "change_image": "是否换图"
            }
        }

    def test_load_offline_tasks_from_excel_sheet(self) -> None:
        dataframe = pd.DataFrame(
            [
                {
                    "店铺名称": "速班达家居",
                    "平台": "Alibaba",
                    "商品ID": "963374911361",
                    "平台店铺商品编码": "ziluo02",
                    "线上商品编码": "SYT000601N11",
                    "处理说明": "全渠道下架",
                    "可替换商品编码": "",
                    "是否换图": "否"
                }
            ]
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "offline.xlsx"
            dataframe.to_excel(path, sheet_name="停产下架通知-链接维度", index=False)

            tasks = load_offline_tasks(path, self.build_input_config())

            self.assertEqual(len(tasks), 1)
            self.assertEqual(tasks[0].store_name, "速班达家居")
            self.assertEqual(tasks[0].product_id, "963374911361")
            self.assertEqual(tasks[0].source_row_number, 2)

    def test_filter_and_dedupe_tasks_respects_store_product_sku_key(self) -> None:
        dataframe = pd.DataFrame(
            [
                {
                    "店铺名称": "速班达家居",
                    "平台": "Alibaba",
                    "商品ID": "963374911361",
                    "平台店铺商品编码": "ziluo02",
                    "线上商品编码": "SYT000601N11",
                    "处理说明": "全渠道下架",
                    "可替换商品编码": "",
                    "是否换图": "否"
                },
                {
                    "店铺名称": "速班达家居",
                    "平台": "Alibaba",
                    "商品ID": "963374911361",
                    "平台店铺商品编码": "ziluo02",
                    "线上商品编码": "SYT000601N11",
                    "处理说明": "全渠道下架",
                    "可替换商品编码": "",
                    "是否换图": "否"
                },
                {
                    "店铺名称": "另一个店铺",
                    "平台": "Alibaba",
                    "商品ID": "963374911361",
                    "平台店铺商品编码": "ziluo02",
                    "线上商品编码": "SYT000601N11",
                    "处理说明": "全渠道下架",
                    "可替换商品编码": "",
                    "是否换图": "否"
                },
                {
                    "店铺名称": "速班达家居",
                    "平台": "Tmall",
                    "商品ID": "111",
                    "平台店铺商品编码": "tmall01",
                    "线上商品编码": "ABC",
                    "处理说明": "全渠道下架",
                    "可替换商品编码": "",
                    "是否换图": "否"
                }
            ]
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "offline.xlsx"
            dataframe.to_excel(path, sheet_name="停产下架通知-链接维度", index=False)
            tasks = load_offline_tasks(path, self.build_input_config())
            filtered, skipped = filter_offline_tasks(tasks, self.build_input_config()["filters"])
            deduped, duplicates = dedupe_offline_tasks(filtered)

            self.assertEqual(len(skipped), 1)
            self.assertEqual(len(deduped), 2)
            self.assertEqual(len(duplicates), 1)
            self.assertEqual(deduped[0].dedupe_key, ("速班达家居", "963374911361", "SYT000601N11"))
            self.assertEqual(deduped[1].dedupe_key, ("另一个店铺", "963374911361", "SYT000601N11"))

    def test_processed_file_registry_uses_file_name_and_timestamp(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            registry_path = Path(temp_dir) / "registry.jsonl"
            task_file = Path(temp_dir) / "offline.xlsx"
            task_file.write_text("demo", encoding="utf-8")

            registry = ProcessedFileRegistry(registry_path)
            self.assertFalse(registry.contains(task_file))
            registry.record(path=task_file, status="processed", moved_to=str(task_file))
            self.assertTrue(registry.contains(task_file))

    def test_build_preview_payload_groups_by_store(self) -> None:
        dataframe = pd.DataFrame(
            [
                {
                    "店铺名称": "速班达家居",
                    "平台": "Alibaba",
                    "商品ID": "963374911361",
                    "平台店铺商品编码": "ziluo02",
                    "线上商品编码": "SYT000601N11",
                    "处理说明": "全渠道下架",
                    "可替换商品编码": "",
                    "是否换图": "否"
                },
                {
                    "店铺名称": "另一个店铺",
                    "平台": "Alibaba",
                    "商品ID": "963374911362",
                    "平台店铺商品编码": "ziluo03",
                    "线上商品编码": "SYT000602N11",
                    "处理说明": "全渠道下架",
                    "可替换商品编码": "",
                    "是否换图": "否"
                }
            ]
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "offline.xlsx"
            dataframe.to_excel(path, sheet_name="停产下架通知-链接维度", index=False)
            tasks = load_offline_tasks(path, self.build_input_config())
            groups = group_tasks_by_store(tasks)
            preview = build_preview_payload(
                source_files=[str(path)],
                loaded_tasks=tasks,
                selected_tasks=tasks,
                filtered_out_tasks=[],
                duplicate_tasks=[],
            )

            self.assertEqual(set(groups.keys()), {"速班达家居", "另一个店铺"})
            self.assertEqual(preview["stores"]["速班达家居"], 1)
            self.assertEqual(preview["stores"]["另一个店铺"], 1)


if __name__ == "__main__":
    unittest.main()
