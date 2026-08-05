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
    build_submit_reapply_contract,
    build_submit_reapply_contract_sha256,
    canonical_sha256,
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
    reapply_hash = build_submit_reapply_contract_sha256(payload)
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
        "field_outcomes": {
            name: {"status": "persisted"} for name in REQUIRED_INSPECTION_CHECKS
        },
        "submit_reapply_contract_sha256": reapply_hash,
        "draft_saved": False,
        "offer_submitted": False,
    }


def reapply_saved_payload() -> dict:
    payload = saved_payload()
    draft = payload["workflow"]["draft"]
    draft["submit_reapply_required_fields"] = [
        "delivery_service",
        "send_address",
        "logistics",
        "buyer_protection",
    ]
    contract = {
        "contract_version": "listing_submit_reapply_v1",
        "draft_id": "draft-1",
        "required_fields": list(draft["submit_reapply_required_fields"]),
        "save": {
            "http_status": 200,
            "success": True,
            "request_draft_id": "draft-1",
            "response_draft_id": "draft-1",
        },
        "fields": {
            "delivery_service": {
                "status": "submit_reapply_required",
                "requested_ids": [365841],
                "allowed_services": [
                    {"id": 365841, "label": "送到楼下"},
                    {"id": 4511641, "label": "市区物流点自提"},
                ],
                "pre_save_selected_ids": [365841],
            },
            "send_address": {
                "status": "submit_reapply_required",
                "pre_save_selected": True,
                "expected_value": "35281125",
                "requested_value": "35281125",
            },
            "logistics": {
                "status": "submit_reapply_required",
                "expected_values": {
                    "length": "55",
                    "width": "47",
                    "height": "62.5",
                    "weight": "15250",
                },
                "requested_values": {
                    "length": "55",
                    "width": "47",
                    "height": "62.5",
                    "weight": "15250",
                },
                "pre_save_values": {
                    "length": "55",
                    "width": "47",
                    "height": "62.5",
                    "weight": "15250",
                },
            },
            "buyer_protection": {
                "status": "submit_reapply_required",
                "pre_save_selected": True,
                "service_name": "24小时发货",
                "service_code": "essxsfh",
                "requested_steps": [
                    {
                        "from": 1,
                        "serviceName": "24小时发货",
                        "serviceCode": "essxsfh",
                    }
                ],
                "available_services": [
                    {"serviceName": "24小时发货", "serviceCode": "essxsfh"}
                ],
            },
        },
    }
    draft["submit_reapply_evidence"] = contract
    draft["submit_reapply_contract_sha256"] = canonical_sha256(contract)
    return payload


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

    def test_nonpersistent_fields_require_complete_bound_reapply_contract(self) -> None:
        payload = reapply_saved_payload()
        contract = build_submit_reapply_contract(payload)
        contract_hash = canonical_sha256(contract)
        artifact = passed_artifact(payload)
        for name in contract["required_fields"]:
            artifact["field_outcomes"][name] = {
                "status": "submit_reapply_required",
                "contract_sha256": contract_hash,
            }

        binding = validate_independent_inspection(
            payload,
            artifact,
            expected_cdp_port=9306,
        )

        self.assertEqual(binding["submit_reapply_contract_sha256"], contract_hash)
        self.assertRegex(binding["field_outcomes_sha256"], r"^[0-9a-f]{64}$")

    def test_delivery_reapply_rejects_service_outside_page_allowlist(self) -> None:
        payload = reapply_saved_payload()
        payload["workflow"]["draft"]["submit_reapply_evidence"]["fields"][
            "delivery_service"
        ]["requested_ids"] = [999999]
        contract = payload["workflow"]["draft"]["submit_reapply_evidence"]
        payload["workflow"]["draft"]["submit_reapply_contract_sha256"] = canonical_sha256(
            contract
        )

        with self.assertRaisesRegex(ListingReviewError, "delivery_service evidence"):
            build_submit_reapply_contract(payload)

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
