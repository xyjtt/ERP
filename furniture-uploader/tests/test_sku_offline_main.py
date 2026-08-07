from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


PROJECT_ROOT = Path(__file__).resolve().parents[1]
RPA_ROOT = PROJECT_ROOT / "rpa"
if str(RPA_ROOT) not in sys.path:
    sys.path.insert(0, str(RPA_ROOT))

from exceptions import (
    OfflineAccountMappingError,
    OfflineLoginRequiredError,
    OfflineRiskControlError,
    PublishSubmitError,
    PublishValidationError,
)
from sku_offline_main import (
    build_jushuitan_handoff_records,
    build_jushuitan_store_name_map,
    build_jushuitan_sync_records,
    build_store_operator_config,
    build_failure_notification_content,
    build_run_report_payload,
    build_summary_notification_content,
    classify_offline_error,
    localize_error_category,
    requires_browser_recovery,
    resolve_store_account_binding,
    resolve_jushuitan_store_name,
    should_retry_offline_error,
    should_stop_store_on_error,
    execute_preview,
)
from sku_offline_tasks import OfflineTask


class FakeRunReport:
    def __init__(self) -> None:
        self.rows: list[dict] = []

    def append(self, payload: dict) -> None:
        self.rows.append(payload)

    def info(self) -> dict[str, str]:
        return {
            "report_path": "logs/sku_offline/run_reports/fake.jsonl",
            "summary_path": "logs/sku_offline/run_reports/fake.summary.json",
        }


