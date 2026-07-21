from __future__ import annotations

import sys
import unittest
from pathlib import Path

from selenium.common.exceptions import TimeoutException
from selenium.webdriver.common.keys import Keys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
RPA_ROOT = PROJECT_ROOT / "rpa"
if str(RPA_ROOT) not in sys.path:
    sys.path.insert(0, str(RPA_ROOT))

from exceptions import (
    OfflineIdentityMismatchError,
    OfflineLoginRequiredError,
    OfflineRiskControlError,
    OfflineStoreMismatchError,
    OfflineTaskNotFoundError,
    OfflineTaskStateError,
    PublishValidationError,
)
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
        self.title = ""
        self.switch_to = type("SwitchTo", (), {"default_content": lambda self: None})()

    def get(self, url: str) -> None:
        self.visited_urls.append(url)
        self.current_url = url


class FakeClickableElement:
    def __init__(self) -> None:
        self.clicked = False

    def click(self) -> None:
        self.clicked = True

    def get_attribute(self, name: str) -> str:
        return ""


class FakeSearchInput:
    def __init__(self, value: str = "") -> None:
        self.value = value
        self.keys: list[tuple[object, ...]] = []

    def click(self) -> None:
        return

    def send_keys(self, *keys: object) -> None:
        self.keys.append(keys)
        if keys == (Keys.DELETE,):
            self.value = ""
        elif keys != (Keys.CONTROL, "a"):
            self.value += "".join(str(item) for item in keys)

    def get_attribute(self, name: str) -> str:
        return self.value if name == "value" else ""


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
    def test_management_search_field_uses_native_keyboard_input(self) -> None:
        browser = SkuOfflineBrowser({}, PROJECT_ROOT)
        browser.driver = FakeSuccessDriver()
        browser.driver.execute_script = lambda *args: None  # type: ignore[attr-defined]
        browser._pause = lambda seconds: None  # type: ignore[method-assign]
        browser._fill_text_field = lambda *args, **kwargs: (_ for _ in ()).throw(  # type: ignore[method-assign]
            AssertionError("JS fallback should not run when native input succeeds")
        )
        element = FakeSearchInput("old-value")

        browser._fill_management_search_field(element, "1022879495664")

        self.assertEqual(element.value, "1022879495664")
        self.assertEqual(
            element.keys,
            [(Keys.CONTROL, "a"), (Keys.DELETE,), ("1022879495664",)],
        )

    def test_management_filter_query_records_official_filter_url(self) -> None:
        browser = SkuOfflineBrowser({}, PROJECT_ROOT)
        browser.driver = FakeSuccessDriver()
        browser.driver.execute_script = lambda script, product_id: (  # type: ignore[attr-defined]
            "https://offer.1688.com/app/pages-group/manage-home/index.html?"
            f"q=filterOfferId%3D{product_id}"
        )
        context = {"product_id": "1005537490740"}

        browser._open_management_filter_query(context)

        self.assertIn("filterOfferId%3D1005537490740", context["management_filter_query_url"])

    def test_direct_edit_activates_sales_info_section(self) -> None:
        browser = SkuOfflineBrowser({}, PROJECT_ROOT)
        browser.driver = FakeSuccessDriver()
        anchor = FakeClickableElement()
        browser._wait_for_element = lambda selector, clickable=False: anchor  # type: ignore[method-assign]
        browser._pause = lambda seconds: None  # type: ignore[method-assign]

        browser._open_edit_page(
            {"sales_info_anchor": {"by": "xpath", "value": "//*[contains(normalize-space(.), '销售信息')]"}},
            {"product_id": "732745838005"},
            prefer_direct=True,
        )

        self.assertEqual(
            browser.driver.visited_urls,
            ["https://offer-new.1688.com/popular/publish.htm?id=732745838005&operator=edit"],
        )
        self.assertTrue(anchor.clicked)

    def test_direct_edit_stops_on_deleted_product_error_page(self) -> None:
        browser = FakeSuccessBrowser(
            page_text="出错啦！没有找到商品。错误码：PUB_BIZCHECK_PRIMARY_ITEM_DELETE"
        )
        context = {"product_id": "1060786095784"}

        with self.assertRaises(OfflineTaskNotFoundError):
            browser._open_edit_page({}, context, prefer_direct=True)

        self.assertEqual(context["page_error_category"], "task_not_found")
        self.assertEqual(context["page_error_stage"], "edit_page_load")
        self.assertEqual(context["edit_page_error_code"], "PUB_BIZCHECK_PRIMARY_ITEM_DELETE")

    def test_direct_edit_marks_product_identity_error_as_route_rejected(self) -> None:
        browser = FakeSuccessBrowser(
            page_text="出错啦！错误码：PUB_BIZCHECK_BIZ_IDENTITY_ERROR，请通过商品管理进入商品编辑页面"
        )
        context = {"product_id": "1022879495664"}

        with self.assertRaises(OfflineTaskStateError):
            browser._open_edit_page({}, context, prefer_direct=True)

        self.assertEqual(context["page_error_category"], "edit_route_rejected")
        self.assertEqual(context["page_error_stage"], "edit_page_load")
        self.assertEqual(context["edit_page_error_code"], "PUB_BIZCHECK_BIZ_IDENTITY_ERROR")

    def test_management_edit_keeps_product_identity_error_terminal(self) -> None:
        browser = FakeSuccessBrowser(
            page_text="出错啦！错误码：PUB_BIZCHECK_BIZ_IDENTITY_ERROR，请通过商品管理进入商品编辑页面"
        )
        context = {"product_id": "1022879495664", "edit_entry_mode": "management"}

        with self.assertRaises(OfflineTaskStateError):
            browser._raise_if_edit_page_unavailable(context)

        self.assertEqual(context["page_error_category"], "product_unavailable")

    def test_task_edit_defaults_to_management_search_path(self) -> None:
        browser = SkuOfflineBrowser({}, PROJECT_ROOT)
        calls: list[str] = []
        browser.open_management_page = lambda config: calls.append("open_management")  # type: ignore[method-assign]
        browser._assert_store_context = lambda *args, **kwargs: calls.append("assert_store")  # type: ignore[method-assign]
        browser._switch_into_management_frame = lambda *args: calls.append("switch_frame")  # type: ignore[method-assign]
        browser._search_product = lambda *args: calls.append("search_product")  # type: ignore[method-assign]
        browser._open_edit_page = lambda *args, **kwargs: calls.append("open_edit")  # type: ignore[method-assign]
        context: dict[str, str] = {"product_id": "1022879495664"}

        browser._open_task_edit_page({}, {}, context, {})

        self.assertEqual(
            calls,
            ["open_management", "assert_store", "switch_frame", "search_product", "open_edit"],
        )
        self.assertEqual(context["edit_entry_mode"], "management")

    def test_task_edit_falls_back_from_rejected_direct_route(self) -> None:
        browser = SkuOfflineBrowser({}, PROJECT_ROOT)
        calls: list[str] = []
        browser.open_management_page = lambda config: calls.append("open_management")  # type: ignore[method-assign]
        browser._assert_store_context = lambda *args, **kwargs: calls.append("assert_store")  # type: ignore[method-assign]
        browser._switch_into_management_frame = lambda *args: calls.append("switch_frame")  # type: ignore[method-assign]
        browser._search_product = lambda *args: calls.append("search_product")  # type: ignore[method-assign]

        def fake_open_edit(selectors: dict[str, str], context: dict[str, str], *, prefer_direct: bool = False) -> None:
            calls.append("open_direct" if prefer_direct else "open_management_edit")
            if prefer_direct:
                context["page_error_category"] = "edit_route_rejected"
                context["page_error_stage"] = "edit_page_load"
                context["page_error_text"] = "direct route rejected"
                context["edit_page_error_code"] = "PUB_BIZCHECK_BIZ_IDENTITY_ERROR"
                raise OfflineTaskStateError("direct route rejected")

        browser._open_edit_page = fake_open_edit  # type: ignore[method-assign]
        context: dict[str, str] = {"product_id": "1022879495664"}

        browser._open_task_edit_page({}, {}, context, {"edit_entry_mode": "direct"})

        self.assertEqual(
            calls,
            [
                "open_management",
                "assert_store",
                "open_direct",
                "open_management",
                "assert_store",
                "switch_frame",
                "search_product",
                "open_management_edit",
            ],
        )
        self.assertEqual(context["edit_entry_mode"], "management")
        self.assertEqual(context["edit_entry_fallback"], "management")
        self.assertEqual(context["direct_edit_rejected_code"], "PUB_BIZCHECK_BIZ_IDENTITY_ERROR")
        self.assertNotIn("page_error_category", context)

    def test_management_result_row_falls_back_to_product_id_text(self) -> None:
        browser = SkuOfflineBrowser({}, PROJECT_ROOT)
        calls: list[dict[str, str]] = []

        def fake_wait(selector: dict[str, str], clickable: bool = False):
            calls.append(selector)
            if selector.get("by") == "css":
                raise TimeoutException("primary selector missed")
            return object()

        browser._wait_for_element = fake_wait  # type: ignore[method-assign]
        result = browser._wait_for_management_result_row(
            {"by": "css", "value": "tr[data-row-key='1001']"},
            {"product_id": "1001"},
        )

        self.assertIsNotNone(result)
        self.assertEqual(calls[1]["by"], "xpath")
        self.assertIn("1001", calls[1]["value"])

    def test_sales_info_activation_recovers_with_cdp_reload(self) -> None:
        browser = SkuOfflineBrowser({}, PROJECT_ROOT)
        cdp_calls: list[tuple[str, dict[str, bool]]] = []
        browser.driver = FakeSuccessDriver()
        browser.driver.execute_cdp_cmd = lambda method, params: cdp_calls.append((method, params))  # type: ignore[attr-defined]
        rows = iter([None, object()])
        browser._find_sku_row_by_runtime_value = lambda context: next(rows)  # type: ignore[method-assign]
        browser._wait_for_element = lambda selector, clickable=False: (_ for _ in ()).throw(TimeoutException())  # type: ignore[method-assign]
        browser._pause = lambda seconds: None  # type: ignore[method-assign]
        context = {"online_sku": "SZT009219N961V01"}

        browser._activate_sales_info_section(
            {"sales_info_anchor": {"by": "xpath", "value": "//*[contains(., '销售信息')]"}},
            context,
        )

        self.assertEqual(cdp_calls, [("Page.reload", {"ignoreCache": True})])
        self.assertEqual(context["sales_info_activation"], "sku_table_visible_after_reload")

    def test_toggle_sku_uses_runtime_value_row_before_xpath_wait(self) -> None:
        browser = SkuOfflineBrowser({}, PROJECT_ROOT)
        browser.driver = FakeSuccessDriver()
        row = object()
        calls: list[str] = []
        browser._find_sku_row_by_runtime_value = lambda context: row  # type: ignore[method-assign]
        browser._wait_for_element = lambda selector: calls.append("wait")  # type: ignore[method-assign]
        browser._toggle_found_sku_row = lambda selectors, context, sku_row: sku_row is row  # type: ignore[method-assign]

        changed = browser._toggle_sku_offline(
            {"sku_row": {"by": "xpath", "value": "//tr[.//input[@value='{online_sku}']]"}},
            {"online_sku": "DSG005015N11V01"},
        )

        self.assertTrue(changed)
        self.assertEqual(calls, [])

    def test_sole_online_sku_is_blocked_before_toggle(self) -> None:
        browser = SkuOfflineBrowser({}, PROJECT_ROOT)
        browser.driver = FakeSuccessDriver()
        browser._read_switch_label = lambda element: "上架"  # type: ignore[method-assign]
        browser._is_online_state = lambda element, label: True  # type: ignore[method-assign]
        switch = type(
            "VisibleSwitch",
            (),
            {
                "is_displayed": lambda self: True,
                "get_attribute": lambda self, name: "true" if name == "aria-checked" else "",
            },
        )()
        browser.driver.execute_script = lambda script: [switch]  # type: ignore[attr-defined]
        context: dict[str, object] = {"online_sku": "SZT009219N961V01"}

        with self.assertRaises(PublishValidationError):
            browser._assert_target_not_sole_online_sku(context)

        self.assertEqual(context["page_error_stage"], "pre_sku_toggle")
        self.assertEqual(context["page_error_category"], "sole_sku_requires_product_offline")
        self.assertEqual(context["sku_switch_summary_before"]["visible_switch_count"], 1)
        self.assertEqual(context["automatic_product_offline_allowed"], "false")

    def test_submit_changes_accepts_success_page_without_submit_trace(self) -> None:
        browser = SkuOfflineBrowser({}, PROJECT_ROOT)
        browser.driver = FakeSuccessDriver()
        submit_element = FakeClickableElement()
        calls: list[str] = []
        browser._resolve_selector = lambda selector, context: selector  # type: ignore[method-assign]
        browser._selector_is_configured = lambda selector: True  # type: ignore[method-assign]
        browser._check_publish_error_state = lambda *args, **kwargs: None  # type: ignore[method-assign]
        browser._prepare_pre_submit_backfill = lambda config, context: None  # type: ignore[method-assign]
        browser._raise_if_inline_validation_present = lambda context, stage_name: None  # type: ignore[method-assign]
        browser._ensure_target_sku_still_offline = lambda selectors, context: None  # type: ignore[method-assign]
        browser._install_offline_submit_trace = lambda: None  # type: ignore[method-assign]
        browser._wait_for_element = lambda selector, clickable=False: submit_element  # type: ignore[method-assign]
        browser._click_submit_element = lambda element, context: calls.append("click")  # type: ignore[method-assign]
        browser._pause = lambda seconds: None  # type: ignore[method-assign]
        browser._click_optional_confirm_button = lambda selector: None  # type: ignore[method-assign]

        def fake_wait_for_success(config: dict[str, object], context: dict[str, str]) -> bool:
            context["success_signal_source"] = "page"
            context["success_message"] = "修改成功，您的商品已提交审核"
            return True

        browser._wait_for_success = fake_wait_for_success  # type: ignore[method-assign]
        browser._assert_offline_submit_trace = lambda context: (_ for _ in ()).throw(  # type: ignore[method-assign]
            AssertionError("submit trace should not be required after success page")
        )
        context: dict[str, str] = {}

        browser._submit_changes(
            {"submit_button": {"by": "id", "value": "submitFormButton"}},
            {"enabled": True},
            {},
            {},
            context,
        )

        self.assertEqual(calls, ["click"])
        self.assertEqual(context["success_detected"], "true")
        self.assertEqual(context["execution_result"], "submitted")

    def test_submit_changes_accepts_success_page_after_dispatch_retry(self) -> None:
        browser = SkuOfflineBrowser({}, PROJECT_ROOT)
        browser.driver = FakeSuccessDriver()
        submit_element = FakeClickableElement()
        calls: list[str] = []
        success_results = iter([False, True])
        browser._resolve_selector = lambda selector, context: selector  # type: ignore[method-assign]
        browser._selector_is_configured = lambda selector: True  # type: ignore[method-assign]
        browser._check_publish_error_state = lambda *args, **kwargs: None  # type: ignore[method-assign]
        browser._prepare_pre_submit_backfill = lambda config, context: None  # type: ignore[method-assign]
        browser._raise_if_inline_validation_present = lambda context, stage_name: None  # type: ignore[method-assign]
        browser._ensure_target_sku_still_offline = lambda selectors, context: None  # type: ignore[method-assign]
        browser._install_offline_submit_trace = lambda: None  # type: ignore[method-assign]
        browser._wait_for_element = lambda selector, clickable=False: submit_element  # type: ignore[method-assign]
        browser._click_submit_element = lambda element, context: calls.append("primary_click")  # type: ignore[method-assign]
        browser._dispatch_submit_button_click = lambda selector, context: calls.append("dispatch_retry")  # type: ignore[method-assign]
        browser._click_optional_confirm_button = lambda selector: None  # type: ignore[method-assign]
        browser._pause = lambda seconds: None  # type: ignore[method-assign]
        browser._wait_for_success = lambda config, context: next(success_results)  # type: ignore[method-assign]
        browser._assert_offline_submit_trace = lambda context: False  # type: ignore[method-assign]
        browser._retry_submit_via_trace = lambda context: (_ for _ in ()).throw(  # type: ignore[method-assign]
            AssertionError("direct retry should not run after the success page appears")
        )
        context: dict[str, str] = {}

        browser._submit_changes(
            {"submit_button": {"by": "id", "value": "submitFormButton"}},
            {"enabled": True},
            {},
            {},
            context,
        )

        self.assertEqual(calls, ["primary_click", "dispatch_retry"])
        self.assertEqual(context["success_detected"], "true")
        self.assertEqual(context["submit_success_phase"], "after_dispatch_retry")
        self.assertEqual(context["execution_result"], "submitted")

    def test_submit_changes_accepts_persisted_offline_after_trace_miss(self) -> None:
        browser = SkuOfflineBrowser({}, PROJECT_ROOT)
        browser.driver = FakeSuccessDriver()
        submit_element = FakeClickableElement()
        browser._resolve_selector = lambda selector, context: selector  # type: ignore[method-assign]
        browser._selector_is_configured = lambda selector: True  # type: ignore[method-assign]
        browser._check_publish_error_state = lambda *args, **kwargs: None  # type: ignore[method-assign]
        browser._prepare_pre_submit_backfill = lambda config, context: None  # type: ignore[method-assign]
        browser._raise_if_inline_validation_present = lambda context, stage_name: None  # type: ignore[method-assign]
        browser._ensure_target_sku_still_offline = lambda selectors, context: None  # type: ignore[method-assign]
        browser._install_offline_submit_trace = lambda: None  # type: ignore[method-assign]
        browser._wait_for_element = lambda selector, clickable=False: submit_element  # type: ignore[method-assign]
        browser._click_submit_element = lambda element, context: None  # type: ignore[method-assign]
        browser._dispatch_submit_button_click = lambda selector, context: None  # type: ignore[method-assign]
        browser._click_optional_confirm_button = lambda selector: None  # type: ignore[method-assign]
        browser._pause = lambda seconds: None  # type: ignore[method-assign]
        browser._wait_for_success = lambda config, context: False  # type: ignore[method-assign]
        browser._assert_offline_submit_trace = lambda context: False  # type: ignore[method-assign]
        browser._retry_submit_via_trace = lambda context: {"ok": False, "reason": "missing_submit_trace"}  # type: ignore[method-assign]
        browser._collect_submit_block_diagnostics = lambda selector: {"assist_messages": []}  # type: ignore[method-assign]
        browser._probe_persisted_offline_after_trace_miss = lambda selectors, context: True  # type: ignore[method-assign]
        context: dict[str, object] = {}

        browser._submit_changes(
            {"submit_button": {"by": "id", "value": "submitFormButton"}},
            {"enabled": True},
            {},
            {},
            context,
        )

        self.assertEqual(context["submit_success_phase"], "persisted_state_after_trace_miss")
        self.assertEqual(context["execution_result"], "submitted_untraced_verified")

    def test_ensure_target_sku_still_offline_uses_runtime_row(self) -> None:
        browser = SkuOfflineBrowser({}, PROJECT_ROOT)
        browser.driver = FakeSuccessDriver()
        sku_row = object()
        switch = object()
        browser._find_sku_row_by_runtime_value = lambda context: sku_row  # type: ignore[method-assign]
        browser._wait_for_element = lambda selector: (_ for _ in ()).throw(  # type: ignore[method-assign]
            AssertionError("XPath fallback should not run when the runtime row exists")
        )
        browser._get_row_switch_element = lambda row, selector: switch  # type: ignore[method-assign]
        browser._read_switch_label = lambda element: "下架"  # type: ignore[method-assign]
        browser._is_already_offline = lambda element, label: True  # type: ignore[method-assign]
        browser._ensure_switch_state_stable = lambda **kwargs: None  # type: ignore[method-assign]
        browser._force_target_sku_offline_in_runtime_state = lambda context: {"supported": False}  # type: ignore[method-assign]
        context = {"online_sku": "CY001301N35"}

        browser._ensure_target_sku_still_offline(
            {
                "sku_row": {"by": "xpath", "value": "//tr[.//input[@value='{online_sku}']]"},
                "sku_switch": {"by": "css", "value": "button.ant-switch"},
            },
            context,
        )

        self.assertEqual(context["switch_label_before_submit"], "下架")

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
        self.assertEqual(context["page_error_category"], "delivery_service_backfill_failed")
        self.assertIn("配送服务为必填项", context["page_error_text"])

    def test_raise_if_inline_validation_classifies_sole_online_sku(self) -> None:
        browser = SkuOfflineBrowser({}, PROJECT_ROOT)
        browser._collect_visible_inline_validation_messages = lambda: [  # type: ignore[method-assign]
            "是否上架: 有产品规格的商品至少要有一个在线状态的sku。"
        ]
        context: dict[str, str] = {}

        with self.assertRaises(PublishValidationError):
            browser._raise_if_inline_validation_present(context, stage_name="pre_offline_submit")

        self.assertEqual(context["page_error_category"], "sole_sku_requires_product_offline")
        self.assertEqual(context["automatic_product_offline_allowed"], "false")

    def test_raise_if_inline_validation_classifies_campaign_restriction(self) -> None:
        browser = SkuOfflineBrowser({}, PROJECT_ROOT)
        browser._collect_visible_inline_validation_messages = lambda: [  # type: ignore[method-assign]
            "该商品已报名天天特卖活动，活动期间不允许修改SKU上下架状态。"
        ]
        context: dict[str, str] = {}

        with self.assertRaises(PublishValidationError):
            browser._raise_if_inline_validation_present(context, stage_name="pre_offline_submit")

        self.assertEqual(context["page_error_category"], "campaign_restriction")
        self.assertEqual(context["campaign_restriction_detected"], "true")

    def test_switch_stability_reuses_runtime_sku_row_after_re_render(self) -> None:
        browser = SkuOfflineBrowser({}, PROJECT_ROOT)
        browser.driver = FakeSuccessDriver()
        switch = type(
            "RuntimeSwitch",
            (),
            {
                "is_displayed": lambda self: True,
                "get_attribute": lambda self, name: "false" if name == "aria-checked" else "",
                "text": "",
            },
        )()
        row = type(
            "RuntimeRow",
            (),
            {
                "is_displayed": lambda self: True,
                "find_element": lambda self, by, value: switch,
            },
        )()
        browser._find_sku_row_by_runtime_value = lambda context: row  # type: ignore[method-assign]
        browser.driver.find_elements = lambda *args: (_ for _ in ()).throw(  # type: ignore[attr-defined]
            AssertionError("static XPath fallback should not run")
        )
        context: dict[str, str] = {"online_sku": "CY001301N35"}

        browser._ensure_switch_state_stable(
            sku_row_selector={"by": "xpath", "value": "//tr[.//input[@value='CY001301N35']]"},
            switch_selector={"by": "css", "value": "button.ant-switch"},
            expect_offline=True,
            context=context,
            context_key="stable_state",
            timeout_seconds=1,
            stable_seconds=0.2,
        )

        self.assertEqual(context["stable_state"], "aria-checked=false")

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

    def test_prepare_pre_submit_backfill_delivery_service_only(self) -> None:
        browser = FakePreSubmitBrowser()
        browser._ensure_required_delivery_service = lambda context: {  # type: ignore[method-assign]
            "label": "配送服务",
            "status": "applied",
            "value": "送货楼下",
        }
        browser._resolve_current_page_category_context = lambda context: (_ for _ in ()).throw(  # type: ignore[method-assign]
            AssertionError("category backfill should not run in delivery_service_only mode")
        )
        context: dict[str, object] = {}

        browser._prepare_pre_submit_backfill(
            {"enabled": True, "mode": "delivery_service_only"},
            context,
        )

        self.assertEqual(context["pre_submit_backfill_status"], "ready")
        self.assertEqual(context["pre_submit_backfill_actions"][0]["status"], "applied")

    def test_prepare_pre_submit_backfill_runs_delivery_service_without_other_rules(self) -> None:
        browser = FakePreSubmitBrowser()
        browser._ensure_required_delivery_service = lambda context: {  # type: ignore[method-assign]
            "label": "配送服务",
            "status": "applied",
            "value": "市区物流点自提",
        }
        context: dict[str, object] = {}

        browser._prepare_pre_submit_backfill(
            {"enabled": True, "mode": "full", "block_on_missing": True},
            context,
        )

        self.assertEqual(context["pre_submit_backfill_status"], "ready")
        self.assertEqual(context["pre_submit_backfill_actions"][0]["status"], "applied")

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

    def test_store_context_classifies_login_before_missing_store_selector(self) -> None:
        browser = SkuOfflineBrowser({}, PROJECT_ROOT)
        browser.driver = FakeSuccessDriver(current_url="https://login.taobao.com/member/login.jhtml")
        browser._wait_for_element = lambda selector: (_ for _ in ()).throw(RuntimeError("missing"))  # type: ignore[method-assign]
        context: dict[str, str] = {}

        with self.assertRaises(OfflineLoginRequiredError):
            browser._assert_store_context(
                {"current_store_name": {"by": "css", "value": ".user-name"}},
                context,
                required=True,
            )

        self.assertEqual(context["page_error_category"], "login_required")
        self.assertEqual(context["page_error_stage"], "login_check")

    def test_assert_edit_page_identity_accepts_matching_product_id(self) -> None:
        browser = SkuOfflineBrowser({}, PROJECT_ROOT)
        browser.driver = FakeSuccessDriver(
            current_url="https://offer-new.1688.com/popular/publish.htm?id=1000406623557&operator=edit"
        )
        context = {"product_id": "1000406623557"}

        browser._assert_edit_page_identity(context, {"verify_edit_page_identity": True})

        self.assertEqual(context["edit_page_product_id"], "1000406623557")

    def test_assert_edit_page_identity_rejects_mismatched_product_id(self) -> None:
        browser = SkuOfflineBrowser({}, PROJECT_ROOT)
        browser.driver = FakeSuccessDriver(
            current_url="https://offer-new.1688.com/popular/publish.htm?id=999&operator=edit"
        )
        context = {"product_id": "1000406623557"}

        with self.assertRaises(OfflineIdentityMismatchError):
            browser._assert_edit_page_identity(context, {"verify_edit_page_identity": True})

        self.assertEqual(context["page_error_category"], "identity_mismatch")

    def test_assert_no_risk_control_block_rejects_captcha_url(self) -> None:
        browser = SkuOfflineBrowser({}, PROJECT_ROOT)
        browser.driver = FakeSuccessDriver(current_url="https://login.1688.com/captcha")
        context: dict[str, str] = {}

        with self.assertRaises(OfflineRiskControlError):
            browser._assert_no_risk_control_block(context)

        self.assertEqual(context["page_error_category"], "risk_control")

    def test_assert_store_context_required_wraps_missing_element(self) -> None:
        browser = SkuOfflineBrowser({}, PROJECT_ROOT)
        browser.driver = FakeSuccessDriver()
        browser.driver.switch_to = type("SwitchTo", (), {"default_content": lambda self: None})()
        browser._wait_for_element = lambda selector: (_ for _ in ()).throw(RuntimeError("missing"))  # type: ignore[method-assign]

        with self.assertRaises(OfflineStoreMismatchError):
            browser._assert_store_context(
                {"current_store_name": {"by": "css", "value": ".user-name"}},
                {"store_name": "阿里巴巴-常州工莱家具"},
                required=True,
            )

    def test_assert_store_context_accepts_configured_store_alias(self) -> None:
        browser = SkuOfflineBrowser({}, PROJECT_ROOT)
        browser.driver = FakeSuccessDriver(current_url="https://work.1688.com/")
        current_store = type("StoreElement", (), {"text": "乐畅家具"})()
        browser._wait_for_element = lambda selector: current_store  # type: ignore[method-assign]
        context: dict[str, object] = {"store_name": "阿里巴巴-常州乐畅家居有限公司"}
        browser._apply_account_binding_context(
            context,
            {
                "account_key": "lechang",
                "store_aliases": ["常州乐畅家居有限公司", "乐畅家具"],
            },
        )

        browser._assert_store_context(
            {"current_store_name": {"by": "css", "value": ".user-name"}},
            context,
            required=True,
        )

        self.assertEqual(context["current_store_name"], "乐畅家具")
        self.assertNotIn("page_error_category", context)

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
