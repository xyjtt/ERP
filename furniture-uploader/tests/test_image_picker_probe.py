from __future__ import annotations

from contextlib import contextmanager
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_ROOT = PROJECT_ROOT / "scripts"
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

import probe_1688_image_picker as probe


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
    def test_new_product_is_not_treated_as_the_current_draft_page(self) -> None:
        self.assertFalse(
            probe.is_same_pending_draft_page(
                "",
                "https://work.1688.com/?_path_=sellerPro/2017sellerbase_offer",
            )
        )
        self.assertTrue(
            probe.is_same_pending_draft_page(
                "draft-123",
                "https://offer-new.1688.com/popular/publish.htm?draftId=draft-123",
            )
        )

    def test_collect_page_diagnostics_captures_failure_context(self) -> None:
        driver = FakeDriver()
        browser = SimpleNamespace(driver=driver)
        with tempfile.TemporaryDirectory() as temp_dir:
            evidence = str(Path(temp_dir) / "capacity.json")

            diagnostics = probe.collect_page_diagnostics(browser, evidence)

        self.assertTrue(diagnostics["available"])
        self.assertEqual(diagnostics["tab_count"], 1)
        self.assertEqual(diagnostics["ready_state"], "complete")
        self.assertTrue(diagnostics["screenshot_saved"])
        self.assertTrue(driver.screenshot_path.endswith("capacity.png"))

    def test_probe_uses_account_bound_session_and_never_saves_or_submits(self) -> None:
        class ProbeBrowser:
            def __init__(self) -> None:
                self.driver = SimpleNamespace(
                    current_url="https://work.1688.com/",
                    get=lambda url: setattr(self.driver, "current_url", url),
                )
                self.browser_config = {"page_load_wait_seconds": 0}

            def _pause(self, _seconds: float) -> None:
                return None

            def _is_publish_form_ready(self) -> bool:
                return False

            def _wait_for_publish_runtime_ready(self, *, timeout_seconds: float) -> None:
                self.timeout_seconds = timeout_seconds

            def _run_picker_upload(self, step, selector, images, context) -> None:
                self.step = step
                self.selector = selector
                self.images = images
                context["image_probe_uploaded_urls"] = [
                    "https://img.example/1.jpg",
                    "https://img.example/2.jpg",
                ]

        session_calls: list[dict] = []
        browser = ProbeBrowser()

        @contextmanager
        def session(**kwargs):
            session_calls.append(kwargs)
            yield (
                browser,
                SimpleNamespace(cdp_port=9306),
                SimpleNamespace(account_key="muke_lixiang", shop_name="木刻理想"),
            )

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            payload_path = root / "payload.json"
            output_path = root / "probe.json"
            payload_path.write_text(
                json.dumps(
                    {
                        "shop": {"account_key": "muke_lixiang", "shop_name": "木刻理想"},
                        "workflow": {"pending_draft_id": "draft-1"},
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            platform_config = {
                "publish": {
                    "steps": [
                        {
                            "name": "detail_images",
                            "fallback_picker_selector": {"by": "css", "value": ".picker"},
                        }
                    ]
                }
            }
            with (
                patch.object(probe, "open_account_bound_listing_browser", side_effect=session),
                patch.object(
                    probe,
                    "build_product_record",
                    return_value=SimpleNamespace(raw={"detail_images": "a.jpg|b.jpg"}),
                ),
                patch.object(
                    probe,
                    "load_json_with_local_override",
                    side_effect=[platform_config, {"browser": {}}],
                ),
                patch.object(
                    probe,
                    "resolve_1688_publish_url",
                    return_value=("https://offer-new.1688.com/popular/publish.htm", "cat-1"),
                ),
                patch.object(
                    sys,
                    "argv",
                    [
                        "probe_1688_image_picker.py",
                        "--payload",
                        str(payload_path),
                        "--evidence-output",
                        str(output_path),
                    ],
                ),
            ):
                return_code = probe.main()

            evidence = json.loads(output_path.read_text(encoding="utf-8"))
            self.assertEqual(return_code, 0)
            self.assertEqual(evidence["account_key"], "muke_lixiang")
            self.assertFalse(evidence["draft_saved"])
            self.assertFalse(evidence["offer_submitted"])
            self.assertEqual(session_calls[0]["expected_cdp_port"], 9306)
            self.assertEqual(session_calls[0]["component"], "erp-listing-image-probe")
            self.assertTrue(browser.step["capture_uploaded_urls_only"])


if __name__ == "__main__":
    unittest.main()