class SkuOfflineMainTests(unittest.TestCase):
    def test_default_store_stop_scope_is_only_true_store_mismatch(self) -> None:
        self.assertTrue(should_stop_store_on_error("store_mismatch", {}))
        self.assertFalse(should_stop_store_on_error("login_required", {}))
        self.assertFalse(should_stop_store_on_error("risk_control", {}))
        self.assertFalse(should_stop_store_on_error("browser_window_closed", {}))

    def test_browser_recovery_covers_renderer_window_and_management_timeouts(self) -> None:
        self.assertTrue(requires_browser_recovery("automation_error"))
        self.assertTrue(requires_browser_recovery("browser_window_closed"))
        self.assertTrue(requires_browser_recovery("management_search_timeout"))
        self.assertFalse(requires_browser_recovery("store_mismatch"))

    def test_replacement_builds_sync_by_link_handoff(self) -> None:
        task = OfflineTask(
            source_file="replace.csv",
            source_sheet="CSV",
            source_row_number=2,
            store_name="阿里巴巴-常州工莱家具",
            platform="Alibaba",
            product_id="732745838005",
            online_sku="OLD",
            handling="全渠道替换",
            replacement_sku="NEW",
            change_image="",
            platform_store_item_code="CODE",
            raw={},
        )
        records = build_jushuitan_sync_records(
            {"selected_tasks": [task]},
            successful_task_statuses={task.dedupe_key: "success"},
        )

        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["action"], "sync_by_link")
        self.assertEqual(records[0]["replacement_sku"], "NEW")
        self.assertEqual(records[0]["source_status"], "success")

    def test_handoff_separates_business_and_jushuitan_store_identity(self) -> None:
        task = OfflineTask(
            source_file="replace.csv",
            source_sheet="CSV",
            source_row_number=2,
            store_name="新佰广1688",
            platform="Alibaba",
            product_id="926805014623",
            online_sku="BG003124N113V01",
            handling="全渠道替换",
            replacement_sku="BG003124N113V02",
            change_image="",
            platform_store_item_code="5976099823638",
            raw={},
        )
        mapping = {"新佰广1688": "阿里巴巴-新佰广"}
        record = build_jushuitan_sync_records(
            {"selected_tasks": [task]},
            successful_task_statuses={task.dedupe_key: "success"},
            jushuitan_store_names=mapping,
        )[0]

        self.assertEqual(record["store_name"], "新佰广1688")
        self.assertEqual(record["jushuitan_store_name"], "阿里巴巴-新佰广")
        self.assertEqual(
            record["task_id"],
            build_jushuitan_sync_records(
                {"selected_tasks": [task]},
                successful_task_statuses={task.dedupe_key: "success"},
            )[0]["task_id"],
        )

    def test_non_prefixed_business_store_requires_explicit_jushuitan_mapping(self) -> None:
        binding = {"store_name": "新佰广1688", "account_key": "xinbaiguang_shanzhu"}
        with self.assertRaisesRegex(
            OfflineAccountMappingError,
            "No verified exact Jushuitan store mapping",
        ):
            resolve_jushuitan_store_name(binding, "新佰广1688")
        binding["jushuitan_store_name"] = "阿里巴巴-新佰广"
        self.assertEqual(
            resolve_jushuitan_store_name(binding, "新佰广1688"),
            "阿里巴巴-新佰广",
        )

    def test_store_name_map_uses_authoritative_binding_without_rewriting_task(self) -> None:
        task = OfflineTask(
            source_file="replace.csv", source_sheet="CSV", source_row_number=2,
            store_name="新佰广1688", platform="Alibaba", product_id="1",
            online_sku="OLD", handling="全渠道替换", replacement_sku="NEW",
            change_image="", platform_store_item_code="CODE", raw={},
        )
        config = {
            "execution": {
                "require_store_account_mapping": True,
                "store_accounts": [{
                    "store_name": "新佰广1688",
                    "account_key": "xinbaiguang_shanzhu",
                    "jushuitan_store_name": "阿里巴巴-新佰广",
                }],
            }
        }
        self.assertEqual(
            build_jushuitan_store_name_map(config, [task]),
            {"新佰广1688": "阿里巴巴-新佰广"},
        )

    def test_execute_preview_dispatches_replacement_and_writes_sync_handoff(self) -> None:
        task = OfflineTask(
            source_file="replace.csv", source_sheet="CSV", source_row_number=2,
            store_name="STORE-A", platform="Alibaba", product_id="1001",
            online_sku="OLD", handling="全渠道替换", replacement_sku="NEW",
            change_image="", platform_store_item_code="CODE", raw={},
        )

        class FakeBrowser:
            def __init__(self, *_args, **_kwargs) -> None:
                self.last_result_context = {}
                self.last_screenshot_path = ""
                self.last_html_snapshot_path = ""

            def open(self): return None
            def prepare_session(self, *_args, **_kwargs): return None
            def reset_runtime_artifacts(self): return None
            def close(self): return None
            def execute_offline_group(self, *_args, **_kwargs):
                raise AssertionError("offline execution must not run for replacement")
            def execute_replace_group(self, _config, tasks, **_kwargs):
                return [{"task": tasks[0], "status": "already_replaced", "context": {}}]

        config = {
            "execution": {
                "operation": "replace",
                "require_store_account_mapping": True,
                "store_accounts": [{
                    "store_name": "STORE-A",
                    "jushuitan_store_name": "阿里巴巴-STORE-A",
                    "account_key": "store_a",
                }],
            },
            "notifications": {"dingtalk": {"enabled": False}},
        }
        with (
            tempfile.TemporaryDirectory() as temp_dir,
            patch("sku_offline_main.SkuOfflineBrowser", FakeBrowser),
            patch("sku_offline_main.send_summary_notification"),
        ):
            handoff = Path(temp_dir) / "sync.jsonl"
            summary = execute_preview(
                project_root=PROJECT_ROOT,
                operator_config={"browser": {}},
                system_config=config,
                preview={"selected_tasks": [task], "duplicate_count": 0, "filtered_out_count": 0},
                skip_login=True,
                no_notify=True,
                run_report=FakeRunReport(),  # type: ignore[arg-type]
                jushuitan_handoff_path=handoff,
            )

            payload = handoff.read_text(encoding="utf-8")

        self.assertEqual(summary["already_replaced"], 1)
        self.assertEqual(summary["jushuitan_action"], "sync_by_link")
        self.assertIn('"source_status": "already_replaced"', payload)

    def test_execute_preview_skips_combination_sku_before_browser_creation(self) -> None:
        task = OfflineTask(
            source_file="replace.csv", source_sheet="CSV", source_row_number=2,
            store_name="STORE-A", platform="Alibaba", product_id="1001",
            online_sku="OLD", handling="全渠道替换", replacement_sku="运营自行组合替换",
            change_image="", platform_store_item_code="CODE", raw={},
        )
        config = {
            "execution": {"operation": "replace"},
            "notifications": {"dingtalk": {"enabled": False}},
        }

        with (
            tempfile.TemporaryDirectory() as temp_dir,
            patch(
                "sku_offline_main.SkuOfflineBrowser",
                side_effect=AssertionError("browser must not be created for combination SKU"),
            ),
            patch("sku_offline_main.send_summary_notification"),
        ):
            summary = execute_preview(
                project_root=PROJECT_ROOT,
                operator_config={"browser": {}},
                system_config=config,
                preview={"selected_tasks": [task], "duplicate_count": 0, "filtered_out_count": 0},
                skip_login=True,
                no_notify=True,
                run_report=FakeRunReport(),  # type: ignore[arg-type]
                jushuitan_handoff_path=Path(temp_dir) / "sync.jsonl",
            )

        self.assertEqual(summary["selected_count"], 0)
        self.assertEqual(summary["business_skipped_count"], 1)
        self.assertEqual(summary["business_skipped_tasks"][0]["exception_reason"], "组合货号")

    def build_task(self) -> SimpleNamespace:
        return SimpleNamespace(
            store_name="阿里巴巴-常州速班达家居有限公司",
            platform="Alibaba",
            product_id="963374911361",
            online_sku="SZ018003N371V01",
            platform_store_item_code="ziluo02",
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
        self.assertEqual(payload["platform_store_item_code"], "ziluo02")
        self.assertEqual(payload["page_error_stage"], "post_offline_submit")
        self.assertEqual(payload["page_error_text"], "“加工方式”不能为空")

    def test_localize_error_category_returns_chinese_label(self) -> None:
        self.assertEqual(localize_error_category("login_required"), "登录态失效")
        self.assertEqual(localize_error_category("business_validation"), "业务校验失败")
        self.assertEqual(localize_error_category("sku_not_found"), "SKU未匹配")
        self.assertEqual(localize_error_category("edit_route_rejected"), "编辑入口被拒绝")
        self.assertEqual(localize_error_category("product_unavailable"), "商品不可编辑")
        self.assertEqual(
            localize_error_category("sole_sku_requires_product_offline"),
            "唯一在线SKU禁止单独下架",
        )
        self.assertEqual(localize_error_category("campaign_restriction"), "平台活动限制SKU下架")
        self.assertEqual(localize_error_category("system_prompt"), "系统提示")
        self.assertEqual(localize_error_category("submit_failed"), "提交失败")
        self.assertEqual(localize_error_category("browser_window_closed"), "浏览器窗口异常关闭")
        self.assertEqual(
            localize_error_category("management_tab_mismatch"),
            "商品管理未切换到全部Tab",
        )
        self.assertEqual(
            localize_error_category("delivery_service_backfill_failed"),
            "配送服务自动补全失败",
        )
        self.assertEqual(localize_error_category(""), "未知异常")

    def test_classify_offline_error_maps_login_required(self) -> None:
        class OfflineLoginRequiredError(Exception):
            pass

        category = classify_offline_error(OfflineLoginRequiredError("login expired"), {})
        self.assertEqual(category, "login_required")

    def test_classify_offline_error_maps_identity_mismatch(self) -> None:
        class OfflineIdentityMismatchError(Exception):
            pass

        category = classify_offline_error(OfflineIdentityMismatchError("wrong product"), {})
        self.assertEqual(category, "identity_mismatch")

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
                "auto_login_attempts": 1,
                "auto_login_success": 1,
                "auto_login_failed": 0,
                "duplicate_count": 2,
                "filtered_out_count": 8,
                "stopped_stores": 1,
                "stopped_store_names": ["阿里巴巴-常州工莱家具"],
                "report_path": "logs/sku_offline/run_reports/demo.jsonl",
                "summary_path": "logs/sku_offline/run_reports/demo.summary.json",
            }
        )

        self.assertIn("1688 SKU下架批次完成", content)
        self.assertIn("任务总数：10", content)
        self.assertIn("执行成功：3", content)
        self.assertIn("自动登录：尝试 1，成功 1，失败 0", content)
        self.assertIn("安全停止店铺：1 阿里巴巴-常州工莱家具", content)
        self.assertIn("汇总路径：logs/sku_offline/run_reports/demo.summary.json", content)

    def test_resolve_store_account_binding_matches_alias(self) -> None:
        binding = resolve_store_account_binding(
            {
                "execution": {
                    "require_store_account_mapping": True,
                    "store_accounts": [
                        {
                            "store_name": "阿里巴巴-广州淘淘家居有限公司",
                            "store_aliases": ["广州淘淘家居"],
                            "account_key": "muke_lixiang",
                            "browser_profile_dir": "D:/script_1688/.local/browser_profiles/1688_profile_muke_lixiang",
                        }
                    ],
                }
            },
            "广州淘淘家居",
        )

        self.assertEqual(binding["account_key"], "muke_lixiang")

    def test_build_store_operator_config_injects_profile_without_secrets(self) -> None:
        operator_config = {
            "browser": {
                "headless": False,
                "debugger_address": "127.0.0.1:9222",
            }
        }
        resolved = build_store_operator_config(
            operator_config,
            account_binding={
                "account_key": "gonglai",
                "browser_type": "edge",
                "browser_profile_dir": "D:/script_1688/.local/browser_profiles/1688_profile_gonglai",
            },
            execution_config={"require_browser_profile_exists": False},
        )

        self.assertEqual(resolved["browser"]["sku_offline_account_key"], "gonglai")
        self.assertEqual(resolved["browser"]["browser_type"], "edge")
        self.assertEqual(
            resolved["browser"]["user_data_dir"],
            "D:/script_1688/.local/browser_profiles/1688_profile_gonglai",
        )
        self.assertEqual(resolved["browser"]["debugger_address"], "")
        self.assertEqual(resolved["browser"]["page_load_strategy"], "eager")
        self.assertEqual(resolved["browser"]["page_load_timeout_seconds"], 60)
        self.assertNotIn("password", resolved["browser"])

    def test_should_stop_store_only_on_safety_categories(self) -> None:
        execution_config = {
            "stop_store_on_error_categories": [
                "login_required",
                "risk_control",
                "store_mismatch",
                "identity_mismatch",
                "browser_window_closed",
                "management_tab_mismatch",
            ]
        }

        self.assertTrue(should_stop_store_on_error("identity_mismatch", execution_config))
        self.assertTrue(should_stop_store_on_error("browser_window_closed", execution_config))
        self.assertTrue(should_stop_store_on_error("management_tab_mismatch", execution_config))
        self.assertFalse(should_stop_store_on_error("delivery_service_backfill_failed", execution_config))
        self.assertFalse(should_stop_store_on_error("management_search_timeout", execution_config))
        self.assertFalse(should_stop_store_on_error("system_prompt", execution_config))
        self.assertFalse(should_stop_store_on_error("submit_blocked_before_request", execution_config))
        self.assertFalse(should_stop_store_on_error("sku_not_found", execution_config))

    def test_classify_window_closed_webdriver_error(self) -> None:
        self.assertEqual(
            classify_offline_error(Exception("no such window: target window already closed"), {}),
            "browser_window_closed",
        )
        self.assertEqual(
            classify_offline_error(Exception("tab crashed"), {}),
            "browser_window_closed",
        )

    def test_classify_refused_local_webdriver_session_as_window_closed(self) -> None:
        error = Exception(
            "HTTPConnectionPool(host='localhost', port=63914): Max retries exceeded "
            "with url: /session/abc/window (Caused by NewConnectionError: "
            "Failed to establish a new connection: [WinError 10061] 由于目标计算机积极拒绝)"
        )

        self.assertEqual(
            classify_offline_error(error, {}),
            "browser_window_closed",
        )

    def test_should_not_retry_terminal_item_categories(self) -> None:
        execution_config: dict[str, object] = {}

        self.assertFalse(
            should_retry_offline_error("sole_sku_requires_product_offline", execution_config)
        )
        self.assertFalse(should_retry_offline_error("campaign_restriction", execution_config))
        self.assertFalse(should_retry_offline_error("task_not_found", execution_config))
        self.assertTrue(should_retry_offline_error("management_search_timeout", execution_config))
        self.assertTrue(should_retry_offline_error("submit_failed", execution_config))

    def test_classify_submit_validation_as_sole_sku_terminal(self) -> None:
        error = PublishSubmitError(
            "Submit button was clicked but no submit request was captured. "
            "validation: 是否上架: 有产品规格的商品至少要有一个在线状态的sku。"
        )

        self.assertEqual(
            classify_offline_error(error, {}),
            "sole_sku_requires_product_offline",
        )
        self.assertFalse(
            should_retry_offline_error(
                "sole_sku_requires_product_offline",
                {},
            )
        )

    def test_execute_preview_records_mapping_missing_without_opening_browser(self) -> None:
        task = OfflineTask(
            source_file="demo.csv",
            source_sheet="",
            source_row_number=2,
            store_name="阿里巴巴-未知店铺",
            platform="Alibaba",
            product_id="1001",
            online_sku="SKU-A",
            handling="全渠道下架",
            replacement_sku="",
            change_image="",
            platform_store_item_code="",
            raw={},
        )
        run_report = FakeRunReport()

        summary = execute_preview(
            project_root=PROJECT_ROOT,
            operator_config={"browser": {}},
            system_config={
                "execution": {
                    "require_store_account_mapping": True,
                    "store_accounts": [],
                },
                "notifications": {"dingtalk": {"enabled": False}},
            },
            preview={
                "selected_tasks": [task],
                "duplicate_count": 0,
                "filtered_out_count": 0,
            },
            skip_login=True,
            no_notify=True,
            run_report=run_report,  # type: ignore[arg-type]
        )

        self.assertEqual(summary["failed"], 1)
        self.assertEqual(summary["stopped_stores"], 1)
        self.assertEqual(run_report.rows[0]["error_category"], "account_mapping")
        self.assertEqual(run_report.rows[0]["page_error_stage"], "pre_execution_store_binding")

    def test_business_exception_notifies_and_continues_next_item(self) -> None:
        tasks = [
            OfflineTask(
                source_file="demo.csv",
                source_sheet="CSV",
                source_row_number=index + 2,
                store_name="STORE-A",
                platform="Alibaba",
                product_id=str(1000 + index),
                online_sku=f"SKU-{index}",
                handling="all-channel-offline",
                replacement_sku="",
                change_image="",
                platform_store_item_code=f"CODE-{index}",
                raw={},
            )
            for index in range(2)
        ]

        class FakeBrowser:
            def __init__(self, *_args, **_kwargs) -> None:
                self.calls = 0
                self.last_result_context: dict[str, str] = {}
                self.last_screenshot_path = ""
                self.last_html_snapshot_path = ""

            def open(self) -> None:
                return None

            def prepare_session(self, *_args, **_kwargs) -> None:
                return None

            def reset_runtime_artifacts(self) -> None:
                return None

            def execute_offline_task(self, _system_config, task, **_kwargs):
                self.calls += 1
                if self.calls == 1:
                    self.last_result_context = {
                        "page_error_category": "system_prompt",
                        "page_error_stage": "submit_before_request",
                        "page_error_text": "毛重必须为数字",
                        "system_prompt": "毛重必须为数字",
                    }
                    raise PublishSubmitError("毛重必须为数字")
                self.last_result_context = {}
                return {"execution_result": "submitted"}

            def execute_offline_group(self, system_config, tasks, **kwargs):
                outcomes = []
                for task in tasks:
                    try:
                        context = self.execute_offline_task(system_config, task, **kwargs)
                    except Exception as exc:
                        outcomes.append(
                            {
                                "task": task,
                                "status": "failed",
                                "context": dict(self.last_result_context),
                                "error": exc,
                            }
                        )
                    else:
                        outcomes.append(
                            {
                                "task": task,
                                "status": "success",
                                "context": context,
                                "error": None,
                            }
                        )
                return outcomes

            def close(self) -> None:
                return None

        run_report = FakeRunReport()
        system_config = {
            "execution": {
                "max_retry": 1,
                "require_store_account_mapping": True,
                "store_accounts": [
                    {
                        "store_name": "STORE-A",
                        "jushuitan_store_name": "阿里巴巴-STORE-A",
                        "account_key": "store_a",
                    }
                ],
            },
            "notifications": {"dingtalk": {"enabled": True}},
        }

        with (
            patch("sku_offline_main.SkuOfflineBrowser", FakeBrowser),
            patch("sku_offline_main.send_failure_notification") as notify_failure,
            patch("sku_offline_main.send_summary_notification"),
        ):
            summary = execute_preview(
                project_root=PROJECT_ROOT,
                operator_config={"browser": {}},
                system_config=system_config,
                preview={
                    "selected_tasks": tasks,
                    "duplicate_count": 0,
                    "filtered_out_count": 0,
                },
                skip_login=True,
                no_notify=False,
                run_report=run_report,  # type: ignore[arg-type]
            )

        self.assertEqual(summary["failed"], 1)
        self.assertEqual(summary["success"], 1)
        self.assertEqual(summary["stopped_stores"], 0)
        self.assertEqual(len(run_report.rows), 2)
        self.assertEqual(run_report.rows[0]["error_category"], "system_prompt")
        self.assertEqual(run_report.rows[0]["page_error_text"], "毛重必须为数字")
        self.assertEqual(run_report.rows[1]["status"], "success")
        notify_failure.assert_called_once()

    def test_session_safety_exception_is_recorded_without_failing_the_process(self) -> None:
        tasks = [
            OfflineTask(
                source_file="demo.csv",
                source_sheet="CSV",
                source_row_number=index + 2,
                store_name="STORE-A",
                platform="Alibaba",
                product_id=str(1000 + index),
                online_sku=f"SKU-{index}",
                handling="all-channel-offline",
                replacement_sku="",
                change_image="",
                platform_store_item_code=f"CODE-{index}",
                raw={},
            )
            for index in range(2)
        ]

        class FakeBrowser:
            def __init__(self, *_args, **_kwargs) -> None:
                self.last_result_context: dict[str, str] = {}
                self.last_screenshot_path = ""
                self.last_html_snapshot_path = ""

            def open(self) -> None:
                return None

            def prepare_session(self, *_args, **_kwargs) -> None:
                raise OfflineLoginRequiredError("login expired")

            def close(self) -> None:
                return None

        run_report = FakeRunReport()
        system_config = {
            "execution": {
                "require_store_account_mapping": True,
                "store_accounts": [{
                    "store_name": "STORE-A",
                    "jushuitan_store_name": "阿里巴巴-STORE-A",
                    "account_key": "store_a",
                }],
                "stop_store_on_error_categories": ["login_required"],
                "auto_login_fallback": {"enabled": False},
            },
            "notifications": {"dingtalk": {"enabled": True}},
        }

        with (
            patch("sku_offline_main.SkuOfflineBrowser", FakeBrowser),
            patch("sku_offline_main.send_failure_notification") as notify_failure,
            patch("sku_offline_main.send_summary_notification"),
        ):
            summary = execute_preview(
                project_root=PROJECT_ROOT,
                operator_config={"browser": {}},
                system_config=system_config,
                preview={
                    "selected_tasks": tasks,
                    "duplicate_count": 0,
                    "filtered_out_count": 0,
                },
                skip_login=True,
                no_notify=False,
                run_report=run_report,  # type: ignore[arg-type]
            )

        self.assertEqual(summary["failed"], 2)
        self.assertEqual(summary["stopped_stores"], 1)
        self.assertEqual(len(run_report.rows), 2)
        self.assertEqual({row["error_category"] for row in run_report.rows}, {"login_required"})
        self.assertEqual(run_report.rows[0]["page_error_stage"], "pre_execution_session")
        notify_failure.assert_called_once()

    def test_login_failure_is_recorded_without_stopping_store_when_only_mismatch_stops(self) -> None:
        tasks = [
            OfflineTask(
                source_file="demo.csv",
                source_sheet="CSV",
                source_row_number=index + 2,
                store_name="STORE-A",
                platform="Alibaba",
                product_id=str(1000 + index),
                online_sku=f"SKU-{index}",
                handling="all-channel-offline",
                replacement_sku="",
                change_image="",
                platform_store_item_code=f"CODE-{index}",
                raw={},
            )
            for index in range(2)
        ]

        class FakeBrowser:
            def __init__(self, *_args, **_kwargs) -> None:
                self.last_result_context: dict[str, str] = {}
                self.last_screenshot_path = ""
                self.last_html_snapshot_path = ""

            def open(self) -> None:
                return None

            def prepare_session(self, *_args, **_kwargs) -> None:
                raise OfflineLoginRequiredError("login expired")

            def close(self) -> None:
                return None

        run_report = FakeRunReport()
        system_config = {
            "execution": {
                "require_store_account_mapping": True,
                "store_accounts": [{
                    "store_name": "STORE-A",
                    "jushuitan_store_name": "阿里巴巴-STORE-A",
                    "account_key": "store_a",
                }],
                "stop_store_on_error_categories": ["store_mismatch"],
                "auto_login_fallback": {"enabled": False},
            },
            "notifications": {"dingtalk": {"enabled": True}},
        }

        with (
            patch("sku_offline_main.SkuOfflineBrowser", FakeBrowser),
            patch("sku_offline_main.send_failure_notification") as notify_failure,
            patch("sku_offline_main.send_summary_notification"),
        ):
            summary = execute_preview(
                project_root=PROJECT_ROOT,
                operator_config={"browser": {}},
                system_config=system_config,
                preview={"selected_tasks": tasks},
                skip_login=True,
                no_notify=False,
                run_report=run_report,  # type: ignore[arg-type]
            )

        self.assertEqual(summary["failed"], 2)
        self.assertEqual(summary["stopped_stores"], 0)
        self.assertEqual({row["error_category"] for row in run_report.rows}, {"login_required"})
        notify_failure.assert_called_once()

    def test_expired_profile_auto_login_reopens_browser_and_continues(self) -> None:
        task = OfflineTask(
            source_file="demo.csv",
            source_sheet="CSV",
            source_row_number=2,
            store_name="STORE-A",
            platform="Alibaba",
            product_id="1001",
            online_sku="SKU-A",
            handling="all-channel-offline",
            replacement_sku="",
            change_image="",
            platform_store_item_code="CODE-A",
            raw={},
        )

        class FakeBrowser:
            instances: list["FakeBrowser"] = []

            def __init__(self, *_args, **_kwargs) -> None:
                self.index = len(self.instances)
                self.instances.append(self)
                self.last_result_context: dict[str, str] = {}
                self.last_screenshot_path = ""
                self.last_html_snapshot_path = ""
                self.closed = False

            def open(self) -> None:
                return None

            def prepare_session(self, *_args, **_kwargs) -> None:
                if self.index == 0:
                    raise OfflineLoginRequiredError("login expired")

            def reset_runtime_artifacts(self) -> None:
                self.last_result_context = {}

            def execute_offline_group(self, _config, tasks, **_kwargs):
                return [{"task": tasks[0], "status": "success", "context": {}}]

            def close(self) -> None:
                self.closed = True

        system_config = {
            "execution": {
                "require_store_account_mapping": True,
                "store_accounts": [{
                    "store_name": "STORE-A",
                    "jushuitan_store_name": "阿里巴巴-STORE-A",
                    "account_key": "store_a",
                }],
                "stop_store_on_error_categories": ["login_required", "risk_control"],
                "auto_login_fallback": {
                    "enabled": True,
                    "timeout_seconds": 123,
                    "max_attempts_per_store": 1,
                },
            },
            "notifications": {"dingtalk": {"enabled": False}},
        }

        with (
            patch("sku_offline_main.SkuOfflineBrowser", FakeBrowser),
            patch("sku_offline_main.ensure_1688_authenticated_session") as auto_login,
            patch("sku_offline_main.send_summary_notification"),
        ):
            summary = execute_preview(
                project_root=PROJECT_ROOT,
                operator_config={"browser": {}},
                system_config=system_config,
                preview={"selected_tasks": [task]},
                skip_login=True,
                shared_runtime_root="D:/runtime",
                no_notify=True,
                run_report=FakeRunReport(),  # type: ignore[arg-type]
            )

        auto_login.assert_called_once_with(
            "D:/runtime",
            "store_a",
            "STORE-A",
            timeout_seconds=123,
        )
        self.assertEqual(len(FakeBrowser.instances), 2)
        self.assertTrue(FakeBrowser.instances[0].closed)
        self.assertTrue(FakeBrowser.instances[1].closed)
        self.assertEqual(summary["success"], 1)
        self.assertEqual(summary["auto_login_attempts"], 1)
        self.assertEqual(summary["auto_login_success"], 1)
        self.assertEqual(summary["auto_login_failed"], 0)

    def test_auto_login_captcha_stops_store_and_reports_risk_control(self) -> None:
        tasks = [
            OfflineTask(
                source_file="demo.csv",
                source_sheet="CSV",
                source_row_number=index + 2,
                store_name="STORE-A",
                platform="Alibaba",
                product_id=str(1000 + index),
                online_sku=f"SKU-{index}",
                handling="all-channel-offline",
                replacement_sku="",
                change_image="",
                platform_store_item_code=f"CODE-{index}",
                raw={},
            )
            for index in range(2)
        ]

        class FakeBrowser:
            instances = 0

            def __init__(self, *_args, **_kwargs) -> None:
                type(self).instances += 1
                self.last_result_context: dict[str, str] = {}
                self.last_screenshot_path = ""
                self.last_html_snapshot_path = ""

            def open(self) -> None:
                return None

            def prepare_session(self, *_args, **_kwargs) -> None:
                raise OfflineLoginRequiredError("login expired")

            def close(self) -> None:
                return None

        run_report = FakeRunReport()
        system_config = {
            "execution": {
                "require_store_account_mapping": True,
                "store_accounts": [{
                    "store_name": "STORE-A",
                    "jushuitan_store_name": "阿里巴巴-STORE-A",
                    "account_key": "store_a",
                }],
                "stop_store_on_error_categories": ["login_required", "risk_control"],
                "auto_login_fallback": {"enabled": True, "max_attempts_per_store": 1},
            },
            "notifications": {"dingtalk": {"enabled": True}},
        }

        with (
            patch("sku_offline_main.SkuOfflineBrowser", FakeBrowser),
            patch(
                "sku_offline_main.ensure_1688_authenticated_session",
                side_effect=OfflineRiskControlError("slider required"),
            ) as auto_login,
            patch("sku_offline_main.send_failure_notification") as notify_failure,
            patch("sku_offline_main.send_summary_notification"),
        ):
            summary = execute_preview(
                project_root=PROJECT_ROOT,
                operator_config={"browser": {}},
                system_config=system_config,
                preview={"selected_tasks": tasks},
                skip_login=True,
                shared_runtime_root="D:/runtime",
                no_notify=False,
                run_report=run_report,  # type: ignore[arg-type]
            )

        auto_login.assert_called_once()
        self.assertEqual(FakeBrowser.instances, 1)
        self.assertEqual(summary["failed"], 2)
        self.assertEqual(summary["stopped_stores"], 1)
        self.assertEqual(summary["auto_login_attempts"], 1)
        self.assertEqual(summary["auto_login_success"], 0)
        self.assertEqual(summary["auto_login_failed"], 1)
        self.assertEqual({row["error_category"] for row in run_report.rows}, {"risk_control"})
        self.assertEqual(run_report.rows[0]["page_error_stage"], "auto_login_fallback")
        notify_failure.assert_called_once()

    def test_runtime_login_redirect_auto_login_reopens_browser_and_retries_group(self) -> None:
        task = OfflineTask(
            source_file="demo.csv",
            source_sheet="CSV",
            source_row_number=2,
            store_name="STORE-A",
            platform="Alibaba",
            product_id="1001",
            online_sku="SKU-A",
            handling="all-channel-offline",
            replacement_sku="",
            change_image="",
            platform_store_item_code="CODE-A",
            raw={},
        )

        class FakeBrowser:
            instances: list["FakeBrowser"] = []

            def __init__(self, *_args, **_kwargs) -> None:
                self.index = len(self.instances)
                self.instances.append(self)
                self.last_result_context: dict[str, str] = {}
                self.last_screenshot_path = ""
                self.last_html_snapshot_path = ""
                self.closed = False

            def open(self) -> None:
                return None

            def prepare_session(self, *_args, **_kwargs) -> None:
                return None

            def reset_runtime_artifacts(self) -> None:
                self.last_result_context = {}

            def execute_offline_group(self, _config, tasks, **_kwargs):
                if self.index == 0:
                    error = OfflineLoginRequiredError("redirected during edit-page navigation")
                    return [
                        {
                            "task": tasks[0],
                            "status": "failed",
                            "context": {"page_error_category": "login_required"},
                            "error": error,
                        }
                    ]
                return [{"task": tasks[0], "status": "success", "context": {}}]

            def close(self) -> None:
                self.closed = True

        system_config = {
            "execution": {
                "max_retry": 0,
                "require_store_account_mapping": True,
                "store_accounts": [{
                    "store_name": "STORE-A",
                    "jushuitan_store_name": "阿里巴巴-STORE-A",
                    "account_key": "store_a",
                }],
                "stop_store_on_error_categories": ["login_required", "risk_control"],
                "auto_login_fallback": {
                    "enabled": True,
                    "timeout_seconds": 123,
                    "max_attempts_per_store": 1,
                },
            },
            "notifications": {"dingtalk": {"enabled": False}},
        }

        with (
            patch("sku_offline_main.SkuOfflineBrowser", FakeBrowser),
            patch("sku_offline_main.ensure_1688_authenticated_session") as auto_login,
            patch("sku_offline_main.send_summary_notification"),
        ):
            summary = execute_preview(
                project_root=PROJECT_ROOT,
                operator_config={"browser": {}},
                system_config=system_config,
                preview={"selected_tasks": [task]},
                skip_login=True,
                shared_runtime_root="D:/runtime",
                no_notify=True,
                run_report=FakeRunReport(),  # type: ignore[arg-type]
            )

        auto_login.assert_called_once_with(
            "D:/runtime",
            "store_a",
            "STORE-A",
            timeout_seconds=123,
        )
        self.assertEqual(len(FakeBrowser.instances), 2)
        self.assertTrue(FakeBrowser.instances[0].closed)
        self.assertTrue(FakeBrowser.instances[1].closed)
        self.assertEqual(summary["success"], 1)
        self.assertEqual(summary["failed"], 0)
        self.assertEqual(summary["auto_login_attempts"], 1)
        self.assertEqual(summary["auto_login_success"], 1)
        self.assertEqual(summary["auto_login_failed"], 0)

    def test_automation_error_recreates_browser_before_group_retry(self) -> None:
        task = OfflineTask(
            source_file="demo.csv",
            source_sheet="CSV",
            source_row_number=2,
            store_name="STORE-A",
            platform="Alibaba",
            product_id="1001",
            online_sku="SKU-A",
            handling="all-channel-offline",
            replacement_sku="",
            change_image="",
            platform_store_item_code="CODE-A",
            raw={},
        )

        class FakeBrowser:
            instances: list["FakeBrowser"] = []

            def __init__(self, *_args, **_kwargs) -> None:
                self.index = len(self.instances)
                self.instances.append(self)
                self.last_result_context: dict[str, str] = {}
                self.last_screenshot_path = ""
                self.last_html_snapshot_path = ""
                self.opened = False
                self.prepared = False
                self.closed = False

            def open(self) -> None:
                self.opened = True

            def prepare_session(self, *_args, **_kwargs) -> None:
                self.prepared = True

            def reset_runtime_artifacts(self) -> None:
                self.last_result_context = {}

            def execute_offline_group(self, _config, tasks, **_kwargs):
                if self.index == 0:
                    return [
                        {
                            "task": tasks[0],
                            "status": "failed",
                            "context": {"page_error_category": "automation_error"},
                            "error": RuntimeError("Timed out receiving message from renderer"),
                        }
                    ]
                return [{"task": tasks[0], "status": "success", "context": {}}]

            def close(self) -> None:
                self.closed = True

        system_config = {
            "execution": {
                "max_retry": 1,
                "require_store_account_mapping": True,
                "store_accounts": [{
                    "store_name": "STORE-A",
                    "jushuitan_store_name": "阿里巴巴-STORE-A",
                    "account_key": "store_a",
                }],
                "auto_login_fallback": {"enabled": False},
            },
            "notifications": {"dingtalk": {"enabled": False}},
        }

        with (
            patch("sku_offline_main.SkuOfflineBrowser", FakeBrowser),
            patch("sku_offline_main.send_summary_notification"),
        ):
            summary = execute_preview(
                project_root=PROJECT_ROOT,
                operator_config={"browser": {}},
                system_config=system_config,
                preview={"selected_tasks": [task]},
                skip_login=True,
                no_notify=True,
                run_report=FakeRunReport(),  # type: ignore[arg-type]
            )

        self.assertEqual(len(FakeBrowser.instances), 2)
        self.assertTrue(FakeBrowser.instances[0].closed)
        self.assertTrue(FakeBrowser.instances[1].opened)
        self.assertTrue(FakeBrowser.instances[1].prepared)
        self.assertTrue(FakeBrowser.instances[1].closed)
        self.assertEqual(summary["success"], 1)
        self.assertEqual(summary["failed"], 0)
        self.assertEqual(summary["browser_recovery_attempts"], 1)
        self.assertEqual(summary["browser_recovery_success"], 1)
        self.assertEqual(summary["browser_recovery_failed"], 0)

    def test_raised_automation_error_recreates_browser_before_retry(self) -> None:
        task = OfflineTask(
            source_file="demo.csv",
            source_sheet="CSV",
            source_row_number=2,
            store_name="STORE-A",
            platform="Alibaba",
            product_id="1001",
            online_sku="SKU-A",
            handling="all-channel-offline",
            replacement_sku="",
            change_image="",
            platform_store_item_code="CODE-A",
            raw={},
        )

        class FakeBrowser:
            instances: list["FakeBrowser"] = []

            def __init__(self, *_args, **_kwargs) -> None:
                self.index = len(self.instances)
                self.instances.append(self)
                self.last_result_context: dict[str, str] = {}
                self.last_screenshot_path = ""
                self.last_html_snapshot_path = ""
                self.closed = False

            def open(self) -> None:
                return None

            def prepare_session(self, *_args, **_kwargs) -> None:
                return None

            def reset_runtime_artifacts(self) -> None:
                self.last_result_context = {}

            def execute_offline_group(self, _config, tasks, **_kwargs):
                if self.index == 0:
                    raise RuntimeError("renderer process is unresponsive")
                return [{"task": tasks[0], "status": "success", "context": {}}]

            def close(self) -> None:
                self.closed = True

        system_config = {
            "execution": {
                "max_retry": 1,
                "require_store_account_mapping": True,
                "store_accounts": [{
                    "store_name": "STORE-A",
                    "jushuitan_store_name": "阿里巴巴-STORE-A",
                    "account_key": "store_a",
                }],
                "auto_login_fallback": {"enabled": False},
            },
            "notifications": {"dingtalk": {"enabled": False}},
        }

        with (
            patch("sku_offline_main.SkuOfflineBrowser", FakeBrowser),
            patch("sku_offline_main.send_summary_notification"),
        ):
            summary = execute_preview(
                project_root=PROJECT_ROOT,
                operator_config={"browser": {}},
                system_config=system_config,
                preview={"selected_tasks": [task]},
                skip_login=True,
                no_notify=True,
                run_report=FakeRunReport(),  # type: ignore[arg-type]
            )

        self.assertEqual(len(FakeBrowser.instances), 2)
        self.assertTrue(FakeBrowser.instances[0].closed)
        self.assertTrue(FakeBrowser.instances[1].closed)
        self.assertEqual(summary["success"], 1)
        self.assertEqual(summary["failed"], 0)
        self.assertEqual(summary["browser_recovery_attempts"], 1)
        self.assertEqual(summary["browser_recovery_success"], 1)
        self.assertEqual(summary["browser_recovery_failed"], 0)

    def test_jushuitan_handoff_preserves_distinct_platform_store_codes(self) -> None:
        first = OfflineTask(
            source_file="demo.csv",
            source_sheet="CSV",
            source_row_number=2,
            store_name="阿里巴巴-常州工莱家具",
            platform="Alibaba",
            product_id="1005537490740",
            online_sku="CY001301N35",
            handling="全渠道下架",
            replacement_sku="",
            change_image="",
            platform_store_item_code="6166627859436",
            raw={},
        )
        duplicate = OfflineTask(
            **{
                **first.__dict__,
                "source_row_number": 3,
                "platform_store_item_code": "6166627859437",
            }
        )
        preview = {"selected_tasks": [first], "duplicate_tasks": [duplicate]}

        pending = build_jushuitan_handoff_records(preview)
        successful = build_jushuitan_handoff_records(
            preview,
            successful_task_statuses={first.dedupe_key: "success"},
        )

        self.assertEqual(len(pending), 2)
        self.assertEqual({row["source_status"] for row in pending}, {"pending_1688"})
        self.assertEqual(len(successful), 2)
        self.assertEqual({row["source_status"] for row in successful}, {"success"})
        self.assertEqual(len({row["task_id"] for row in successful}), 2)


if __name__ == "__main__":
    unittest.main()
