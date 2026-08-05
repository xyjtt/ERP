from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from urllib.parse import parse_qs, urlparse

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
    PublishSubmitError,
    PublishValidationError,
)
from sku_offline_browser import SkuOfflineBrowser
from sku_offline_tasks import OfflineTask
from config_loader import load_json_with_local_override


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
        self.refresh_count = 0

    def get(self, url: str) -> None:
        self.visited_urls.append(url)
        self.current_url = url

    def refresh(self) -> None:
        self.refresh_count += 1

    def execute_script(self, script: str, *args: object) -> None:
        return None


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
    def test_stop_sale_config_only_backfills_delivery_service(self) -> None:
        config = json.loads(
            (PROJECT_ROOT / "config" / "systems" / "1688_sku_offline.json").read_text(
                encoding="utf-8"
            )
        )

        self.assertEqual(
            config["workflow"]["pre_submit_backfill"]["mode"],
            "delivery_service_only",
        )
        management_query = parse_qs(
            urlparse(config["management_url"]).query,
            keep_blank_values=True,
        )
        self.assertEqual(management_query["tab"], ["all"])
        self.assertEqual(management_query["q"], [""])
        self.assertEqual(management_query["filterOfferId"], [""])

    def test_stop_sale_config_uses_external_executor_profiles(self) -> None:
        config = json.loads(
            (PROJECT_ROOT / "config" / "systems" / "1688_sku_offline.json").read_text(
                encoding="utf-8"
            )
        )
        bindings = config["execution"]["store_accounts"]

        self.assertEqual(
            {
                binding["account_key"]: binding["browser_profile_dir"]
                for binding in bindings
            },
            {
                account_key: f"C:/ProgramData/YYDD/1688-crawler/profiles/{account_key}"
                for account_key in (
                    "muke_lixiang",
                    "guangzhou_wolai",
                    "gonglai",
                    "lechang",
                )
            },
        )

    def test_management_url_normalizes_to_unfiltered_all_tab(self) -> None:
        normalized = SkuOfflineBrowser._normalize_management_all_tab_url(
            "https://work.1688.com/?_path_=sellerPro/offer&tab=onsale&q=123&filterOfferId=123"
        )
        query = parse_qs(urlparse(normalized).query, keep_blank_values=True)

        self.assertEqual(query["tab"], ["all"])
        self.assertEqual(query["q"], [""])
        self.assertEqual(query["filterOfferId"], [""])

    def test_replace_config_inherits_unfiltered_all_tab(self) -> None:
        config = load_json_with_local_override(
            PROJECT_ROOT / "config" / "systems" / "1688_sku_replace.json"
        )
        query = parse_qs(urlparse(config["management_url"]).query, keep_blank_values=True)

        self.assertEqual(config["execution"]["operation"], "replace")
        self.assertEqual(config["input"]["filters"]["handling"], "全渠道替换")
        self.assertEqual(query["tab"], ["all"])
        self.assertIn("全部", config["workflow"]["selectors"]["all_products_tab"]["value"])

    def test_prepare_session_checks_login_and_risk_after_opening_management_page(self) -> None:
        class PrepareBrowser(SkuOfflineBrowser):
            def __init__(self) -> None:
                super().__init__({}, PROJECT_ROOT)
                self.calls: list[str] = []

            def open_management_page(self, _system_config: dict) -> None:
                self.calls.append("open_management_page")

            def _assert_not_redirected_to_login(self, context: dict) -> None:
                self.calls.append("login_check")

            def _assert_no_risk_control_block(self, context: dict) -> None:
                self.calls.append("risk_check")

        browser = PrepareBrowser()
        browser.prepare_session({}, skip_login=True)

        self.assertEqual(
            browser.calls,
            ["open_management_page", "login_check", "risk_check"],
        )

    def test_grouped_replacement_submits_product_once(self) -> None:
        tasks = [
            OfflineTask(
                source_file="replace.csv",
                source_sheet="CSV",
                source_row_number=index + 2,
                store_name="STORE-A",
                platform="Alibaba",
                product_id="1001",
                online_sku=f"OLD-{index}",
                handling="全渠道替换",
                replacement_sku=f"NEW-{index}",
                change_image="",
                platform_store_item_code=f"CODE-{index}",
                raw={},
            )
            for index in range(2)
        ]
        browser = SkuOfflineBrowser({}, PROJECT_ROOT)
        browser.driver = FakeSuccessDriver()
        browser.driver.current_window_handle = "main"  # type: ignore[attr-defined]
        calls: list[str] = []
        browser._open_task_edit_page = lambda *args: calls.append("open")  # type: ignore[method-assign]
        browser._assert_not_redirected_to_login = lambda *args: None  # type: ignore[method-assign]
        browser._assert_no_risk_control_block = lambda *args: None  # type: ignore[method-assign]
        browser._assert_edit_page_identity = lambda *args: None  # type: ignore[method-assign]
        browser._replace_sku_codes_in_runtime_state = lambda contexts: {  # type: ignore[method-assign]
            "supported": True,
            "results": [
                {"status": "changed", "verified": True},
                {"status": "changed", "verified": True},
            ],
        }
        browser._assert_replacement_group_ready = lambda contexts: calls.append(f"ready:{len(contexts)}")  # type: ignore[method-assign]
        browser._submit_changes = lambda *args, **kwargs: calls.append("submit")  # type: ignore[method-assign]
        browser._verify_group_persisted_replacement = lambda selectors, config, contexts: calls.append(f"verify:{len(contexts)}")  # type: ignore[method-assign]
        browser._record_page_metadata = lambda context: None  # type: ignore[method-assign]
        browser._restore_management_window = lambda handle: None  # type: ignore[method-assign]

        outcomes = browser.execute_replace_group({}, tasks)

        self.assertEqual([item["status"] for item in outcomes], ["success", "success"])
        self.assertEqual(calls, ["open", "ready:2", "submit", "verify:2"])

    def test_replacement_idempotency_reports_already_replaced(self) -> None:
        task = OfflineTask(
            source_file="replace.csv", source_sheet="CSV", source_row_number=2,
            store_name="STORE-A", platform="Alibaba", product_id="1001",
            online_sku="OLD", handling="全渠道替换", replacement_sku="NEW",
            change_image="", platform_store_item_code="CODE", raw={},
        )
        browser = SkuOfflineBrowser({}, PROJECT_ROOT)
        browser.driver = FakeSuccessDriver()
        browser.driver.current_window_handle = "main"  # type: ignore[attr-defined]
        browser._open_task_edit_page = lambda *args: None  # type: ignore[method-assign]
        browser._assert_not_redirected_to_login = lambda *args: None  # type: ignore[method-assign]
        browser._assert_no_risk_control_block = lambda *args: None  # type: ignore[method-assign]
        browser._assert_edit_page_identity = lambda *args: None  # type: ignore[method-assign]
        browser._replace_sku_codes_in_runtime_state = lambda contexts: {  # type: ignore[method-assign]
            "supported": True, "results": [{"status": "already_replaced"}],
        }
        browser._record_page_metadata = lambda context: None  # type: ignore[method-assign]
        browser._restore_management_window = lambda handle: None  # type: ignore[method-assign]

        outcome = browser.execute_replace_group({}, [task])[0]

        self.assertEqual(outcome["status"], "already_replaced")

    def test_dom_replacement_uses_keyboard_input_and_commits_on_tab(self) -> None:
        class InputElement:
            def __init__(self, value: str) -> None:
                self.value = value
                self.selected = False
                self.keys: list[tuple[object, ...]] = []

            def click(self) -> None:
                return None

            def get_attribute(self, name: str) -> str:
                return self.value if name == "value" else ""

            def send_keys(self, *keys: object) -> None:
                self.keys.append(keys)
                if keys == (Keys.CONTROL, "a"):
                    self.selected = True
                elif keys == (Keys.DELETE,) and self.selected:
                    self.value = ""
                elif keys == (Keys.TAB,):
                    self.selected = False
                elif keys and isinstance(keys[0], str):
                    self.value = str(keys[0])

        class RowElement:
            def __init__(self, cargo_input: InputElement) -> None:
                self.cargo_input = cargo_input

            def find_elements(self, _by: str, _value: str) -> list[InputElement]:
                return [self.cargo_input]

        class DomDriver(FakeSuccessDriver):
            def __init__(self, cargo_input: InputElement) -> None:
                super().__init__()
                self.row = RowElement(cargo_input)

            def find_elements(self, _by: str, _value: str) -> list[RowElement]:
                return [self.row]

        cargo_input = InputElement("OLD-SKU")
        browser = SkuOfflineBrowser({}, PROJECT_ROOT)
        browser.driver = DomDriver(cargo_input)
        browser._pause = lambda _seconds: None  # type: ignore[method-assign]

        result = browser._replace_sku_codes_in_dom(
            [{"online_sku": "OLD-SKU", "replacement_sku": "NEW-SKU"}]
        )

        self.assertTrue(result["supported"])
        self.assertEqual(result["changed_count"], 1)
        self.assertEqual(result["results"][0]["status"], "changed")
        self.assertEqual(cargo_input.value, "NEW-SKU")
        self.assertIn((Keys.TAB,), cargo_input.keys)

    def test_open_management_page_reuses_unfiltered_all_tab(self) -> None:
        browser = SkuOfflineBrowser({}, PROJECT_ROOT)
        management_url = (
            "https://work.1688.com/?_path_=sellerPro/offer&tab=all&q=&filterOfferId="
        )
        driver = FakeSuccessDriver(current_url=management_url)
        browser.driver = driver

        browser.open_management_page({"management_url": management_url})

        self.assertEqual(driver.visited_urls, [])

    def test_open_management_page_activates_all_tab_after_navigation(self) -> None:
        class TabElement:
            def get_attribute(self, name: str) -> str:
                return "tabs-tab inactive" if name == "class" else "false"

        class TabDriver(FakeSuccessDriver):
            def __init__(self) -> None:
                super().__init__()
                self.clicked = False

            def find_elements(self, by: str, value: str) -> list[TabElement]:
                return [TabElement()]

            def execute_script(self, script: str, *args: object) -> None:
                if "arguments[0].click" in script:
                    self.clicked = True

        browser = SkuOfflineBrowser({}, PROJECT_ROOT)
        browser._pause = lambda seconds: None  # type: ignore[method-assign]
        driver = TabDriver()
        browser.driver = driver

        browser.open_management_page(
            {"management_url": "https://work.1688.com/?_path_=sellerPro/offer"}
        )

        query = parse_qs(urlparse(driver.visited_urls[0]).query, keep_blank_values=True)
        self.assertEqual(query["tab"], ["all"])
        self.assertTrue(driver.clicked)

    def test_active_all_tab_is_not_clicked_again(self) -> None:
        class TabElement:
            def get_attribute(self, name: str) -> str:
                return "tabs-tab tabs-tab-active" if name == "class" else "true"

        class TabDriver(FakeSuccessDriver):
            def __init__(self) -> None:
                super().__init__()
                self.clicked = False

            def find_elements(self, by: str, value: str) -> list[TabElement]:
                return [TabElement()]

            def execute_script(self, script: str, *args: object) -> None:
                self.clicked = True

        browser = SkuOfflineBrowser({}, PROJECT_ROOT)
        driver = TabDriver()
        browser.driver = driver

        self.assertFalse(browser._activate_all_products_tab())
        self.assertFalse(driver.clicked)

    def test_management_frame_clicks_all_tab_and_confirms_active_state(self) -> None:
        state = {"active": False}

        class TabElement:
            def click(self) -> None:
                state["active"] = True

            def get_attribute(self, name: str) -> str:
                if name == "class":
                    return "tabs-tab tabs-tab-active" if state["active"] else "tabs-tab"
                if name == "aria-selected":
                    return "true" if state["active"] else "false"
                return ""

            def is_displayed(self) -> bool:
                return True

        class TabDriver(FakeSuccessDriver):
            def find_elements(self, by: str, value: str) -> list[TabElement]:
                return [TabElement()]

            def execute_script(self, script: str, *args: object) -> None:
                return None

        browser = SkuOfflineBrowser(
            {"management_tab_timeout_seconds": 0.1},
            PROJECT_ROOT,
        )
        browser._pause = lambda seconds: None  # type: ignore[method-assign]
        browser.driver = TabDriver()
        context: dict[str, object] = {}

        browser._ensure_all_products_tab_active({}, context)

        self.assertTrue(state["active"])
        self.assertEqual(context["management_products_tab_click_mode"], "native")
        self.assertEqual(context["management_products_tab"], "all")
        self.assertTrue(context["management_products_tab_verified"])

    def test_management_frame_accepts_all_tab_with_count_suffix(self) -> None:
        observed_xpaths: list[str] = []

        class TabElement:
            def get_attribute(self, name: str) -> str:
                if name == "class":
                    return "ant-tabs-tab ant-tabs-tab-active"
                if name == "aria-selected":
                    return "true"
                return ""

            def is_displayed(self) -> bool:
                return True

        class TabDriver(FakeSuccessDriver):
            def find_elements(self, by: str, value: str) -> list[TabElement]:
                observed_xpaths.append(value)
                if '" ant-tabs-tab "' in value and "starts-with" in value:
                    return [TabElement()]
                return []

        browser = SkuOfflineBrowser(
            {"management_tab_timeout_seconds": 0},
            PROJECT_ROOT,
        )
        browser.driver = TabDriver()
        context: dict[str, object] = {}

        browser._ensure_all_products_tab_active({}, context)

        self.assertTrue(any("starts-with" in xpath for xpath in observed_xpaths))
        self.assertTrue(any("全部商品" in xpath for xpath in observed_xpaths))
        self.assertEqual(context["management_products_tab"], "all")
        self.assertTrue(context["management_products_tab_verified"])

    def test_new_window_timeout_keeps_original_management_window(self) -> None:
        class SwitchTo:
            def __init__(self, driver: object) -> None:
                self.driver = driver

            def window(self, handle: str) -> None:
                self.driver.current_window_handle = handle

        class WindowDriver:
            def __init__(self) -> None:
                self.window_handles = ["management", "preexisting-new-tab"]
                self.current_window_handle = "management"
                self.switch_to = SwitchTo(self)

        browser = SkuOfflineBrowser(
            {"explicit_wait_seconds": 0},
            PROJECT_ROOT,
        )
        driver = WindowDriver()
        browser.driver = driver

        browser._switch_to_newest_window(list(driver.window_handles))

        self.assertEqual(driver.current_window_handle, "management")

    def test_management_edit_uses_row_href_when_popup_does_not_open(self) -> None:
        product_id = "1058226583685"
        edit_url = (
            "https://offer-new.1688.com/popular/publish.htm"
            f"?id={product_id}&operator=edit"
        )

        class EditButton:
            def click(self) -> None:
                return

            def get_attribute(self, name: str) -> str:
                return edit_url if name == "href" else ""

        class ResultRow:
            def find_element(self, by: str, value: str) -> EditButton:
                return EditButton()

        class SwitchTo:
            def __init__(self, driver: object) -> None:
                self.driver = driver

            def default_content(self) -> None:
                return

            def window(self, handle: str) -> None:
                self.driver.current_window_handle = handle

        class EditDriver(FakeSuccessDriver):
            def __init__(self) -> None:
                super().__init__(current_url="https://work.1688.com/?_path_=sellerPro")
                self.window_handles = ["management"]
                self.current_window_handle = "management"
                self.switch_to = SwitchTo(self)

        browser = SkuOfflineBrowser({"explicit_wait_seconds": 0}, PROJECT_ROOT)
        browser.driver = EditDriver()
        browser._wait_for_management_result_row = (  # type: ignore[method-assign]
            lambda selector, context: ResultRow()
        )
        browser._raise_if_edit_page_unavailable = lambda context: None  # type: ignore[method-assign]
        browser._activate_sales_info_section = lambda selectors, context: None  # type: ignore[method-assign]
        browser._pause = lambda seconds: None  # type: ignore[method-assign]
        context: dict[str, object] = {"product_id": product_id}

        browser._open_edit_page(
            {
                "product_result_row": {"by": "xpath", "value": "//tr"},
                "edit_button_in_row": {"by": "xpath", "value": ".//a"},
            },
            context,
        )

        self.assertEqual(browser.driver.current_url, edit_url)
        self.assertEqual(context["management_edit_click_mode"], "native")
        self.assertEqual(context["management_edit_navigation"], "row_href_fallback")
        self.assertEqual(context["edit_entry_stage"], "edit_page_ready")

    def test_management_edit_constructs_same_product_fallback_without_row_href(self) -> None:
        product_id = "1065419644989"

        class EditButton:
            def click(self) -> None:
                return

            def get_attribute(self, name: str) -> str:
                return ""

        class ResultRow:
            def find_element(self, by: str, value: str) -> EditButton:
                return EditButton()

        class SwitchTo:
            def __init__(self, driver: object) -> None:
                self.driver = driver

            def default_content(self) -> None:
                return

            def window(self, handle: str) -> None:
                self.driver.current_window_handle = handle

        class EditDriver(FakeSuccessDriver):
            def __init__(self) -> None:
                super().__init__(current_url="https://work.1688.com/?_path_=sellerPro")
                self.window_handles = ["management"]
                self.current_window_handle = "management"
                self.switch_to = SwitchTo(self)

        browser = SkuOfflineBrowser({"explicit_wait_seconds": 0}, PROJECT_ROOT)
        browser.driver = EditDriver()
        browser._wait_for_management_result_row = (  # type: ignore[method-assign]
            lambda selector, context: ResultRow()
        )
        browser._raise_if_edit_page_unavailable = lambda context: None  # type: ignore[method-assign]
        browser._activate_sales_info_section = lambda selectors, context: None  # type: ignore[method-assign]
        browser._pause = lambda seconds: None  # type: ignore[method-assign]
        context: dict[str, object] = {"product_id": product_id}

        browser._open_edit_page(
            {
                "product_result_row": {"by": "xpath", "value": "//tr"},
                "edit_button_in_row": {"by": "xpath", "value": ".//a"},
            },
            context,
        )

        fallback_url = str(context["management_edit_fallback_url"])
        self.assertEqual(browser.driver.current_url, fallback_url)
        self.assertEqual(parse_qs(urlparse(fallback_url).query)["id"], [product_id])
        self.assertEqual(context["management_edit_navigation"], "constructed_url_fallback")
        self.assertEqual(context["edit_entry_stage"], "edit_page_ready")

    def test_management_edit_rejects_unsafe_row_href(self) -> None:
        browser = SkuOfflineBrowser({}, PROJECT_ROOT)
        browser.driver = FakeSuccessDriver(current_url="https://work.1688.com/")

        class EditButton:
            def get_attribute(self, name: str) -> str:
                return "https://example.com/publish.htm?id=1001" if name == "href" else ""

        self.assertEqual(browser._resolve_management_edit_href(EditButton(), "1001"), "")
        self.assertFalse(
            browser._is_expected_edit_page_url(
                "https://offer-new.1688.com/popular/publish.htm?id=1002&operator=edit",
                "1001",
            )
        )

    def test_management_frame_reloads_once_when_iframe_is_missing(self) -> None:
        calls: list[str] = []
        iframe = object()

        class SwitchTo:
            def default_content(self) -> None:
                calls.append("default_content")

            def frame(self, value: object) -> None:
                self.frame_value = value
                calls.append("frame")

        class FrameDriver:
            current_url = "https://work.1688.com/?_path_=sellerPro"
            switch_to = SwitchTo()

        browser = SkuOfflineBrowser(
            {"management_frame_reload_wait_seconds": 0},
            PROJECT_ROOT,
        )
        browser.driver = FrameDriver()
        attempts = iter((TimeoutException("missing iframe"), iframe))

        def wait_for_element(selector: dict[str, object]) -> object:
            result = next(attempts)
            if isinstance(result, Exception):
                raise result
            return result

        browser._wait_for_element = wait_for_element  # type: ignore[method-assign]
        browser._navigate_with_timeout_recovery = (  # type: ignore[method-assign]
            lambda url: calls.append(f"reload:{url}") or False
        )
        browser._assert_not_redirected_to_login = (  # type: ignore[method-assign]
            lambda context: calls.append("auth_checked")
        )
        context: dict[str, object] = {}

        browser._switch_into_management_frame(
            {"management_iframe": {"by": "xpath", "value": "//iframe"}},
            context,
        )

        self.assertEqual(context["management_frame_retry"], "reload")
        self.assertEqual(context["edit_entry_stage"], "management_frame_entered")
        self.assertIn("reload:https://work.1688.com/?_path_=sellerPro", calls)
        self.assertEqual(browser.driver.switch_to.frame_value, iframe)

    def test_management_frame_missing_all_tab_fails_closed(self) -> None:
        class MissingTabDriver(FakeSuccessDriver):
            def find_elements(self, by: str, value: str) -> list[object]:
                return []

        browser = SkuOfflineBrowser(
            {"management_tab_timeout_seconds": 0},
            PROJECT_ROOT,
        )
        browser.driver = MissingTabDriver()
        context: dict[str, object] = {}

        with self.assertRaisesRegex(OfflineTaskStateError, "全部"):
            browser._ensure_all_products_tab_active({}, context)

        self.assertEqual(context["page_error_category"], "management_tab_mismatch")
        self.assertEqual(context["page_error_stage"], "management_tab_selection")

    def test_management_search_verifies_all_tab_before_search_readiness(self) -> None:
        browser = SkuOfflineBrowser({}, PROJECT_ROOT)
        calls: list[str] = []
        browser._ensure_all_products_tab_active = (  # type: ignore[method-assign]
            lambda selectors, context: calls.append("verify_all_tab")
        )

        def stop_after_readiness(context: dict[str, object]) -> None:
            calls.append("wait_search_ready")
            raise RuntimeError("stop after ordering assertion")

        browser._wait_for_management_search_ready = stop_after_readiness  # type: ignore[method-assign]

        with self.assertRaisesRegex(RuntimeError, "ordering assertion"):
            browser._search_product({}, {})

        self.assertEqual(calls, ["verify_all_tab", "wait_search_ready"])

    def test_navigation_timeout_stops_loading_and_continues_validation(self) -> None:
        class TimeoutDriver(FakeSuccessDriver):
            def __init__(self) -> None:
                super().__init__()
                self.scripts: list[str] = []

            def get(self, url: str) -> None:
                self.current_url = url
                raise TimeoutException("renderer still loading")

            def execute_script(self, script: str, *args: object) -> None:
                self.scripts.append(script)

        browser = SkuOfflineBrowser({}, PROJECT_ROOT)
        driver = TimeoutDriver()
        browser.driver = driver

        self.assertTrue(browser._navigate_with_timeout_recovery("https://work.1688.com/"))
        self.assertEqual(driver.scripts, ["window.stop();"])

    def test_management_search_no_data_requires_an_explicit_page_marker(self) -> None:
        self.assertTrue(SkuOfflineBrowser._management_search_shows_no_data("查询完成\n暂无数据"))
        self.assertTrue(SkuOfflineBrowser._management_search_shows_no_data("没有找到商品"))
        self.assertFalse(SkuOfflineBrowser._management_search_shows_no_data("页面仍在加载"))

    def test_grouped_product_opens_once_and_submits_multiple_skus_once(self) -> None:
        tasks = [
            OfflineTask(
                source_file="demo.csv",
                source_sheet="CSV",
                source_row_number=index + 2,
                store_name="STORE-A",
                platform="Alibaba",
                product_id="1007556214138",
                online_sku=f"SKU-{index}",
                handling="all-channel-offline",
                replacement_sku="",
                change_image="",
                platform_store_item_code=f"CODE-{index}",
                raw={},
            )
            for index in range(2)
        ]
        browser = SkuOfflineBrowser({}, PROJECT_ROOT)
        browser.driver = FakeSuccessDriver()
        browser.driver.current_window_handle = "main"  # type: ignore[attr-defined]
        calls: list[str] = []
        browser._open_task_edit_page = lambda *args: calls.append("open")  # type: ignore[method-assign]
        browser._assert_not_redirected_to_login = lambda *args: None  # type: ignore[method-assign]
        browser._assert_no_risk_control_block = lambda *args: None  # type: ignore[method-assign]
        browser._assert_edit_page_identity = lambda *args: None  # type: ignore[method-assign]
        browser._toggle_sku_offline = (  # type: ignore[method-assign]
            lambda selectors, context: calls.append(f"toggle:{context['online_sku']}") or True
        )
        browser._ensure_target_sku_still_offline = lambda *args: None  # type: ignore[method-assign]
        browser._submit_changes = lambda *args: calls.append("submit")  # type: ignore[method-assign]
        browser._verify_group_persisted_offline = (  # type: ignore[method-assign]
            lambda selectors, config, contexts: calls.append(f"verify:{len(contexts)}")
        )
        browser._record_page_metadata = lambda context: None  # type: ignore[method-assign]
        browser._restore_management_window = lambda handle: None  # type: ignore[method-assign]

        outcomes = browser.execute_offline_group({}, tasks)

        self.assertEqual([item["status"] for item in outcomes], ["success", "success"])
        self.assertEqual(
            calls,
            ["open", "toggle:SKU-0", "toggle:SKU-1", "submit", "verify:2"],
        )

    def test_grouped_product_keeps_valid_sku_when_another_sku_fails(self) -> None:
        tasks = [
            OfflineTask(
                source_file="demo.csv",
                source_sheet="CSV",
                source_row_number=index + 2,
                store_name="STORE-A",
                platform="Alibaba",
                product_id="1007556214138",
                online_sku=f"SKU-{index}",
                handling="all-channel-offline",
                replacement_sku="",
                change_image="",
                platform_store_item_code=f"CODE-{index}",
                raw={},
            )
            for index in range(2)
        ]
        browser = SkuOfflineBrowser({}, PROJECT_ROOT)
        browser.driver = FakeSuccessDriver()
        browser.driver.current_window_handle = "main"  # type: ignore[attr-defined]
        submit_calls: list[str] = []
        browser._open_task_edit_page = lambda *args: None  # type: ignore[method-assign]
        browser._assert_not_redirected_to_login = lambda *args: None  # type: ignore[method-assign]
        browser._assert_no_risk_control_block = lambda *args: None  # type: ignore[method-assign]
        browser._assert_edit_page_identity = lambda *args: None  # type: ignore[method-assign]

        def toggle(selectors, context):
            if context["online_sku"] == "SKU-0":
                raise OfflineTaskNotFoundError("missing SKU")
            return True

        browser._toggle_sku_offline = toggle  # type: ignore[method-assign]
        browser._ensure_target_sku_still_offline = lambda *args: None  # type: ignore[method-assign]
        browser._submit_changes = lambda *args: submit_calls.append("submit")  # type: ignore[method-assign]
        browser._verify_group_persisted_offline = lambda *args: None  # type: ignore[method-assign]
        browser._record_page_metadata = lambda context: None  # type: ignore[method-assign]
        browser._capture_screenshot = lambda name: None  # type: ignore[method-assign]
        browser._restore_management_window = lambda handle: None  # type: ignore[method-assign]

        outcomes = browser.execute_offline_group({}, tasks)

        self.assertEqual([item["status"] for item in outcomes], ["failed", "success"])
        self.assertEqual(submit_calls, ["submit"])

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

    def test_task_edit_reloads_all_tab_once_after_stale_management_page(self) -> None:
        browser = SkuOfflineBrowser({}, PROJECT_ROOT)
        browser.driver = FakeSuccessDriver()
        calls: list[str] = []
        browser.open_management_page = lambda config: calls.append("open_management")  # type: ignore[method-assign]
        browser._assert_store_context = lambda *args, **kwargs: calls.append("assert_store")  # type: ignore[method-assign]
        browser._switch_into_management_frame = lambda *args: calls.append("switch_frame")  # type: ignore[method-assign]
        browser._navigate_with_timeout_recovery = (  # type: ignore[method-assign]
            lambda url: calls.append("forced_reload") or False
        )
        browser._pause = lambda seconds: None  # type: ignore[method-assign]
        browser._open_edit_page = lambda *args, **kwargs: calls.append("open_edit")  # type: ignore[method-assign]
        search_attempts = 0

        def fake_search(selectors: dict[str, str], context: dict[str, str]) -> None:
            nonlocal search_attempts
            search_attempts += 1
            calls.append("search_product")
            if search_attempts == 1:
                context["page_error_category"] = "management_tab_mismatch"
                context["page_error_stage"] = "management_tab_selection"
                context["page_error_text"] = "stale management page"
                raise OfflineTaskStateError("stale management page")

        browser._search_product = fake_search  # type: ignore[method-assign]
        context: dict[str, str] = {"product_id": "1022879495664"}

        browser._open_task_edit_page(
            {"management_url": "https://work.1688.com/?_path_=sellerPro/offer"},
            {},
            context,
            {},
        )

        self.assertEqual(
            calls,
            [
                "open_management",
                "assert_store",
                "switch_frame",
                "search_product",
                "forced_reload",
                "assert_store",
                "switch_frame",
                "search_product",
                "open_edit",
            ],
        )
        self.assertEqual(context["management_all_tab_retry"], "forced_reload")
        self.assertNotIn("page_error_category", context)

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

    def test_submit_click_falls_back_to_webdriver_after_cdp_oserror(self) -> None:
        class SubmitDriver(FakeSuccessDriver):
            def execute_script(self, script: str, *args: object) -> dict[str, float]:
                return {"x": 100.0, "y": 200.0, "width": 20.0, "height": 10.0}

            def execute_cdp_cmd(self, method: str, params: dict[str, object]) -> None:
                raise OSError(22, "Invalid argument")

        browser = SkuOfflineBrowser({}, PROJECT_ROOT)
        browser.driver = SubmitDriver()
        submit_element = FakeClickableElement()
        context: dict[str, str] = {}

        browser._click_submit_element(submit_element, context)

        self.assertTrue(submit_element.clicked)
        self.assertEqual(context["submit_click_mode"], "webdriver_click")
        self.assertIn("CDP click failed", context["submit_cdp_click_error"])

    def test_submit_click_uses_cdp_when_coordinates_are_valid(self) -> None:
        class SubmitDriver(FakeSuccessDriver):
            def __init__(self) -> None:
                super().__init__()
                self.cdp_events: list[str] = []

            def execute_script(self, script: str, *args: object) -> dict[str, float]:
                return {"x": 100.0, "y": 200.0, "width": 20.0, "height": 10.0}

            def execute_cdp_cmd(self, method: str, params: dict[str, object]) -> None:
                self.cdp_events.append(str(params["type"]))

        browser = SkuOfflineBrowser({}, PROJECT_ROOT)
        driver = SubmitDriver()
        browser.driver = driver
        submit_element = FakeClickableElement()
        context: dict[str, str] = {}

        browser._click_submit_element(submit_element, context)

        self.assertFalse(submit_element.clicked)
        self.assertEqual(context["submit_click_mode"], "cdp_mouse")
        self.assertEqual(driver.cdp_events, ["mouseMoved", "mousePressed", "mouseReleased"])

    def test_restore_management_window_closes_all_extra_tabs(self) -> None:
        class SwitchTarget:
            def __init__(self, driver: "MultiTabDriver") -> None:
                self.driver = driver

            def window(self, handle: str) -> None:
                self.driver.current_window_handle = handle

            def default_content(self) -> None:
                return None

        class MultiTabDriver(FakeSuccessDriver):
            def __init__(self) -> None:
                super().__init__()
                self.window_handles = ["main", "edit-1", "edit-2"]
                self.current_window_handle = "edit-2"
                self.switch_to = SwitchTarget(self)

            def close(self) -> None:
                self.window_handles.remove(self.current_window_handle)

        browser = SkuOfflineBrowser({}, PROJECT_ROOT)
        driver = MultiTabDriver()
        browser.driver = driver

        browser._restore_management_window("main")

        self.assertEqual(driver.window_handles, ["main"])
        self.assertEqual(driver.current_window_handle, "main")

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
        browser._find_sku_rows_by_runtime_value = lambda context: [row]  # type: ignore[method-assign]
        browser._wait_for_element = lambda selector: calls.append("wait")  # type: ignore[method-assign]
        browser._toggle_found_sku_rows = lambda selectors, context, sku_rows: sku_rows == [row]  # type: ignore[method-assign]

        changed = browser._toggle_sku_offline(
            {"sku_row": {"by": "xpath", "value": "//tr[.//input[@value='{online_sku}']]"}},
            {"online_sku": "DSG005015N11V01"},
        )

        self.assertTrue(changed)
        self.assertEqual(calls, [])

    def test_duplicate_barcode_rows_are_all_toggled_offline(self) -> None:
        class DuplicateSwitch:
            def __init__(self, online: bool) -> None:
                self.online = online

            @property
            def text(self) -> str:
                return "上架" if self.online else "下架"

            def get_attribute(self, name: str) -> str:
                return ("true" if self.online else "false") if name == "aria-checked" else ""

            def is_displayed(self) -> bool:
                return True

        class DuplicateRow:
            def __init__(self, row_id: str, switch: DuplicateSwitch) -> None:
                self.id = row_id
                self.switch = switch

            def find_element(self, by: str, value: str) -> DuplicateSwitch:
                return self.switch

        class DuplicateDriver(FakeSuccessDriver):
            def __init__(self, switches: list[DuplicateSwitch]) -> None:
                super().__init__()
                self.switches = switches

            def execute_script(self, script: str, *args: object) -> object:
                if "button[role=\"switch\"].ant-switch" in script:
                    return self.switches
                if "arguments[0].click" in script:
                    args[0].online = not args[0].online  # type: ignore[attr-defined]
                return None

        duplicate_switches = [DuplicateSwitch(True), DuplicateSwitch(True)]
        other_switch = DuplicateSwitch(True)
        browser = SkuOfflineBrowser({}, PROJECT_ROOT)
        browser.driver = DuplicateDriver([*duplicate_switches, other_switch])
        rows = [
            DuplicateRow("row-1", duplicate_switches[0]),
            DuplicateRow("row-2", duplicate_switches[1]),
        ]
        browser._find_sku_rows_by_runtime_value = lambda context: rows  # type: ignore[method-assign]
        browser._pause = lambda seconds: None  # type: ignore[method-assign]
        browser._wait_for_switch_change = lambda *args, **kwargs: None  # type: ignore[method-assign]
        context: dict[str, object] = {"online_sku": "DUPLICATE-CODE"}

        changed = browser._toggle_sku_offline(
            {
                "sku_row": {"by": "xpath", "value": "//tr"},
                "sku_switch": {"by": "css", "value": "button.ant-switch"},
            },
            context,
        )

        self.assertTrue(changed)
        self.assertEqual([switch.online for switch in duplicate_switches], [False, False])
        self.assertTrue(other_switch.online)
        self.assertEqual(context["matching_sku_row_count"], 2)
        self.assertEqual(context["matching_sku_changed_count"], 2)

    def test_duplicate_barcode_rows_are_idempotent_when_all_already_offline(self) -> None:
        class OfflineSwitch:
            text = "下架"

            def get_attribute(self, name: str) -> str:
                return "false" if name == "aria-checked" else ""

            def is_displayed(self) -> bool:
                return True

        class OfflineRow:
            def __init__(self, row_id: str) -> None:
                self.id = row_id
                self.switch = OfflineSwitch()

            def find_element(self, by: str, value: str) -> OfflineSwitch:
                return self.switch

        browser = SkuOfflineBrowser({}, PROJECT_ROOT)
        browser.driver = FakeSuccessDriver()
        rows = [OfflineRow("row-1"), OfflineRow("row-2")]
        browser._find_sku_rows_by_runtime_value = lambda context: rows  # type: ignore[method-assign]
        browser._assert_target_not_sole_online_sku = lambda *args, **kwargs: None  # type: ignore[method-assign]
        context: dict[str, object] = {"online_sku": "DUPLICATE-CODE"}

        changed = browser._toggle_sku_offline(
            {
                "sku_row": {"by": "xpath", "value": "//tr"},
                "sku_switch": {"by": "css", "value": "button.ant-switch"},
            },
            context,
        )

        self.assertFalse(changed)
        self.assertEqual(context["matching_sku_offline_before"], 2)

    def test_duplicate_barcode_rows_block_when_they_cover_all_online_skus(self) -> None:
        class OnlineSwitch:
            text = "上架"

            def get_attribute(self, name: str) -> str:
                return "true" if name == "aria-checked" else ""

            def is_displayed(self) -> bool:
                return True

        class OnlineRow:
            def __init__(self, row_id: str, switch: OnlineSwitch) -> None:
                self.id = row_id
                self.switch = switch

            def find_element(self, by: str, value: str) -> OnlineSwitch:
                return self.switch

        switches = [OnlineSwitch(), OnlineSwitch()]
        rows = [OnlineRow("row-1", switches[0]), OnlineRow("row-2", switches[1])]
        browser = SkuOfflineBrowser({}, PROJECT_ROOT)
        browser.driver = FakeSuccessDriver()
        browser.driver.execute_script = lambda script, *args: switches  # type: ignore[attr-defined]
        browser._find_sku_rows_by_runtime_value = lambda context: rows  # type: ignore[method-assign]
        context: dict[str, object] = {"online_sku": "DUPLICATE-CODE"}

        with self.assertRaises(PublishValidationError):
            browser._toggle_sku_offline(
                {
                    "sku_row": {"by": "xpath", "value": "//tr"},
                    "sku_switch": {"by": "css", "value": "button.ant-switch"},
                },
                context,
            )

        self.assertEqual(context["page_error_category"], "sole_sku_requires_product_offline")
        self.assertEqual(context["sku_switch_summary_before"]["target_online_switch_count"], 2)

    def test_post_submit_verification_rejects_any_duplicate_row_still_online(self) -> None:
        class VerifySwitch:
            def __init__(self, online: bool) -> None:
                self.online = online

            @property
            def text(self) -> str:
                return "上架" if self.online else "下架"

            def get_attribute(self, name: str) -> str:
                return ("true" if self.online else "false") if name == "aria-checked" else ""

        class VerifyRow:
            def __init__(self, row_id: str, switch: VerifySwitch) -> None:
                self.id = row_id
                self.switch = switch

            def find_element(self, by: str, value: str) -> VerifySwitch:
                return self.switch

        rows = [
            VerifyRow("row-1", VerifySwitch(False)),
            VerifyRow("row-2", VerifySwitch(True)),
        ]
        browser = SkuOfflineBrowser({}, PROJECT_ROOT)
        browser.driver = FakeSuccessDriver()
        browser._find_sku_rows_by_runtime_value = lambda context: rows  # type: ignore[method-assign]
        context: dict[str, object] = {"online_sku": "DUPLICATE-CODE"}

        with self.assertRaisesRegex(PublishSubmitError, "Duplicate SKU rows"):
            browser._read_current_sku_switch_state(
                {"sku_switch": {"by": "css", "value": "button.ant-switch"}},
                context,
            )

        self.assertEqual(context["post_submit_matching_sku_row_count"], 2)

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

    def test_sole_online_sku_validation_is_detected_from_submit_diagnostics(self) -> None:
        self.assertTrue(
            SkuOfflineBrowser._contains_sole_online_sku_validation(
                "是否上架: 有产品规格的商品至少要有一个在线状态的sku。"
            )
        )
        self.assertFalse(
            SkuOfflineBrowser._contains_sole_online_sku_validation(
                "配送服务为必填项"
            )
        )

    def test_submit_validation_records_platform_message_as_system_prompt(self) -> None:
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
        browser._probe_persisted_offline_after_trace_miss = lambda selectors, context: False  # type: ignore[method-assign]
        browser._collect_submit_block_diagnostics = lambda selector: {  # type: ignore[method-assign]
            "assist_messages": [],
            "validation_nodes": [{"text": "毛重必须为数字"}],
        }
        context: dict[str, object] = {}

        with self.assertRaises(PublishSubmitError):
            browser._submit_changes(
                {"submit_button": {"by": "id", "value": "submitFormButton"}},
                {"enabled": True},
                {},
                {},
                context,
            )

        self.assertEqual(context["page_error_category"], "system_prompt")
        self.assertEqual(context["page_error_text"], "毛重必须为数字")
        self.assertEqual(context["system_prompt"], "毛重必须为数字")

    def test_trace_miss_probe_resets_document_and_reactivates_sales_section(self) -> None:
        browser = SkuOfflineBrowser({}, PROJECT_ROOT)
        browser.driver = FakeSuccessDriver(
            current_url="https://offer-new.1688.com/popular/publish.htm?id=1023529250812&operator=edit"
        )
        switch_element = FakeClickableElement()
        sku_row = type("SkuRow", (), {"find_element": lambda self, *args: switch_element})()
        activated: list[str] = []
        browser._pause = lambda seconds: None  # type: ignore[method-assign]
        browser._assert_not_redirected_to_login = lambda context=None: None  # type: ignore[method-assign]
        browser._assert_no_risk_control_block = lambda context: None  # type: ignore[method-assign]
        browser._activate_sales_info_section = lambda selectors, context: activated.append("sales")  # type: ignore[method-assign]
        browser._extract_product_id_from_current_url = lambda: "1023529250812"  # type: ignore[method-assign]
        browser._find_sku_rows_by_runtime_value = lambda context: [sku_row]  # type: ignore[method-assign]
        browser._read_switch_label = lambda element: "下架"  # type: ignore[method-assign]
        browser._is_already_offline = lambda element, label: True  # type: ignore[method-assign]

        context: dict[str, object] = {
            "product_id": "1023529250812",
            "online_sku": "ZH-SZD000628N693V01-1-SZT009219N961V01-1",
        }

        result = browser._probe_persisted_offline_after_trace_miss(
            {"sku_switch": {"by": "css", "value": ".switch"}},
            context,
        )

        self.assertTrue(result)
        self.assertEqual(browser.driver.refresh_count, 1)
        self.assertEqual(activated, ["sales"])
        self.assertEqual(context["trace_miss_persistence_probe"], "offline")

    def test_ensure_target_sku_still_offline_uses_runtime_row(self) -> None:
        browser = SkuOfflineBrowser({}, PROJECT_ROOT)
        browser.driver = FakeSuccessDriver()
        sku_row = object()
        switch = object()
        browser._find_sku_rows_by_runtime_value = lambda context: [sku_row]  # type: ignore[method-assign]
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
        browser._find_sku_rows_by_runtime_value = lambda context: [row]  # type: ignore[method-assign]
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

        context = {"store_name": "阿里巴巴-常州工莱家具"}
        with self.assertRaises(OfflineLoginRequiredError):
            browser._assert_store_context(
                {"current_store_name": {"by": "css", "value": ".user-name"}},
                context,
                required=True,
            )

        self.assertEqual(context["page_error_category"], "login_required")
        self.assertEqual(context["page_error_stage"], "store_context_check")

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
