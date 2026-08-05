from __future__ import annotations

import copy
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch


PROJECT_ROOT = Path(__file__).resolve().parents[1]
RPA_ROOT = PROJECT_ROOT / "rpa"
SCRIPTS_ROOT = PROJECT_ROOT / "scripts"
TESTS_ROOT = PROJECT_ROOT / "tests"
for path in (RPA_ROOT, SCRIPTS_ROOT, TESTS_ROOT):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from auto_listing import ListingContractError, advance_listing_state  # noqa: E402
from auto_listing_executor import assert_execution_allowed  # noqa: E402
from listing_review import (  # noqa: E402
    INSPECTION_ARTIFACT_VERSION,
    REQUIRED_INSPECTION_CHECKS,
    ListingReviewError,
    build_listing_operation_key,
    build_review_contract_sha256,
    require_unique_existing_draft_id,
    validate_independent_inspection,
)
from run_1688_listing_task import _prepare_listing_saga  # noqa: E402
from test_auto_listing import sample_payload  # noqa: E402


def saved_payload() -> dict:
    return advance_listing_state(
        sample_payload(),
        "draft_saved",
        evidence={"draft_id": "draft-1", "draft_url": "https://draft.invalid/1"},
    )


def passed_artifact(payload: dict) -> dict:
    return {
        "artifact_version": INSPECTION_ARTIFACT_VERSION,
        "inspector_build_sha": "a" * 40,
        "payload_contract_sha256": build_review_contract_sha256(payload),
        "status": "passed",
        "checked_at": "2026-08-05T08:00:00+08:00",
        "task_id": payload["task_id"],
        "draft_id": require_unique_existing_draft_id(payload),
        "account_key": payload["shop"]["account_key"],
        "shop_name": payload["shop"]["shop_name"],
        "cdp_port": 9306,
        "checks": {name: True for name in REQUIRED_INSPECTION_CHECKS},
        "draft_saved": False,
        "offer_submitted": False,
    }


class ListingReviewContractTests(unittest.TestCase):
    def test_daily_contract_requires_exactly_one_current_draft(self) -> None:
        payload = sample_payload()
        with self.assertRaisesRegex(ListingReviewError, "found 0"):
            require_unique_existing_draft_id(payload)

        payload["workflow"]["pending_draft_id"] = "draft-1"
        payload["workflow"]["draft"] = {"draft_id": "draft-2"}
        with self.assertRaisesRegex(ListingReviewError, "found 2"):
            require_unique_existing_draft_id(payload)

    def test_independent_artifact_binds_identity_cdp_version_and_all_fields(self) -> None:
        payload = saved_payload()
        binding = validate_independent_inspection(
            payload,
            passed_artifact(payload),
            expected_cdp_port=9306,
        )
        self.assertEqual(binding["draft_id"], "draft-1")
        self.assertEqual(binding["cdp_port"], 9306)
        self.assertEqual(set(binding["required_checks"]), REQUIRED_INSPECTION_CHECKS)

    def test_independent_artifact_rejects_failed_field_and_stale_payload(self) -> None:
        payload = saved_payload()
        failed = passed_artifact(payload)
        failed["checks"]["logistics"] = False
        with self.assertRaisesRegex(ListingReviewError, "not all passed"):
            validate_independent_inspection(payload, failed, expected_cdp_port=9306)

        stale = passed_artifact(payload)
        changed = copy.deepcopy(payload)
        changed["product"]["selected_title"] = "changed after inspection"
        with self.assertRaisesRegex(ListingReviewError, "contract is stale"):
            validate_independent_inspection(changed, stale, expected_cdp_port=9306)

    def test_approval_and_submit_require_the_same_independent_artifact(self) -> None:
        payload = saved_payload()
        binding = validate_independent_inspection(
            payload,
            passed_artifact(payload),
            expected_cdp_port=9306,
        )
        approved = advance_listing_state(
            payload,
            "review_approved",
            evidence={"approved_by": "reviewer", "inspection_binding": binding},
        )
        self.assertTrue(approved["workflow"]["draft"]["post_save_verified"])
        with patch.dict(
            os.environ,
            {"ENABLE_1688_LISTING_EXECUTION": "1", "ENABLE_1688_LISTING_SUBMIT": "1"},
            clear=True,
        ):
            assert_execution_allowed(approved, "submit")

        approved["workflow"]["draft"]["inspection_binding"]["artifact_sha256"] = "c" * 64
        with patch.dict(
            os.environ,
            {"ENABLE_1688_LISTING_EXECUTION": "1", "ENABLE_1688_LISTING_SUBMIT": "1"},
            clear=True,
        ):
            with self.assertRaisesRegex(ListingContractError, "does not match"):
                assert_execution_allowed(approved, "submit")

    def test_draft_and_submit_have_distinct_stable_operation_keys(self) -> None:
        values = {
            "account_key": "muke_lixiang",
            "task_id": "task-1",
            "draft_id": "draft-1",
        }
        draft_key = build_listing_operation_key(**values, mode="draft")
        self.assertEqual(draft_key, build_listing_operation_key(**values, mode="draft"))
        self.assertNotEqual(draft_key, build_listing_operation_key(**values, mode="submit"))

    def test_terminal_saga_short_circuits_with_recorded_workflow(self) -> None:
        payload = saved_payload()

        class Repository:
            prepare_calls = 0

            def prepare(self, operation, **_kwargs):
                self.prepare_calls += 1
                self.operation_key = operation.operation_key
                return "completed"

            def get_saga_state(self, operation_key):
                return {
                    "operation_key": operation_key,
                    "state": "completed",
                    "ali1688_status": "draft_saved",
                    "evidence_json": '{"workflow":{"state":"draft_pending_review","draft":{"draft_id":"draft-1"}}}',
                }

        repository = Repository()
        operation = type(
            "Operation",
            (),
            {"operation_key": "operation-1"},
        )()
        result = _prepare_listing_saga(
            repository,
            operation,
            payload=payload,
            owner_token="owner",
            account_fencing_token=1,
            browser_slot_key="slot-1",
            browser_slot_fencing_token=2,
        )
        self.assertEqual(repository.prepare_calls, 1)
        self.assertEqual(result["saga_replay"]["status"], "terminal_success_short_circuit")
        self.assertEqual(result["workflow"]["draft"]["draft_id"], "draft-1")


if __name__ == "__main__":
    unittest.main()
