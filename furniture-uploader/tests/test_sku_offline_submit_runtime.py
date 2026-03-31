from __future__ import annotations

import sys
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
RPA_ROOT = PROJECT_ROOT / "rpa"
if str(RPA_ROOT) not in sys.path:
    sys.path.insert(0, str(RPA_ROOT))

from exceptions import PublishSubmitError, PublishValidationError
from sku_offline_browser import SkuOfflineBrowser


class FakeDriver:
    def __init__(self) -> None:
        self.records: list[dict[str, object]] = []
        self.runtime_patch_result: object = {}
        self.last_script_call: tuple[str, tuple[object, ...]] | None = None

    def execute_script(self, script: str, *args: object) -> object:
        self.last_script_call = (script, args)
        if "__codexOfflineSubmitRecords" in script:
            return list(self.records)
        if "targetSku" in script and "skuTable" in script:
            return self.runtime_patch_result
        return None


class SkuOfflineSubmitRuntimeTests(unittest.TestCase):
    def test_assert_offline_submit_trace_sets_absent_when_no_record(self) -> None:
        browser = SkuOfflineBrowser({}, PROJECT_ROOT)
        browser.driver = FakeDriver()
        context: dict[str, object] = {}

        browser._assert_offline_submit_trace(context)
        self.assertEqual(context["submit_request_trace_present"], False)

    def test_assert_offline_submit_trace_raises_when_backend_rejects(self) -> None:
        browser = SkuOfflineBrowser({}, PROJECT_ROOT)
        driver = FakeDriver()
        driver.records = [
            {
                "transport": "fetch",
                "method": "POST",
                "url": "https://offer-new.1688.com/popular/submit.htm",
                "status": 200,
                "responseJson": {"success": False, "message": "invalid field"},
                "responseText": '{"success":false,"message":"invalid field"}',
            }
        ]
        browser.driver = driver
        context: dict[str, object] = {}

        with self.assertRaises(PublishSubmitError):
            browser._assert_offline_submit_trace(context)
        self.assertEqual(context["submit_request_trace_present"], True)

    def test_assert_offline_submit_trace_raises_when_http_error(self) -> None:
        browser = SkuOfflineBrowser({}, PROJECT_ROOT)
        driver = FakeDriver()
        driver.records = [
            {
                "transport": "xhr",
                "method": "POST",
                "url": "https://offer-new.1688.com/popular/draftSubmit.htm",
                "status": 500,
                "responseText": "server error",
                "responseJson": None,
            }
        ]
        browser.driver = driver
        context: dict[str, object] = {}

        with self.assertRaises(PublishSubmitError):
            browser._assert_offline_submit_trace(context)
        self.assertEqual(context["submit_request_trace_present"], True)

    def test_assert_offline_submit_trace_raises_validation_on_backend_code_error(self) -> None:
        browser = SkuOfflineBrowser({}, PROJECT_ROOT)
        driver = FakeDriver()
        driver.records = [
            {
                "transport": "xhr",
                "method": "POST",
                "url": "https://offer-new.1688.com/popular/submit.htm",
                "status": 200,
                "responseJson": {
                    "code": 500,
                    "message": "CHK_BASIC_ONEOF",
                    "data": {
                        "data": {
                            "models": {
                                "formError": {
                                    "catProp": {
                                        "itemMessage": {
                                            "p-1141": {
                                                "message": [
                                                    {"msg": "输入值不在预期范围之内"},
                                                ]
                                            }
                                        }
                                    }
                                }
                            }
                        }
                    },
                },
                "responseText": '{"code":500,"message":"CHK_BASIC_ONEOF"}',
            }
        ]
        browser.driver = driver
        context: dict[str, object] = {}

        with self.assertRaises(PublishValidationError):
            browser._assert_offline_submit_trace(context)
        self.assertEqual(context["submit_request_trace_present"], True)
        self.assertEqual(context["submit_backend_error_code"], "500")
        self.assertEqual(context["submit_backend_invalid_props"], ["p-1141"])
        self.assertEqual(context["page_error_category"], "business_validation")

    def test_extract_submit_backend_error_message_includes_prop_detail(self) -> None:
        browser = SkuOfflineBrowser({}, PROJECT_ROOT)
        message = browser._extract_submit_backend_error_message(
            {
                "code": 500,
                "message": "CHK_BASIC_ONEOF",
                "data": {
                    "data": {
                        "models": {
                            "formError": {
                                "catProp": {
                                    "itemMessage": {
                                        "p-1141": {
                                            "message": [
                                                {"msg": "输入值不在预期范围之内"},
                                            ]
                                        }
                                    }
                                }
                            }
                        }
                    }
                },
            }
        )
        self.assertIn("CHK_BASIC_ONEOF", message)
        self.assertIn("p-1141", message)
        self.assertIn("输入值不在预期范围之内", message)

    def test_install_offline_submit_trace_uses_large_capture_defaults(self) -> None:
        browser = SkuOfflineBrowser({}, PROJECT_ROOT)
        driver = FakeDriver()
        browser.driver = driver

        browser._install_offline_submit_trace()

        self.assertIsNotNone(driver.last_script_call)
        _, args = driver.last_script_call or ("", ())
        self.assertEqual(args, (300000, 12000))

    def test_install_offline_submit_trace_uses_configured_capture_limits(self) -> None:
        browser = SkuOfflineBrowser(
            {
                "offline_submit_trace_capture_body_chars": 64000,
                "offline_submit_trace_capture_response_chars": 15000,
            },
            PROJECT_ROOT,
        )
        driver = FakeDriver()
        browser.driver = driver

        browser._install_offline_submit_trace()

        self.assertIsNotNone(driver.last_script_call)
        _, args = driver.last_script_call or ("", ())
        self.assertEqual(args, (64000, 15000))

    def test_force_target_sku_offline_in_runtime_state_returns_guard_result(self) -> None:
        browser = SkuOfflineBrowser({}, PROJECT_ROOT)
        driver = FakeDriver()
        driver.runtime_patch_result = "unexpected"
        browser.driver = driver
        context = {"online_sku": "DNZ023901N1221V01"}

        result = browser._force_target_sku_offline_in_runtime_state(context)
        self.assertEqual(result["supported"], False)
        self.assertEqual(result["reason"], "invalid_runtime_result")

    def test_extract_inline_required_labels_reads_quoted_label(self) -> None:
        browser = SkuOfflineBrowser({}, PROJECT_ROOT)
        labels = browser._extract_inline_required_labels(
            ['“适用空间”不能为空', '“材质”不能为空']
        )
        self.assertEqual(labels, ["适用空间", "材质"])

    def test_try_recover_inline_required_category_props_applies_first_option_rule(self) -> None:
        class RecoveryBrowser(SkuOfflineBrowser):
            def __init__(self) -> None:
                super().__init__({}, PROJECT_ROOT)
                self.applied: list[str] = []

            def _read_category_prop_state(self, label: str) -> dict[str, object]:
                return {"label": label, "exists": True, "current_value": ""}

            def _apply_category_prop_rule(self, rule: dict[str, object], context: dict[str, object]) -> None:
                self.applied.append(str(rule.get("label", "")))

            def _pause(self, seconds: float) -> None:
                return

            def _patch_category_prop_with_first_option(self, label: str) -> dict[str, object]:
                return {"ok": False, "label": label}

        browser = RecoveryBrowser()
        context: dict[str, object] = {}
        recovered = browser._try_recover_inline_required_category_props(["“适用空间”不能为空"], context)
        self.assertTrue(recovered)
        self.assertEqual(browser.applied, ["适用空间"])
        self.assertEqual(context["inline_validation_recovery_applied"], ["适用空间"])


if __name__ == "__main__":
    unittest.main()
