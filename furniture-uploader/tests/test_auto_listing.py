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

    def test_state_machine_requires_draft_then_approval_then_offer_writeback(self) -> None:
        payload = sample_payload()
        draft = advance_listing_state(payload, "draft_saved", evidence={"draft_url": "https://draft.invalid/1"})
        self.assertEqual(draft["workflow"]["state"], STATE_DRAFT_PENDING_REVIEW)
        approved = advance_listing_state(draft, "review_approved", evidence={"approved_by": "reviewer"})
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
