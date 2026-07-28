from __future__ import annotations

import base64
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from selenium.common.exceptions import (
    ElementClickInterceptedException,
    StaleElementReferenceException,
    TimeoutException,
    WebDriverException,
)
from selenium.webdriver.common.keys import Keys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
RPA_ROOT = PROJECT_ROOT / "rpa"
if str(RPA_ROOT) not in sys.path:
    sys.path.insert(0, str(RPA_ROOT))

from browser_rpa import BrowserRPA
import browser_rpa as browser_rpa_module
from exceptions import ImageAlbumFullError, PublishSubmitError, PublishValidationError


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

    def test_build_picker_album_name_uses_prefix(self) -> None:
        album_name = self.browser._build_picker_album_name({"auto_album_name_prefix": "DETAIL"})
        self.assertTrue(album_name.startswith("DETAIL_"))
        self.assertLessEqual(len(album_name), 20)

    def test_switch_to_existing_business_page_skips_edge_ntp(self) -> None:
        class SwitchTarget:
            def __init__(self, driver: "BusinessPageDriver") -> None:
                self.driver = driver

            def window(self, handle: str) -> None:
                self.driver.active_handle = handle
                self.driver.current_url = self.driver.urls[handle]

        class BusinessPageDriver(FakeDriver):
            def __init__(self) -> None:
                super().__init__()
                self.window_handles = ["ntp", "business"]
                self.urls = {
                    "ntp": "https://ntp.msn.cn/edge/ntp",
                    "business": "https://offer.1688.com/app/pages-group/manage-home/index.html",
                }
                self.active_handle = ""
                self.switch_to = SwitchTarget(self)

        driver = BusinessPageDriver()
        self.browser.driver = driver

        self.assertTrue(self.browser._switch_to_existing_business_page())
        self.assertEqual(driver.active_handle, "business")

    def test_switch_to_existing_business_page_prefers_publish_draft(self) -> None:
        class SwitchTarget:
            def __init__(self, driver: "BusinessPageDriver") -> None:
                self.driver = driver

            def window(self, handle: str) -> None:
                self.driver.active_handle = handle
                self.driver.current_url = self.driver.urls[handle]

        class BusinessPageDriver(FakeDriver):
            def __init__(self) -> None:
                super().__init__()
                self.window_handles = ["management", "draft"]
                self.urls = {
                    "management": "https://work.1688.com/?tab=all",
                    "draft": "https://offer-new.1688.com/popular/publish.htm?draftId=draft-1",
                }
                self.active_handle = ""
                self.switch_to = SwitchTarget(self)

        driver = BusinessPageDriver()
        self.browser.driver = driver

        self.assertTrue(self.browser._switch_to_existing_business_page())
        self.assertEqual(driver.active_handle, "draft")

    def test_prune_duplicate_automation_tabs_keeps_publish_and_all_management(self) -> None:
        class SwitchTarget:
            def __init__(self, driver: "MultiTabDriver") -> None:
                self.driver = driver

            def window(self, handle: str) -> None:
                self.driver.current_window_handle = handle
                self.driver.current_url = self.driver.urls[handle]

        class MultiTabDriver(FakeDriver):
            def __init__(self) -> None:
                super().__init__()
                self.window_handles = [
                    "draft-current",
                    "draft-old",
                    "category",
                    "management-selling",
                    "management-all",
                    "unrelated",
                ]
                self.urls = {
                    "draft-current": "https://offer-new.1688.com/popular/publish.htm?draftId=current",
                    "draft-old": "https://offer-new.1688.com/popular/publish.htm?draftId=old",
                    "category": "https://offer-new.1688.com/select.htm",
                    "management-selling": (
                        "https://work.1688.com/?_path_=sellerPro/2017sellerbase_offer/"
                        "shasngpinguanlinew&tab=onsale"
                    ),
                    "management-all": (
                        "https://work.1688.com/?_path_=sellerPro/2017sellerbase_offer/"
                        "shasngpinguanlinew&tab=all&q=&filterOfferId="
                    ),
                    "unrelated": "https://example.com/keep-me",
                }
                self.current_window_handle = "draft-current"
                self.current_url = self.urls[self.current_window_handle]
                self.switch_to = SwitchTarget(self)

            def close(self) -> None:
                handle = self.current_window_handle
                self.window_handles.remove(handle)
                self.urls.pop(handle, None)

        driver = MultiTabDriver()
        self.browser.driver = driver

        closed_count = self.browser._prune_duplicate_automation_tabs()

        self.assertEqual(closed_count, 3)
        self.assertEqual(
            driver.window_handles,
            ["draft-current", "management-all", "unrelated"],
        )
        self.assertEqual(driver.current_window_handle, "draft-current")

        class ManagementFirstBrowser(BrowserRPA):
            def _business_page_priority(self, current_url: str) -> int:
                if "shasngpinguanlinew" in current_url:
                    return 400
                return super()._business_page_priority(current_url)

        management_driver = MultiTabDriver()
        management_driver.switch_to.window("management-selling")
        management_browser = ManagementFirstBrowser({}, PROJECT_ROOT)
        management_browser.driver = management_driver

        management_browser._prune_duplicate_automation_tabs()

        self.assertEqual(management_driver.current_window_handle, "management-all")

    def test_expected_publish_category_accepts_matching_runtime_category(self) -> None:
        class CategoryDriver(FakeDriver):
            def execute_script(self, script: str, *args: object) -> dict[str, object]:
                return {
                    "category_id": "122942001",
                    "category_path": ["家装建材", "卧室家具", "床头柜"],
                }

        self.browser.driver = CategoryDriver()
        context: dict[str, object] = {}

        self.browser._assert_expected_publish_category(
            {
                "expected_category_id": "122942001",
                "expected_category_path": "家装建材 > 卧室家具 > 床头柜",
            },
            context,
        )

        self.assertEqual(context["actual_category_id"], "122942001")
        self.assertEqual(context["actual_category_path"], ["家装建材", "卧室家具", "床头柜"])

    def test_expected_publish_category_rejects_mismatched_runtime_category(self) -> None:
        class CategoryDriver(FakeDriver):
            def execute_script(self, script: str, *args: object) -> dict[str, object]:
                return {
                    "category_id": "123620022",
                    "category_path": ["家装建材", "书房家具", "书柜"],
                }

        self.browser.driver = CategoryDriver()

        with self.assertRaisesRegex(PublishValidationError, "publish category mismatch"):
            self.browser._assert_expected_publish_category(
                {
                    "expected_category_id": "122942001",
                    "expected_category_path": "家装建材 > 卧室家具 > 床头柜",
                },
                {},
            )

    def test_create_picker_album_fills_name_and_submits(self) -> None:
        class FakePickerElement:
            def __init__(self, on_click=None, *, selected: bool = False) -> None:
                self.clicked = 0
                self.value = ""
                self.on_click = on_click
                self.selected = selected

            def is_displayed(self) -> bool:
                return True

            def click(self) -> None:
                self.clicked += 1
                self.selected = True
                if self.on_click:
                    self.on_click()

            def is_selected(self) -> bool:
                return self.selected

            def get_attribute(self, name: str) -> str:
                if name == "value":
                    return self.value
                return ""

        class PickerDriver(FakeDriver):
            def __init__(self, mapping: dict[str, list[FakePickerElement]]) -> None:
                super().__init__()
                self.mapping = mapping
                self.scripts: list[tuple[str, tuple[object, ...]]] = []

            def find_elements(self, by: object, value: object) -> list[FakePickerElement]:
                return self.mapping.get(str(value), [])

            def execute_script(self, script: str, *args: object) -> None:
                self.scripts.append((script, args))
                return None

        create_link = FakePickerElement()
        name_input = FakePickerElement()
        private_label = FakePickerElement()
        driver = PickerDriver(
            {
                "#create-link": [create_link],
                "#album-name": [name_input],
                "#private": [private_label],
            }
        )
        submit_button = FakePickerElement(
            on_click=lambda: driver.mapping.__setitem__("#album-name", [])
        )
        driver.mapping["#submit"] = [submit_button]
        self.browser.driver = driver
        self.browser._pause = lambda _seconds: None  # type: ignore[assignment]
        self.browser._picker_album_option_matches = (  # type: ignore[assignment]
            lambda _selector, album_name: album_name == "AUTO_DETAIL_001"
        )

        def fake_fill_text_field(element: FakePickerElement, value: str, *, clear: bool = True) -> None:
            element.value = str(value)

        self.browser._fill_text_field = fake_fill_text_field  # type: ignore[assignment]

        created = self.browser._create_picker_album(
            link_selector={"by": "css", "value": "#create-link"},
            name_input_selector={"by": "css", "value": "#album-name"},
            private_selector={"by": "css", "value": "#private"},
            submit_selector={"by": "css", "value": "#submit"},
            album_select_selector={"by": "css", "value": "#album-select"},
            album_name="AUTO_DETAIL_001",
            timeout_seconds=1,
        )

        self.assertTrue(created)
        self.assertEqual(name_input.value, "AUTO_DETAIL_001")
        self.assertGreaterEqual(create_link.clicked, 1)
        self.assertGreaterEqual(private_label.clicked, 1)
        self.assertGreaterEqual(submit_button.clicked, 1)

    def test_create_picker_album_rejects_missing_private_control(self) -> None:
        class FakePickerElement:
            def __init__(self, on_click=None) -> None:
                self.clicked = 0
                self.value = ""
                self.on_click = on_click

            def is_displayed(self) -> bool:
                return True

            def click(self) -> None:
                self.clicked += 1
                if self.on_click:
                    self.on_click()

            def is_selected(self) -> bool:
                return False

            def get_attribute(self, name: str) -> str:
                return self.value if name == "value" else ""

        class PickerDriver(FakeDriver):
            def __init__(self, mapping: dict[str, list[FakePickerElement]]) -> None:
                super().__init__()
                self.mapping = mapping

            def find_elements(self, by: object, value: object) -> list[FakePickerElement]:
                return self.mapping.get(str(value), [])

        create_link = FakePickerElement()
        name_input = FakePickerElement()
        submit_button = FakePickerElement()
        self.browser.driver = PickerDriver(
            {
                "#create-link": [create_link],
                "#album-name": [name_input],
                "#submit": [submit_button],
            }
        )
        self.browser._pause = lambda _seconds: None  # type: ignore[assignment]
        self.browser._fill_text_field = (  # type: ignore[assignment]
            lambda element, value, clear=True: setattr(element, "value", str(value))
        )

        created = self.browser._create_picker_album(
            link_selector={"by": "css", "value": "#create-link"},
            name_input_selector={"by": "css", "value": "#album-name"},
            private_selector={"by": "css", "value": "#private"},
            submit_selector={"by": "css", "value": "#submit"},
            album_select_selector={"by": "css", "value": "#album-select"},
            album_name="AUTO_DETAIL_002",
            timeout_seconds=0.01,
        )

        self.assertFalse(created)
        self.assertEqual(submit_button.clicked, 0)

    def test_create_picker_album_rejects_public_or_password_only_state(self) -> None:
        class FakeRadio:
            def __init__(self, *, selected: bool, selectable: bool = True) -> None:
                self.selected = selected
                self.selectable = selectable
                self.clicked = 0
                self.value = ""

            def is_displayed(self) -> bool:
                return True

            def click(self) -> None:
                self.clicked += 1
                if self.selectable:
                    self.selected = True

            def is_selected(self) -> bool:
                return self.selected

            def get_attribute(self, name: str) -> str:
                return self.value if name == "value" else ""

        class PickerDriver(FakeDriver):
            def __init__(self, mapping: dict[str, list[FakeRadio]]) -> None:
                super().__init__()
                self.mapping = mapping

            def find_elements(self, by: object, value: object) -> list[FakeRadio]:
                return self.mapping.get(str(value), [])

        private = FakeRadio(selected=False, selectable=False)
        public = FakeRadio(selected=True)
        password = FakeRadio(selected=True)
        submit = FakeRadio(selected=False)
        name_input = FakeRadio(selected=False)
        driver = PickerDriver(
            {
                "#create-link": [FakeRadio(selected=False)],
                "#album-name": [name_input],
                "#private": [private],
                "#public": [public],
                "#password": [password],
                "#submit": [submit],
            }
        )
        self.browser.driver = driver
        self.browser._pause = lambda _seconds: None  # type: ignore[assignment]
        self.browser._fill_text_field = (  # type: ignore[assignment]
            lambda element, value, clear=True: setattr(element, "value", str(value))
        )

        created = self.browser._create_picker_album(
            link_selector={"by": "css", "value": "#create-link"},
            name_input_selector={"by": "css", "value": "#album-name"},
            private_selector={"by": "css", "value": "#private"},
            submit_selector={"by": "css", "value": "#submit"},
            album_select_selector={"by": "css", "value": "#album-select"},
            album_name="AUTO_DETAIL_003",
            timeout_seconds=0.01,
        )

        self.assertFalse(created)
        self.assertTrue(public.is_selected())
        self.assertTrue(password.is_selected())
        self.assertFalse(private.is_selected())
        self.assertEqual(submit.clicked, 0)

    def test_record_picker_album_creation_includes_private_capacity_evidence(self) -> None:
        context: dict[str, object] = {}

        self.browser._record_picker_album_creation(
            context,
            "image_probe",
            "AUTO_PROBE_001",
            {
                "auto_album_access": "private",
                "auto_album_access_label": "不公开",
                "auto_album_capacity": 500,
            },
        )

        self.assertTrue(context["image_probe_album_created"])
        self.assertEqual(context["image_probe_album_name"], "AUTO_PROBE_001")
        self.assertEqual(context["image_probe_album_access"], "private")
        self.assertEqual(context["image_probe_album_access_label"], "不公开")
        self.assertTrue(context["image_probe_album_access_verified"])
        self.assertEqual(context["image_probe_album_capacity"], 500)

    def test_picker_batches_limit_each_dialog_to_four_files(self) -> None:
        self.browser.driver = FakeDriver()
        calls: list[list[str]] = []

        def fake_picker_upload(step, _selector, values, context):
            calls.append(list(values))
            context[f"{step['name']}_uploaded_urls"] = [
                f"https://cbu01.alicdn.com/{Path(value).name}" for value in values
            ]

        self.browser._run_picker_upload = fake_picker_upload  # type: ignore[assignment]
        values = [f"C:/images/{index}.jpg" for index in range(10)]
        context: dict[str, object] = {}

        uploaded = self.browser._upload_images_via_picker_batches(
            {"name": "detail_images", "picker_batch_size": 50},
            {"by": "css", "value": "#picker"},
            values,
            context,
        )

        self.assertEqual([len(batch) for batch in calls], [4, 4, 2])
        self.assertEqual(len(uploaded), 10)
        self.assertEqual(context["detail_images_uploaded_urls"], uploaded)

    def test_picker_batch_urls_keep_latest_unique_urls_from_cumulative_responses(self) -> None:
        selected = self.browser._select_current_picker_batch_urls(
            [
                "https://cbu01.alicdn.com/stale.jpg",
                "https://cbu01.alicdn.com/detail-1.jpg",
                "https://cbu01.alicdn.com/stale.jpg",
                "https://cbu01.alicdn.com/detail-1.jpg",
                "https://cbu01.alicdn.com/detail-2.jpg",
            ],
            2,
        )

        self.assertEqual(
            selected,
            [
                "https://cbu01.alicdn.com/detail-1.jpg",
                "https://cbu01.alicdn.com/detail-2.jpg",
            ],
        )

    def test_picker_batches_reject_missing_remote_urls(self) -> None:
        self.browser.driver = FakeDriver()

        def fake_picker_upload(step, _selector, values, context):
            context[f"{step['name']}_uploaded_urls"] = ["https://cbu01.alicdn.com/only-one.jpg"]

        self.browser._run_picker_upload = fake_picker_upload  # type: ignore[assignment]

        with self.assertRaisesRegex(PublishValidationError, "unexpected URL count"):
            self.browser._upload_images_via_picker_batches(
                {"name": "detail_images"},
                {"by": "css", "value": "#picker"},
                ["C:/images/1.jpg", "C:/images/2.jpg"],
                {},
            )

    def test_select_picker_album_by_exact_option_title(self) -> None:
        class FakeOption:
            def __init__(self, *, text: str, title: str, value: str) -> None:
                self.text = text
                self.title = title
                self.value = value
                self.selected = False

            def get_attribute(self, name: str) -> str:
                return {"title": self.title, "value": self.value}.get(name, "")

            def is_enabled(self) -> bool:
                return True

        class FakeSelectElement:
            def __init__(self) -> None:
                self.options = [
                    FakeOption(text="full album", title="FULL", value="1"),
                    FakeOption(
                        text="AUTO_DETAIL_001 (0/100)",
                        title="AUTO_DETAIL_001",
                        value="2",
                    ),
                ]

            def is_displayed(self) -> bool:
                return True

        class FakeSelectControl:
            def __init__(self, element: FakeSelectElement) -> None:
                self.element = element
                self.options = element.options

            def select_by_value(self, value: str) -> None:
                for option in self.options:
                    option.selected = option.value == value

            def select_by_visible_text(self, text: str) -> None:
                for option in self.options:
                    option.selected = option.text == text

            @property
            def first_selected_option(self) -> FakeOption:
                return next(option for option in self.options if option.selected)

        class PickerDriver(FakeDriver):
            def __init__(self) -> None:
                super().__init__()
                self.select = FakeSelectElement()
                self.scripts: list[str] = []

            def find_elements(self, by: object, value: object) -> list[FakeSelectElement]:
                return [self.select] if str(value) == "#album-select" else []

            def execute_script(self, script: str, *args: object) -> str:
                self.scripts.append(script)
                return ""

        driver = PickerDriver()
        self.browser.driver = driver
        self.browser._pause = lambda _seconds: None  # type: ignore[assignment]

        with patch.object(browser_rpa_module, "Select", FakeSelectControl):
            selected = self.browser._select_picker_album_by_text(
                {"by": "css", "value": "#album-select"},
                "AUTO_DETAIL_001",
            )

        self.assertTrue(selected)
        self.assertTrue(any("dispatchEvent" in script for script in driver.scripts))

    def test_picker_album_full_message_detects_capacity_error(self) -> None:
        class AlbumFullDriver(FakeDriver):
            def execute_script(self, script: str, *args: object) -> str:
                return "抱歉，您的相册已满。以下23张图片无法上传"

        self.browser.driver = AlbumFullDriver()
        self.assertIn("相册已满", self.browser._picker_album_full_message())

    def test_mark_image_album_full_stops_remaining_shop_batch(self) -> None:
        context: dict[str, object] = {}
        self.browser._mark_image_album_full(
            context,
            ImageAlbumFullError("Picker album is full"),
        )
        self.assertTrue(context["shop_blocked"])
        self.assertEqual(context["shop_block_reason"], "image_album_full")
        self.assertTrue(context["shop_skip_remaining"])

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

    def test_tinymce_images_uses_external_urls_after_capacity_block(self) -> None:
        self.browser.driver = FakeDriver()
        self.browser._ensure_old_tinymce_mode = lambda _step: None  # type: ignore[assignment]
        self.browser._upload_images_via_picker_batches = (  # type: ignore[assignment]
            lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("picker must be skipped"))
        )
        captured: dict[str, object] = {}

        def fake_write(step, selector, value):
            captured["step"] = step
            captured["selector"] = selector
            captured["value"] = value

        self.browser._write_tinymce_content = fake_write  # type: ignore[assignment]
        context: dict[str, object] = {
            "detail_images_remote_list": [
                "https://images.example.com/1.webp",
                "https://images.example.com/2.webp",
            ]
        }

        self.browser._insert_tinymce_images(
            {
                "name": "detail_images",
                "use_remote_detail_urls": True,
                "fallback_picker_selector": {"by": "css", "value": "#picker"},
            },
            {"by": "css", "value": "#tinyMCE-0"},
            ["C:/images/1.jpg", "C:/images/2.jpg"],
            context,
        )

        self.assertEqual(context["detail_images_delivery_mode"], "external_url_capacity_fallback")
        self.assertEqual(len(context["detail_images_uploaded_urls"]), 2)
        self.assertEqual(captured["step"]["append_mode"], "replace")
        self.assertIn("https://images.example.com/1.webp", captured["value"])

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

    def test_normalize_dimension_value(self) -> None:
        self.assertEqual(self.browser._normalize_dimension_value("49"), "49")
        self.assertEqual(self.browser._normalize_dimension_value("18.500"), "18.5")
        self.assertEqual(self.browser._normalize_dimension_value(""), "")

    def test_normalize_weight_value_converts_small_decimal_to_grams(self) -> None:
        self.assertEqual(self.browser._normalize_weight_value("4.32"), "4320")
        self.assertEqual(self.browser._normalize_weight_value("37600"), "37600")
        self.assertEqual(self.browser._normalize_weight_value(""), "")

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

    def test_category_matches_current_page_accepts_suffix_path(self) -> None:
        class CategoryDriver(FakeDriver):
            def execute_script(self, script: str, *args: object) -> str:
                return "当前类目：其他类目 > 家装建材 > 书房家具 > 书柜"

        self.browser.driver = CategoryDriver(current_url="https://offer-new.1688.com/popular/publish.htm")

        self.assertTrue(self.browser._category_matches_current_page(["家装建材", "书房家具", "书柜"]))

    def test_select_category_path_skips_reselect_when_form_ready(self) -> None:
        self.browser.driver = FakeDriver(current_url="https://offer-new.1688.com/popular/publish.htm")
        self.browser._category_matches_current_page = lambda _levels: False  # type: ignore[assignment]
        self.browser._is_publish_form_ready = lambda: True  # type: ignore[assignment]
        self.browser._build_category_select_url = lambda _step: (_ for _ in ()).throw(  # type: ignore[assignment]
            AssertionError("should not jump to select page when form is already ready")
        )

        self.browser._select_category_path(
            {
                "name": "category",
                "source": "resolved_category_levels",
                "allow_skip_when_form_ready": True,
            },
            {"by": "css", "value": "#guid-catNamer"},
            {"resolved_category_levels": ["瀹惰寤烘潗", "涔︽埧瀹跺叿", "涔︽煖"]},
        )

    def test_is_publish_form_ready_by_title_selector(self) -> None:
        class PublishReadyDriver(FakeDriver):
            def execute_script(self, script: str, *args: object) -> object:
                if "#guid-title input[maxlength=\"60\"]" in script and "selectors.some" in script:
                    return True
                return ""

        self.browser.driver = PublishReadyDriver(
            current_url="https://offer-new.1688.com/popular/publish.htm?operator=new"
        )
        self.assertTrue(self.browser._is_publish_form_ready())

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

    def test_run_publish_steps_falls_back_to_bridge_when_main_image_picker_times_out(self) -> None:
        self.browser.driver = FakeDriver(current_url="https://offer-new.1688.com/popular/publish.htm")
        invoked = {"bridge": 0}

        def fake_picker_upload(step, selector, values, context):
            raise TimeoutException("picker timeout")

        def fake_bridge_upload(step, selector, values, context):
            invoked["bridge"] += 1
            return ["https://example.com/main-1.jpg"]

        self.browser._run_picker_upload = fake_picker_upload  # type: ignore[assignment]
        self.browser._upload_images_via_primary_picture_bridge = fake_bridge_upload  # type: ignore[assignment]
        self.browser._draft_main_image_present = lambda: True  # type: ignore[assignment]

        self.browser._run_publish_steps(
            [
                {
                    "name": "main_image",
                    "action": "picker_upload",
                    "source": "main_image",
                    "required": True,
                    "selector": {"by": "xpath", "value": "//div[@id='guid-primaryPicture']"},
                }
            ],
            {"main_image": "D:/images/main.jpg"},
        )

        self.assertEqual(invoked["bridge"], 1)

    def test_trigger_picker_opener_uses_react_fallback_when_picker_not_ready(self) -> None:
        class ScriptDriver(FakeDriver):
            def __init__(self) -> None:
                super().__init__()
                self.script_calls = 0

            def execute_script(self, script: str, *args: object) -> object:
                self.script_calls += 1
                return None

        driver = ScriptDriver()
        self.browser.driver = driver
        self.browser._pause = lambda _seconds: None  # type: ignore[assignment]
        state = {"ready": False, "react_calls": 0}

        self.browser._is_picker_ui_ready = lambda: bool(state["ready"])  # type: ignore[assignment]

        def fake_react_picker(_element: object) -> int:
            state["react_calls"] += 1
            state["ready"] = True
            return 1

        self.browser._trigger_react_picker_opener = fake_react_picker  # type: ignore[assignment]

        self.browser._trigger_picker_opener(object())  # type: ignore[arg-type]

        self.assertEqual(state["react_calls"], 1)
        self.assertGreaterEqual(driver.script_calls, 2)

    def test_trigger_picker_opener_skips_react_fallback_when_picker_ready(self) -> None:
        class ScriptDriver(FakeDriver):
            def execute_script(self, script: str, *args: object) -> object:
                return None

        self.browser.driver = ScriptDriver()
        self.browser._pause = lambda _seconds: None  # type: ignore[assignment]
        self.browser._is_picker_ui_ready = lambda: True  # type: ignore[assignment]
        state = {"react_calls": 0}

        def fake_react_picker(_element: object) -> int:
            state["react_calls"] += 1
            return 1

        self.browser._trigger_react_picker_opener = fake_react_picker  # type: ignore[assignment]

        self.browser._trigger_picker_opener(object())  # type: ignore[arg-type]

        self.assertEqual(state["react_calls"], 0)

    def test_close_picker_dialog_uses_real_close_control(self) -> None:
        state = {"ready": True}

        class CloseButton:
            def is_displayed(self) -> bool:
                return True

            def click(self) -> None:
                state["ready"] = False

        class SwitchTarget:
            def default_content(self) -> None:
                return None

        class PickerDriver(FakeDriver):
            def __init__(self) -> None:
                super().__init__()
                self.switch_to = SwitchTarget()

            def execute_script(self, script: str, *args: object) -> object:
                if "arguments[0].click()" in script:
                    state["ready"] = False
                return None

            def find_elements(self, by: object, value: object) -> list[CloseButton]:
                if str(value) == "div.ibank-picker-dialog .picker-header a.close":
                    return [CloseButton()]
                return []

        self.browser.driver = PickerDriver()
        self.browser._is_picker_ui_ready = lambda: bool(state["ready"])  # type: ignore[assignment]

        closed = self.browser._close_picker_dialog(
            {"by": "css", "value": "div.ibank-picker-dialog, div.ui-dialog"}
        )

        self.assertTrue(closed)
        self.assertFalse(state["ready"])

    def test_picker_ui_ignores_hidden_about_blank_frame(self) -> None:
        class HiddenFrame:
            def is_displayed(self) -> bool:
                return False

            def get_attribute(self, name: str) -> str:
                return "about:blank" if name == "src" else ""

        class PickerDriver(FakeDriver):
            def find_elements(self, by: object, value: object) -> list[HiddenFrame]:
                if str(value) == "iframe.picker-frame":
                    return [HiddenFrame()]
                return []

        self.browser.driver = PickerDriver()

        self.assertFalse(self.browser._is_picker_ui_ready())

    def test_select_picker_album_retries_stale_dom(self) -> None:
        self.browser.driver = FakeDriver()
        attempts = {"count": 0}

        def fake_select_once(_selector, *, excluded_values=None):
            attempts["count"] += 1
            if attempts["count"] == 1:
                raise StaleElementReferenceException("picker refreshed")
            return True

        self.browser._select_picker_album_once = fake_select_once  # type: ignore[assignment]
        self.browser._pause = lambda _seconds: None  # type: ignore[assignment]

        selected = self.browser._select_picker_album(
            {"by": "css", "value": "select"},
            excluded_values={"full-album"},
        )

        self.assertTrue(selected)
        self.assertEqual(attempts["count"], 2)

    def test_run_picker_upload_falls_back_to_dialog_when_react_bridge_fails(self) -> None:
        class MinimalDriver(FakeDriver):
            class _SwitchTo:
                def frame(self, _frame: object) -> None:
                    return None

                def default_content(self) -> None:
                    return None

            def __init__(self) -> None:
                super().__init__(current_url="https://offer-new.1688.com/popular/publish.htm")
                self.switch_to = MinimalDriver._SwitchTo()

            def execute_script(self, script: str, *args: object) -> object:
                return None

        self.browser.driver = MinimalDriver()
        self.browser._pause = lambda _seconds: None  # type: ignore[assignment]
        self.browser._upload_images_via_primary_picture_bridge = lambda *args, **kwargs: (_ for _ in ()).throw(  # type: ignore[assignment]
            ValueError("bridge missing")
        )
        self.browser._remove_elements = lambda _selector: None  # type: ignore[assignment]
        self.browser._wait_for_element = lambda selector, clickable=False: object()  # type: ignore[assignment]
        self.browser._trigger_picker_opener = lambda _element: None  # type: ignore[assignment]
        self.browser._wait_for_dialog = lambda _selector: (_ for _ in ()).throw(TimeoutException("dialog timeout"))  # type: ignore[assignment]

        with self.assertRaises(TimeoutException):
            self.browser._run_picker_upload(
                {
                    "name": "main_image",
                    "upload_via_react_bridge": True,
                },
                {"by": "css", "value": "#guid-primaryPicture"},
                ["D:/images/main.jpg"],
                {},
            )

    def test_run_picker_upload_saves_bridge_uploaded_urls_into_context(self) -> None:
        self.browser.driver = FakeDriver(current_url="https://offer-new.1688.com/popular/publish.htm")
        self.browser._upload_images_via_primary_picture_bridge = (  # type: ignore[assignment]
            lambda *args, **kwargs: ["https://example.com/main-1.jpg"]
        )

        context: dict[str, object] = {}
        self.browser._run_picker_upload(
            {
                "name": "main_image",
                "upload_via_react_bridge": True,
            },
            {"by": "css", "value": "#guid-primaryPicture"},
            ["D:/images/main.jpg"],
            context,
        )

        self.assertEqual(context.get("main_image_uploaded_urls"), ["https://example.com/main-1.jpg"])

    def test_resolve_publish_mode_prefers_draft_then_submit(self) -> None:
        self.assertEqual(self.browser._resolve_publish_mode({"auto_save_draft": True}), "draft")
        self.assertEqual(self.browser._resolve_publish_mode({"auto_submit": True}), "submit")

    def test_expected_publish_draft_accepts_matching_url_id(self) -> None:
        class DraftDriver(FakeDriver):
            def execute_script(self, script: str, *args: object) -> str:
                return "draft-123"

        self.browser.driver = DraftDriver()
        self.browser._assert_expected_publish_draft(
            {"expected_draft_id": "draft-123", "draft_assert_timeout_seconds": 1},
            {},
        )

    def test_expected_publish_draft_rejects_mismatch(self) -> None:
        class DraftDriver(FakeDriver):
            def execute_script(self, script: str, *args: object) -> str:
                return "wrong-draft"

        self.browser.driver = DraftDriver()
        with self.assertRaisesRegex(PublishValidationError, "publish draft mismatch"):
            self.browser._assert_expected_publish_draft(
                {"expected_draft_id": "expected-draft", "draft_assert_timeout_seconds": 1},
                {},
            )
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

    def test_select_buyer_protection_scrolls_virtual_list_before_click(self) -> None:
        self.browser.driver = FakeDriver()
        selected_values = iter(["", "15天发货"])
        events: list[str] = []
        self.browser._read_buyer_protection_row_selected_text = lambda: next(selected_values)  # type: ignore[assignment]
        self.browser._open_buyer_protection_row_dropdown = lambda: "select-list"  # type: ignore[assignment]
        self.browser._scroll_ant_dropdown_option_into_view = (  # type: ignore[assignment]
            lambda value, dropdown_id="": events.append(f"scroll:{value}:{dropdown_id}") or True
        )
        self.browser._click_visible_dropdown_option_native = (  # type: ignore[assignment]
            lambda value, dropdown_id="": events.append(f"click:{value}:{dropdown_id}") or True
        )
        self.browser._pause = lambda _seconds: None  # type: ignore[assignment]

        with patch.object(browser_rpa_module, "WebDriverWait") as wait_mock:
            wait_mock.return_value.until.return_value = True
            selected = self.browser._select_buyer_protection_ship_time_from_row(
                "15天发货",
                select_first_option_if_unmatched=True,
            )

        self.assertEqual(selected, "15天发货")
        self.assertEqual(
            events,
            ["scroll:15天发货:select-list", "click:15天发货:select-list"],
        )

    def test_ensure_buyer_protection_fails_before_save_when_selection_is_empty(self) -> None:
        self.browser.driver = FakeDriver()
        self.browser._select_buyer_protection_ship_time_from_row = lambda *args, **kwargs: ""  # type: ignore[assignment]

        with self.assertRaisesRegex(PublishValidationError, "could not be selected before draft save"):
            self.browser._ensure_buyer_protection_ship_time_before_draft_save(
                {
                    "draft_verification": {
                        "buyer_protection_default_value": "15天发货",
                    }
                },
                {},
            )

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

    def test_draft_logistics_dimension_values_falls_back_to_offer_info(self) -> None:
        class LogisticsOfferInfoDriver(FakeDriver):
            def execute_script(self, script: str, *args: object) -> object:
                if "components || {}).officialLogistics" in script:
                    return {
                        "length": "49",
                        "width": "18.5",
                        "height": "10.5",
                        "weight": "4320",
                    }
                return {}

        self.browser.driver = LogisticsOfferInfoDriver()
        self.assertEqual(
            self.browser._draft_logistics_dimension_values(),
            {
                "length": "49",
                "width": "18.5",
                "height": "10.5",
                "weight": "4320",
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

    def test_draft_main_image_state_reads_square_dimensions(self) -> None:
        class MainImageStateDriver(FakeDriver):
            def execute_script(self, script: str, *args: object) -> object:
                return {
                    "present": True,
                    "square": True,
                    "width": 1067,
                    "height": 1067,
                    "url": "https://cbu01.alicdn.com/img/ibank/square.jpg",
                }

        self.browser.driver = MainImageStateDriver()
        state = self.browser._draft_main_image_state()
        self.assertTrue(state["present"])
        self.assertTrue(state["square"])
        self.assertEqual(state["width"], 1067)

    def test_prepare_main_image_slot_reuses_existing_square_image(self) -> None:
        class SquareImageDriver(FakeDriver):
            def execute_script(self, script: str, *args: object) -> object:
                return {
                    "present": True,
                    "square": True,
                    "width": 1000,
                    "height": 1000,
                    "url": "https://example.com/square.jpg",
                }

        self.browser.driver = SquareImageDriver()
        context: dict[str, object] = {}

        reused = self.browser._prepare_main_image_slot(
            {"ensure_square_first_image": True},
            context,
        )

        self.assertTrue(reused)
        self.assertTrue(context["main_image_reused_square"])
        self.assertEqual(context["main_image_uploaded_urls"], ["https://example.com/square.jpg"])

    def test_prepare_main_image_slot_removes_existing_non_square_image(self) -> None:
        class NonSquareImageDriver(FakeDriver):
            def __init__(self) -> None:
                super().__init__()
                self.calls = 0

            def execute_script(self, script: str, *args: object) -> object:
                self.calls += 1
                if self.calls == 1:
                    return {
                        "present": True,
                        "square": False,
                        "width": 800,
                        "height": 1067,
                        "url": "https://example.com/non-square.jpg",
                    }
                return True

        self.browser.driver = NonSquareImageDriver()
        self.browser._draft_main_image_present = lambda: False  # type: ignore[assignment]
        context: dict[str, object] = {}

        replaced = self.browser._prepare_main_image_slot(
            {
                "ensure_square_first_image": True,
                "main_image_replace_wait_seconds": 0.1,
            },
            context,
        )

        self.assertFalse(replaced)
        self.assertTrue(context["main_image_replaced_non_square"])

    def test_draft_description_image_count_reads_sdk_html(self) -> None:
        class DescriptionDriver(FakeDriver):
            def execute_script(self, script: str, *args: object) -> int:
                return 3

        self.browser.driver = DescriptionDriver()
        self.assertEqual(self.browser._draft_description_image_count(), 3)

    def test_primary_picture_bridge_searches_descendants_and_ancestors(self) -> None:
        class BridgeDriver(FakeDriver):
            def __init__(self) -> None:
                super().__init__(current_url="https://offer-new.1688.com/popular/publish.htm")
                self.script = ""

            def execute_script(self, script: str, *args: object) -> dict[str, object]:
                self.script = script
                return {"ok": True}

        driver = BridgeDriver()
        self.browser.driver = driver
        self.browser._wait_for_element = lambda _selector: object()  # type: ignore[assignment]

        result = self.browser._execute_primary_picture_bridge_script(
            {"by": "css", "value": "#guid-primaryPicture"},
            "return {ok: true};",
        )

        self.assertEqual(result, {"ok": True})
        self.assertIn("querySelectorAll('*')", driver.script)
        self.assertIn("parentElement", driver.script)

    def test_verify_saved_draft_waits_for_delayed_description_images(self) -> None:
        class DelayedDescriptionDriver(FakeDriver):
            def __init__(self) -> None:
                super().__init__(current_url="https://offer-new.1688.com/popular/publish.htm")
                self.refresh_count = 0

            def refresh(self) -> None:
                self.refresh_count += 1

        driver = DelayedDescriptionDriver()
        self.browser.driver = driver
        self.browser._pause = lambda _seconds: None  # type: ignore[assignment]
        self.browser._is_publish_form_ready = lambda: True  # type: ignore[assignment]
        self.browser._collect_assist_messages = lambda: []  # type: ignore[assignment]
        self.browser._draft_main_image_present = lambda: True  # type: ignore[assignment]
        self.browser._draft_description_present = lambda: True  # type: ignore[assignment]
        self.browser._collect_spec_values = lambda: {}  # type: ignore[assignment]
        self.browser._draft_selected_send_address = lambda: "861873672"  # type: ignore[assignment]
        self.browser._draft_logistics_dimension_values = lambda: {}  # type: ignore[assignment]
        self.browser._draft_selected_buyer_protection = lambda: "15天发货"  # type: ignore[assignment]
        self.browser._draft_selected_buyer_protection_schedule = lambda: []  # type: ignore[assignment]
        image_counts = iter([0, 0, 2])
        self.browser._draft_description_image_count = lambda: next(image_counts, 2)  # type: ignore[assignment]

        context: dict[str, object] = {}
        self.browser._verify_saved_draft(
            {
                "draft_verification": {
                    "enabled": True,
                    "refresh_after_save": True,
                    "refresh_wait_seconds": 0,
                    "description_image_wait_seconds": 1,
                    "description_image_poll_seconds": 0.1,
                    "require_main_image": True,
                    "require_description": True,
                    "require_specs": False,
                    "require_send_address": False,
                    "require_logistics_dimensions": False,
                    "require_buyer_protection": False,
                    "minimum_description_image_count": 2,
                }
            },
            context,
        )

        self.assertEqual(driver.refresh_count, 1)
        self.assertEqual(context.get("draft_description_image_count"), 2)
        self.assertTrue(context.get("draft_description_images_delayed"))
        self.assertGreaterEqual(int(context.get("draft_description_image_wait_attempts", 0)), 2)

    def test_verify_saved_draft_ignores_stale_description_assist_after_images_persist(self) -> None:
        class StaleAssistDriver(FakeDriver):
            def __init__(self) -> None:
                super().__init__(current_url="https://offer-new.1688.com/popular/publish.htm")
                self.refresh_count = 0

            def refresh(self) -> None:
                self.refresh_count += 1

        driver = StaleAssistDriver()
        self.browser.driver = driver
        self.browser._pause = lambda _seconds: None  # type: ignore[assignment]
        self.browser._is_publish_form_ready = lambda: True  # type: ignore[assignment]
        self.browser._collect_assist_messages = lambda: ["请填写图文详情"]  # type: ignore[assignment]
        self.browser._draft_main_image_present = lambda: True  # type: ignore[assignment]
        self.browser._draft_description_present = lambda: False  # type: ignore[assignment]
        self.browser._draft_description_image_count = lambda: 2  # type: ignore[assignment]
        self.browser._collect_spec_values = lambda: {}  # type: ignore[assignment]
        self.browser._draft_selected_send_address = lambda: "861873672"  # type: ignore[assignment]
        self.browser._draft_logistics_dimension_values = lambda: {}  # type: ignore[assignment]
        self.browser._draft_selected_buyer_protection = lambda: "15天发货"  # type: ignore[assignment]
        self.browser._draft_selected_buyer_protection_schedule = lambda: []  # type: ignore[assignment]

        context: dict[str, object] = {}
        self.browser._verify_saved_draft(
            {
                "draft_verification": {
                    "enabled": True,
                    "refresh_after_save": True,
                    "refresh_wait_seconds": 0,
                    "require_main_image": True,
                    "require_description": True,
                    "minimum_description_image_count": 2,
                    "require_specs": False,
                    "require_send_address": False,
                    "require_logistics_dimensions": False,
                    "require_buyer_protection": False,
                    "forbidden_assist_keywords": ["请填写图文详情"],
                }
            },
            context,
        )

        self.assertEqual(driver.refresh_count, 1)
        self.assertEqual(
            context.get("draft_stale_assist_messages_ignored"),
            ["请填写图文详情"],
        )
        self.assertEqual(context.get("draft_assist_messages_effective"), [])
        self.assertTrue(context.get("draft_description_present"))

    def test_ensure_core_fields_prefers_uploaded_detail_image_html(self) -> None:
        class CoreFieldsDriver(FakeDriver):
            def __init__(self) -> None:
                super().__init__()
                self.payload: dict[str, object] = {}
                self.script = ""

            def execute_script(self, script: str, *args: object) -> object:
                self.script = script
                self.payload = dict(args[0])
                return {"ok": True, "applied": {"description": True}}

        driver = CoreFieldsDriver()
        self.browser.driver = driver
        context: dict[str, object] = {
            "description": "商品文字",
            "detail_images_uploaded_urls": [
                "https://cbu01.alicdn.com/detail-1.jpg",
                "https://cbu01.alicdn.com/detail-2.jpg",
            ],
            "price": "325.0",
            "quantity": "999",
        }

        self.browser._ensure_core_publish_fields_before_draft_save({}, context)

        description_html = str(driver.payload.get("descriptionHtml") or "")
        self.assertEqual(description_html.count("<img"), 2)
        self.assertIn("detail-1.jpg", description_html)
        self.assertNotIn("商品文字", description_html)
        self.assertIn("#guid-description", driver.script)
        self.assertIn("descriptionHandleNode.handleChange", driver.script)
        self.assertTrue((context.get("draft_core_fields_pre_save") or {}).get("ok"))

    def test_pre_save_reapplies_main_image_specs_and_title_before_core_patch(self) -> None:
        events: list[str] = []
        state: dict[str, object] = {
            "main_image": False,
            "title": "stale title",
            "specs": {"color": "old", "size": ""},
        }

        class CoreFieldsDriver(FakeDriver):
            def execute_script(self, script: str, *args: object) -> object:
                events.append("core")
                return {"ok": True, "applied": {"description": True}}

        self.browser.driver = CoreFieldsDriver()
        self.browser._pause = lambda _seconds: None  # type: ignore[assignment]
        self.browser._draft_main_image_state = lambda: {  # type: ignore[assignment]
            "present": bool(state["main_image"]),
            "square": bool(state["main_image"]),
        }
        self.browser._draft_main_image_present = lambda: bool(state["main_image"])  # type: ignore[assignment]
        self.browser._draft_title_value = lambda: str(state["title"])  # type: ignore[assignment]
        self.browser._collect_spec_values = lambda: dict(state["specs"])  # type: ignore[assignment]
        self.browser._draft_description_image_count = lambda: 2  # type: ignore[assignment]

        def clear_spec(label: str) -> None:
            events.append(f"clear:{label}")
            current_specs = dict(state["specs"])
            current_specs[label] = ""
            state["specs"] = current_specs

        def run_steps(steps: list[dict[str, object]], _context: dict[str, object]) -> None:
            step_name = str(steps[0]["name"])
            events.append(step_name)
            if step_name == "main_image":
                state["main_image"] = True
            elif step_name == "spec_values":
                state["specs"] = {"color": "walnut", "size": "48/40/50"}
            elif step_name == "title":
                state["title"] = "Bedside cabinet walnut 48x40x50"

        self.browser._clear_committed_spec_values = clear_spec  # type: ignore[assignment]
        self.browser._run_publish_steps = run_steps  # type: ignore[assignment]
        context: dict[str, object] = {
            "title": "Bedside cabinet walnut 48x40x50",
            "color": "walnut",
            "size": "48/40/50",
            "price": "745.0",
            "quantity": "999",
            "detail_images_uploaded_urls": [
                "https://cbu01.alicdn.com/detail-1.jpg",
                "https://cbu01.alicdn.com/detail-2.jpg",
            ],
        }
        publish_config = {
            "steps": [
                {"name": "main_image", "action": "picker_upload"},
                {
                    "name": "spec_values",
                    "action": "spec_values",
                    "profiles": {
                        "*": {
                            "rules": [
                                {"label": "color", "source": "color"},
                                {"label": "size", "source": "size"},
                            ]
                        }
                    },
                },
                {"name": "title", "action": "input", "source": "title"},
            ],
            "draft_verification": {
                "enabled": True,
                "require_title": True,
                "require_main_image": True,
                "require_square_main_image": True,
                "minimum_description_image_count": 1,
                "require_specs": True,
                "required_spec_labels": ["color", "size"],
            },
        }

        self.browser._ensure_core_publish_fields_before_draft_save(publish_config, context)

        self.assertEqual(
            events,
            ["main_image", "clear:color", "clear:size", "spec_values", "title", "core"],
        )
        self.assertTrue(context["draft_main_image_reapplied_pre_save"])
        self.assertEqual(context["draft_specs_reapplied_pre_save"], ["color", "size"])
        self.assertTrue(context["draft_title_reapplied_pre_save"])
        self.assertEqual(context["draft_description_image_count_expected_pre_save"], 2)

    def test_pre_save_core_guard_rejects_nonmatching_title(self) -> None:
        self.browser.driver = FakeDriver()
        self.browser._draft_title_value = lambda: "wrong title"  # type: ignore[assignment]

        with self.assertRaisesRegex(PublishValidationError, "title does not match"):
            self.browser._verify_core_fields_before_draft_save(
                {
                    "draft_verification": {
                        "enabled": True,
                        "require_title": True,
                        "require_main_image": False,
                        "require_specs": False,
                    }
                },
                {"title": "expected title"},
            )

    def test_pre_save_core_guard_rejects_nonmatching_committed_specs(self) -> None:
        self.browser.driver = FakeDriver()
        self.browser._collect_spec_values = lambda: {  # type: ignore[assignment]
            "color": "oak",
            "size": "48/40/50",
        }

        with self.assertRaisesRegex(PublishValidationError, "committed specs do not match"):
            self.browser._verify_core_fields_before_draft_save(
                {
                    "steps": [
                        {
                            "name": "spec_values",
                            "action": "spec_values",
                            "profiles": {
                                "*": {
                                    "rules": [
                                        {"label": "color", "source": "color"},
                                        {"label": "size", "source": "size"},
                                    ]
                                }
                            },
                        }
                    ],
                    "draft_verification": {
                        "enabled": True,
                        "require_title": False,
                        "require_main_image": False,
                        "require_specs": True,
                        "required_spec_labels": ["color", "size"],
                    },
                },
                {"color": "walnut", "size": "48/40/50"},
            )

    def test_collect_spec_values_reads_only_committed_inputs(self) -> None:
        class SpecDriver(FakeDriver):
            def __init__(self) -> None:
                super().__init__()
                self.script = ""

            def execute_script(self, script: str, *args: object) -> object:
                self.script = script
                return {"color": "walnut", "size": "48/40/50"}

        driver = SpecDriver()
        self.browser.driver = driver

        values = self.browser._collect_spec_values()

        self.assertEqual(values, {"color": "walnut", "size": "48/40/50"})
        self.assertIn(".value-select-item:not(.resident) input", driver.script)

    def test_pre_save_core_guard_rejects_missing_required_size(self) -> None:
        self.browser.driver = FakeDriver()
        self.browser._draft_title_value = lambda: "胡桃色床头柜"  # type: ignore[assignment]
        self.browser._draft_main_image_present = lambda: True  # type: ignore[assignment]
        self.browser._draft_description_image_count = lambda: 2  # type: ignore[assignment]
        self.browser._collect_spec_values = lambda: {"颜色": "胡桃色", "尺寸": ""}  # type: ignore[assignment]
        context: dict[str, object] = {}

        with self.assertRaisesRegex(PublishValidationError, "required specs are empty.*尺寸"):
            self.browser._verify_core_fields_before_draft_save(
                {
                    "draft_verification": {
                        "enabled": True,
                        "require_title": True,
                        "require_main_image": True,
                        "minimum_description_image_count": 1,
                        "require_specs": True,
                        "required_spec_labels": ["颜色", "尺寸"],
                    }
                },
                context,
            )

        self.assertEqual(context["draft_title_pre_save"], "胡桃色床头柜")
        self.assertTrue(context["draft_main_image_pre_save"])
        self.assertEqual(context["draft_description_image_count_pre_save"], 2)

    def test_pre_save_core_guard_accepts_complete_draft_fields(self) -> None:
        self.browser.driver = FakeDriver()
        self.browser._draft_title_value = lambda: "胡桃色床头柜"  # type: ignore[assignment]
        self.browser._draft_main_image_present = lambda: True  # type: ignore[assignment]
        self.browser._draft_description_image_count = lambda: 2  # type: ignore[assignment]
        self.browser._collect_spec_values = lambda: {"颜色": "胡桃色", "尺寸": "50x40x45cm"}  # type: ignore[assignment]
        context: dict[str, object] = {}

        self.browser._verify_core_fields_before_draft_save(
            {
                "draft_verification": {
                    "enabled": True,
                    "require_title": True,
                    "require_main_image": True,
                    "minimum_description_image_count": 1,
                    "require_specs": True,
                    "required_spec_labels": ["颜色", "尺寸"],
                }
            },
            context,
        )

        self.assertEqual(
            context["draft_spec_values_pre_save"],
            {"颜色": "胡桃色", "尺寸": "50x40x45cm"},
        )

    def test_draft_description_image_count_uses_javascript_word_boundary(self) -> None:
        class DescriptionCountDriver(FakeDriver):
            def __init__(self) -> None:
                super().__init__()
                self.script = ""

            def execute_script(self, script: str, *args: object) -> object:
                self.script = script
                return 2

        driver = DescriptionCountDriver()
        self.browser.driver = driver

        self.assertEqual(self.browser._draft_description_image_count(), 2)
        self.assertIn(r"/<img\b/gi", driver.script)
        self.assertNotIn("<img\x08", driver.script)

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
                "strict_buyer_protection_persist": False,
                "buyer_protection_default_value": "当日发",
            }
        }

        self.browser._verify_saved_draft(publish_config, context)
        self.assertEqual(context.get("draft_buyer_protection_schedule_source"), "draft_submit_trace")
        self.assertEqual(context.get("draft_buyer_protection_value_source"), "draft_submit_trace")

    def test_verify_saved_draft_rejects_buyer_protection_trace_in_strict_mode(self) -> None:
        self.browser.driver = FakeDriver(current_url="https://offer-new.1688.com/popular/publish.htm")
        self.browser._pause = lambda _seconds: None  # type: ignore[assignment]
        self.browser._collect_assist_messages = lambda: []  # type: ignore[assignment]
        self.browser._draft_main_image_present = lambda: True  # type: ignore[assignment]
        self.browser._draft_description_present = lambda: True  # type: ignore[assignment]
        self.browser._draft_description_image_count = lambda: 0  # type: ignore[assignment]
        self.browser._collect_spec_values = lambda: {}  # type: ignore[assignment]
        self.browser._draft_selected_send_address = lambda: "861873672"  # type: ignore[assignment]
        self.browser._draft_logistics_dimension_values = lambda: {}  # type: ignore[assignment]
        self.browser._draft_selected_buyer_protection = lambda: ""  # type: ignore[assignment]
        self.browser._draft_selected_buyer_protection_schedule = lambda: []  # type: ignore[assignment]
        context: dict[str, object] = {
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

        with self.assertRaisesRegex(PublishValidationError, "schedule did not persist"):
            self.browser._verify_saved_draft(
                {
                    "draft_verification": {
                        "enabled": True,
                        "refresh_after_save": False,
                        "require_main_image": True,
                        "require_description": True,
                        "require_specs": False,
                        "require_buyer_protection": True,
                        "strict_buyer_protection_persist": True,
                        "buyer_protection_default_value": "当日发",
                    }
                },
                context,
            )
        self.assertEqual(context.get("draft_buyer_protection_value_trace"), None)

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

    def test_verify_saved_draft_rejects_send_address_trace_in_strict_mode(self) -> None:
        self.browser.driver = FakeDriver(current_url="https://offer-new.1688.com/popular/publish.htm")
        self.browser._pause = lambda _seconds: None  # type: ignore[assignment]
        self.browser._collect_assist_messages = lambda: []  # type: ignore[assignment]
        self.browser._draft_main_image_present = lambda: True  # type: ignore[assignment]
        self.browser._draft_description_present = lambda: True  # type: ignore[assignment]
        self.browser._draft_description_image_count = lambda: 0  # type: ignore[assignment]
        self.browser._collect_spec_values = lambda: {}  # type: ignore[assignment]
        self.browser._draft_selected_send_address = lambda: ""  # type: ignore[assignment]
        self.browser._draft_logistics_dimension_values = lambda: {}  # type: ignore[assignment]
        self.browser._draft_selected_buyer_protection = lambda: ""  # type: ignore[assignment]
        self.browser._draft_selected_buyer_protection_schedule = lambda: []  # type: ignore[assignment]
        context: dict[str, object] = {
            "draft_submit_trace": {"patch": {"patchSnapshot": {"sendAddressId": 861873672}}},
        }

        with self.assertRaisesRegex(PublishValidationError, "send address did not persist"):
            self.browser._verify_saved_draft(
                {
                    "draft_verification": {
                        "enabled": True,
                        "refresh_after_save": False,
                        "require_main_image": True,
                        "require_description": True,
                        "require_specs": False,
                        "require_send_address": True,
                        "strict_send_address_persist": True,
                        "require_buyer_protection": False,
                    }
                },
                context,
            )
        self.assertEqual(context.get("draft_send_address_trace"), "861873672")

    def test_verify_saved_draft_allows_nonpersistent_fields_only_with_complete_evidence(self) -> None:
        class RefreshDriver(FakeDriver):
            def refresh(self) -> None:
                return None

        self.browser.driver = RefreshDriver(current_url="https://offer-new.1688.com/popular/publish.htm")
        self.browser._pause = lambda _seconds: None  # type: ignore[assignment]
        self.browser._is_publish_form_ready = lambda: True  # type: ignore[assignment]
        self.browser._collect_assist_messages = lambda: [  # type: ignore[assignment]
            "买家保障 第1行的发货时间为必填项"
        ]
        self.browser._draft_main_image_state = lambda: {"present": True, "square": True}  # type: ignore[assignment]
        self.browser._draft_main_image_present = lambda: True  # type: ignore[assignment]
        self.browser._draft_description_present = lambda: True  # type: ignore[assignment]
        self.browser._draft_description_image_count = lambda: 2  # type: ignore[assignment]
        self.browser._collect_spec_values = lambda: {}  # type: ignore[assignment]
        self.browser._draft_selected_send_address = lambda: ""  # type: ignore[assignment]
        self.browser._draft_logistics_dimension_values = lambda: {}  # type: ignore[assignment]
        self.browser._draft_selected_buyer_protection = lambda: ""  # type: ignore[assignment]
        self.browser._draft_selected_buyer_protection_schedule = lambda: []  # type: ignore[assignment]
        context: dict[str, object] = {
            "send_address_id": "35281125",
            "draft_send_address_state": {
                "selected": True,
                "selectedText": "江苏省 常州市 武进区",
            },
            "buyer_protection_ship_time": "15天发货",
            "buyer_protection_ship_time_code": "swtfh",
            "draft_buyer_protection_pre_save_selected": True,
            "draft_buyer_protection_pre_save_selected_text": "15天发货",
            "draft_page_state_patch": {
                "buyerProtectionDesiredSteps": [
                    {"from": 1, "serviceName": "15天发货", "value": "swtfh"},
                ]
            },
            "draft_submit_response_status": 200,
            "draft_submit_trace": {
                "status": 200,
                "responseJson": {"success": True, "data": {"draftId": "draft-1"}},
                "patch": {
                    "patchSnapshot": {
                        "sendAddressId": 35281125,
                        "buyerProtectionSteps": [
                            {"from": 1, "serviceName": "15天发货", "value": "swtfh"},
                        ],
                    }
                },
            },
        }
        publish_config = {
            "submit_reapply_nonpersistent_fields": ["send_address", "buyer_protection"],
            "draft_verification": {
                "enabled": True,
                "refresh_after_save": True,
                "refresh_wait_seconds": 0,
                "buyer_protection_warning_grace_refresh_count": 0,
                "require_main_image": True,
                "require_description": True,
                "minimum_description_image_count": 2,
                "require_specs": False,
                "require_send_address": True,
                "strict_send_address_persist": True,
                "require_logistics_dimensions": False,
                "require_buyer_protection": True,
                "strict_buyer_protection_persist": True,
                "buyer_protection_default_value": "15天发货",
                "buyer_protection_expected_code": "swtfh",
                "buyer_protection_step_template": [
                    {"from": 1, "service_name": "15天发货", "service_code": "swtfh"},
                ],
                "forbidden_assist_keywords": ["第1行的发货时间为必填项"],
            },
        }

        self.browser._verify_saved_draft(publish_config, context)

        self.assertEqual(
            context.get("draft_submit_reapply_required_fields"),
            ["send_address", "buyer_protection"],
        )
        self.assertEqual(
            context.get("draft_nonpersistent_assist_messages_ignored"),
            ["买家保障 第1行的发货时间为必填项"],
        )

    def test_verify_saved_draft_rejects_wrong_persisted_send_address(self) -> None:
        self.browser.driver = FakeDriver(current_url="https://offer-new.1688.com/popular/publish.htm")
        self.browser._pause = lambda _seconds: None  # type: ignore[assignment]
        self.browser._collect_assist_messages = lambda: []  # type: ignore[assignment]
        self.browser._draft_main_image_state = lambda: {"present": True, "square": True}  # type: ignore[assignment]
        self.browser._draft_main_image_present = lambda: True  # type: ignore[assignment]
        self.browser._draft_description_present = lambda: True  # type: ignore[assignment]
        self.browser._draft_description_image_count = lambda: 0  # type: ignore[assignment]
        self.browser._collect_spec_values = lambda: {}  # type: ignore[assignment]
        self.browser._draft_selected_send_address = lambda: "999"  # type: ignore[assignment]
        self.browser._draft_logistics_dimension_values = lambda: {}  # type: ignore[assignment]
        self.browser._draft_selected_buyer_protection = lambda: ""  # type: ignore[assignment]
        self.browser._draft_selected_buyer_protection_schedule = lambda: []  # type: ignore[assignment]
        context: dict[str, object] = {"send_address_id": "123"}

        with self.assertRaisesRegex(PublishValidationError, "does not match"):
            self.browser._verify_saved_draft(
                {
                    "submit_reapply_nonpersistent_fields": ["send_address"],
                    "draft_verification": {
                        "enabled": True,
                        "refresh_after_save": False,
                        "require_main_image": True,
                        "require_description": True,
                        "require_specs": False,
                        "require_send_address": True,
                        "strict_send_address_persist": True,
                        "require_logistics_dimensions": False,
                        "require_buyer_protection": False,
                    },
                },
                context,
            )

    def test_submit_required_fields_block_empty_address_and_wrong_buyer_protection(self) -> None:
        self.browser.driver = FakeDriver(current_url="https://offer-new.1688.com/popular/publish.htm")
        self.browser._ensure_draft_send_address_selected = lambda _context: None  # type: ignore[assignment]
        self.browser._ensure_draft_required_delivery_service = lambda _context: None  # type: ignore[assignment]
        self.browser._apply_draft_page_state_patch = lambda _config, _context: None  # type: ignore[assignment]
        self.browser._ensure_draft_logistics_dimensions_before_save = lambda _config, _context: None  # type: ignore[assignment]
        self.browser._ensure_required_cat_props_before_draft_save = lambda _config, _context: None  # type: ignore[assignment]
        self.browser._ensure_buyer_protection_ship_time_before_draft_save = lambda _config, _context: None  # type: ignore[assignment]
        publish_config = {
            "draft_verification": {
                "require_send_address": True,
                "require_buyer_protection": True,
                "buyer_protection_default_value": "15天发货",
                "buyer_protection_expected_code": "swtfh",
            }
        }
        self.browser._draft_selected_send_address = lambda: ""  # type: ignore[assignment]

        with self.assertRaisesRegex(PublishValidationError, "send address is empty"):
            self.browser._prepare_and_verify_submit_required_fields(publish_config, {})

        self.browser._draft_selected_send_address = lambda: "35281125"  # type: ignore[assignment]
        self.browser._draft_selected_buyer_protection = lambda: "当日发"  # type: ignore[assignment]
        self.browser._draft_selected_buyer_protection_schedule = lambda: [  # type: ignore[assignment]
            {"from": 1, "serviceName": "当日发", "serviceCode": "drfh"}
        ]
        with self.assertRaisesRegex(PublishValidationError, "15天发货/swtfh"):
            self.browser._prepare_and_verify_submit_required_fields(publish_config, {})

    def test_submit_required_fields_accept_exact_reapplied_values(self) -> None:
        self.browser.driver = FakeDriver(current_url="https://offer-new.1688.com/popular/publish.htm")
        self.browser._ensure_draft_send_address_selected = lambda _context: None  # type: ignore[assignment]
        self.browser._ensure_draft_required_delivery_service = lambda _context: None  # type: ignore[assignment]
        self.browser._apply_draft_page_state_patch = lambda _config, _context: None  # type: ignore[assignment]
        self.browser._ensure_draft_logistics_dimensions_before_save = lambda _config, _context: None  # type: ignore[assignment]
        self.browser._ensure_required_cat_props_before_draft_save = lambda _config, _context: None  # type: ignore[assignment]
        self.browser._ensure_buyer_protection_ship_time_before_draft_save = lambda _config, _context: None  # type: ignore[assignment]
        self.browser._draft_selected_send_address = lambda: "35281125"  # type: ignore[assignment]
        self.browser._draft_selected_buyer_protection = lambda: "15天发货"  # type: ignore[assignment]
        self.browser._draft_selected_buyer_protection_schedule = lambda: [  # type: ignore[assignment]
            {"from": 1, "serviceName": "15天发货", "serviceCode": "swtfh"}
        ]
        self.browser._collect_assist_messages = lambda: []  # type: ignore[assignment]
        context: dict[str, object] = {}

        self.browser._prepare_and_verify_submit_required_fields(
            {
                "draft_verification": {
                    "require_send_address": True,
                    "require_buyer_protection": True,
                    "buyer_protection_default_value": "15天发货",
                    "buyer_protection_expected_code": "swtfh",
                }
            },
            context,
        )

        self.assertTrue(context.get("submit_required_fields_verified"))

    def test_submit_success_navigation_extracts_offer_before_retry_click(self) -> None:
        self.browser.driver = FakeDriver(
            current_url="https://offer-new.1688.com/result.htm?offerId=1068081966540"
        )
        context: dict[str, object] = {}

        detected = self.browser._submit_success_navigation_detected(
            {
                "submit_verification": {
                    "success_url_keywords": ["/result.htm"],
                }
            },
            context,
        )

        self.assertTrue(detected)
        self.assertEqual(context.get("platform_link_id"), "1068081966540")
        self.assertEqual(
            context.get("platform_link_url"),
            "https://detail.1688.com/offer/1068081966540.html",
        )

    def test_verify_saved_draft_accepts_logistics_trace_fallback_from_patch_snapshot(self) -> None:
        self.browser.driver = FakeDriver(current_url="https://offer-new.1688.com/popular/publish.htm")
        self.browser._pause = lambda _seconds: None  # type: ignore[assignment]
        self.browser._collect_assist_messages = lambda: []  # type: ignore[assignment]
        self.browser._draft_main_image_present = lambda: True  # type: ignore[assignment]
        self.browser._draft_description_present = lambda: True  # type: ignore[assignment]
        self.browser._collect_spec_values = lambda: {}  # type: ignore[assignment]
        self.browser._draft_selected_send_address = lambda: "861873672"  # type: ignore[assignment]
        self.browser._draft_logistics_dimension_values = lambda: {}  # type: ignore[assignment]
        self.browser._draft_selected_buyer_protection = lambda: ""  # type: ignore[assignment]
        self.browser._draft_selected_buyer_protection_schedule = lambda: []  # type: ignore[assignment]

        context: dict[str, object] = {
            "length_cm": "49",
            "width_cm": "18.5",
            "height_cm": "10.5",
            "weight_g": "4320",
            "draft_submit_trace": {
                "patch": {
                    "patchSnapshot": {
                        "logisticsDimensions": {
                            "length": "49",
                            "width": "18.5",
                            "height": "10.5",
                            "weight": "4320",
                        }
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
        self.assertEqual(
            context.get("draft_logistics_dimensions"),
            {
                "length": "49",
                "width": "18.5",
                "height": "10.5",
                "weight": "4320",
            },
        )
        self.assertEqual(context.get("draft_logistics_dimensions_source"), "draft_submit_trace")
        self.assertEqual(
            context.get("draft_logistics_dimensions_trace_source"),
            "draft_submit_trace.patch.patchSnapshot.logisticsDimensions",
        )

    def test_verify_saved_draft_accepts_logistics_trace_fallback_from_body_preview(self) -> None:
        self.browser.driver = FakeDriver(current_url="https://offer-new.1688.com/popular/publish.htm")
        self.browser._pause = lambda _seconds: None  # type: ignore[assignment]
        self.browser._collect_assist_messages = lambda: []  # type: ignore[assignment]
        self.browser._draft_main_image_present = lambda: True  # type: ignore[assignment]
        self.browser._draft_description_present = lambda: True  # type: ignore[assignment]
        self.browser._collect_spec_values = lambda: {}  # type: ignore[assignment]
        self.browser._draft_selected_send_address = lambda: "861873672"  # type: ignore[assignment]
        self.browser._draft_logistics_dimension_values = lambda: {}  # type: ignore[assignment]
        self.browser._draft_selected_buyer_protection = lambda: ""  # type: ignore[assignment]
        self.browser._draft_selected_buyer_protection_schedule = lambda: []  # type: ignore[assignment]

        body_preview = json.dumps(
            {
                "formValues": {
                    "officialLogistics": {
                        "skuInfo": [
                            {
                                "width": 49,
                                "height": 10.5,
                                "weight": 4320,
                            }
                        ],
                        "offerInfo": {
                            "length": 49,
                            "width": 18.5,
                            "height": 10.5,
                            "weight": 4320,
                        },
                    }
                }
            }
        )
        context: dict[str, object] = {
            "length_cm": "49",
            "width_cm": "18.5",
            "height_cm": "10.5",
            "weight_g": "4320",
            "draft_submit_trace": {
                "patchedBodyPreview": body_preview,
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
        self.assertEqual(
            context.get("draft_logistics_dimensions"),
            {
                "length": "49",
                "width": "18.5",
                "height": "10.5",
                "weight": "4320",
            },
        )
        self.assertEqual(
            context.get("draft_logistics_dimensions_trace_source"),
            "draft_submit_trace.patchedBodyPreview",
        )

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
        self.browser._is_publish_form_ready = lambda: True  # type: ignore[assignment]

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
        self.assertFalse(
            self.browser._should_retry_draft_submit(
                PublishSubmitError("draft_submit backend rejected request: 系统错误，请稍后尝试")
            )
        )
        self.assertFalse(
            self.browser._should_retry_draft_submit(
                PublishSubmitError("draft_submit backend rejected request: 数据格式不合法")
            )
        )
        self.assertTrue(
            self.browser._should_retry_draft_submit(
                PublishSubmitError("Draft button was clicked but no draftSubmit request was captured.")
            )
        )

    def test_is_draft_submit_store_blocked_error(self) -> None:
        self.assertTrue(
            self.browser._is_draft_submit_store_blocked_error(
                PublishSubmitError("draft_submit backend rejected request: 系统错误，请稍后尝试"),
            )
        )
        self.assertFalse(
            self.browser._is_draft_submit_store_blocked_error(
                PublishSubmitError("draft_submit backend rejected request: 数据格式不合法"),
            )
        )

    def test_mark_store_blocked_for_draft_save_sets_context_flags(self) -> None:
        context: dict[str, object] = {}
        self.browser._mark_store_blocked_for_draft_save(
            context,
            PublishSubmitError("draft_submit backend rejected request: 系统错误，请稍后尝试"),
        )
        self.assertTrue(context.get("shop_blocked"))
        self.assertEqual(context.get("shop_block_reason"), "draft_box_full")
        self.assertEqual(context.get("shop_skip_remaining"), True)

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
                    "quotation_type_text": "按产品规格报价",
                    "unit_text": "件",
                    "min_begin_amount": "1",
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
        self.assertEqual(payload.get("quotationTypeText"), "按产品规格报价")
        self.assertEqual(payload.get("unitText"), "件")
        self.assertEqual(payload.get("minBeginAmount"), "1")

    def test_install_draft_request_patch_prefers_uploaded_primary_url(self) -> None:
        class PatchDriver(FakeDriver):
            def __init__(self) -> None:
                super().__init__()
                self.last_script = ""

            def execute_script(self, script: str, *args: object) -> object:
                self.last_script = script
                return None

        driver = PatchDriver()
        self.browser.driver = driver
        self.browser._install_draft_request_patch(
            {"draft_request_patch": {"enabled": True}},
            {"main_image_uploaded_urls": ["https://cbu01.alicdn.com/img/ibank/new-square.jpg"]},
        )

        self.assertIn(
            "fallbackPrimaryUrls.length > 0 ? fallbackPrimaryUrls : primaryUrls",
            driver.last_script,
        )


    def test_install_draft_request_patch_includes_logistics_dimensions(self) -> None:
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
                }
            },
            {
                "title": "logistics-smoke",
                "length_cm": "49",
                "width_cm": "18.5",
                "height_cm": "10.5",
                "weight_g": "4.32",
            },
        )

        payload = driver.last_args[0]
        self.assertIsInstance(payload, dict)
        self.assertEqual(
            payload.get("logisticsDimensions"),
            {
                "length": "49",
                "width": "18.5",
                "height": "10.5",
                "weight": "4320",
            },
        )

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
        self.browser._wait_for_spec_container = lambda label: (_ for _ in ()).throw(TimeoutException())
        self.browser._apply_spec_rule(
            {
                "label": "颜色",
                "value": "白色",
            },
            {},
        )

    def test_apply_spec_rule_raises_when_required_input_missing(self) -> None:
        self.browser._wait_for_spec_container = lambda label: (_ for _ in ()).throw(TimeoutException())
        with self.assertRaises(TimeoutException):
            self.browser._apply_spec_rule(
                {
                    "label": "颜色",
                    "value": "白色",
                    "required": True,
                },
                {},
            )

    def test_apply_spec_rule_supports_multi_values_with_dynamic_inputs(self) -> None:
        class FakeSpecInput:
            def __init__(self) -> None:
                self.value = ""
                self.enter_count = 0

            def is_displayed(self) -> bool:
                return True

            def get_attribute(self, name: str) -> str:
                if name in {"disabled", "readonly"}:
                    return ""
                if name == "value":
                    return self.value
                return ""

            def send_keys(self, *args: object) -> None:
                if Keys.ENTER in args:
                    self.enter_count += 1

        class FakeSpecContainer:
            def __init__(self, inputs: list[FakeSpecInput]) -> None:
                self.inputs = inputs

            def find_elements(self, by: object, value: object) -> list[FakeSpecInput]:
                return self.inputs

        inputs = [FakeSpecInput(), FakeSpecInput()]
        container = FakeSpecContainer(inputs)
        self.browser._wait_for_spec_container = lambda label: container  # type: ignore[assignment]
        self.browser._pause = lambda _seconds: None  # type: ignore[assignment]

        def fake_fill(
            element: FakeSpecInput,
            label: str,
            value: str,
            rule: dict[str, object],
        ) -> None:
            element.value = value
            element.send_keys(Keys.ENTER)

        self.browser._fill_spec_text_value = fake_fill  # type: ignore[assignment]

        self.browser._apply_spec_rule(
            {
                "label": "棰滆壊",
                "value": "SKU-A|SKU-B",
                "multi_value": True,
            },
            {},
        )

        self.assertEqual(inputs[0].value, "SKU-A")
        self.assertEqual(inputs[1].value, "SKU-B")
        self.assertEqual(inputs[0].enter_count, 1)
        self.assertEqual(inputs[1].enter_count, 1)

    def test_fill_spec_text_value_uses_direct_chinese_entry_and_enter(self) -> None:
        class FakeSpecInput:
            def __init__(self) -> None:
                self.value = ""
                self.keys: list[object] = []

            def send_keys(self, *args: object) -> None:
                self.keys.extend(args)

        class FakeSpecDriver:
            def __init__(self) -> None:
                self.scripts: list[str] = []

            def execute_script(self, script: str, *args: object) -> object:
                self.scripts.append(script)
                return None

        input_element = FakeSpecInput()
        driver = FakeSpecDriver()
        self.browser.driver = driver
        self.browser._pause = lambda _seconds: None  # type: ignore[assignment]
        self.browser._fill_text_field = (  # type: ignore[assignment]
            lambda element, value, clear=True: setattr(element, "value", value)
        )
        verified: dict[str, object] = {}
        self.browser._verify_spec_text_value = (  # type: ignore[assignment]
            lambda label, value, wait_seconds: verified.update(
                {"label": label, "value": value, "wait_seconds": wait_seconds}
            )
        )

        self.browser._fill_spec_text_value(
            input_element,
            "颜色",
            "胡桃色",
            {"verify_wait_seconds": 1.5},
        )

        self.assertEqual(input_element.value, "胡桃色")
        self.assertIn(Keys.ENTER, input_element.keys)
        self.assertEqual(verified["label"], "颜色")
        self.assertEqual(verified["value"], "胡桃色")
        self.assertIn('.value-select-container[aria-haspopup="true"]', driver.scripts[0])

    def test_fill_spec_text_value_uses_tab_when_configured(self) -> None:
        class FakeSpecInput:
            def __init__(self) -> None:
                self.value = ""
                self.keys: list[object] = []

            def send_keys(self, *args: object) -> None:
                self.keys.extend(args)

        class FakeSpecDriver:
            def execute_script(self, script: str, *args: object) -> object:
                return None

        input_element = FakeSpecInput()
        self.browser.driver = FakeSpecDriver()
        self.browser._pause = lambda _seconds: None  # type: ignore[assignment]
        self.browser._fill_text_field = (  # type: ignore[assignment]
            lambda element, value, clear=True: setattr(element, "value", value)
        )
        self.browser._verify_spec_text_value = lambda *args, **kwargs: None  # type: ignore[assignment]

        self.browser._fill_spec_text_value(
            input_element,
            "颜色",
            "胡桃色",
            {"commit_key": "tab"},
        )

        self.assertIn(Keys.TAB, input_element.keys)
        self.assertNotIn(Keys.ENTER, input_element.keys)

    def test_read_spec_text_state_only_accepts_committed_values(self) -> None:
        class FakeSpecDriver:
            def __init__(self) -> None:
                self.script = ""

            def execute_script(self, script: str, *args: object) -> object:
                self.script = script
                return {
                    "values": ["胡桃色"],
                    "exact_match": True,
                    "required_warning": False,
                }

        driver = FakeSpecDriver()
        self.browser.driver = driver

        state = self.browser._read_spec_text_state(object(), "胡桃色")

        self.assertTrue(state["exact_match"])
        self.assertIn(".value-select-item:not(.resident) input", driver.script)

    def test_verify_spec_text_value_rejects_required_warning(self) -> None:
        self.browser._wait_for_spec_container = lambda label: object()  # type: ignore[assignment]
        self.browser._read_spec_text_state = lambda container, value: {  # type: ignore[assignment]
            "values": [value],
            "exact_match": True,
            "required_warning": True,
        }

        with self.assertRaisesRegex(PublishValidationError, "required-field warning"):
            self.browser._verify_spec_text_value("颜色", "胡桃色", wait_seconds=0)

    def test_apply_required_spec_rule_rejects_missing_source_value(self) -> None:
        with self.assertRaisesRegex(PublishValidationError, "has no source value"):
            self.browser._apply_spec_rule(
                {"label": "尺寸", "source": "size", "required": True},
                {"size": ""},
            )

    def test_resolve_profile_rule_value_prefers_source_candidates(self) -> None:
        value = self.browser._resolve_profile_rule_value(
            {
                "source_candidates": ["company_sku_names", "color"],
                "source": "outer_sku",
                "default_value": "DEFAULT",
            },
            {
                "company_sku_names": "SKU-A|SKU-B",
                "color": "原木色",
                "outer_sku": "OUTER-001",
            },
        )
        self.assertEqual(value, "SKU-A|SKU-B")

    def test_1688_color_spec_prefers_structured_color_value(self) -> None:
        config = json.loads(
            (PROJECT_ROOT / "config" / "platforms" / "1688.json").read_text(encoding="utf-8")
        )
        spec_step = next(
            step
            for step in config["publish"]["steps"]
            if step.get("name") == "spec_values"
        )

        for profile_name in ("bedside_table", "*"):
            color_rule = next(
                rule
                for rule in spec_step["profiles"][profile_name]["rules"]
                if rule.get("label") == "颜色"
            )
            self.assertEqual(color_rule["source_candidates"][0], "color")
            self.assertTrue(color_rule["required"])
            self.assertEqual(color_rule["commit_key"], "tab")
            size_rule = next(
                rule
                for rule in spec_step["profiles"][profile_name]["rules"]
                if rule.get("label") == "尺寸"
            )
            self.assertTrue(size_rule["required"])
            self.assertEqual(size_rule["commit_key"], "tab")

        verification = config["publish"]["draft_verification"]
        self.assertEqual(verification["required_spec_labels"], ["颜色", "尺寸"])
        self.assertTrue(verification["require_title"])
        self.assertEqual(verification["minimum_description_image_count"], 1)

    def test_split_spec_rule_values_supports_custom_pattern(self) -> None:
        values = self.browser._split_spec_rule_values(
            "SKU-A|SKU-B，SKU-C;SKU-D\nSKU-E|SKU-A",
            {
                "multi_value": True,
                "split_pattern": r"\s*(?:\||,|，|;|；|\n)+\s*",
            },
        )
        self.assertEqual(values, ["SKU-A", "SKU-B", "SKU-C", "SKU-D", "SKU-E"])

    def test_resolve_company_sku_names_supports_list_values(self) -> None:
        value = self.browser._resolve_company_sku_names(
            {
                "company_sku_names": ["SKU-A", " SKU-B "],
                "outer_sku": "OUTER-001",
            }
        )
        self.assertEqual(value, "SKU-A|SKU-B")

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

    def test_extract_required_labels_from_assist_messages_for_colon_empty_messages(self) -> None:
        labels = self.browser._extract_required_labels_from_assist_messages(
            [
                "产品属性 材质：“材质” 不能为空 附加功能：“附加功能” 不能为空",
                "材质：“材质” 不能为空",
                "附加功能：“附加功能” 不能为空",
            ]
        )
        self.assertIn("材质", labels)
        self.assertIn("附加功能", labels)

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
