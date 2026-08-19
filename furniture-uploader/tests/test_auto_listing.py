from __future__ import annotations

import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
RPA_ROOT = PROJECT_ROOT / "rpa"
if str(RPA_ROOT) not in sys.path:
    sys.path.insert(0, str(RPA_ROOT))

from auto_listing import (
    ListingContractError,
    STATE_BLOCKED,
    STATE_DRAFT_PENDING,
    STATE_DRAFT_PENDING_REVIEW,
    STATE_OFFER_WRITTEN_BACK,
    STATE_SUBMIT_PENDING,
    STATE_SUBMITTED,
    advance_listing_state,
    allocate_category_counts,
    build_listing_payload,
    calculate_publish_price,
    novelty_note,
    select_latest_complete_yidian_bundle,
    validate_listing_payload,
)
from listing_review import (
    INSPECTION_ARTIFACT_VERSION,
    REQUIRED_INSPECTION_CHECKS,
    build_review_contract_sha256,
    build_submit_reapply_contract_sha256,
)


def sample_product() -> dict:
    return {
        "sku_id": "SKU-1",
        "spu": "SPU-1",
        "name": "cabinet black 50x40x49",
        "category": "cabinet",
        "sale_price": "10.01",
        "properties_value": "black;50x40x49",
        "standard_model_spu": "MODEL-1",
        "attributes": {"material": "wood"},
        "l": "50",
        "w": "40",
        "h": "49",
        "weight": "12.5",
        "enabled": 1,
        "stock_disabled": 0,
        "other_5": "销售",
    }


def image_candidates() -> list[dict]:
    return [
        {
            "materialId": "old-complete",
            "uploadedAt": "2026-05-01T00:00:00Z",
            "isFullSet": True,
            "mainUrls": ["https://example.invalid/old-main.jpg"],
            "skuUrls": ["https://example.invalid/old-sku.jpg"],
            "detailUrls": ["https://example.invalid/old-detail.jpg"],
        },
        {
            "materialId": "new-incomplete",
            "uploadedAt": "2026-06-01T00:00:00Z",
            "isFullSet": False,
            "mainUrls": ["https://example.invalid/bad-main.jpg"],
            "skuUrls": [],
            "detailUrls": [],
        },
        {
            "materialId": "new-complete",
            "uploadedAt": "2026-05-03T00:00:00Z",
            "isFullSet": True,
            "mainUrls": ["https://example.invalid/main.jpg"],
            "skuUrls": ["https://example.invalid/sku.jpg"],
            "detailUrls": ["https://example.invalid/detail.jpg"],
        },
    ]


def sample_payload(duplicate_status: str = "clear") -> dict:
    return build_listing_payload(
        sample_product(),
        shop_name="trial-shop",
        account_key="trial-account",
        novelty_type="new_spu",
        selected_title="selected cabinet title",
        yidian_candidates=image_candidates(),
        duplicate_status=duplicate_status,
        require_duplicate_clear=True,
    )


def review_approval_evidence(payload: dict, *, approved_by: str = "reviewer") -> dict:
    draft_id = str(((payload.get("workflow") or {}).get("draft") or {}).get("draft_id") or "")
    return {
        "approved_by": approved_by,
        "inspection_binding": {
            "artifact_version": INSPECTION_ARTIFACT_VERSION,
            "artifact_sha256": "b" * 64,
            "inspector_build_sha": "a" * 40,
            "payload_contract_sha256": build_review_contract_sha256(payload),
            "submit_reapply_contract_sha256": build_submit_reapply_contract_sha256(payload),
            "task_id": str(payload.get("task_id") or ""),
            "draft_id": draft_id,
            "account_key": str(((payload.get("shop") or {}).get("account_key") or "")),
            "shop_name": str(((payload.get("shop") or {}).get("shop_name") or "")),
            "cdp_port": 9306,
            "checked_at": "2026-08-05T00:00:00+08:00",
            "required_checks": sorted(REQUIRED_INSPECTION_CHECKS),
            "field_outcomes_sha256": "c" * 64,
        },
    }


