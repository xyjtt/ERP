from __future__ import annotations

from pathlib import Path
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import patch


RPA_ROOT = Path(__file__).resolve().parents[1] / "rpa"
if str(RPA_ROOT) not in sys.path:
    sys.path.insert(0, str(RPA_ROOT))

from listing_browser_session import (  # noqa: E402
    build_account_bound_browser_config,
    open_account_bound_listing_browser,
    resolve_listing_browser_identity,
)


class FakeBrowser:
    def __init__(self, config, project_root) -> None:
        self.config = config
        self.project_root = project_root
        self.events: list[str] = []

    def open(self) -> None:
        self.events.append("open")

    def close(self) -> None:
        self.events.append("close")

    def set_runtime_action_guard(self, guard) -> None:
        self.runtime_action_guard = guard


class RecordingContext:
    def __init__(self, events: list[str], name: str) -> None:
        self.events = events
        self.name = name

    def __enter__(self):
        self.events.append(f"{self.name}_enter")
        return self

    def __exit__(self, *_args) -> None:
        self.events.append(f"{self.name}_exit")


class RecordingLeaseGuard(RecordingContext):
    def __init__(self, events: list[str]) -> None:
        super().__init__(events, "lease")
        self.closers = []

    def assert_active(self) -> None:
        self.events.append("lease_active")

    def register_owned_browser_closer(self, closer) -> None:
        self.closers.append(closer)


