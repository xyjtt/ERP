from __future__ import annotations

import argparse
from pathlib import Path
import sys
import unittest
from unittest.mock import patch


SCRIPT_ROOT = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPT_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPT_ROOT))

from run_1688_listing_task import (  # noqa: E402
    _apply_draft_rebind,
    _build_listing_browser_config,
    _known_historical_draft_ids,
    _listing_operation_key,
    _open_authenticated_listing_browser,
    _record_controlled_saga_failure,
)
from auto_listing import ListingContractError  # noqa: E402
from listing_review import ListingReviewError  # noqa: E402
from exceptions import (  # noqa: E402
    OfflineLoginRequiredError,
    OfflineRiskControlError,
)


def _args(mode: str = "draft", draft_id: str = "") -> argparse.Namespace:
    return argparse.Namespace(mode=mode, draft_id=draft_id)


def _payload_with_history(
    *,
    current_draft_id: str = "",
    event_draft_ids: list[str] | None = None,
    pending_draft_id: str = "",
    last_event: str = "",
) -> dict:
    workflow: dict = {
        "state": "draft_pending",
        "last_event": last_event,
        "event_history": [
            {"event": "draft_saved", "evidence": {"draft_id": draft_id}}
            for draft_id in (event_draft_ids or [])
        ],
    }
    if current_draft_id:
        workflow["draft"] = {"draft_id": current_draft_id}
    if pending_draft_id:
        workflow["pending_draft_id"] = pending_draft_id
    return {"task_id": "task-1", "workflow": workflow}


class KnownHistoricalDraftIdTests(unittest.TestCase):
    def test_collects_current_and_event_draft_ids(self) -> None:
        payload = _payload_with_history(
            current_draft_id="draft-current",
            event_draft_ids=["draft-old-1", "draft-old-2"],
        )
        self.assertEqual(
            _known_historical_draft_ids(payload),
            {"draft-current", "draft-old-1", "draft-old-2"},
        )

    def test_ignores_non_draft_saved_events(self) -> None:
        payload = _payload_with_history()
        payload["workflow"]["event_history"] = [
            {"event": "draft_verification_failed", "evidence": {"draft_id": "draft-x"}},
            {"event": "draft_saved", "evidence": {}},
        ]
        self.assertEqual(_known_historical_draft_ids(payload), set())

    def test_collects_pending_draft_id_for_explicit_daily_rebind(self) -> None:
        payload = _payload_with_history(pending_draft_id="draft-pending")
        self.assertEqual(_known_historical_draft_ids(payload), {"draft-pending"})

    def test_listing_operation_key_separates_submit_from_draft(self) -> None:
        payload = _payload_with_history(current_draft_id="draft-current")
        payload["shop"] = {"account_key": "muke_lixiang"}
        self.assertNotEqual(
            _listing_operation_key(payload, "draft"),
            _listing_operation_key(payload, "submit"),
        )

    def test_listing_operation_key_requires_unique_existing_draft_for_draft(self) -> None:
        payload = _payload_with_history()
        payload["shop"] = {"account_key": "muke_lixiang"}
        with self.assertRaisesRegex(ListingReviewError, "found 0"):
            _listing_operation_key(payload, "draft")

    def test_listing_operation_key_rejects_ambiguous_existing_drafts(self) -> None:
        payload = _payload_with_history(
            current_draft_id="draft-current",
            pending_draft_id="draft-pending",
        )
        payload["shop"] = {"account_key": "muke_lixiang"}
        with self.assertRaisesRegex(ListingReviewError, "found 2"):
            _listing_operation_key(payload, "draft")


