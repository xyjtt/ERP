from __future__ import annotations

from contextlib import contextmanager
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


@contextmanager
def fake_account_browser_session(**kwargs):
    if kwargs["login_timeout_seconds"] != 300:
        raise AssertionError("unexpected login timeout")
    progress_callback = kwargs.get("progress_callback")
    if progress_callback is not None:
        progress_callback("login_subprocess_starting")
    browser = kwargs["browser_class"]({}, Path.cwd())
    browser.open()
    if progress_callback is not None:
        progress_callback("browser_attached")
    try:
        yield (
            browser,
            SimpleNamespace(cdp_port=9306),
            SimpleNamespace(account_key="muke_lixiang", shop_name="木刻理想"),
        )
    finally:
        browser.close()


class InspectSavedDraftTests(unittest.TestCase):
    def test_offer_target_requires_exact_numeric_detail_url(self) -> None:
        url = "https://detail.1688.com/offer/1072868453052.html"
        self.assertEqual(
            inspector._resolve_offer_target(url, "1072868453052"),
            (url, "1072868453052"),
        )
        self.assertEqual(
            inspector._resolve_offer_target("", "1072868453052"),
            (url, "1072868453052"),
        )
        with self.assertRaisesRegex(ValueError, "exact detail"):
            inspector._resolve_offer_target(
                "https://example.com/offer/1072868453052.html",
                "1072868453052",
            )
        with self.assertRaisesRegex(ValueError, "exact detail"):
            inspector._resolve_offer_target(
                "https://detail.1688.com/offer/1072868453052.html?from=test",
                "1072868453052",
            )

    def test_offer_page_inspection_is_read_only_and_matches_identity(self) -> None:
        class OfferDriver:
            current_url = ""

            def get(self, url: str) -> None:
                self.current_url = url

            def execute_script(self, _script: str) -> dict[str, object]:
                return {
                    "current_url": self.current_url,
                    "page_title": "Target product",
                    "ready_state": "complete",
                    "body_text": "木刻理想 Target product",
                    "product_title": "Target product",
                    "seller_text": "木刻理想",
                }

            def save_screenshot(self, _path: str) -> bool:
                return True

        browser = SimpleNamespace(driver=OfferDriver(), _pause=lambda _seconds: None)
        evidence = inspector._inspect_offer_detail_page(
            browser,
            offer_url="https://detail.1688.com/offer/1072868453052.html",
            expected_offer_id="1072868453052",
            expected_title="Target product",
            expected_shop="木刻理想",
            output_path=Path("inspection.json"),
        )

        self.assertEqual(evidence["status"], "passed")
        self.assertTrue(evidence["offer_id_matched"])
        self.assertTrue(evidence["title_matched"])
        self.assertTrue(evidence["read_only"])

    def test_offer_page_stops_on_auth_challenge(self) -> None:
        class AuthDriver:
            current_url = "https://login.1688.com/member/signin.htm"

            def get(self, _url: str) -> None:
                return None

            def execute_script(self, _script: str) -> dict[str, object]:
                return {
                    "current_url": self.current_url,
                    "page_title": "Login",
                    "ready_state": "complete",
                    "body_text": "请登录并完成安全验证",
                    "product_title": "",
                    "seller_text": "",
                }

            def save_screenshot(self, _path: str) -> bool:
                return True

        evidence = inspector._inspect_offer_detail_page(
            SimpleNamespace(driver=AuthDriver(), _pause=lambda _seconds: None),
            offer_url="https://detail.1688.com/offer/1072868453052.html",
            expected_offer_id="1072868453052",
            expected_title="Target product",
            expected_shop="木刻理想",
            output_path=Path("inspection.json"),
        )

        self.assertEqual(evidence["status"], "blocked_auth")
        self.assertTrue(evidence["auth_challenge"])

    def test_offer_page_does_not_use_public_seller_name_as_task_identity(self) -> None:
        class OfferDriver:
            current_url = ""

            def get(self, url: str) -> None:
                self.current_url = url

            def execute_script(self, _script: str) -> dict[str, object]:
                return {
                    "current_url": self.current_url,
                    "page_title": "Target product - 阿里巴巴",
                    "ready_state": "complete",
                    "body_text": "常州洁秋家居有限公司 Target product",
                    "product_title": "常州洁秋家居有限公司",
                    "seller_text": "常州洁秋家居有限公司",
                }

            def save_screenshot(self, _path: str) -> bool:
                return True

        evidence = inspector._inspect_offer_detail_page(
            SimpleNamespace(driver=OfferDriver(), _pause=lambda _seconds: None),
            offer_url="https://detail.1688.com/offer/1072868453052.html",
            expected_offer_id="1072868453052",
            expected_title="Target product",
            expected_shop="木刻理想",
            output_path=Path("inspection.json"),
        )

        self.assertEqual(evidence["status"], "passed")
        self.assertEqual(evidence["product_title"], "Target product")
        self.assertEqual(evidence["identity_source"], "account_bound_payload")
        self.assertFalse(evidence["identity_mismatch"])
        self.assertFalse(evidence["seller_display_matches_task_shop"])

    def test_inspection_draft_id_must_match_unique_payload_draft(self) -> None:
        payload = {
            "workflow": {
                "pending_draft_id": "draft-1",
                "draft": {"draft_id": "draft-1"},
            }
        }
        self.assertEqual(
            inspector._resolve_inspection_draft_id(payload, "draft-1"),
            "draft-1",
        )
        with self.assertRaisesRegex(ValueError, "must match"):
            inspector._resolve_inspection_draft_id(payload, "draft-2")

    def test_inspection_requires_one_existing_payload_draft(self) -> None:
        with self.assertRaisesRegex(ValueError, "found 0"):
            inspector._resolve_inspection_draft_id({"workflow": {}}, "draft-1")

    def test_expected_shop_comes_from_payload_and_rejects_override(self) -> None:
        payload = {"shop": {"shop_name": "木刻理想"}}
        self.assertEqual(inspector._resolve_expected_shop(payload, ""), "木刻理想")
        self.assertEqual(
            inspector._resolve_expected_shop(payload, "木刻理想"),
            "木刻理想",
        )
        with self.assertRaisesRegex(ValueError, "must match"):
            inspector._resolve_expected_shop(payload, "其他店铺")

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

    def test_nonpersistent_contract_field_is_explicitly_marked_for_submit_reapply(self) -> None:
        checks, outcomes = inspector._classify_field_outcomes(
            {
                "title": True,
                "delivery_service": False,
            },
            submit_reapply_fields={"delivery_service"},
            submit_reapply_contract_sha256="a" * 64,
        )

        self.assertEqual(checks, {"title": True, "delivery_service": True})
        self.assertEqual(outcomes["title"], {"status": "persisted"})
        self.assertEqual(
            outcomes["delivery_service"],
            {
                "status": "submit_reapply_required",
                "contract_sha256": "a" * 64,
            },
        )

    def test_missing_field_outside_reapply_contract_remains_failed(self) -> None:
        checks, outcomes = inspector._classify_field_outcomes(
            {"main_image_count": False},
            submit_reapply_fields={"delivery_service"},
            submit_reapply_contract_sha256="a" * 64,
        )

        self.assertFalse(checks["main_image_count"])
        self.assertEqual(outcomes["main_image_count"], {"status": "failed"})

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
                        "shop": {
                            "account_key": "muke_lixiang",
                            "shop_name": "木刻理想",
                        },
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
                patch.object(
                    inspector,
                    "open_account_bound_listing_browser",
                    side_effect=fake_account_browser_session,
                ),
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
            progress = json.loads(
                Path(f"{output_path}.progress.json").read_text(encoding="utf-8")
            )
            self.assertEqual(exit_code, 3)
            self.assertEqual(evidence["status"], "unavailable")
            self.assertEqual(evidence["current_url"], "https://offer.1688.com/error")
            self.assertFalse(evidence["sell_publish_sdk_present"])
            self.assertFalse(evidence["draft_saved"])
            self.assertFalse(evidence["offer_submitted"])
            self.assertEqual(progress["status"], "completed")
            self.assertEqual(progress["stage"], "inspection_unavailable")
            self.assertEqual(progress["exit_code"], 3)
            self.assertFalse(progress["draft_saved"])
            self.assertFalse(progress["offer_submitted"])

    def test_management_entry_is_recorded_when_runtime_is_unavailable(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            payload_path = root / "payload.json"
            output_path = root / "inspection.json"
            payload_path.write_text(
                json.dumps(
                    {
                        "task_id": "task-1",
                        "shop": {
                            "account_key": "muke_lixiang",
                            "shop_name": "木刻理想",
                        },
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
                patch.object(
                    inspector,
                    "open_account_bound_listing_browser",
                    side_effect=fake_account_browser_session,
                ),
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

    def test_offer_only_inspection_does_not_require_management_draft_row(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            payload_path = root / "payload.json"
            output_path = root / "inspection.json"
            payload_path.write_text(
                json.dumps(
                    {
                        "task_id": "task-1",
                        "shop": {
                            "account_key": "muke_lixiang",
                            "shop_name": "木刻理想",
                        },
                        "workflow": {"draft": {"draft_id": "draft-1"}},
                        "images": {"detail_urls": []},
                        "product": {"selected_title": "Target product"},
                        "pricing": {"publish_price": "1.0"},
                        "inventory": {"quantity": 999},
                        "attributes": {"color": "black", "size": "small"},
                        "logistics": {},
                    }
                ),
                encoding="utf-8",
            )
            offer_evidence = {
                "status": "passed",
                "current_url": "https://detail.1688.com/offer/1072868453052.html",
                "offer_id_matched": True,
                "title_matched": True,
                "identity_mismatch": False,
            }

            with (
                patch.object(inspector, "SkuOfflineBrowser", FakeBrowser),
                patch.object(
                    inspector,
                    "open_account_bound_listing_browser",
                    side_effect=fake_account_browser_session,
                ),
                patch.object(inspector, "load_json_with_local_override", return_value={}),
                patch.object(
                    inspector,
                    "_inspect_offer_detail_page",
                    return_value=offer_evidence,
                ) as inspect_offer,
                patch.object(inspector, "_open_draft_from_management") as open_draft,
                patch.object(inspector, "_wait_for_inspection_runtime") as wait_runtime,
                patch.object(
                    sys,
                    "argv",
                    [
                        "inspect_1688_saved_draft.py",
                        "--payload",
                        str(payload_path),
                        "--output",
                        str(output_path),
                        "--offer-id",
                        "1072868453052",
                        "--offer-only",
                    ],
                ),
            ):
                exit_code = inspector.main()

            evidence = json.loads(output_path.read_text(encoding="utf-8"))
            self.assertEqual(exit_code, 0)
            self.assertEqual(evidence["status"], "passed")
            self.assertEqual(evidence["entry"]["entry_mode"], "published_offer_detail")
            self.assertTrue(evidence["checks"]["offer_exists"])
            self.assertFalse(evidence["draft_saved"])
            self.assertFalse(evidence["offer_submitted"])
            inspect_offer.assert_called_once()
            open_draft.assert_not_called()
            wait_runtime.assert_not_called()


if __name__ == "__main__":
    unittest.main()
