from __future__ import annotations

import base64
import sys
import tempfile
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
RPA_ROOT = PROJECT_ROOT / "rpa"
if str(RPA_ROOT) not in sys.path:
    sys.path.insert(0, str(RPA_ROOT))

from browser_rpa import BrowserRPA


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