class ListingBrowserSessionTests(unittest.TestCase):
    def payload(self) -> dict:
        return {
            "shop": {
                "account_key": "muke_lixiang",
                "shop_name": "木刻理想",
            }
        }

    def binding(self, *, port: int = 9306):
        return SimpleNamespace(
            account_key="muke_lixiang",
            cdp_port=port,
            browser_profile_dir="D:/profiles/muke_lixiang",
        )

    def test_identity_preserves_payload_business_values(self) -> None:
        identity = resolve_listing_browser_identity(
            self.payload(),
            expected_account_key="muke_lixiang",
            expected_shop_name="木刻理想",
        )
        self.assertEqual(identity.account_key, "muke_lixiang")
        self.assertEqual(identity.shop_name, "木刻理想")

    def test_identity_mismatch_fails_closed(self) -> None:
        with self.assertRaisesRegex(ValueError, "account_key does not match"):
            resolve_listing_browser_identity(
                self.payload(),
                expected_account_key="another_account",
            )
        with self.assertRaisesRegex(ValueError, "shop_name does not match"):
            resolve_listing_browser_identity(
                self.payload(),
                expected_shop_name="平台显示名称",
            )

    def test_browser_config_overrides_generic_debug_port(self) -> None:
        result = build_account_bound_browser_config(
            {
                "browser": {
                    "type": "chrome",
                    "browser_type": "chrome",
                    "debugger_address": "127.0.0.1:9222",
                    "headless": False,
                }
            },
            self.binding(),
        )
        self.assertEqual(result["debugger_address"], "127.0.0.1:9306")
        self.assertEqual(result["browser_type"], "edge")
        self.assertFalse(result["headless"])

    def test_session_logs_in_then_attaches_and_releases_owned_runtime(self) -> None:
        events: list[str] = []

        def login(*args, **kwargs):
            events.append("login")
            self.assertEqual(args[1], "muke_lixiang")
            self.assertEqual(args[2], "木刻理想")
            self.assertTrue(kwargs["keep_browser_open"])
            return {"status": "success", "browser_runtime_preserved": True}

        class RecordingBrowser(FakeBrowser):
            def open(inner_self) -> None:
                events.append("open")
                super().open()

            def close(inner_self) -> None:
                events.append("close")
                super().close()

        guard = RecordingLeaseGuard(events)

        with (
            patch("listing_browser_session.resolve_executor_binding", return_value=self.binding()),
            patch("listing_browser_session.resolve_stop_sale_app_config", return_value=object()),
            patch("listing_browser_session.RuntimeLeaseRepository", return_value=object()),
            patch("listing_browser_session.RuntimeLeaseGuard", return_value=guard),
            patch("listing_browser_session.resolve_build_sha", return_value="a" * 40),
            patch(
                "listing_browser_session.build_listing_tool_account_lock",
                return_value=RecordingContext(events, "lock"),
            ),
            patch("listing_browser_session.ensure_1688_authenticated_session", side_effect=login),
            patch(
                "listing_browser_session.stop_owned_1688_account_runtime",
                side_effect=lambda *_args: events.append("stop_owned"),
            ),
        ):
            with open_account_bound_listing_browser(
                payload=self.payload(),
                browser_class=RecordingBrowser,
                operator_config={"browser": {}},
                project_root=Path.cwd(),
                shared_runtime_root="D:/runtime",
                expected_account_key="muke_lixiang",
                expected_cdp_port=9306,
            ) as (browser, binding, identity):
                self.assertEqual(browser.config["debugger_address"], "127.0.0.1:9306")
                self.assertEqual(binding.cdp_port, 9306)
                self.assertEqual(identity.shop_name, "木刻理想")
                events.append("work")

        self.assertEqual(
            events,
            [
                "lock_enter",
                "lease_enter",
                "lease_active",
                "login",
                "open",
                "lease_active",
                "work",
                "close",
                "stop_owned",
                "lease_exit",
                "lock_exit",
            ],
        )
        self.assertEqual(len(guard.closers), 1)

    def test_wrong_external_cdp_binding_stops_before_login(self) -> None:
        with (
            patch("listing_browser_session.resolve_executor_binding", return_value=self.binding(port=9222)),
            patch("listing_browser_session.ensure_1688_authenticated_session") as login,
        ):
            with self.assertRaisesRegex(ValueError, "account CDP mismatch"):
                with open_account_bound_listing_browser(
                    payload=self.payload(),
                    browser_class=FakeBrowser,
                    operator_config={"browser": {}},
                    project_root=Path.cwd(),
                    shared_runtime_root="D:/runtime",
                    expected_account_key="muke_lixiang",
                    expected_cdp_port=9306,
                ):
                    pass
        login.assert_not_called()

    def test_browser_construction_failure_still_releases_owned_runtime(self) -> None:
        events: list[str] = []
        guard = RecordingLeaseGuard(events)

        class BrokenBrowser:
            def __init__(self, *_args, **_kwargs) -> None:
                raise RuntimeError("browser construction failed")

        with (
            patch("listing_browser_session.resolve_executor_binding", return_value=self.binding()),
            patch("listing_browser_session.resolve_stop_sale_app_config", return_value=object()),
            patch("listing_browser_session.RuntimeLeaseRepository", return_value=object()),
            patch("listing_browser_session.RuntimeLeaseGuard", return_value=guard),
            patch("listing_browser_session.resolve_build_sha", return_value="a" * 40),
            patch(
                "listing_browser_session.build_listing_tool_account_lock",
                return_value=RecordingContext(events, "lock"),
            ),
            patch(
                "listing_browser_session.ensure_1688_authenticated_session",
                return_value={"status": "success", "browser_runtime_preserved": True},
            ),
            patch(
                "listing_browser_session.stop_owned_1688_account_runtime",
                side_effect=lambda *_args: events.append("stop_owned"),
            ),
        ):
            with self.assertRaisesRegex(RuntimeError, "construction failed"):
                with open_account_bound_listing_browser(
                    payload=self.payload(),
                    browser_class=BrokenBrowser,
                    operator_config={"browser": {}},
                    project_root=Path.cwd(),
                    shared_runtime_root="D:/runtime",
                    expected_account_key="muke_lixiang",
                    expected_cdp_port=9306,
                ):
                    pass

        self.assertIn("stop_owned", events)


if __name__ == "__main__":
    unittest.main()
