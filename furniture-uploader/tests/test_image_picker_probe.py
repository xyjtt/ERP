from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_ROOT = PROJECT_ROOT / "scripts"
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

from probe_1688_image_picker import collect_page_diagnostics


class FakeDriver:
    current_url = "https://offer-new.1688.com/popular/publish.htm?operator=new"
    title = "publish"
    window_handles = ["business-tab"]

    def __init__(self) -> None:
        self.screenshot_path = ""

    def execute_script(self, _script: str):
        return {
            "ready_state": "complete",
            "body_text": "page body",
            "selector_counts": {"#saveDraftButton": 0},
            "frames": [],
        }

    def save_screenshot(self, path: str) -> bool:
        self.screenshot_path = path
        return True


class ImagePickerProbeTests(unittest.TestCase):
    def test_collect_page_diagnostics_captures_failure_context(self) -> None:
        driver = FakeDriver()
        browser = SimpleNamespace(driver=driver)
        with tempfile.TemporaryDirectory() as temp_dir:
            evidence = str(Path(temp_dir) / "capacity.json")

            diagnostics = collect_page_diagnostics(browser, evidence)

        self.assertTrue(diagnostics["available"])
        self.assertEqual(diagnostics["tab_count"], 1)
        self.assertEqual(diagnostics["ready_state"], "complete")
        self.assertTrue(diagnostics["screenshot_saved"])
        self.assertTrue(driver.screenshot_path.endswith("capacity.png"))


if __name__ == "__main__":
    unittest.main()