class ApplyDraftRebindTests(unittest.TestCase):
    def test_draft_mode_rebinds_known_current_draft(self) -> None:
        payload = _payload_with_history(current_draft_id="6a69bee6e4b01cad1b297a52")
        execution = _apply_draft_rebind(
            _args(draft_id="6a69bee6e4b01cad1b297a52"), payload, _payload_with_history()
        )
        self.assertEqual(
            execution["workflow"]["pending_draft_id"], "6a69bee6e4b01cad1b297a52"
        )

    def test_draft_mode_rebinds_known_event_history_draft(self) -> None:
        payload = _payload_with_history(event_draft_ids=["draft-old-1"])
        execution = _apply_draft_rebind(
            _args(draft_id="draft-old-1"), payload, _payload_with_history()
        )
        self.assertEqual(execution["workflow"]["pending_draft_id"], "draft-old-1")

    def test_draft_mode_rejects_unknown_draft_id(self) -> None:
        payload = _payload_with_history(current_draft_id="draft-known")
        with self.assertRaises(ValueError):
            _apply_draft_rebind(_args(draft_id="draft-unknown"), payload, dict(payload))

    def test_draft_mode_rejects_invalid_draft_id_format(self) -> None:
        payload = _payload_with_history(current_draft_id="draft-known")
        with self.assertRaises(ValueError):
            _apply_draft_rebind(_args(draft_id="bad id!"), payload, dict(payload))

    def test_submit_mode_rejects_draft_id_rebind(self) -> None:
        payload = _payload_with_history(current_draft_id="draft-known")
        with self.assertRaises(ValueError):
            _apply_draft_rebind(
                _args(mode="submit", draft_id="draft-known"), payload, dict(payload)
            )

    def test_existing_pending_draft_id_is_kept_without_rebind(self) -> None:
        payload = _payload_with_history(pending_draft_id="draft-pending")
        execution = _apply_draft_rebind(_args(), payload, dict(payload))
        self.assertEqual(execution["workflow"]["pending_draft_id"], "draft-pending")

    def test_fail_closed_when_history_exists_without_pending_or_rebind(self) -> None:
        # The 2026-07-31 canary trap: draft mode with a historical draft but no
        # pending_draft_id must not silently create a new 1688 draft.
        payload = _payload_with_history(
            current_draft_id="6a69bee6e4b01cad1b297a52",
            last_event="execution_resumed",
        )
        with self.assertRaises(ListingContractError):
            _apply_draft_rebind(_args(), payload, dict(payload))

    def test_authorized_rebuild_fails_closed_without_existing_draft(self) -> None:
        payload = _payload_with_history(
            current_draft_id="",
            event_draft_ids=["draft-deleted"],
            last_event="authorized_draft_rebuild_resumed",
        )
        with self.assertRaisesRegex(ListingContractError, "new 1688 draft creation is disabled"):
            _apply_draft_rebind(_args(), payload, dict(payload))

    def test_brand_new_task_fails_closed_without_existing_draft(self) -> None:
        payload = _payload_with_history()
        with self.assertRaisesRegex(ListingContractError, "new 1688 draft creation is disabled"):
            _apply_draft_rebind(_args(), payload, dict(payload))


class _RecordingSagaRepository:
    def __init__(self, *, fail: bool = False) -> None:
        self.calls: list[dict] = []
        self.fail = fail

    def record_ali1688_result(self, **kwargs) -> None:
        if self.fail:
            raise RuntimeError("saga write unavailable")
        self.calls.append(kwargs)


class RecordControlledSagaFailureTests(unittest.TestCase):
    def test_records_failed_terminal_with_error_code(self) -> None:
        repository = _RecordingSagaRepository()
        note = _record_controlled_saga_failure(
            repository,
            operation_key="op-1",
            account_fencing_token=7,
            exc=ValueError("controlled failure"),
        )
        self.assertEqual(note, "saga=failed_terminal")
        self.assertEqual(len(repository.calls), 1)
        call = repository.calls[0]
        self.assertEqual(call["operation_key"], "op-1")
        self.assertEqual(call["account_fencing_token"], 7)
        self.assertEqual(call["status"], "failed")
        self.assertEqual(call["error_code"], "ValueError")
        self.assertIn("controlled failure", call["error_summary"])

    def test_saga_record_failure_does_not_mask_original_error(self) -> None:
        repository = _RecordingSagaRepository(fail=True)
        note = _record_controlled_saga_failure(
            repository,
            operation_key="op-1",
            account_fencing_token=7,
            exc=ValueError("controlled failure"),
        )
        self.assertTrue(note.startswith("saga_record_failed=RuntimeError"))


class _FakeBrowser:
    def __init__(self, events: list[str]) -> None:
        self.events = events

    def open(self) -> None:
        self.events.append("browser_open")

    def run_system_workflow(self, _config: dict, _context: dict) -> None:
        self.events.append("interactive_login")


