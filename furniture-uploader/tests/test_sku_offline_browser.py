from __future__ import annotations

import sys
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
RPA_ROOT = PROJECT_ROOT / "rpa"
if str(RPA_ROOT) not in sys.path:
    sys.path.insert(0, str(RPA_ROOT))

from exceptions import OfflineLoginRequiredError, PublishValidationError
from sku_offline_browser import SkuOfflineBrowser


class FakePreSubmitBrowser(SkuOfflineBrowser):
    def __init__(self) -> None:
        super().__init__({}, PROJECT_ROOT)
        self.scan_results: list[list[dict[str, str]]] = []
        self.applied_actions: list[tuple[str, str]] = []
        self.field_values: dict[str, str] = {}
        self.shipment_values: dict[str, str] = {}
        self.state_patch_payload: dict[str, str] = {}

    def _resolve_current_page_category_context(self, context: dict[str, str]) -> None:
        context["resolved_category_name"] = "家装建材 > 商业、办公家具 > 电脑桌"
        context["platform_category"] = "computer_desk"

    def _scan_pre_submit_backfill_rules(self, rules: list[dict[str, str]]) -> list[dict[str, str]]:
        if self.scan_results:
            return self.scan_results.pop(0)
        return []

    def _apply_category_prop_rule(self, rule: dict[str, str], context: dict[str, str]) -> None:
        self.applied_actions.append(
            (
                str(rule.get("label", "")).strip(),
                self._resolve_profile_rule_value(rule, context),
            )
        )

    def _read_pre_submit_field_state(self, rule: dict[str, str], context: dict[str, str]) -> dict[str, str]:
        label = str(rule.get("label", "")).strip()
        current_value = self.field_values.get(label, "")
        return {
            "label": label,
            "exists": True,
            "current_value": current_value,
            "raw_value": current_value,
        }

    def _apply_pre_submit_field_rule(
        self,
        rule: dict[str, str],
        context: dict[str, str],
        desired_value: str,
    ) -> None:
        label = str(rule.get("label", "")).strip()
        self.field_values[label] = desired_value
        self.applied_actions.append((label, desired_value))

    def _read_shipment_table_rule_state(
        self,
        rule: dict[str, str],
        context: dict[str, str],
    ) -> dict[str, str]:
        label = str(rule.get("label", "")).strip()
        current_value = self.shipment_values.get(label, "")
        return {
            "label": label,
            "exists": True,
            "current_value": current_value,
            "raw_value": current_value,
        }

    def _apply_shipment_table_rule(
        self,
        rule: dict[str, str],
        context: dict[str, str],
        desired_value: str,
    ) -> None:
        label = str(rule.get("label", "")).strip()
        self.shipment_values[label] = desired_value
        self.applied_actions.append((label, desired_value))

    def _apply_draft_page_state_patch(
        self,
        publish_config: dict[str, str],
        context: dict[str, str],
    ) -> None:
        self.state_patch_payload = publish_config
        context["draft_page_state_patch"] = {
            "ok": True,
            "buyerProtectionApplied": "15天发货",
        }


class FakeSuccessDriver:
    def __init__(self, *, current_url: str = "") -> None:
        self.visited_urls: list[str] = []
        self.current_url = current_url

    def get(self, url: str) -> None:
        self.visited_urls.append(url)
        self.current_url = url


class FakeSuccessBrowser(SkuOfflineBrowser):
    def __init__(self, *, message_text: str = "", page_text: str = "") -> None:
        super().__init__({}, PROJECT_ROOT)
        self.driver = FakeSuccessDriver()
        self.message_text = message_text
        self.page_text = page_text

    def _extract_message_text(self, selector: dict[str, str]) -> str:
        return self.message_text

    def _extract_page_body_text(self) -> str:
        return self.page_text

    def _pause(self, seconds: float) -> None:
        return


