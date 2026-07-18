from __future__ import annotations

import csv
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_ROOT = PROJECT_ROOT / "scripts"
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

from build_1688_stop_sale_preview import (  # noqa: E402
    HANDLING,
    METRIC_DATE,
    ONLINE_SKU,
    PLATFORM,
    PRODUCT_ID,
    STORE_NAME,
    build_preview_outputs,
    config_from_env,
    dedupe_rows,
    mask_network_endpoint,
    normalize_value,
    safe_filename,
    task_key,
    parse_args,
)


class Build1688StopSalePreviewTests(unittest.TestCase):
    def test_normalize_value_trims_float_like_ids(self) -> None:
        self.assertEqual(normalize_value("1000406623557.0"), "1000406623557")
        self.assertEqual(normalize_value(date(2026, 7, 16)), "2026-07-16")

    def test_task_key_and_dedupe_use_store_product_sku(self) -> None:
        rows = [
            {
                STORE_NAME: "阿里巴巴-常州工莱家具",
                PRODUCT_ID: "1001",
                ONLINE_SKU: "SKU-A",
                PLATFORM: "Alibaba",
                HANDLING: "全渠道下架",
                METRIC_DATE: "2026-07-16",
            },
            {
                STORE_NAME: "阿里巴巴-常州工莱家具",
                PRODUCT_ID: "1001",
                ONLINE_SKU: "SKU-A",
                PLATFORM: "Alibaba",
                HANDLING: "全渠道下架",
                METRIC_DATE: "2026-07-16",
            },
            {
                STORE_NAME: "阿里巴巴-常州乐畅家居有限公司",
                PRODUCT_ID: "1001",
                ONLINE_SKU: "SKU-A",
                PLATFORM: "Alibaba",
                HANDLING: "全渠道下架",
                METRIC_DATE: "2026-07-16",
            },
        ]

        selected, duplicates = dedupe_rows(rows)

        self.assertEqual(task_key(rows[0]), ("阿里巴巴-常州工莱家具", "1001", "SKU-A"))
        self.assertEqual(len(selected), 2)
        self.assertEqual(len(duplicates), 1)
        self.assertEqual(duplicates[0]["dedupe_key"][STORE_NAME], "阿里巴巴-常州工莱家具")

    def test_build_preview_outputs_writes_aggregate_and_store_csvs(self) -> None:
        rows = [
            {
                STORE_NAME: "阿里巴巴-常州工莱家具",
                PLATFORM: "Alibaba",
                PRODUCT_ID: "1001",
                "平台店铺商品编码": "P1001",
                ONLINE_SKU: "SKU-A",
                HANDLING: "全渠道下架",
                "可替换商品编码": "",
                "是否换图": "",
                METRIC_DATE: "2026-07-16",
            },
            {
                STORE_NAME: "阿里巴巴-常州工莱家具",
                PLATFORM: "Alibaba",
                PRODUCT_ID: "1001",
                "平台店铺商品编码": "P1001",
                ONLINE_SKU: "SKU-A",
                HANDLING: "全渠道下架",
                "可替换商品编码": "",
                "是否换图": "",
                METRIC_DATE: "2026-07-16",
            },
            {
                STORE_NAME: "阿里巴巴-广州沃来贸易有限公司",
                PLATFORM: "Alibaba",
                PRODUCT_ID: "1002",
                "平台店铺商品编码": "P1002",
                ONLINE_SKU: "SKU-B",
                HANDLING: "全渠道下架",
                "可替换商品编码": "",
                "是否换图": "",
                METRIC_DATE: "2026-07-16",
            },
        ]
        with tempfile.TemporaryDirectory() as temp_dir:
            output = build_preview_outputs(
                rows=rows,
                output_dir=Path(temp_dir),
                metric_date=date(2026, 7, 16),
            )

            self.assertEqual(output["loaded_count"], 3)
            self.assertEqual(output["selected_count"], 2)
            self.assertEqual(output["duplicate_count"], 1)
            self.assertEqual(output["selected_store_counts"]["阿里巴巴-常州工莱家具"], 1)
            self.assertEqual(len(output["per_store_preview_csv"]), 2)

            with Path(output["preview_csv"]).open("r", encoding="utf-8-sig", newline="") as handle:
                rows_from_csv = list(csv.DictReader(handle))
            self.assertEqual(len(rows_from_csv), 2)
            self.assertEqual(rows_from_csv[0][STORE_NAME], "阿里巴巴-常州工莱家具")

    def test_safe_filename_keeps_chinese_store_name(self) -> None:
        self.assertEqual(safe_filename("阿里巴巴-常州工莱家具"), "阿里巴巴-常州工莱家具")

    def test_mask_network_endpoint_redacts_ip_address(self) -> None:
        self.assertEqual(mask_network_endpoint("218.93.191.16"), "218.93.***.***")

    def test_source_environment_names_fall_back_to_legacy_variables(self) -> None:
        with patch.dict(
            "os.environ",
            {
                "STOP_SALE_SQLSERVER_HOST": "legacy-source",
                "STOP_SALE_SQLSERVER_USER": "reader",
                "STOP_SALE_SQLSERVER_PASSWORD": "secret",
            },
            clear=True,
        ):
            config = config_from_env(parse_args([]))

        self.assertEqual(config.server, "legacy-source")
        self.assertEqual(config.database, "JSDataMiddlePlatform")
        self.assertEqual(config.user, "reader")



if __name__ == "__main__":
    unittest.main()
