from __future__ import annotations

import sys
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
RPA_ROOT = PROJECT_ROOT / "rpa"
if str(RPA_ROOT) not in sys.path:
    sys.path.insert(0, str(RPA_ROOT))

from webdriver_factory import extract_version_from_text, resolve_browser_type


class WebdriverFactoryTests(unittest.TestCase):
    def test_resolve_browser_type_prefers_explicit_value(self) -> None:
        self.assertEqual(resolve_browser_type(browser_type="edge"), "edge")
        self.assertEqual(resolve_browser_type(browser_type="chrome"), "chrome")

    def test_resolve_browser_type_infers_from_binary_name(self) -> None:
        self.assertEqual(
            resolve_browser_type(browser_binary_path="C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe"),
            "edge",
        )
        self.assertEqual(
            resolve_browser_type(browser_binary_path="C:/Program Files/Google/Chrome/Application/chrome.exe"),
            "chrome",
        )

    def test_extract_version_from_text_reads_edge_version(self) -> None:
        self.assertEqual(
            extract_version_from_text('{"Browser":"Edg/146.0.3856.72"}'),
            "146.0.3856.72",
        )
        self.assertEqual(
            extract_version_from_text("Microsoft Edge Edg/145.1.2.3"),
            "145.1.2.3",
        )


if __name__ == "__main__":
    unittest.main()