class OpenAuthenticatedListingBrowserTests(unittest.TestCase):
    def test_starts_authenticated_runtime_before_browser_attach(self) -> None:
        events: list[str] = []
        browser = _FakeBrowser(events)

        def login(*_args, **kwargs) -> dict:
            events.append("shared_login")
            self.assertTrue(kwargs["keep_browser_open"])
            self.assertTrue(kwargs["allow_unconfirmed_identity"])
            return {"status": "success", "browser_runtime_preserved": True}

        with patch("run_1688_listing_task.ensure_1688_authenticated_session", side_effect=login):
            owns_runtime = _open_authenticated_listing_browser(
                browser,
                shared_runtime_root="runtime",
                account_key="muke_lixiang",
                shop_name="木刻理想",
                system_config={},
                skip_login=False,
            )

        self.assertEqual(events, ["shared_login", "browser_open"])
        self.assertTrue(owns_runtime)

    def test_login_required_fails_closed_without_interactive_fallback(self) -> None:
        events: list[str] = []
        browser = _FakeBrowser(events)
        with patch(
            "run_1688_listing_task.ensure_1688_authenticated_session",
            side_effect=OfflineLoginRequiredError("login required"),
        ):
            with self.assertRaises(OfflineLoginRequiredError):
                _open_authenticated_listing_browser(
                    browser,
                    shared_runtime_root="runtime",
                    account_key="muke_lixiang",
                    shop_name="木刻理想",
                    system_config={"login": {}},
                    skip_login=False,
                )

        self.assertEqual(events, [])

    def test_identity_unconfirmed_session_can_attach_without_manual_prompt(self) -> None:
        events: list[str] = []
        browser = _FakeBrowser(events)
        with patch(
            "run_1688_listing_task.ensure_1688_authenticated_session",
            return_value={
                "status": "identity_unconfirmed",
                "browser_runtime_preserved": True,
            },
        ):
            owns_runtime = _open_authenticated_listing_browser(
                browser,
                shared_runtime_root="runtime",
                account_key="muke_lixiang",
                shop_name="木刻理想",
                system_config={"login": {"mode": "manual"}},
                skip_login=False,
            )

        self.assertEqual(events, ["browser_open"])
        self.assertTrue(owns_runtime)

    def test_risk_control_fails_before_browser_attach(self) -> None:
        events: list[str] = []
        browser = _FakeBrowser(events)
        with patch(
            "run_1688_listing_task.ensure_1688_authenticated_session",
            side_effect=OfflineRiskControlError("risk control"),
        ):
            with self.assertRaises(OfflineRiskControlError):
                _open_authenticated_listing_browser(
                    browser,
                    shared_runtime_root="runtime",
                    account_key="muke_lixiang",
                    shop_name="木刻理想",
                    system_config={},
                    skip_login=False,
                )

        self.assertEqual(events, [])


class BuildListingBrowserConfigTests(unittest.TestCase):
    def test_uses_the_account_binding_cdp_port(self) -> None:
        operator_config = {
            "browser": {
                "debugger_address": "127.0.0.1:9222",
                "headless": False,
            }
        }

        result = _build_listing_browser_config(operator_config, cdp_port=9317)

        self.assertEqual(result["debugger_address"], "127.0.0.1:9317")
        self.assertEqual(result["browser_type"], "edge")
        self.assertFalse(result["headless"])
        self.assertEqual(
            operator_config["browser"]["debugger_address"],
            "127.0.0.1:9222",
        )

    def test_forces_edge_for_the_account_runtime(self) -> None:
        result = _build_listing_browser_config(
            {"browser": {"type": "chrome", "browser_type": "chrome"}},
            cdp_port=9306,
        )

        self.assertEqual(result["browser_type"], "edge")
        self.assertEqual(result["debugger_address"], "127.0.0.1:9306")

    def test_rejects_invalid_cdp_port(self) -> None:
        with self.assertRaisesRegex(ValueError, "cdp_port must be positive"):
            _build_listing_browser_config({}, cdp_port=0)


if __name__ == "__main__":
    unittest.main()