class AutoListingContractTests(unittest.TestCase):
    def test_price_uses_sale_price_multiplier_and_ceiling_one_decimal(self) -> None:
        result = calculate_publish_price("10.01")
        self.assertEqual(result.raw_price, "25.025")
        self.assertEqual(result.publish_price, "25.1")

    def test_novelty_note_has_required_spu_and_sku_values(self) -> None:
        self.assertEqual(novelty_note("new_spu"), "\u8d27\u53f7\uff1a\u65b0 SPU")
        self.assertEqual(novelty_note("new_sku"), "\u8d27\u53f7\uff1a\u65b0 SKU")

    def test_latest_complete_image_bundle_wins(self) -> None:
        selected = select_latest_complete_yidian_bundle(image_candidates())
        self.assertEqual(selected["material_id"], "new-complete")

    def test_payload_contains_selected_title_price_quantity_and_independent_sku_row(self) -> None:
        payload = sample_payload()
        self.assertEqual(payload["product"]["selected_title"], "selected cabinet title")
        self.assertEqual(payload["pricing"]["publish_price"], "25.1")
        self.assertEqual(payload["inventory"]["quantity"], 999)
        self.assertEqual(len(payload["sku"]["rows"]), 1)
        self.assertTrue(payload["workflow"]["independent_link_required"])
        self.assertEqual(payload["workflow"]["state"], STATE_DRAFT_PENDING)
        self.assertEqual(payload["preflight"]["status"], "passed")

    def test_duplicate_check_blocks_preflight(self) -> None:
        payload = sample_payload("pending")
        report = validate_listing_payload(payload, require_duplicate_clear=True)
        self.assertEqual(report["status"], "blocked")
        self.assertIn("live duplicate check must be clear", report["errors"])

    def test_source_lifecycle_is_recorded_and_required(self) -> None:
        payload = sample_payload()
        self.assertEqual(payload["source"]["lifecycle_field"], "other_5")
        self.assertEqual(payload["source"]["lifecycle_status"], "销售")

        payload["source"]["lifecycle_status"] = "停产"
        report = validate_listing_payload(payload, require_duplicate_clear=True)

        self.assertEqual(report["status"], "blocked")
        self.assertIn(
            "source product must have enabled=1, stock_disabled=0 and other_5=销售",
            report["errors"],
        )

    def test_payload_builder_rejects_stopped_source_product(self) -> None:
        product = sample_product()
        product["other_5"] = "停产"

        with self.assertRaisesRegex(ListingContractError, "other_5 must be 销售"):
            build_listing_payload(
                product,
                shop_name="trial-shop",
                account_key="trial-account",
                novelty_type="new_spu",
                selected_title="selected cabinet title",
                yidian_candidates=image_candidates(),
                duplicate_status="clear",
                require_duplicate_clear=True,
            )

    def test_state_machine_requires_draft_then_approval_then_offer_writeback(self) -> None:
        payload = sample_payload()
        draft = advance_listing_state(
            payload,
            "draft_saved",
            evidence={"draft_id": "draft-1", "draft_url": "https://draft.invalid/1"},
        )
        self.assertEqual(draft["workflow"]["state"], STATE_DRAFT_PENDING_REVIEW)
        approved = advance_listing_state(
            draft,
            "review_approved",
            evidence=review_approval_evidence(draft),
        )
        self.assertEqual(approved["workflow"]["state"], STATE_SUBMIT_PENDING)
        submitted = advance_listing_state(approved, "submit_succeeded", evidence={"offer_id": "123"})
        self.assertEqual(submitted["workflow"]["state"], STATE_SUBMITTED)
        completed = advance_listing_state(submitted, "offer_written_back", evidence={"offer_url": "https://detail.1688.com/offer/123.html"})
        self.assertEqual(completed["workflow"]["state"], STATE_OFFER_WRITTEN_BACK)

    def test_state_machine_blocks_submit_without_approval(self) -> None:
        with self.assertRaises(ListingContractError):
            advance_listing_state(sample_payload(), "review_approved", evidence={})

    def test_review_rejection_blocks_pending_draft(self) -> None:
        draft = advance_listing_state(
            sample_payload(),
            "draft_saved",
            evidence={"draft_id": "draft-1", "draft_url": "https://draft.invalid/1"},
        )

        rejected = advance_listing_state(
            draft,
            "review_rejected",
            evidence={"rejected_by": "reviewer"},
        )

        self.assertEqual(rejected["workflow"]["state"], STATE_BLOCKED)
        self.assertEqual(rejected["workflow"]["last_event_evidence"]["rejected_by"], "reviewer")

    def test_review_rejection_can_resume_same_draft_for_required_field_repair(self) -> None:
        draft = advance_listing_state(
            sample_payload(),
            "draft_saved",
            evidence={"draft_id": "draft-1", "draft_url": "https://draft.invalid/1"},
        )
        rejected = advance_listing_state(
            draft,
            "review_rejected",
            evidence={"rejected_by": "reviewer"},
        )

        resumed = advance_listing_state(
            rejected,
            "review_repair_resumed",
            evidence={
                "resumed_by": "authorized-operator",
                "reason": "review_required_fields_repair",
                "capacity_probe": {
                    "status": "passed",
                    "probe_count": 2,
                    "uploaded_count": 2,
                    "remote_hosts": ["cbu01.alicdn.com"],
                    "draft_id": "draft-1",
                    "checked_at": datetime.now(timezone.utc).isoformat(),
                    "draft_saved": False,
                    "offer_submitted": False,
                },
            },
        )

        self.assertEqual(resumed["workflow"]["state"], STATE_DRAFT_PENDING)
        self.assertEqual(resumed["workflow"]["pending_draft_id"], "draft-1")
        self.assertEqual(resumed["workflow"]["last_event"], "review_repair_resumed")

    def test_review_repair_can_rebind_a_known_historical_draft(self) -> None:
        first_draft = advance_listing_state(
            sample_payload(),
            "draft_saved",
            evidence={"draft_id": "draft-old", "draft_url": "https://draft.invalid/old"},
        )
        first_rejected = advance_listing_state(
            first_draft,
            "review_rejected",
            evidence={"rejected_by": "reviewer"},
        )
        first_resumed = advance_listing_state(
            first_rejected,
            "review_repair_resumed",
            evidence={
                "resumed_by": "authorized-operator",
                "reason": "review_required_fields_repair",
                "draft_id": "draft-old",
                "capacity_probe": {
                    "status": "passed",
                    "probe_count": 2,
                    "uploaded_count": 2,
                    "remote_hosts": ["cbu01.alicdn.com"],
                    "draft_id": "draft-old",
                    "checked_at": datetime.now(timezone.utc).isoformat(),
                    "draft_saved": False,
                    "offer_submitted": False,
                },
            },
        )
        replacement_draft = advance_listing_state(
            first_resumed,
            "draft_saved",
            evidence={"draft_id": "draft-new", "draft_url": "https://draft.invalid/new"},
        )
        replacement_rejected = advance_listing_state(
            replacement_draft,
            "review_rejected",
            evidence={"rejected_by": "reviewer"},
        )

        rebound = advance_listing_state(
            replacement_rejected,
            "review_repair_resumed",
            evidence={
                "resumed_by": "authorized-operator",
                "reason": "review_required_fields_repair",
                "draft_id": "draft-old",
                "capacity_probe": {
                    "status": "passed",
                    "probe_count": 2,
                    "uploaded_count": 2,
                    "remote_hosts": ["cbu01.alicdn.com"],
                    "draft_id": "draft-old",
                    "checked_at": datetime.now(timezone.utc).isoformat(),
                    "draft_saved": False,
                    "offer_submitted": False,
                },
            },
        )

        self.assertEqual(rebound["workflow"]["pending_draft_id"], "draft-old")
        self.assertEqual(rebound["workflow"]["draft"]["draft_id"], "draft-new")

    def test_review_repair_rejects_an_unknown_draft_id(self) -> None:
        draft = advance_listing_state(
            sample_payload(),
            "draft_saved",
            evidence={"draft_id": "draft-known", "draft_url": "https://draft.invalid/known"},
        )
        rejected = advance_listing_state(
            draft,
            "review_rejected",
            evidence={"rejected_by": "reviewer"},
        )

        with self.assertRaisesRegex(ListingContractError, "not present in workflow history"):
            advance_listing_state(
                rejected,
                "review_repair_resumed",
                evidence={
                    "resumed_by": "authorized-operator",
                    "reason": "review_required_fields_repair",
                    "draft_id": "draft-unknown",
                    "capacity_probe": {},
                },
            )

    def test_authorized_rebuild_requires_all_historical_drafts_deleted(self) -> None:
        first = advance_listing_state(
            sample_payload(),
            "draft_saved",
            evidence={"draft_id": "draft-old", "draft_url": "https://draft.invalid/old"},
        )
        rejected = advance_listing_state(first, "review_rejected", evidence={"rejected_by": "reviewer"})
        resumed = advance_listing_state(
            rejected,
            "review_repair_resumed",
            evidence={
                "resumed_by": "operator",
                "reason": "review_required_fields_repair",
                "draft_id": "draft-old",
                "capacity_probe": {
                    "status": "passed",
                    "probe_count": 2,
                    "uploaded_count": 2,
                    "remote_hosts": ["cbu01.alicdn.com"],
                    "draft_id": "draft-old",
                    "checked_at": datetime.now(timezone.utc).isoformat(),
                    "draft_saved": False,
                    "offer_submitted": False,
                },
            },
        )
        second = advance_listing_state(
            resumed,
            "draft_saved",
            evidence={"draft_id": "draft-new", "draft_url": "https://draft.invalid/new"},
        )
        rejected_again = advance_listing_state(
            second,
            "review_rejected",
            evidence={"rejected_by": "reviewer"},
        )
        capacity_probe = {
            "status": "passed",
            "probe_count": 2,
            "uploaded_count": 2,
            "remote_hosts": ["cbu01.alicdn.com"],
            "draft_id": "",
            "checked_at": datetime.now(timezone.utc).isoformat(),
            "draft_saved": False,
            "offer_submitted": False,
        }

        with self.assertRaisesRegex(ListingContractError, "every historical draft_id"):
            advance_listing_state(
                rejected_again,
                "authorized_draft_rebuild_resumed",
                evidence={
                    "authorized_by": "user-approved",
                    "reason": "authorized_corrupt_draft_rebuild",
                    "deletion_evidence": {
                        "status": "passed",
                        "deleted_draft_ids": ["draft-new"],
                        "remaining_target_ids": [],
                    },
                    "capacity_probe": capacity_probe,
                },
            )

        rebuilt = advance_listing_state(
            rejected_again,
            "authorized_draft_rebuild_resumed",
            evidence={
                "authorized_by": "user-approved",
                "reason": "authorized_corrupt_draft_rebuild",
                "deletion_evidence": {
                    "status": "passed",
                    "deleted_draft_ids": ["draft-old", "draft-new"],
                    "remaining_target_ids": [],
                },
                "capacity_probe": capacity_probe,
            },
        )

        self.assertEqual(rebuilt["workflow"]["state"], STATE_DRAFT_PENDING)
        self.assertEqual(rebuilt["workflow"]["last_event"], "authorized_draft_rebuild_resumed")
        self.assertNotIn("draft", rebuilt["workflow"])
        self.assertNotIn("pending_draft_id", rebuilt["workflow"])

    def test_failed_server_inspection_can_only_resume_the_same_draft(self) -> None:
        saved = advance_listing_state(
            sample_payload(),
            "draft_saved",
            evidence={
                "draft_id": "draft-current",
                "draft_url": "https://draft.invalid/current",
                "publish_url": (
                    "https://offer-new.1688.com/popular/publish.htm?"
                    "catId=122942001&operator=new&draftId=draft-current"
                ),
                "post_save_verified": True,
            },
        )
        failed = advance_listing_state(
            saved,
            "draft_verification_failed",
            evidence={
                "reason": "server_draft_fields_missing",
                "draft_id": "draft-current",
                "inspection_status": "failed",
                "missing_checks": ["title", "main_image_present"],
            },
        )

        self.assertEqual(failed["workflow"]["state"], STATE_BLOCKED)
        self.assertEqual(failed["workflow"]["pending_draft_id"], "draft-current")
        self.assertEqual(failed["workflow"]["draft"]["repair_scope"], "full")
        self.assertFalse(failed["workflow"]["draft"]["post_save_verified"])

        capacity_probe = {
            "status": "passed",
            "probe_count": 2,
            "uploaded_count": 2,
            "remote_hosts": ["cbu01.alicdn.com"],
            "draft_id": "draft-current",
            "checked_at": datetime.now(timezone.utc).isoformat(),
            "draft_saved": False,
            "offer_submitted": False,
        }
        with self.assertRaisesRegex(ListingContractError, "reuse the failed draft_id"):
            advance_listing_state(
                failed,
                "draft_verification_repair_resumed",
                evidence={
                    "resumed_by": "operator",
                    "reason": "server_draft_fields_missing",
                    "draft_id": "draft-other",
                    "capacity_probe": {**capacity_probe, "draft_id": "draft-other"},
                },
            )

        resumed = advance_listing_state(
            failed,
            "draft_verification_repair_resumed",
            evidence={
                "resumed_by": "operator",
                "reason": "server_draft_fields_missing",
                "draft_id": "draft-current",
                "capacity_probe": capacity_probe,
            },
        )
        self.assertEqual(resumed["workflow"]["state"], STATE_DRAFT_PENDING)
        self.assertEqual(resumed["workflow"]["pending_draft_id"], "draft-current")
        self.assertEqual(resumed["workflow"]["draft"]["repair_scope"], "full")

    def test_execution_blocked_records_image_album_capacity_gate(self) -> None:
        blocked = advance_listing_state(
            sample_payload(),
            "execution_blocked",
            evidence={"reason": "image_album_full", "shop_skip_remaining": True},
        )
        self.assertEqual(blocked["workflow"]["state"], "blocked")
        self.assertEqual(
            blocked["workflow"]["last_event_evidence"]["reason"],
            "image_album_full",
        )

    def test_image_album_capacity_block_can_resume_same_pending_draft(self) -> None:
        payload = sample_payload()
        payload["workflow"]["pending_draft_id"] = "draft-existing-1"
        blocked = advance_listing_state(
            payload,
            "execution_blocked",
            evidence={"reason": "image_album_full", "shop_skip_remaining": True},
        )

        resumed = advance_listing_state(
            blocked,
            "execution_resumed",
            evidence={
                "resumed_by": "authorized-operator",
                "reason": "image_album_capacity_verified",
                "capacity_probe": {
                    "status": "passed",
                    "probe_count": 2,
                    "uploaded_count": 2,
                    "remote_hosts": ["cbu01.alicdn.com"],
                    "draft_id": "draft-existing-1",
                    "checked_at": datetime.now(timezone.utc).isoformat(),
                    "draft_saved": False,
                    "offer_submitted": False,
                },
            },
        )

        self.assertEqual(resumed["workflow"]["state"], STATE_DRAFT_PENDING)
        self.assertEqual(resumed["workflow"]["pending_draft_id"], "draft-existing-1")

    def test_image_album_capacity_resume_rejects_failed_probe(self) -> None:
        payload = sample_payload()
        payload["workflow"]["pending_draft_id"] = "draft-existing-1"
        blocked = advance_listing_state(
            payload,
            "execution_blocked",
            evidence={"reason": "image_album_full", "shop_skip_remaining": True},
        )

        with self.assertRaisesRegex(ListingContractError, "invalid image capacity probe"):
            advance_listing_state(
                blocked,
                "execution_resumed",
                evidence={
                    "resumed_by": "authorized-operator",
                    "reason": "image_album_capacity_verified",
                    "capacity_probe": {
                        "status": "blocked",
                        "probe_count": 2,
                        "uploaded_count": 0,
                        "draft_id": "draft-existing-1",
                        "checked_at": datetime.now(timezone.utc).isoformat(),
                    },
                },
            )

    def test_image_album_capacity_reverification_keeps_draft_pending(self) -> None:
        payload = sample_payload()
        payload["workflow"]["pending_draft_id"] = "draft-existing-1"

        reverified = advance_listing_state(
            payload,
            "capacity_reverified",
            evidence={
                "verified_by": "authorized-operator",
                "reason": "image_album_capacity_verified",
                "capacity_probe": {
                    "status": "passed",
                    "probe_count": 2,
                    "uploaded_count": 2,
                    "remote_hosts": ["cbu01.alicdn.com"],
                    "draft_id": "draft-existing-1",
                    "checked_at": datetime.now(timezone.utc).isoformat(),
                    "draft_saved": False,
                    "offer_submitted": False,
                },
            },
        )

        self.assertEqual(reverified["workflow"]["state"], STATE_DRAFT_PENDING)
        self.assertEqual(reverified["workflow"]["last_event"], "capacity_reverified")

    def test_non_album_block_cannot_resume_automatically(self) -> None:
        payload = sample_payload()
        payload["workflow"]["pending_draft_id"] = "draft-existing-1"
        blocked = advance_listing_state(
            payload,
            "execution_blocked",
            evidence={"reason": "other_failure"},
        )

        with self.assertRaisesRegex(ListingContractError, "only image_album_full"):
            advance_listing_state(
                blocked,
                "execution_resumed",
                evidence={"resumed_by": "authorized-operator"},
            )

    def test_category_remainder_is_randomized_but_reproducible(self) -> None:
        first = allocate_category_counts(["a", "b", "c"], 5, seed=42)
        second = allocate_category_counts(["a", "b", "c"], 5, seed=42)
        self.assertEqual(first, second)
        self.assertEqual(sum(first.values()), 5)
        self.assertLessEqual(max(first.values()) - min(first.values()), 1)


if __name__ == "__main__":
    unittest.main()