class SkuOfflineBrowserTests(unittest.TestCase):
    def test_match_available_sku_supports_case_and_symbol_normalization(self) -> None:
        browser = SkuOfflineBrowser({}, PROJECT_ROOT)
        matched = browser._match_available_sku(
            " cbz-000233 n961v01 ",
            ["DNZ023901N1221V01", "CBZ000233N961V01"],
        )
        self.assertEqual(matched, "CBZ000233N961V01")

    def test_match_available_sku_returns_empty_when_ambiguous(self) -> None:
        browser = SkuOfflineBrowser({}, PROJECT_ROOT)
        matched = browser._match_available_sku(
            "CBZ000233N961V01",
            ["CBZ-000233N961V01", "CBZ000233N961V01"],
        )
        self.assertEqual(matched, "")

    def test_build_sku_not_found_message_contains_template_hint(self) -> None:
        browser = SkuOfflineBrowser({}, PROJECT_ROOT)
        message = browser._build_sku_not_found_message(
            "SYT000601N11",
            ["SZ018005", "SZ018003N371V01"],
        )
        self.assertIn("SYT000601N11", message)
        self.assertIn("Available single-SKU codes", message)
        self.assertIn("template SKU may be outdated", message)

    def test_raise_if_inline_validation_present_raises_publish_validation(self) -> None:
        browser = SkuOfflineBrowser({}, PROJECT_ROOT)
        browser._collect_visible_inline_validation_messages = lambda: ["配送服务为必填项"]  # type: ignore[method-assign]
        context: dict[str, str] = {}

        with self.assertRaises(PublishValidationError):
            browser._raise_if_inline_validation_present(context, stage_name="post_offline_submit")

        self.assertEqual(context["page_error_stage"], "post_offline_submit")
        self.assertEqual(context["page_error_category"], "business_validation")
        self.assertIn("配送服务为必填项", context["page_error_text"])

    def test_raise_if_inline_validation_present_noop_when_empty(self) -> None:
        browser = SkuOfflineBrowser({}, PROJECT_ROOT)
        browser._collect_visible_inline_validation_messages = lambda: []  # type: ignore[method-assign]
        context: dict[str, str] = {}

        browser._raise_if_inline_validation_present(context, stage_name="post_offline_submit")
        self.assertEqual(context, {})

    def test_retry_missing_category_prop_rules_can_force_select_first_option(self) -> None:
        class RetryBrowser(SkuOfflineBrowser):
            def __init__(self) -> None:
                super().__init__({}, PROJECT_ROOT)
                self.values = {"是否带滚轮": ""}
                self.applied_rules: list[dict[str, str]] = []

            def _scan_pre_submit_backfill_rules(self, rules: list[dict[str, str]]) -> list[dict[str, str]]:
                return [
                    {
                        "label": "是否带滚轮",
                        "exists": True,
                        "current_value": self.values["是否带滚轮"],
                    }
                ]

            def _read_category_prop_state(self, label: str) -> dict[str, str]:
                return {
                    "label": label,
                    "exists": True,
                    "current_value": self.values.get(label, ""),
                }

            def _apply_category_prop_rule(self, rule: dict[str, str], context: dict[str, str]) -> None:
                if "value" in rule:
                    candidate_value = str(rule.get("value", "")).strip()
                else:
                    candidate_value = str(rule.get("default_value", "")).strip()
                self.applied_rules.append(
                    {
                        "label": str(rule.get("label", "")).strip(),
                        "value": candidate_value,
                    }
                )
                if candidate_value:
                    raise RuntimeError("simulate first-pass mismatch")
                self.values["是否带滚轮"] = "否"

            def _run_with_stale_retry(
                self,
                callback,
                *,
                retries: int = 2,
                wait_seconds: float = 0.5,
            ):
                return callback()

        browser = RetryBrowser()
        actions = browser._retry_missing_category_prop_rules(
            [
                {
                    "label": "是否带滚轮",
                    "default_value": "否",
                    "missing_retry_rounds": 2,
                }
            ],
            {},
        )

        self.assertEqual(actions[0]["status"], "retry_applied")
        self.assertEqual(actions[0]["attempt"], 2)
        self.assertEqual(browser.values["是否带滚轮"], "否")

    def test_ensure_required_delivery_service_applies_when_empty(self) -> None:
        class DeliveryBrowser(SkuOfflineBrowser):
            def __init__(self) -> None:
                super().__init__({}, PROJECT_ROOT)
                self.driver = object()
                self.states = [
                    {"exists": True, "selected": False, "selected_options": [], "option_count": 1, "message_text": ""},
                    {"exists": True, "selected": False, "selected_options": [], "option_count": 1, "message_text": ""},
                    {
                        "exists": True,
                        "selected": True,
                        "selected_options": ["市区物流点自提 +0.00"],
                        "option_count": 1,
                        "message_text": "",
                    },
                ]
                self.clicked = False

            def _read_delivery_service_state(self) -> dict[str, str]:
                return dict(self.states.pop(0))

            def _select_first_delivery_service_option(self) -> bool:
                self.clicked = True
                return True

            def _enable_delivery_service_auto_switch(self) -> bool:
                return False

            def _pause(self, seconds: float) -> None:
                return

        browser = DeliveryBrowser()
        action = browser._ensure_required_delivery_service({})
        self.assertTrue(browser.clicked)
        self.assertEqual(action["status"], "applied")
        self.assertIn("市区物流点自提", action["value"])

    def test_ensure_required_delivery_service_marks_failed_when_still_empty(self) -> None:
        class DeliveryFailBrowser(SkuOfflineBrowser):
            def __init__(self) -> None:
                super().__init__({}, PROJECT_ROOT)
                self.driver = object()
                self.states = [
                    {"exists": True, "selected": False, "selected_options": [], "option_count": 1, "message_text": ""},
                    {"exists": True, "selected": False, "selected_options": [], "option_count": 1, "message_text": "配送服务为必填项"},
                    {"exists": True, "selected": False, "selected_options": [], "option_count": 1, "message_text": "配送服务为必填项"},
                ]

            def _read_delivery_service_state(self) -> dict[str, str]:
                return dict(self.states.pop(0))

            def _select_first_delivery_service_option(self) -> bool:
                return False

            def _enable_delivery_service_auto_switch(self) -> bool:
                return False

            def _pause(self, seconds: float) -> None:
                return

        browser = DeliveryFailBrowser()
        action = browser._ensure_required_delivery_service({})
        self.assertEqual(action["status"], "apply_failed")
        self.assertEqual(action["error"], "delivery_service_not_clickable")

    def test_prepare_pre_submit_backfill_uses_category_profile_defaults(self) -> None:
        browser = FakePreSubmitBrowser()
        browser.scan_results = [
            [
                {"label": "加工方式", "exists": True, "current_value": ""},
                {"label": "组合形式", "exists": True, "current_value": ""},
                {"label": "材质", "exists": True, "current_value": ""},
                {"label": "品牌", "exists": True, "current_value": ""},
                {"label": "风格", "exists": True, "current_value": ""},
                {"label": "是否可折叠", "exists": True, "current_value": ""},
            ],
            [
                {"label": "加工方式", "exists": True, "current_value": "手工"},
                {"label": "组合形式", "exists": True, "current_value": "自由组合"},
                {"label": "材质", "exists": True, "current_value": "板材"},
                {"label": "品牌", "exists": True, "current_value": "其它"},
                {"label": "风格", "exists": True, "current_value": "其它"},
                {"label": "是否可折叠", "exists": True, "current_value": "否"},
            ],
        ]
        context: dict[str, str] = {}

        browser._prepare_pre_submit_backfill(
            {
                "enabled": True,
                "block_on_missing": True,
                "profiles": {
                    "computer_desk": {
                        "rules": [
                            {"label": "加工方式", "default_value": "手工"},
                            {"label": "组合形式", "default_value": "自由组合"},
                            {"label": "材质", "default_value": "板材"},
                            {"label": "品牌", "type": "input", "default_value": "其它"},
                            {"label": "风格", "default_value": "其它"},
                            {"label": "是否可折叠", "default_value": "否"},
                        ]
                    }
                },
            },
            context,
        )

        self.assertEqual(context["platform_category"], "computer_desk")
        self.assertEqual(context["pre_submit_backfill_rule_source"], "profile:computer_desk")
        self.assertEqual(
            browser.applied_actions,
            [
                ("加工方式", "手工"),
                ("组合形式", "自由组合"),
                ("材质", "板材"),
                ("品牌", "其它"),
                ("风格", "其它"),
                ("是否可折叠", "否"),
            ],
        )
        self.assertEqual(context["pre_submit_backfill_status"], "ready")
        self.assertEqual(context["pre_submit_missing_attributes"], [])

    def test_resolve_pre_submit_backfill_rules_falls_back_to_direct_rules(self) -> None:
        browser = FakePreSubmitBrowser()
        context = {"platform_category": "unknown_category"}

        rules = browser._resolve_pre_submit_backfill_rules(
            {
                "enabled": True,
                "rules": [
                    {"label": "是否可折叠", "default_value": "否"},
                ],
                "profiles": {
                    "computer_desk": {
                        "rules": [
                            {"label": "加工方式", "default_value": "手工"},
                        ]
                    }
                },
            },
            context,
        )

        self.assertEqual(rules, [{"label": "是否可折叠", "default_value": "否"}])
        self.assertEqual(context["pre_submit_backfill_rule_source"], "rules")

    def test_prepare_pre_submit_backfill_applies_field_rules(self) -> None:
        browser = FakePreSubmitBrowser()
        browser.scan_results = [[], []]
        browser.field_values = {"发货时间": "30天发货"}
        context: dict[str, str] = {}

        browser._prepare_pre_submit_backfill(
            {
                "enabled": True,
                "block_on_missing": True,
                "field_rules": [
                    {
                        "label": "发货时间",
                        "type": "ant_select",
                        "default_value": "15天发货",
                        "overwrite_if_mismatch": True,
                        "allow_keep_existing": False,
                    }
                ],
            },
            context,
        )

        self.assertEqual(browser.field_values["发货时间"], "15天发货")
        self.assertIn(("发货时间", "15天发货"), browser.applied_actions)
        self.assertEqual(context["pre_submit_backfill_status"], "ready")

    def test_prepare_pre_submit_backfill_applies_shipment_table_rules(self) -> None:
        browser = FakePreSubmitBrowser()
        browser.scan_results = [[], []]
        browser.shipment_values = {"发货时间": "30天发货"}
        context: dict[str, str] = {}

        browser._prepare_pre_submit_backfill(
            {
                "enabled": True,
                "block_on_missing": True,
                "shipment_table_rules": [
                    {
                        "name": "buyer_protection_ship_time",
                        "label": "发货时间",
                        "default_value": "15天发货",
                        "overwrite_if_mismatch": True,
                        "allow_keep_existing": False,
                        "required": True,
                    }
                ],
            },
            context,
        )

        self.assertEqual(browser.shipment_values["发货时间"], "15天发货")
        self.assertIn(("发货时间", "15天发货"), browser.applied_actions)
        self.assertEqual(context["pre_submit_backfill_status"], "ready")
        self.assertEqual(
            context["pre_submit_shipment_table_after"],
            [
                {
                    "label": "发货时间",
                    "exists": True,
                    "current_value": "15天发货",
                    "raw_value": "15天发货",
                }
            ],
        )

    def test_prepare_pre_submit_backfill_applies_state_patch(self) -> None:
        browser = FakePreSubmitBrowser()
        browser.scan_results = [[], []]
        context: dict[str, str] = {}

        browser._prepare_pre_submit_backfill(
            {
                "enabled": True,
                "draft_page_state_patch": {
                    "enabled": True,
                    "buyer_protection_default_value": "15天发货",
                    "buyer_protection_step_template": [
                        {"from": 1, "service_name": "15天发货"},
                    ],
                },
            },
            context,
        )

        self.assertTrue(browser.state_patch_payload)
        self.assertEqual(context["pre_submit_state_patch_status"], "applied")
        self.assertEqual(context["draft_page_state_patch"]["buyerProtectionApplied"], "15天发货")

    def test_assert_not_redirected_to_login_raises_clear_error(self) -> None:
        browser = SkuOfflineBrowser({}, PROJECT_ROOT)
        browser.driver = FakeSuccessDriver(
            current_url="https://login.taobao.com/?redirect_url=https%3A%2F%2Foffer-new.1688.com%2Fpopular%2Fpublish.htm"
        )

        with self.assertRaises(OfflineLoginRequiredError):
            browser._assert_not_redirected_to_login()

    def test_wait_for_success_detects_review_success_page_text(self) -> None:
        browser = FakeSuccessBrowser(page_text="修改成功，您的商品已提交审核，请到未上架产品-审核中列表查看。")
        context: dict[str, str] = {}

        success_detected = browser._wait_for_success(
            {
                "enabled": True,
                "timeout_seconds": 0,
                "keywords": ["提交成功"],
                "page_keywords": ["修改成功", "商品已提交审核"],
            },
            context,
        )

        self.assertTrue(success_detected)
        self.assertEqual(context["success_signal_source"], "page")
        self.assertIn("商品已提交审核", context["success_message"])

    def test_verify_persisted_offline_accepts_review_submission_success(self) -> None:
        browser = FakeSuccessBrowser(page_text="修改成功，您的商品已提交审核，请到未上架产品-审核中列表查看。")
        context = {
            "product_id": "965507704749",
            "online_sku": "SJ023214N956V01",
            "success_signal_source": "page",
            "success_message": "修改成功，您的商品已提交审核，请到未上架产品-审核中列表查看。",
            "execution_result": "submitted",
        }

        browser._verify_persisted_offline(
            {},
            {
                "enabled": True,
                "accept_review_submission": True,
                "review_submission_keywords": ["修改成功", "商品已提交审核"],
            },
            context,
        )

        self.assertEqual(context["post_submit_verified"], "review_submitted")
        self.assertEqual(context["execution_result"], "review_submitted")
        self.assertEqual(browser.driver.visited_urls, [])


if __name__ == "__main__":
    unittest.main()
