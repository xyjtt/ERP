from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from selenium.common.exceptions import TimeoutException


SCRIPT_ROOT = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPT_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPT_ROOT))

import inspect_1688_saved_draft as inspector  # noqa: E402


class FakeDriver:
    current_url = "https://offer.1688.com/error"

    def get(self, _url: str) -> None:
        return None

    def refresh(self) -> None:
        return None

    def execute_script(self, _script: str) -> dict[str, object]:
        return {
            "current_url": self.current_url,
            "page_title": "Draft unavailable",
            "ready_state": "complete",
            "sell_publish_sdk_present": False,
            "body_text": "The draft page is unavailable.",
        }

    def save_screenshot(self, _path: str) -> bool:
        return True


class FakeBrowser:
    def __init__(self, *_args: object, **_kwargs: object) -> None:
        self.driver = FakeDriver()
        self.closed = False

    def open(self) -> None:
        return None

    def close(self) -> None:
        self.closed = True

    def _pause(self, _seconds: float) -> None:
        return None

    def _wait_for_publish_runtime_ready(self, *, timeout_seconds: float) -> None:
        self.timeout_seconds = timeout_seconds
        raise TimeoutException("runtime unavailable")


class InspectSavedDraftTests(unittest.TestCase):
    def test_expected_main_image_count_caps_at_four(self) -> None:
        self.assertEqual(
            inspector._expected_main_image_count(
                {"images": {"main_urls": ["1", "2", "3", "4", "5"]}}
            ),
            4,
        )

    def test_buyer_protection_requires_persisted_name_and_code(self) -> None:
        schedule = [
            {"from": 1, "serviceName": "24小时发货", "serviceCode": "essxsfh"},
        ]
        self.assertTrue(
            inspector._buyer_protection_matches(
                "24小时发货",
                schedule,
                expected_value="24小时发货",
                expected_code="essxsfh",
            )
        )
        self.assertFalse(
            inspector._buyer_protection_matches(
                "",
                schedule,
                expected_value="24小时发货",
                expected_code="essxsfh",
            )
        )
        self.assertFalse(
            inspector._buyer_protection_matches(
                "24小时发货",
                [{"from": 1, "serviceName": "24小时发货", "serviceCode": "wrong"}],
                expected_value="24小时发货",
                expected_code="essxsfh",
            )
        )

    def test_boot_network_probe_fails_closed_when_cdp_is_unavailable(self) -> None:
        browser = SimpleNamespace(driver=FakeDriver())
        self.assertFalse(inspector._install_draft_boot_network_probe(browser))
        self.assertEqual(inspector._collect_draft_boot_network_records(browser), [])

    def test_publish_url_override_requires_matching_draft_and_category(self) -> None:
        url = (
            "https://offer-new.1688.com/popular/publish.htm?"
            "catId=122942001&operator=new&draftId=draft-1"
        )
        self.assertEqual(inspector._validate_publish_url_override(url, "draft-1"), url)
        with self.assertRaisesRegex(ValueError, "expected draft and category"):
            inspector._validate_publish_url_override(
                "https://offer-new.1688.com/popular/publish.htm?catId=122942001&draftId=other",
                "draft-1",
            )
        with self.assertRaisesRegex(ValueError, "expected draft and category"):
            inspector._validate_publish_url_override(
                "https://example.com/popular/publish.htm?catId=122942001&draftId=draft-1",
                "draft-1",
            )
        with self.assertRaisesRegex(ValueError, "expected draft and category"):
            inspector._validate_publish_url_override(
                "https://offer-new.1688.com/popular/publish.htm?draftId=draft-1",
                "draft-1",
            )

    def test_management_inspection_gate_does_not_require_new_listing_clearance(self) -> None:
        inspector._assert_management_inspection_gate(
            {
                "status": "blocked",
                "tab_all": True,
                "shop_identity": {"matched": True},
                "draft_identity_evidence": {"status": "passed"},
            }
        )

    def test_management_inspection_gate_requires_target_draft(self) -> None:
        with self.assertRaisesRegex(ValueError, "draft identity gate"):
            inspector._assert_management_inspection_gate(
                {
                    "tab_all": True,
                    "shop_identity": {"matched": True},
                    "draft_identity_evidence": {"status": "blocked"},
                }
            )

    def test_fatal_sys_error_page_fails_fast(self) -> None:
        class SysErrorDriver:
            def execute_script(self, _script: str):
                return {
                    "ready_state": "complete",
                    "body_text": "出错啦！ 系统错误,请稍后重试 错误码：SYS_ERROR",
                }

        with self.assertRaisesRegex(TimeoutException, "SYS_ERROR"):
            inspector._raise_for_fatal_runtime_page(SimpleNamespace(driver=SysErrorDriver()))

    def test_inspection_runtime_poll_stops_on_fatal_page(self) -> None:
        class DelayedSysErrorDriver:
            calls = 0

            def execute_script(self, _script: str):
                self.calls += 1
                return {
                    "ready_state": "complete",
                    "body_text": (
                        "loading"
                        if self.calls == 1
                        else "出错啦！ 系统错误,请稍后重试 错误码：SYS_ERROR"
                    ),
                }

        browser = SimpleNamespace(
            driver=DelayedSysErrorDriver(),
            _pause=lambda _seconds: None,
            _wait_for_publish_runtime_ready=lambda **_kwargs: self.fail("must fail fast"),
        )
        with self.assertRaisesRegex(TimeoutException, "SYS_ERROR"):
            inspector._wait_for_inspection_runtime(browser, timeout_seconds=180)

    def test_runtime_timeout_writes_read_only_unavailable_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            payload_path = root / "payload.json"
            output_path = root / "inspection.json"
            payload_path.write_text(
                json.dumps(
                    {
                        "task_id": "task-1",
                        "workflow": {"draft": {"draft_id": "draft-1"}},
                        "images": {"detail_urls": []},
                        "product": {"selected_title": "title"},
                        "pricing": {"publish_price": "1.0"},
                        "inventory": {"quantity": 999},
                        "attributes": {"color": "black", "size": "small"},
                        "logistics": {},
                    }
                ),
                encoding="utf-8",
            )

            with (
                patch.object(inspector, "BrowserRPA", FakeBrowser),
                patch.object(inspector, "load_json_with_local_override", return_value={"browser": {}}),
                patch.object(
                    inspector,
                    "resolve_1688_publish_url",
                    return_value=("https://offer.1688.com/draft", "category-1"),
                ),
                patch.object(
                    sys,
                    "argv",
                    [
                        "inspect_1688_saved_draft.py",
                        "--payload",
                        str(payload_path),
                        "--output",
                        str(output_path),
                    ],
                ),
            ):
                exit_code = inspector.main()

            evidence = json.loads(output_path.read_text(encoding="utf-8"))
            self.assertEqual(exit_code, 3)
            self.assertEqual(evidence["status"], "unavailable")
            self.assertEqual(evidence["current_url"], "https://offer.1688.com/error")
            self.assertFalse(evidence["sell_publish_sdk_present"])
            self.assertFalse(evidence["draft_saved"])
            self.assertFalse(evidence["offer_submitted"])

    def test_management_entry_is_recorded_when_runtime_is_unavailable(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            payload_path = root / "payload.json"
            output_path = root / "inspection.json"
            payload_path.write_text(
                json.dumps(
                    {
                        "task_id": "task-1",
                        "workflow": {"draft": {"draft_id": "draft-1"}},
                        "images": {"detail_urls": []},
                        "product": {
                            "sku_code": "SKU-1",
                            "spu_code": "SPU-1",
                            "selected_title": "title",
                        },
                        "pricing": {"publish_price": "1.0"},
                        "inventory": {"quantity": 999},
                        "attributes": {"color": "black", "size": "small"},
                        "logistics": {},
                    }
                ),
                encoding="utf-8",
            )
            management_entry = {
                "entry_mode": "management_draft_link_click",
                "clicked_href": "https://offer.1688.com/draft?offerDraftId=draft-1",
            }

            with (
                patch.object(inspector, "SkuOfflineBrowser", FakeBrowser),
                patch.object(inspector, "load_json_with_local_override", return_value={"browser": {}}),
                patch.object(inspector, "_open_draft_from_management", return_value=management_entry),
                patch.object(
                    inspector,
                    "resolve_1688_publish_url",
                    return_value=("https://offer.1688.com/draft", "category-1"),
                ) as resolve_publish_url,
                patch.object(
                    sys,
                    "argv",
                    [
                        "inspect_1688_saved_draft.py",
                        "--payload",
                        str(payload_path),
                        "--output",
                        str(output_path),
                        "--open-from-management",
                    ],
                ),
            ):
                exit_code = inspector.main()

            evidence = json.loads(output_path.read_text(encoding="utf-8"))
            self.assertEqual(exit_code, 3)
            resolve_publish_url.assert_not_called()
            self.assertEqual(evidence["entry"], management_entry)
            self.assertEqual(evidence["requested_url"], management_entry["clicked_href"])


if __name__ == "__main__":
    unittest.main()
