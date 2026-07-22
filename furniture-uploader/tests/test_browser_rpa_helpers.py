from __future__ import annotations

import base64
import sys
import tempfile
import unittest
from pathlib import Path

from selenium.common.exceptions import (
    ElementClickInterceptedException,
    StaleElementReferenceException,
    TimeoutException,
    WebDriverException,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
RPA_ROOT = PROJECT_ROOT / "rpa"
if str(RPA_ROOT) not in sys.path:
    sys.path.insert(0, str(RPA_ROOT))

from browser_rpa import BrowserRPA
from exceptions import PublishSubmitError, PublishValidationError


class FakeTextElement:
    def __init__(self, text: str) -> None:
        self.text = text


class FakeDriver:
    def __init__(self, *, current_url: str = "", page_source: str = "") -> None:
        self.current_url = current_url
        self.page_source = page_source

    def execute_script(self, script: str, *args: object) -> str:
        return ""

    def find_elements(self, by: object, value: object) -> list[FakeTextElement]:
        return []


class BrowserRPAHelperTests(unittest.TestCase):
    def setUp(self) -> None:
        self.browser = BrowserRPA({}, PROJECT_ROOT)

    def test_close_clears_driver_when_session_is_already_unavailable(self) -> None:
        class DeadDriver:
            def quit(self) -> None:
                raise RuntimeError("driver connection refused")

        self.browser.driver = DeadDriver()

        self.browser.close()

        self.assertIsNone(self.browser.driver)

    def test_build_tinymce_html_wraps_plain_text_lines(self) -> None:
        html_value = self.browser._build_tinymce_html("line one\nline two")
        self.assertEqual(html_value, "<p>line one</p><p>line two</p>")

    def test_build_tinymce_html_preserves_existing_html(self) -> None:
        html_value = self.browser._build_tinymce_html("<p>already rich</p>")
        self.assertEqual(html_value, "<p>already rich</p>")

    def test_build_tinymce_image_html_renders_img_tags(self) -> None:
        html_value = self.browser._build_tinymce_image_html(
            [
                "https://example.com/a.png",
                "https://example.com/b.png",
            ]
        )
        self.assertEqual(
            html_value,
            '<p><img src="https://example.com/a.png" /></p>'
            '<p><img src="https://example.com/b.png" /></p>',
        )

    def test_encode_file_as_data_url_uses_file_contents(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            image_path = Path(temp_dir) / "demo.png"
            image_path.write_bytes(b"png-bytes")

            data_url = self.browser._encode_file_as_data_url(str(image_path))

        self.assertTrue(data_url.startswith("data:image/png;base64,"))
        self.assertEqual(
            data_url.split(",", 1)[1],
            base64.b64encode(b"png-bytes").decode("ascii"),
        )

    def test_resolve_bridge_slot_index_defaults_to_zero(self) -> None:
        self.assertEqual(self.browser._resolve_bridge_slot_index({}), 0)
        self.assertEqual(self.browser._resolve_bridge_slot_index({"bridge_slot_index": "3"}), 3)
        self.assertEqual(self.browser._resolve_bridge_slot_index({"bridge_slot_index": "-2"}), 0)

    def test_resolve_category_levels_supports_list_and_path(self) -> None:
        self.assertEqual(
            self.browser._resolve_category_levels(
                {"source": "resolved_category_levels"},
                {"resolved_category_levels": ["家装建材", "客厅家具", "角几/边几"]},
            ),
            ["家装建材", "客厅家具", "角几/边几"],
        )
        self.assertEqual(
            self.browser._resolve_category_levels(
                {"source": "resolved_category_name"},
                {"resolved_category_name": "家装建材 > 客厅家具 > 角几/边几"},
            ),
            ["家装建材", "客厅家具", "角几/边几"],
        )

    def test_extract_value_supports_current_url_regex(self) -> None:
        self.browser.driver = FakeDriver(current_url="https://detail.1688.com/offer/1234567890123.html")
        context: dict[str, str] = {}

        self.browser._extract_value(
            {
                "name": "platform_link_id",
                "target": "platform_link_id",
                "action": "extract",
                "from": "current_url",
                "pattern": r"/offer/(\d+)",
                "group": 1,
            },
            context,
        )

        self.assertEqual(context["platform_link_id"], "1234567890123")

    def test_category_matches_current_page_without_delimiters(self) -> None:
        class CategoryDriver(FakeDriver):
            def execute_script(self, script: str, *args: object) -> str:
                return "您选择的类目：家装建材客厅家具角几/边几"

        self.browser.driver = CategoryDriver(current_url="https://offer-new.1688.com/popular/publish.htm")

        self.assertTrue(
            self.browser._category_matches_current_page(["家装建材", "客厅家具", "角几/边几"])
        )

    def test_is_preferred_new_publish_url(self) -> None:
        self.assertTrue(
            self.browser._is_preferred_new_publish_url(
                "https://offer-new.1688.com/popular/publish.htm?catId=201337003&operator=new"
            )
        )
        self.assertFalse(
            self.browser._is_preferred_new_publish_url(
                "https://offer-new.1688.com/popular/publish.htm?id=973480751525&operator=edit"
            )
        )

    def test_run_publish_steps_skips_optional_combobox_timeout(self) -> None:
        self.browser.driver = FakeDriver(current_url="https://offer-new.1688.com/popular/publish.htm")
        self.browser._fill_combobox = lambda selector, step, value: (_ for _ in ()).throw(TimeoutException())

        self.browser._run_publish_steps(
            [
                {
                    "name": "brand",
                    "action": "combobox",
                    "source": "brand",
                    "required": False,
                    "selector": {"by": "css", "value": "#missing-brand"},
                }
            ],
            {"brand": "SmokeBrand"},
        )

    def test_run_publish_steps_retries_stale_tinymce_images(self) -> None:
        self.browser.driver = FakeDriver(current_url="https://offer-new.1688.com/popular/publish.htm")
        attempts = {"count": 0}

        def flaky_insert(
            step: dict[str, object],
            selector: dict[str, str],
            values: list[str],
            context: dict[str, object],
        ) -> None:
            attempts["count"] += 1
            if attempts["count"] == 1:
                raise StaleElementReferenceException("stale element reference")

        self.browser._insert_tinymce_images = flaky_insert

        self.browser._run_publish_steps(
            [
                {
                    "name": "detail_images",
                    "action": "tinymce_images",
                    "source": "detail_images",
                    "required": False,
                    "stale_retry_count": 1,
                    "stale_retry_wait_seconds": 0,
                    "selector": {"by": "css", "value": "#tinyMCE-0"},
                }
            ],
            {"detail_images_list": ["https://example.com/detail-a.jpg"]},
        )

        self.assertEqual(attempts["count"], 2)

    def test_fill_text_field_handles_click_intercepted_and_retries_js_set(self) -> None:
        class InterceptedElement:
            def __init__(self) -> None:
                self._value = ""

            def get_attribute(self, name: str) -> str:
                if name == "value":
                    return self._value
                return ""

            def click(self) -> None:
                raise ElementClickInterceptedException("blocked by overlay")

            def send_keys(self, *args: object) -> None:
                return None

        class RetryJsDriver(FakeDriver):
            def __init__(self) -> None:
                super().__init__()
                self._write_attempt = 0

            def execute_script(self, script: str, *args: object) -> object:
                if len(args) >= 2:
                    self._write_attempt += 1
                    element = args[0]
                    next_value = str(args[1])
                    if self._write_attempt >= 2:
                        element._value = next_value
                return None

        driver = RetryJsDriver()
        element = InterceptedElement()
        self.browser.driver = driver
        self.browser._pause = lambda _seconds: None  # type: ignore[assignment]

        self.browser._fill_text_field(element, "999")

        self.assertEqual(element.get_attribute("value"), "999")

    def test_run_publish_steps_invokes_logistics_dimensions_action(self) -> None:
        self.browser.driver = FakeDriver(current_url="https://offer-new.1688.com/popular/publish.htm")
        invoked = {"count": 0}

        def fake_fill(step: dict[str, object], context: dict[str, object]) -> bool:
            invoked["count"] += 1
            return True

        self.browser._fill_logistics_dimensions = fake_fill  # type: ignore[assignment]
        self.browser._run_publish_steps(
            [
                {
                    "name": "logistics_dimensions",
                    "action": "logistics_dimensions",
                    "required": False,
                }
            ],
            {
                "length_cm": "120",
                "width_cm": "60",
                "height_cm": "75",
                "weight_g": "37600",
            },
        )

        self.assertEqual(invoked["count"], 1)

    def test_resolve_publish_mode_prefers_draft_then_submit(self) -> None:
        self.assertEqual(self.browser._resolve_publish_mode({"auto_save_draft": True}), "draft")
        self.assertEqual(self.browser._resolve_publish_mode({"auto_submit": True}), "submit")
        self.assertEqual(self.browser._resolve_publish_mode({}), "manual")

    def test_resolve_tinymce_editor_id_prefers_explicit_id(self) -> None:
        editor_id = self.browser._resolve_tinymce_editor_id(
            {"editor_id": "custom-editor"},
            {"by": "css", "value": "#tinyMCE-0"},
        )
        self.assertEqual(editor_id, "custom-editor")

    def test_resolve_tinymce_editor_id_falls_back_to_css_id(self) -> None:
        editor_id = self.browser._resolve_tinymce_editor_id(
            {},
            {"by": "css", "value": "#tinyMCE-0"},
        )
        self.assertEqual(editor_id, "tinyMCE-0")

    def test_resolve_value_falls_back_to_default_value(self) -> None:
        value = self.browser._resolve_value(
            {
                "source": "buyer_protection_ship_time",
                "default_value": "30天发货",
            },
            {},
        )
        self.assertEqual(value, "30天发货")

    def test_check_publish_error_state_raises_validation_error_for_required_field_message(self) -> None:
        class ValidationDriver(FakeDriver):
            def find_elements(self, by: object, value: object) -> list[FakeTextElement]:
                return [FakeTextElement("“加工方式”不能为空")]

        self.browser.driver = ValidationDriver()
        context: dict[str, str] = {}

        with self.assertRaises(PublishValidationError):
            self.browser._check_publish_error_state(
                {
                    "enabled": True,
                    "timeout_seconds": 0,
                    "validation_keywords": ["不能为空"],
                    "message_selector": {"by": "css", "value": ".error"},
                },
                context=context,
                stage_name="post_offline_submit",
                exception_cls=PublishSubmitError,
            )

        self.assertEqual(context["page_error_category"], "business_validation")
        self.assertEqual(context["page_error_stage"], "post_offline_submit")
        self.assertEqual(context["page_error_text"], "“加工方式”不能为空")


    def test_normalize_1688_ibank_image_url_returns_relative_path(self) -> None:
        self.assertEqual(
            self.browser._normalize_1688_ibank_image_url(
                "https://cbu01.alicdn.com/img/ibank/O1CN01demo_!!2220391183908-0-cib.jpg"
            ),
            "img/ibank/O1CN01demo_!!2220391183908-0-cib.jpg",
        )
        self.assertEqual(
            self.browser._normalize_1688_ibank_image_url("img/ibank/O1CN01demo_!!2220391183908-0-cib.jpg"),
            "img/ibank/O1CN01demo_!!2220391183908-0-cib.jpg",
        )
        self.assertEqual(self.browser._normalize_1688_ibank_image_url(""), "")

    def test_resolve_context_preferred_value_uses_context_before_default(self) -> None:
        self.assertEqual(
            self.browser._resolve_context_preferred_value(
                context={"buyer_protection_ship_time": "72小时发货"},
                source="buyer_protection_ship_time",
                default_value="48小时发货",
            ),
            "72小时发货",
        )
        self.assertEqual(
            self.browser._resolve_context_preferred_value(
                context={},
                source="buyer_protection_ship_time",
                default_value="48小时发货",
            ),
            "48小时发货",
        )

    def test_draft_logistics_dimension_values_reads_runtime_state(self) -> None:
        class LogisticsStateDriver(FakeDriver):
            def execute_script(self, script: str, *args: object) -> object:
                if "components || {}).officialLogistics" in script:
                    return {
                        "length": "120",
                        "width": "60",
                        "height": "75",
                        "weight": "37600",
                    }
                return {}

        self.browser.driver = LogisticsStateDriver()
        self.assertEqual(
            self.browser._draft_logistics_dimension_values(),
            {
                "length": "120",
                "width": "60",
                "height": "75",
                "weight": "37600",
            },
        )

    def test_draft_main_image_present_supports_sdk_state_fallback(self) -> None:
        class MainImageStateDriver(FakeDriver):
            def execute_script(self, script: str, *args: object) -> object:
                return {
                    "domPresent": False,
                    "statePresent": True,
                }

        self.browser.driver = MainImageStateDriver()
        self.assertTrue(self.browser._draft_main_image_present())

    def test_verify_saved_draft_accepts_buyer_protection_trace_fallback(self) -> None:
        self.browser.driver = FakeDriver(current_url="https://offer-new.1688.com/popular/publish.htm")
        self.browser._pause = lambda _seconds: None  # type: ignore[assignment]
        self.browser._collect_assist_messages = lambda: []  # type: ignore[assignment]
        self.browser._draft_main_image_present = lambda: True  # type: ignore[assignment]
        self.browser._draft_description_present = lambda: True  # type: ignore[assignment]
        self.browser._collect_spec_values = lambda: {}  # type: ignore[assignment]
        self.browser._draft_selected_send_address = lambda: "861873672"  # type: ignore[assignment]
        self.browser._draft_logistics_dimension_values = lambda: {  # type: ignore[assignment]
            "length": "120",
            "width": "60",
            "height": "75",
            "weight": "37600",
        }
        self.browser._draft_selected_buyer_protection = lambda: ""  # type: ignore[assignment]
        self.browser._draft_selected_buyer_protection_schedule = lambda: []  # type: ignore[assignment]

        context: dict[str, object] = {
            "length_cm": "120",
            "width_cm": "60",
            "height_cm": "75",
            "weight_g": "37600",
            "draft_page_state_patch": {
                "buyerProtectionDesiredSteps": [
                    {"from": 1, "serviceName": "当日发", "value": "drfh"},
                ]
            },
            "draft_submit_trace": {
                "patch": {
                    "patchSnapshot": {
                        "buyerProtectionSteps": [
                            {"from": 1, "serviceName": "当日发", "value": "drfh"},
                        ],
                        "buyerProtectionServiceName": "当日发",
                    }
                }
            },
        }
        publish_config = {
            "draft_verification": {
                "enabled": True,
                "refresh_after_save": False,
                "require_main_image": True,
                "require_description": True,
                "require_specs": False,
                "require_send_address": True,
                "require_logistics_dimensions": True,
                "required_logistics_fields": [
                    {"name": "length", "source": "length_cm"},
                    {"name": "width", "source": "width_cm"},
                    {"name": "height", "source": "height_cm"},
                    {"name": "weight", "source": "weight_g"},
                ],
                "require_buyer_protection": True,
                "strict_buyer_protection_persist": True,
                "buyer_protection_default_value": "当日发",
            }
        }

        self.browser._verify_saved_draft(publish_config, context)
        self.assertEqual(context.get("draft_buyer_protection_schedule_source"), "draft_submit_trace")
        self.assertEqual(context.get("draft_buyer_protection_value_source"), "draft_submit_trace")

    def test_verify_saved_draft_accepts_send_address_trace_fallback(self) -> None:
        self.browser.driver = FakeDriver(current_url="https://offer-new.1688.com/popular/publish.htm")
        self.browser._pause = lambda _seconds: None  # type: ignore[assignment]
        self.browser._collect_assist_messages = lambda: []  # type: ignore[assignment]
        self.browser._draft_main_image_present = lambda: True  # type: ignore[assignment]
        self.browser._draft_description_present = lambda: True  # type: ignore[assignment]
        self.browser._collect_spec_values = lambda: {}  # type: ignore[assignment]
        self.browser._draft_selected_send_address = lambda: ""  # type: ignore[assignment]
        self.browser._draft_logistics_dimension_values = lambda: {  # type: ignore[assignment]
            "length": "120",
            "width": "60",
            "height": "75",
            "weight": "37600",
        }
        self.browser._draft_selected_buyer_protection = lambda: ""  # type: ignore[assignment]
        self.browser._draft_selected_buyer_protection_schedule = lambda: []  # type: ignore[assignment]

        context: dict[str, object] = {
            "length_cm": "120",
            "width_cm": "60",
            "height_cm": "75",
            "weight_g": "37600",
            "draft_submit_trace": {
                "patch": {
                    "patchSnapshot": {
                        "sendAddressId": 861873672,
                    }
                }
            },
        }
        publish_config = {
            "draft_verification": {
                "enabled": True,
                "refresh_after_save": False,
                "require_main_image": True,
                "require_description": True,
                "require_specs": False,
                "require_send_address": True,
                "require_logistics_dimensions": True,
                "required_logistics_fields": [
                    {"name": "length", "source": "length_cm"},
                    {"name": "width", "source": "width_cm"},
                    {"name": "height", "source": "height_cm"},
                    {"name": "weight", "source": "weight_g"},
                ],
                "require_buyer_protection": False,
            }
        }

        self.browser._verify_saved_draft(publish_config, context)
        self.assertEqual(context.get("draft_send_address_value"), "861873672")
        self.assertEqual(context.get("draft_send_address_source"), "draft_submit_trace")

    def test_verify_saved_draft_recovers_after_buyer_protection_grace_refresh(self) -> None:
        class GraceRefreshDriver(FakeDriver):
            def __init__(self) -> None:
                super().__init__(current_url="https://offer-new.1688.com/popular/publish.htm")
                self.refresh_count = 0

            def refresh(self) -> None:
                self.refresh_count += 1

        driver = GraceRefreshDriver()
        self.browser.driver = driver
        self.browser._pause = lambda _seconds: None  # type: ignore[assignment]

        assist_rounds = [
            ["买家保障 第1行的发货时间为必填项"],
            [],
        ]

        def collect_assist_messages() -> list[str]:
            return assist_rounds.pop(0) if assist_rounds else []

        self.browser._collect_assist_messages = collect_assist_messages  # type: ignore[assignment]
        self.browser._draft_main_image_present = lambda: True  # type: ignore[assignment]
        self.browser._draft_description_present = lambda: True  # type: ignore[assignment]
        self.browser._collect_spec_values = lambda: {}  # type: ignore[assignment]
        self.browser._draft_selected_send_address = lambda: "861873672"  # type: ignore[assignment]
        self.browser._draft_logistics_dimension_values = lambda: {}  # type: ignore[assignment]
        self.browser._draft_selected_buyer_protection = lambda: "当日发"  # type: ignore[assignment]
        self.browser._draft_selected_buyer_protection_schedule = lambda: [  # type: ignore[assignment]
            {"from": 1, "serviceCode": "drfh", "serviceName": "当日发"}
        ]

        context: dict[str, object] = {}
        publish_config = {
            "draft_verification": {
                "enabled": True,
                "refresh_after_save": True,
                "refresh_wait_seconds": 0,
                "require_main_image": True,
                "require_description": True,
                "require_specs": False,
                "require_send_address": False,
                "require_logistics_dimensions": False,
                "require_buyer_protection": False,
                "forbidden_assist_keywords": ["发货时间为必填项"],
                "buyer_protection_warning_grace_refresh_count": 1,
                "buyer_protection_warning_grace_wait_seconds": 0,
                "buyer_protection_warning_grace_refresh_wait_seconds": 0,
            }
        }

        self.browser._verify_saved_draft(publish_config, context)

        self.assertEqual(driver.refresh_count, 2)
        self.assertEqual(context.get("draft_buyer_protection_grace_recheck_count"), 1)
        self.assertTrue(context.get("draft_buyer_protection_grace_recovered"))

    def test_should_retry_draft_submit_for_transient_backend_error(self) -> None:
        self.assertTrue(
            self.browser._should_retry_draft_submit(
                PublishSubmitError("draft_submit backend rejected request: 系统错误，请稍后尝试")
            )
        )
        self.assertFalse(
            self.browser._should_retry_draft_submit(
                PublishSubmitError("draft_submit backend rejected request: 数据格式不合法")
            )
        )

    def test_resolve_draft_request_patch_modes_defaults_to_full_then_capture_only(self) -> None:
        modes = self.browser._resolve_draft_request_patch_modes(
            {
                "draft_request_patch": {
                    "enabled": True,
                }
            }
        )
        self.assertEqual(modes, ["full", "capture_only"])

    def test_resolve_next_draft_request_patch_mode_steps_forward(self) -> None:
        publish_config = {
            "draft_request_patch": {"enabled": True},
            "draft_request_patch_retry_modes": ["full", "capture_only"],
        }
        self.assertEqual(
            self.browser._resolve_next_draft_request_patch_mode(
                publish_config,
                {"draft_request_patch_mode": "full"},
            ),
            "capture_only",
        )
        self.assertEqual(
            self.browser._resolve_next_draft_request_patch_mode(
                publish_config,
                {"draft_request_patch_mode": "capture_only"},
            ),
            "",
        )

    def test_is_draft_submit_backend_reject_error(self) -> None:
        self.assertTrue(
            self.browser._is_draft_submit_backend_reject_error(
                PublishSubmitError("draft_submit backend rejected request: 系统错误，请稍后尝试")
            )
        )
        self.assertFalse(
            self.browser._is_draft_submit_backend_reject_error(
                PublishSubmitError("Draft button was clicked but no draftSubmit request was captured.")
            )
        )

    def test_install_draft_request_patch_respects_capture_only_mode(self) -> None:
        class PatchDriver(FakeDriver):
            def __init__(self) -> None:
                super().__init__()
                self.last_args: tuple[object, ...] = ()

            def execute_script(self, script: str, *args: object) -> object:
                self.last_args = args
                return None

        driver = PatchDriver()
        self.browser.driver = driver
        self.browser._install_draft_request_patch(
            {
                "draft_request_patch": {
                    "enabled": True,
                    "capture_body_chars": 1200,
                }
            },
            {
                "draft_request_patch_mode": "capture_only",
                "title": "测试标题",
            },
        )

        payload = driver.last_args[0]
        self.assertIsInstance(payload, dict)
        self.assertEqual(payload.get("patchMode"), "capture_only")
        self.assertEqual(payload.get("applyPatch"), False)


    def test_build_draft_page_state_patch_payload_resolves_cat_props_and_buyer_protection(self) -> None:
        payload = self.browser._build_draft_page_state_patch_payload(
            {
                "draft_page_state_patch": {
                    "enabled": True,
                    "buyer_protection_source": "buyer_protection_ship_time",
                    "buyer_protection_default_value": "48灏忔椂鍙戣揣",
                    "cat_props": [
                        {
                            "label": "材质",
                            "source": "material",
                            "default_value": "木",
                        },
                        {
                            "label": "台面材质",
                            "source": "table_material",
                            "default_value": "人造板",
                        },
                    ],
                }
            },
            {
                "material": "人造板",
                "buyer_protection_ship_time": "72灏忔椂鍙戣揣",
            },
        )

        self.assertEqual(
            payload,
            {
                "catPropPatches": [
                    {
                        "label": "材质",
                        "value": "人造板",
                    },
                    {
                        "label": "台面材质",
                        "value": "人造板",
                    },
                ],
                "buyerProtectionServiceName": "72灏忔椂鍙戣揣",
                "buyerProtectionServiceCode": "",
                "buyerProtectionStepTemplate": [],
                "deliveryServiceIds": [],
                "includeBuyerProtectionSpsCode": False,
            },
        )

    def test_build_buyer_protection_step_template_resolves_and_sorts_steps(self) -> None:
        payload = self.browser._build_buyer_protection_step_template(
            [
                {
                    "from": 3,
                    "service_name": "45天发货",
                },
                {
                    "from": 1,
                    "end": 2,
                    "service_name": "30天发货",
                },
            ],
            {},
        )

        self.assertEqual(
            payload,
            [
                {
                    "from": 1,
                    "end": 2,
                    "serviceName": "30天发货",
                },
                {
                    "from": 3,
                    "serviceName": "45天发货",
                },
            ],
        )

    """
    def test_build_buyer_protection_step_template_supports_service_code(self) -> None:
        payload = self.browser._build_buyer_protection_step_template(
            [
                {
                    "from": 1,
                    "end": 2,
                    "service_name": "30澶╁彂璐?,
                    "service_code": "sstfh",
                },
                {
                    "from": 3,
                    "service_code": "sswtfh",
                },
            ],
            {},
        )

        self.assertEqual(
            payload,
            [
                {
                    "from": 1,
                    "end": 2,
                    "serviceName": "30澶╁彂璐?,
                    "serviceCode": "sstfh",
                },
                {
                    "from": 3,
                    "serviceCode": "sswtfh",
                },
            ],
        )

    """

    def test_build_buyer_protection_step_template_supports_service_code(self) -> None:
        payload = self.browser._build_buyer_protection_step_template(
            [
                {
                    "from": 1,
                    "end": 2,
                    "service_name": "service_30d",
                    "service_code": "sstfh",
                },
                {
                    "from": 3,
                    "service_code": "sswtfh",
                },
            ],
            {},
        )

        self.assertEqual(
            payload,
            [
                {
                    "from": 1,
                    "end": 2,
                    "serviceName": "service_30d",
                    "serviceCode": "sstfh",
                },
                {
                    "from": 3,
                    "serviceCode": "sswtfh",
                },
            ],
        )

    def test_build_draft_page_state_patch_payload_includes_buyer_protection_step_template(self) -> None:
        payload = self.browser._build_draft_page_state_patch_payload(
            {
                "draft_page_state_patch": {
                    "enabled": True,
                    "buyer_protection_default_value": "30天发货",
                    "buyer_protection_step_template": [
                        {
                            "from": 1,
                            "end": 2,
                            "service_name": "30天发货",
                        },
                        {
                            "from": 3,
                            "service_name": "45天发货",
                        },
                    ],
                }
            },
            {},
        )

        self.assertEqual(
            payload,
            {
                "catPropPatches": [],
                "buyerProtectionServiceName": "30天发货",
                "buyerProtectionServiceCode": "",
                "buyerProtectionStepTemplate": [
                    {
                        "from": 1,
                        "end": 2,
                        "serviceName": "30天发货",
                    },
                    {
                        "from": 3,
                        "serviceName": "45天发货",
                    },
                ],
                "deliveryServiceIds": [],
                "includeBuyerProtectionSpsCode": False,
            },
        )

    def test_build_draft_page_state_patch_payload_includes_delivery_service_ids(self) -> None:
        payload = self.browser._build_draft_page_state_patch_payload(
            {
                "draft_page_state_patch": {
                    "enabled": True,
                    "delivery_service_default_ids": [3385307],
                    "delivery_service_label_id_map": {
                        "市区物流点自提": 3385307,
                    },
                }
            },
            {
                "draft_delivery_service_state": {
                    "selectedLabels": ["市区物流点自提"],
                }
            },
        )

        self.assertEqual(
            payload,
            {
                "catPropPatches": [],
                "buyerProtectionServiceName": "",
                "buyerProtectionServiceCode": "",
                "buyerProtectionStepTemplate": [],
                "deliveryServiceIds": [3385307],
                "includeBuyerProtectionSpsCode": False,
            },
        )

    def test_resolve_delivery_service_ids_filters_checkbox_on_value(self) -> None:
        resolved = self.browser._resolve_delivery_service_ids(
            patch_config={"delivery_service_default_ids": [3385307]},
            context={
                "draft_delivery_service_state": {
                    "selectedServiceIds": ["on", "3385307"],
                }
            },
        )
        self.assertEqual(resolved, [3385307])

    def test_build_draft_page_state_patch_payload_returns_empty_when_disabled(self) -> None:
        self.assertEqual(
            self.browser._build_draft_page_state_patch_payload(
                {"draft_page_state_patch": {"enabled": False}},
                {"material": "人造板"},
            ),
            {},
        )

    def test_run_with_stale_retry_accepts_webdriver_stale_message(self) -> None:
        attempts = {"count": 0}

        def flaky_callback() -> str:
            attempts["count"] += 1
            if attempts["count"] == 1:
                raise WebDriverException("stale element reference: stale element not found in the current frame")
            return "ok"

        result = self.browser._run_with_stale_retry(flaky_callback, retries=1, wait_seconds=0)
        self.assertEqual(result, "ok")
        self.assertEqual(attempts["count"], 2)

    def test_run_with_stale_retry_does_not_swallow_non_stale_webdriver_error(self) -> None:
        def broken_callback() -> str:
            raise WebDriverException("invalid session id")

        with self.assertRaises(WebDriverException):
            self.browser._run_with_stale_retry(broken_callback, retries=2, wait_seconds=0)

    def test_apply_category_prop_rule_skips_missing_optional_field(self) -> None:
        self.browser._wait_for_category_prop_container = lambda label: (_ for _ in ()).throw(TimeoutException())
        self.browser._apply_category_prop_rule(
            {
                "label": "品牌",
            },
            {},
        )

    def test_apply_category_prop_rule_raises_when_required_field_missing(self) -> None:
        self.browser._wait_for_category_prop_container = lambda label: (_ for _ in ()).throw(TimeoutException())
        with self.assertRaises(TimeoutException):
            self.browser._apply_category_prop_rule(
                {
                    "label": "品牌",
                    "required": True,
                },
                {},
            )

    def test_apply_spec_rule_skips_missing_optional_input(self) -> None:
        self.browser._wait_for_spec_input = lambda label: (_ for _ in ()).throw(TimeoutException())
        self.browser._apply_spec_rule(
            {
                "label": "颜色",
                "value": "白色",
            },
            {},
        )

    def test_apply_spec_rule_raises_when_required_input_missing(self) -> None:
        self.browser._wait_for_spec_input = lambda label: (_ for _ in ()).throw(TimeoutException())
        with self.assertRaises(TimeoutException):
            self.browser._apply_spec_rule(
                {
                    "label": "颜色",
                    "value": "白色",
                    "required": True,
                },
                {},
            )

    def test_assert_draft_request_trace_returns_false_when_no_record(self) -> None:
        class TraceDriver(FakeDriver):
            def execute_script(self, script: str, *args: object) -> object:
                if "window.__codexDraftSubmitRecords" in script:
                    return []
                return None

        self.browser.driver = TraceDriver()
        context: dict[str, object] = {}
        detected = self.browser._assert_draft_request_trace(
            {
                "draft_request_patch": {
                    "enabled": True,
                    "timeout_seconds": 0,
                }
            },
            context,
        )
        self.assertFalse(detected)
        self.assertEqual(context["draft_submit_trace_present"], False)

    def test_assert_draft_request_trace_raises_for_http_error(self) -> None:
        class TraceDriver(FakeDriver):
            def execute_script(self, script: str, *args: object) -> object:
                if "window.__codexDraftSubmitRecords" in script:
                    return [
                        {
                            "status": 500,
                            "responseText": "internal error",
                            "responseJson": None,
                        }
                    ]
                return None

        self.browser.driver = TraceDriver()
        with self.assertRaises(PublishSubmitError):
            self.browser._assert_draft_request_trace(
                {
                    "draft_request_patch": {
                        "enabled": True,
                        "timeout_seconds": 0,
                    }
                },
                {},
            )

    def test_assert_draft_request_trace_returns_true_for_success(self) -> None:
        class TraceDriver(FakeDriver):
            def execute_script(self, script: str, *args: object) -> object:
                if "window.__codexDraftSubmitRecords" in script:
                    return [
                        {
                            "status": 200,
                            "responseText": "{\"success\":true}",
                            "responseJson": {"success": True},
                        }
                    ]
                return None

        self.browser.driver = TraceDriver()
        detected = self.browser._assert_draft_request_trace(
            {
                "draft_request_patch": {
                    "enabled": True,
                    "timeout_seconds": 0,
                }
            },
            {},
        )
        self.assertTrue(detected)

    def test_assert_submit_request_trace_returns_false_when_no_record(self) -> None:
        class TraceDriver(FakeDriver):
            def execute_script(self, script: str, *args: object) -> object:
                if "window.__codexPublishSubmitRecords" in script:
                    return []
                return None

        self.browser.driver = TraceDriver()
        context: dict[str, object] = {}
        detected = self.browser._assert_submit_request_trace(
            {
                "submit_request_trace": {
                    "enabled": True,
                    "timeout_seconds": 0,
                }
            },
            context,
        )
        self.assertFalse(detected)
        self.assertEqual(context["submit_request_trace_present"], False)

    def test_assert_submit_request_trace_raises_for_http_error(self) -> None:
        class TraceDriver(FakeDriver):
            def execute_script(self, script: str, *args: object) -> object:
                if "window.__codexPublishSubmitRecords" in script:
                    return [
                        {
                            "status": 500,
                            "responseText": "internal error",
                            "responseJson": None,
                        }
                    ]
                return None

        self.browser.driver = TraceDriver()
        with self.assertRaises(PublishSubmitError):
            self.browser._assert_submit_request_trace(
                {
                    "submit_request_trace": {
                        "enabled": True,
                        "timeout_seconds": 0,
                    }
                },
                {},
            )

    def test_assert_submit_request_trace_returns_true_for_success(self) -> None:
        class TraceDriver(FakeDriver):
            def execute_script(self, script: str, *args: object) -> object:
                if "window.__codexPublishSubmitRecords" in script:
                    return [
                        {
                            "status": 200,
                            "responseText": "{\"success\":true}",
                            "responseJson": {"success": True},
                        }
                    ]
                return None

        self.browser.driver = TraceDriver()
        detected = self.browser._assert_submit_request_trace(
            {
                "submit_request_trace": {
                    "enabled": True,
                    "timeout_seconds": 0,
                }
            },
            {},
        )
        self.assertTrue(detected)

    def test_extract_required_labels_from_assist_messages(self) -> None:
        labels = self.browser._extract_required_labels_from_assist_messages(
            [
                "买家保障 第1行的发货时间为必填项",
                "特色服务 配送服务为必填项",
                "请填写图文详情",
                "请完善台面材质",
            ]
        )
        self.assertEqual(labels, ["发货时间", "配送服务", "图文详情", "台面材质"])

    def test_build_draft_page_state_patch_payload_adds_required_cat_prop_labels(self) -> None:
        payload = self.browser._build_draft_page_state_patch_payload(
            {
                "steps": [
                    {
                        "name": "category_defaults",
                        "action": "category_prop_defaults",
                        "profiles": {
                            "*": {
                                "rules": [
                                    {
                                        "label": "台面材质",
                                        "value": "人造板",
                                    }
                                ]
                            }
                        },
                    }
                ],
                "draft_page_state_patch": {
                    "enabled": True,
                    "cat_props": [
                        {
                            "label": "材质",
                            "default_value": "白蜡木",
                        }
                    ],
                },
            },
            {
                "draft_required_field_labels": ["台面材质", "配送服务", "图文详情"],
            },
        )

        self.assertEqual(payload["buyerProtectionServiceName"], "")
        self.assertEqual(payload["buyerProtectionServiceCode"], "")
        self.assertEqual(payload["buyerProtectionStepTemplate"], [])
        self.assertEqual(payload["deliveryServiceIds"], [])
        self.assertEqual(payload["includeBuyerProtectionSpsCode"], False)
        self.assertEqual(
            payload["catPropPatches"],
            [
                {"label": "材质", "value": "白蜡木"},
                {"label": "台面材质", "value": "人造板"},
            ],
        )


if __name__ == "__main__":
    unittest.main()
