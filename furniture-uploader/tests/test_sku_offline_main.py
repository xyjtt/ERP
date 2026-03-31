from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace


PROJECT_ROOT = Path(__file__).resolve().parents[1]
RPA_ROOT = PROJECT_ROOT / "rpa"
if str(RPA_ROOT) not in sys.path:
    sys.path.insert(0, str(RPA_ROOT))

from exceptions import PublishValidationError
from sku_offline_main import (
    build_failure_notification_content,
    build_run_report_payload,
    build_summary_notification_content,
    classify_offline_error,
    localize_error_category,
)


class SkuOfflineMainTests(unittest.TestCase):
    def build_task(self) -> SimpleNamespace:
        return SimpleNamespace(
            store_name="阿里巴巴-常州速班达家居有限公司",
            platform="Alibaba",
            product_id="963374911361",
            online_sku="SZ018003N371V01",
            handling="全渠道下架",
            source_file="demo.xlsx",
            source_sheet="停产下架通知-链接维度",
            source_row_number=2,
        )

    def test_build_run_report_payload_keeps_business_validation_details(self) -> None:
        task = self.build_task()
        result_context = {
            "page_error_category": "business_validation",
            "page_error_stage": "post_offline_submit",
            "page_error_text": "“加工方式”不能为空",
        }

        payload = build_run_report_payload(
            task=task,
            status="failed",
            attempts=2,
            result_context=result_context,
            exc=PublishValidationError("post_offline_submit blocked by page validation: “加工方式”不能为空"),
        )

        self.assertEqual(payload["error_category"], "business_validation")
        self.assertEqual(payload["page_error_stage"], "post_offline_submit")
        self.assertEqual(payload["page_error_text"], "“加工方式”不能为空")

    def test_localize_error_category_returns_chinese_label(self) -> None:
        self.assertEqual(localize_error_category("login_required"), "登录态失效")
        self.assertEqual(localize_error_category("business_validation"), "业务校验失败")
        self.assertEqual(localize_error_category("sku_not_found"), "SKU未匹配")
        self.assertEqual(localize_error_category("submit_failed"), "提交失败")
        self.assertEqual(localize_error_category(""), "未知异常")

    def test_classify_offline_error_maps_login_required(self) -> None:
        class OfflineLoginRequiredError(Exception):
            pass

        category = classify_offline_error(OfflineLoginRequiredError("login expired"), {})
        self.assertEqual(category, "login_required")

    def test_classify_offline_error_maps_sku_not_found_when_page_has_visible_skus(self) -> None:
        class OfflineTaskNotFoundError(Exception):
            pass

        category = classify_offline_error(
            OfflineTaskNotFoundError(
                "SKU 'SYT000601N11' was not found on the edit page. "
                "Available single-SKU codes: SZ018005, SZ018003N371V01"
            ),
            {},
        )
        self.assertEqual(category, "sku_not_found")

    def test_build_failure_notification_content_includes_chinese_context(self) -> None:
        content = build_failure_notification_content(
            {
                "store_name": "阿里巴巴-常州速班达家居有限公司",
                "product_id": "963374911361",
                "online_sku": "SZ018003N371V01",
                "attempts": 2,
                "error_category": "business_validation",
                "page_error_stage": "post_offline_submit",
                "page_error_text": "“加工方式”不能为空",
                "error_message": "post_offline_submit blocked by page validation: “加工方式”不能为空",
                "screenshot_path": "logs/screenshots/a.png",
                "html_snapshot_path": "logs/html_snapshots/a.html",
            }
        )

        self.assertIn("错误分类：业务校验失败", content)
        self.assertIn("页面阶段：post_offline_submit", content)
        self.assertIn("页面提示：“加工方式”不能为空", content)
        self.assertIn("截图路径：logs/screenshots/a.png", content)

    def test_build_summary_notification_content_uses_chinese_labels(self) -> None:
        content = build_summary_notification_content(
            {
                "total": 10,
                "success": 3,
                "already_offline": 4,
                "failed": 3,
                "duplicate_count": 2,
                "filtered_out_count": 8,
                "report_path": "logs/sku_offline/run_reports/demo.jsonl",
                "summary_path": "logs/sku_offline/run_reports/demo.summary.json",
            }
        )

        self.assertIn("1688 SKU下架批次完成", content)
        self.assertIn("任务总数：10", content)
        self.assertIn("执行成功：3", content)
        self.assertIn("汇总路径：logs/sku_offline/run_reports/demo.summary.json", content)


if __name__ == "__main__":
    unittest.main()
