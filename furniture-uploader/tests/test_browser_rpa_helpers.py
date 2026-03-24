from __future__ import annotations

import base64
import sys
import tempfile
import unittest
from pathlib import Path

from selenium.common.exceptions import TimeoutException


PROJECT_ROOT = Path(__file__).resolve().parents[1]
RPA_ROOT = PROJECT_ROOT / "rpa"
if str(RPA_ROOT) not in sys.path:
    sys.path.insert(0, str(RPA_ROOT))

from browser_rpa import BrowserRPA


class FakeDriver:
    def __init__(self, *, current_url: str = "", page_source: str = "") -> None:
        self.current_url = current_url
        self.page_source = page_source

    def execute_script(self, script: str, *args: object) -> str:
        return ""


class BrowserRPAHelperTests(unittest.TestCase):
    def setUp(self) -> None:
        self.browser = BrowserRPA({}, PROJECT_ROOT)

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


if __name__ == "__main__":
    unittest.main()
