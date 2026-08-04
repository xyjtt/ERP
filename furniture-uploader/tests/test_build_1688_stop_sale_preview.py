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
    BUSINESS_SKIP_REASON,
    BUSINESS_SKIP_REASON_CODE,
    HANDLING,
    METRIC_DATE,
    ONLINE_SKU,
    PLATFORM,
    PRODUCT_ID,
    STORE_NAME,
    REPLACEMENT_HANDLING,
    REPLACEMENT_SKU,
    REJECTED_REASON_CODE,
    build_preview_outputs,
    config_from_env,
    dedupe_rows,
    mask_network_endpoint,
    normalize_value,
    partition_replacement_rows,
    hydrate_preview_database_credentials,
    safe_filename,
    send_replacement_rejection_notification,
    task_key,
    validate_replacement_rows,
    parse_args,
)


class Build1688StopSalePreviewTests(unittest.TestCase):
    def test_replacement_preview_uses_new_header_and_replace_prefix(self) -> None:
        rows = [{
            STORE_NAME: "阿里巴巴-常州工莱家具",
            PLATFORM: "Alibaba",
            PRODUCT_ID: "1001",
            ONLINE_SKU: "OLD-A",
            REPLACEMENT_SKU: "NEW-A",
            HANDLING: REPLACEMENT_HANDLING,
            METRIC_DATE: "2026-07-23",
        }]
        with tempfile.TemporaryDirectory() as temp_dir:
            output = build_preview_outputs(
                rows=rows,
                output_dir=Path(temp_dir),
                metric_date=date(2026, 7, 23),
                handling=REPLACEMENT_HANDLING,
            )
            with Path(output["preview_csv"]).open("r", encoding="utf-8-sig", newline="") as handle:
                header = next(csv.reader(handle))

        self.assertEqual(output["operation"], "replace")
        self.assertIn("1688_sku_replace_preview", Path(output["preview_csv"]).name)
        self.assertIn(REPLACEMENT_SKU, header)

    def test_replacement_validation_rejects_conflicting_mappings(self) -> None:
        rows = [
            {STORE_NAME: "S", PRODUCT_ID: "P", ONLINE_SKU: "OLD", REPLACEMENT_SKU: "NEW-1"},
            {STORE_NAME: "S", PRODUCT_ID: "P", ONLINE_SKU: "OLD", REPLACEMENT_SKU: "NEW-2"},
        ]
        with self.assertRaisesRegex(ValueError, "映射到多个目标编码"):
            validate_replacement_rows(rows)

    def test_replacement_preview_skips_combination_sku_and_keeps_valid_rows(self) -> None:
        rows = [
            {
                STORE_NAME: "阿里巴巴-常州工莱家具",
                PLATFORM: "Alibaba",
                PRODUCT_ID: "1001",
                ONLINE_SKU: "OLD-A",
                REPLACEMENT_SKU: "NEW-A",
                HANDLING: REPLACEMENT_HANDLING,
                METRIC_DATE: "2026-07-23",
            },
            {
                STORE_NAME: "阿里巴巴-常州工莱家具",
                PLATFORM: "Alibaba",
                PRODUCT_ID: "1002",
                ONLINE_SKU: "OLD-B",
                REPLACEMENT_SKU: "运营自行组合替换",
                HANDLING: REPLACEMENT_HANDLING,
                METRIC_DATE: "2026-07-23",
            },
        ]
        with tempfile.TemporaryDirectory() as temp_dir:
            output = build_preview_outputs(
                rows=rows,
                output_dir=Path(temp_dir),
                metric_date=date(2026, 7, 23),
                handling=REPLACEMENT_HANDLING,
            )
            with Path(output["preview_csv"]).open("r", encoding="utf-8-sig", newline="") as handle:
                executable = list(csv.DictReader(handle))
            with Path(output["rejected_csv"]).open("r", encoding="utf-8-sig", newline="") as handle:
                rejected = list(csv.DictReader(handle))
            with Path(output["business_skipped_csv"]).open(
                "r", encoding="utf-8-sig", newline=""
            ) as handle:
                business_skipped = list(csv.DictReader(handle))

        self.assertEqual(output["loaded_count"], 2)
        self.assertEqual(output["accepted_count"], 1)
        self.assertEqual(output["selected_count"], 1)
        self.assertEqual(output["rejected_count"], 0)
        self.assertEqual(output["business_skipped_count"], 1)
        self.assertEqual(executable[0][REPLACEMENT_SKU], "NEW-A")
        self.assertEqual(rejected, [])
        self.assertEqual(business_skipped[0][BUSINESS_SKIP_REASON_CODE], "combination_sku")
        self.assertEqual(business_skipped[0][BUSINESS_SKIP_REASON], "组合货号")

    def test_replacement_preview_still_rejects_other_invalid_target_sku(self) -> None:
        rows = [{
            STORE_NAME: "S",
            PRODUCT_ID: "P",
            ONLINE_SKU: "OLD-A",
            REPLACEMENT_SKU: "请运营处理",
        }]

        accepted, business_skipped, rejected = partition_replacement_rows(rows)

        self.assertEqual(accepted, [])
        self.assertEqual(business_skipped, [])
        self.assertEqual(rejected[0][REJECTED_REASON_CODE], "invalid_replacement_sku_format")

    def test_partition_rejects_all_conflict_rows_but_keeps_unrelated_mapping(self) -> None:
        rows = [
            {STORE_NAME: "S", PRODUCT_ID: "P", ONLINE_SKU: "OLD", REPLACEMENT_SKU: "NEW-1"},
            {STORE_NAME: "S", PRODUCT_ID: "P", ONLINE_SKU: "OLD", REPLACEMENT_SKU: "NEW-2"},
            {STORE_NAME: "S", PRODUCT_ID: "P", ONLINE_SKU: "OTHER", REPLACEMENT_SKU: "NEW-3"},
        ]

        accepted, business_skipped, rejected = partition_replacement_rows(rows)

        self.assertEqual([row[ONLINE_SKU] for row in accepted], ["OTHER"])
        self.assertEqual(business_skipped, [])
        self.assertEqual(len(rejected), 2)
        self.assertTrue(all("source_mapping_conflict" in row[REJECTED_REASON_CODE] for row in rejected))

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

    def test_app_source_reuses_app_writer_credential(self) -> None:
        args = parse_args([
            "--database",
            "JSReportReplica",
            "--table",
            "app.op_stop_sale",
        ])
        app_config = type("AppConfig", (), {
            "server": "app-db",
            "port": 1433,
            "database": "JSReportReplica",
            "user": "app-user",
            "password": "app-secret",
            "credential_ref": "credential-ref",
        })()
        with (
            patch("build_1688_stop_sale_preview.resolve_stop_sale_app_config", return_value=app_config),
            patch.dict("os.environ", {}, clear=True),
        ):
            result = hydrate_preview_database_credentials(args)
            config = config_from_env(args)

        self.assertTrue(result["configured"])
        self.assertEqual(result["source"], "app_writer_credential")
        self.assertEqual(config.server, "app-db")
        self.assertEqual(config.user, "app-user")

    def test_rejected_rows_send_dingtalk_exception_notification(self) -> None:
        report = {
            "filters": {METRIC_DATE: "2026-07-23"},
            "loaded_count": 10,
            "selected_count": 8,
            "rejected_count": 2,
            "rejected_reason_counts": {"invalid_replacement_sku_format": 2},
            "rejected_csv": "rejected.csv",
        }
        with (
            patch(
                "build_1688_stop_sale_preview.hydrate_dingtalk_credentials",
                return_value={"DINGTALK_WEBHOOK": True, "DINGTALK_SECRET": True},
            ),
            patch(
                "build_1688_stop_sale_preview.post_dingtalk_text_message",
                return_value=True,
            ) as post_message,
        ):
            result = send_replacement_rejection_notification(
                report,
                shared_runtime_root="D:/runtime",
                disabled=False,
            )

        self.assertEqual(result, {"attempted": True, "sent": True, "reason": "sent"})
        self.assertIn("拒绝：2", post_message.call_args.args[1])



if __name__ == "__main__":
    unittest.main()
