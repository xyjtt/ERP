from __future__ import annotations

import json
import os
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


PROJECT_ROOT = Path(__file__).resolve().parents[1]
RPA_ROOT = PROJECT_ROOT / "rpa"
if str(RPA_ROOT) not in sys.path:
    sys.path.insert(0, str(RPA_ROOT))
SCRIPTS_ROOT = PROJECT_ROOT / "scripts"
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

from auto_listing import ListingContractError, advance_listing_state
from auto_listing_executor import (
    _configure_auto_listing_steps,
    _configure_draft_repair_steps,
    _configure_submit_steps,
    _has_image_album_capacity_block,
    assert_repaired_draft_id,
    assert_execution_allowed,
    build_execution_payload,
    build_release_variant_payload,
    extract_draft_id,
    extract_detail_upload_resume_evidence,
    extract_draft_reconciliation_evidence,
    extract_submit_reconciliation_evidence,
    extract_offer_id,
    restore_execution_only_detail_images,
    resolve_1688_publish_url,
)
from test_auto_listing import sample_payload
from run_1688_listing_task import _resolve_listing_account_lock_path


class AutoListingExecutorTests(unittest.TestCase):
    def test_1688_publish_contract_uses_24_hour_buyer_protection(self) -> None:
        config = json.loads(
            (PROJECT_ROOT / "config" / "platforms" / "1688.json").read_text(encoding="utf-8")
        )
        publish = config["publish"]
        expected_name = "24小时发货"
        expected_code = "essxsfh"

        self.assertEqual(publish["draft_verification"]["buyer_protection_default_value"], expected_name)
        self.assertEqual(publish["draft_verification"]["buyer_protection_expected_code"], expected_code)
        for section_name in ("draft_verification", "draft_request_patch", "draft_page_state_patch"):
            section = publish[section_name]
            self.assertEqual(section["buyer_protection_default_value"], expected_name)
            self.assertEqual(
                section["buyer_protection_step_template"],
                [{"from": 1, "service_name": expected_name, "service_code": expected_code}],
            )
        buyer_step = next(
            step for step in publish["steps"] if step.get("name") == "buyer_protection_ship_time"
        )
        self.assertEqual(buyer_step["default_value"], expected_name)

    def test_listing_lock_is_scoped_to_the_payload_account(self) -> None:
        payload = sample_payload()
        payload["shop"]["account_key"] = "muke_lixiang"

        lock_path, account_key = _resolve_listing_account_lock_path(
            SimpleNamespace(shared_runtime_root=str(PROJECT_ROOT / "runtime")),
            payload,
        )

        self.assertEqual(account_key, "muke_lixiang")
        self.assertEqual(lock_path.name, "ali1688_account_muke_lixiang.lock")

    def test_browser_execution_is_disabled_by_default(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(ListingContractError, "ENABLE_1688_LISTING_EXECUTION"):
                assert_execution_allowed(sample_payload(), "draft")

    def test_submit_requires_separate_submit_switch(self) -> None:
        draft = advance_listing_state(
            sample_payload(), "draft_saved", evidence={"draft_id": "draft-1", "draft_url": "draft"}
        )
        approved = advance_listing_state(draft, "review_approved", evidence={"approved_by": "reviewer"})
        with patch.dict(os.environ, {"ENABLE_1688_LISTING_EXECUTION": "true"}, clear=True):
            with self.assertRaisesRegex(ListingContractError, "ENABLE_1688_LISTING_SUBMIT"):
                assert_execution_allowed(approved, "submit")

    def test_submit_guard_accepts_approved_single_item_task(self) -> None:
        draft = advance_listing_state(
            sample_payload(), "draft_saved", evidence={"draft_id": "draft-1", "draft_url": "draft"}
        )
        approved = advance_listing_state(draft, "review_approved", evidence={"approved_by": "reviewer"})
        with patch.dict(
            os.environ,
            {"ENABLE_1688_LISTING_EXECUTION": "true", "ENABLE_1688_LISTING_SUBMIT": "true"},
            clear=True,
        ):
            assert_execution_allowed(approved, "submit")

    def test_release_variant_uses_contract_title_price_stock_and_yidian_images(self) -> None:
        variant = build_release_variant_payload(sample_payload())
        self.assertEqual(variant["title"], "selected cabinet title")
        self.assertEqual(variant["price_value"], "25.1")
        self.assertEqual(variant["quantity"], "999")
        self.assertEqual(variant["image_candidate_id"], "new-complete")
        self.assertEqual(len(variant["main_images"]), 1)
        self.assertEqual(variant["length_cm"], "50")
        self.assertEqual(variant["width_cm"], "40")
        self.assertEqual(variant["height_cm"], "49")
        self.assertEqual(variant["weight_g"], "12500")

    def test_publish_url_uses_bedside_table_category_id(self) -> None:
        payload = sample_payload()
        payload["product"]["category"] = "bedside_table"

        url, category_id = resolve_1688_publish_url(payload)

        self.assertEqual(category_id, "122942001")
        self.assertIn("catId=122942001", url)
        self.assertNotIn("123620022", url)

    def test_auto_listing_uploads_detail_images_to_1688_image_bank(self) -> None:
        publish = {
            "steps": [
                {"name": "open_publish_page", "action": "manual"},
                {"name": "main_image", "action": "picker_upload", "source": "main_image"},
                {"name": "detail_images", "action": "tinymce_images", "source": "detail_images"},
            ]
        }

        _configure_auto_listing_steps(publish)

        self.assertEqual(
            [step["name"] for step in publish["steps"]],
            ["main_image", "detail_images"],
        )
        detail_step = publish["steps"][1]
        self.assertEqual(detail_step["action"], "tinymce_images")
        self.assertEqual(detail_step["source"], "detail_images")
        self.assertTrue(detail_step["required"])
        self.assertEqual(detail_step["picker_batch_size"], 3)
        self.assertEqual(detail_step["max_album_rotations_per_batch"], 12)
        self.assertEqual(detail_step["append_mode"], "replace")
        self.assertFalse(detail_step["use_remote_detail_urls"])
        self.assertTrue(detail_step["reload_page_before_picker_batch"])

    def test_album_capacity_history_blocks_draft_execution(self) -> None:
        payload = sample_payload()
        payload["workflow"]["event_history"] = [
            {
                "event": "execution_blocked",
                "evidence": {"reason": "image_album_full"},
            }
        ]
        self.assertTrue(_has_image_album_capacity_block(payload))
        with patch.dict(os.environ, {"ENABLE_1688_LISTING_EXECUTION": "true"}, clear=True):
            with self.assertRaisesRegex(ListingContractError, "capacity is blocked"):
                assert_execution_allowed(payload, "draft")

    def test_unverified_album_capacity_resume_remains_blocked(self) -> None:
        payload = sample_payload()
        payload["workflow"]["pending_draft_id"] = "draft-1"
        payload["workflow"]["event_history"] = [
            {"event": "execution_blocked", "evidence": {"reason": "image_album_full"}},
            {"event": "execution_resumed", "evidence": {"reason": "image_album_capacity_recovered"}},
        ]

        self.assertTrue(_has_image_album_capacity_block(payload))

    def test_verified_album_capacity_resume_clears_block(self) -> None:
        payload = sample_payload()
        payload["workflow"]["pending_draft_id"] = "draft-1"
        payload["workflow"]["event_history"] = [
            {"event": "execution_blocked", "evidence": {"reason": "image_album_full"}},
            {
                "event": "execution_resumed",
                "evidence": {
                    "reason": "image_album_capacity_verified",
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
            },
        ]

        self.assertFalse(_has_image_album_capacity_block(payload))

    def test_verified_album_capacity_reverification_refreshes_stale_resume(self) -> None:
        payload = sample_payload()
        payload["workflow"]["pending_draft_id"] = "draft-1"
        payload["workflow"]["event_history"] = [
            {"event": "execution_blocked", "evidence": {"reason": "image_album_full"}},
            {"event": "execution_resumed", "evidence": {"reason": "image_album_capacity_recovered"}},
            {
                "event": "capacity_reverified",
                "evidence": {
                    "reason": "image_album_capacity_verified",
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
            },
        ]

        self.assertFalse(_has_image_album_capacity_block(payload))

    def test_submit_url_uses_reviewed_draft_id(self) -> None:
        pending = sample_payload()
        pending["product"]["category"] = "bedside_table"
        payload = advance_listing_state(
            pending,
            "draft_saved",
            evidence={"draft_id": "draft-123", "draft_url": "https://draft.invalid/123"},
        )

        url, category_id = resolve_1688_publish_url(payload, mode="submit")

        self.assertEqual(category_id, "122942001")
        self.assertIn("operator=draft2offer", url)
        self.assertIn("offerDraftId=draft-123", url)

    def test_draft_repair_url_uses_pending_draft_id(self) -> None:
        payload = sample_payload()
        payload["product"]["category"] = "bedside_table"
        payload["workflow"]["pending_draft_id"] = "draft-repair-123"

        url, category_id = resolve_1688_publish_url(payload, mode="draft")

        self.assertEqual(category_id, "122942001")
        self.assertIn("operator=draft2offer", url)
        self.assertIn("offerDraftId=draft-repair-123", url)
        self.assertTrue(url.startswith("https://offer.1688.com/offer/post/fillProductInfo.htm?"))

    def test_draft_repair_uses_official_entry_even_with_saved_publish_url(self) -> None:
        payload = sample_payload()
        payload["product"]["category"] = "bedside_table"
        payload["workflow"]["pending_draft_id"] = "draft-repair-123"
        saved_url = (
            "https://offer-new.1688.com/popular/publish.htm?"
            "catId=122942001&saleChannel=default&operator=new&draftId=draft-repair-123"
        )
        payload["workflow"]["draft"] = {
            "draft_id": "draft-repair-123",
            "publish_url": saved_url,
        }

        url, category_id = resolve_1688_publish_url(payload, mode="draft")

        self.assertEqual(category_id, "122942001")
        self.assertIn("operator=draft2offer", url)
        self.assertIn("offerDraftId=draft-repair-123", url)
        self.assertTrue(url.startswith("https://offer.1688.com/offer/post/fillProductInfo.htm?"))

    def test_draft_repair_does_not_trust_saved_publish_url_for_another_draft(self) -> None:
        payload = sample_payload()
        payload["product"]["category"] = "bedside_table"
        payload["workflow"]["pending_draft_id"] = "draft-repair-123"
        payload["workflow"]["draft"] = {
            "draft_id": "draft-repair-123",
            "publish_url": (
                "https://offer-new.1688.com/popular/publish.htm?"
                "catId=122942001&operator=new&draftId=draft-other"
            ),
        }

        url, _category_id = resolve_1688_publish_url(payload, mode="draft")

        self.assertIn("offerDraftId=draft-repair-123", url)
        self.assertNotIn("draft-other", url)

    def test_draft_repair_steps_skip_existing_specs_and_core_fields(self) -> None:
        publish = {
            "steps": [
                {"name": "title"},
                {"name": "price"},
                {"name": "quantity"},
                {"name": "spec_values"},
                {"name": "main_image"},
                {"name": "detail_images"},
                {"name": "buyer_protection_ship_time"},
                {"name": "logistics_dimensions"},
            ]
        }

        _configure_draft_repair_steps(publish)

        self.assertEqual(
            [step["name"] for step in publish["steps"]],
            ["main_image", "detail_images", "buyer_protection_ship_time", "logistics_dimensions"],
        )

    def test_full_draft_repair_repopulates_core_fields(self) -> None:
        publish = {
            "steps": [
                {"name": "category"},
                {"name": "title"},
                {"name": "price"},
                {"name": "quantity"},
                {"name": "category_defaults"},
                {"name": "spec_values"},
                {"name": "main_image"},
                {"name": "detail_images"},
                {"name": "buyer_protection_ship_time"},
                {"name": "logistics_dimensions"},
            ]
        }

        _configure_draft_repair_steps(publish, full_rebuild=True)

        self.assertEqual(
            [step["name"] for step in publish["steps"]],
            [
                "title",
                "price",
                "quantity",
                "category_defaults",
                "spec_values",
                "main_image",
                "detail_images",
                "buyer_protection_ship_time",
                "logistics_dimensions",
            ],
        )

    def test_capacity_repair_preserves_existing_main_image(self) -> None:
        publish = {
            "steps": [
                {"name": "main_image"},
                {"name": "detail_images"},
                {"name": "buyer_protection_ship_time"},
            ]
        }

        _configure_draft_repair_steps(publish, skip_main_image=True)

        self.assertEqual(
            [step["name"] for step in publish["steps"]],
            ["detail_images", "buyer_protection_ship_time"],
        )

    def test_submit_mode_does_not_repeat_draft_field_or_image_steps(self) -> None:
        publish = {
            "steps": [
                {"name": "title"},
                {"name": "main_image"},
                {"name": "detail_images"},
                {"name": "buyer_protection_ship_time"},
            ]
        }

        _configure_submit_steps(publish)

        self.assertEqual(publish["steps"], [])

    def test_detail_limit_is_execution_only_and_output_restores_full_list(self) -> None:
        payload = sample_payload()
        payload["images"]["detail_urls"] = ["detail-1", "detail-2", "detail-3"]

        execution_payload = build_execution_payload(payload, mode="draft", detail_limit=2)

        self.assertEqual(payload["images"]["detail_urls"], ["detail-1", "detail-2", "detail-3"])
        self.assertEqual(execution_payload["images"]["detail_urls"], ["detail-1", "detail-2"])
        restored = restore_execution_only_detail_images(execution_payload, payload)
        self.assertEqual(restored["images"]["detail_urls"], ["detail-1", "detail-2", "detail-3"])

    def test_extract_detail_upload_resume_uses_only_contiguous_complete_batches(self) -> None:
        failure_payload = {
            "task_id": "task-1",
            "result_context": {
                "detail_images_list": [f"detail-{index}" for index in range(8)],
                "detail_images_batch_01_uploaded_urls": ["u1", "u2", "u3"],
                "detail_images_batch_02_uploaded_urls": ["u4", "u5", "u6"],
                "detail_images_batch_03_uploaded_urls": ["u7"],
                "detail_images_batch_03_failed_album_values": ["album-full"],
            },
        }

        evidence = extract_detail_upload_resume_evidence(
            failure_payload,
            expected_task_id="task-1",
            expected_detail_count=8,
            batch_size=3,
        )

        self.assertEqual(evidence["uploaded_urls"], ["u1", "u2", "u3", "u4", "u5", "u6"])
        self.assertEqual(evidence["completed_count"], 6)
        self.assertEqual(evidence["next_batch_number"], 3)
        self.assertEqual(evidence["excluded_album_values"], ["album-full"])
        self.assertFalse(evidence["complete"])

    def test_extract_detail_upload_resume_accepts_full_contiguous_checkpoint(self) -> None:
        failure_payload = {
            "task_id": "task-1",
            "result_context": {
                "detail_images_list": [f"detail-{index}" for index in range(6)],
                "detail_images_batch_01_uploaded_urls": ["u1", "u2", "u3"],
                "detail_images_batch_02_uploaded_urls": ["u4", "u5", "u6"],
            },
        }

        evidence = extract_detail_upload_resume_evidence(
            failure_payload,
            expected_task_id="task-1",
            expected_detail_count=6,
            batch_size=3,
        )

        self.assertEqual(evidence["uploaded_urls"], ["u1", "u2", "u3", "u4", "u5", "u6"])
        self.assertEqual(evidence["completed_count"], 6)
        self.assertEqual(evidence["next_batch_number"], 3)
        self.assertEqual(evidence["excluded_album_values"], [])
        self.assertTrue(evidence["complete"])

    def test_submit_reconciliation_requires_success_page_and_exact_reapplied_fields(self) -> None:
        draft = advance_listing_state(
            sample_payload(), "draft_saved", evidence={"draft_id": "draft-1", "draft_url": "draft"}
        )
        approved = advance_listing_state(draft, "review_approved", evidence={"approved_by": "reviewer"})
        failure_payload = {
            "task_id": approved["task_id"],
            "result_context": {
                "current_url": "https://offer-new.1688.com/result.htm?offerId=1068081966540",
                "page_title": "商品发布成功 - 卖家工作台",
                "submit_required_fields_verified": True,
                "submit_send_address_value": "35281125",
                "submit_buyer_protection_value": "24小时发货",
                "submit_buyer_protection_schedule": [
                    {"from": 1, "serviceName": "24小时发货", "serviceCode": "essxsfh"}
                ],
                "submit_blocking_assist_messages": [],
            },
        }

        evidence = extract_submit_reconciliation_evidence(approved, failure_payload)

        self.assertEqual(evidence["offer_id"], "1068081966540")
        self.assertEqual(evidence["post_submit_verified"], "reconciled_success_page")

    def test_draft_reconciliation_requires_matching_success_and_read_only_inspection(self) -> None:
        payload = sample_payload()
        failure_payload = {
            "task_id": payload["task_id"],
            "error_type": "PublishValidationError",
            "error": "main image did not persist",
            "result_context": {
                "current_url": "https://offer-new.1688.com/popular/publish.htm?draftId=draft-1",
                "draft_submit_trace": {
                    "status": 200,
                    "url": "https://offer-new.1688.com/popular/draftSubmit.htm",
                    "responseJson": {"success": True, "data": {"draftId": "draft-1"}},
                },
                "draft_submit_reapply_required_fields": ["send_address", "buyer_protection"],
            },
        }
        inspection_payload = {
            "status": "failed",
            "checked_at": datetime.now(timezone.utc).isoformat(),
            "task_id": payload["task_id"],
            "draft_id": "draft-1",
            "checks": {
                "draft_id": True,
                "title": False,
                "price": False,
                "quantity": False,
                "main_image_present": False,
            },
            "draft_saved": False,
            "offer_submitted": False,
        }

        evidence = extract_draft_reconciliation_evidence(
            payload, failure_payload, inspection_payload
        )

        self.assertEqual(evidence["draft_id"], "draft-1")
        self.assertEqual(evidence["repair_scope"], "full")
        self.assertFalse(evidence["post_save_verified"])

    def test_draft_reconciliation_rejects_mismatched_inspection_draft(self) -> None:
        payload = sample_payload()
        failure_payload = {
            "task_id": payload["task_id"],
            "error_type": "PublishValidationError",
            "result_context": {
                "current_url": "https://offer-new.1688.com/popular/publish.htm?draftId=draft-1",
                "draft_submit_trace": {
                    "status": 200,
                    "url": "https://offer-new.1688.com/popular/draftSubmit.htm",
                    "responseJson": {"success": True, "data": {"draftId": "draft-1"}},
                },
            },
        }
        inspection_payload = {
            "status": "failed",
            "task_id": payload["task_id"],
            "draft_id": "draft-2",
            "checks": {"draft_id": True, "title": False},
            "draft_saved": False,
            "offer_submitted": False,
        }

        with self.assertRaisesRegex(ListingContractError, "inspection draft_id"):
            extract_draft_reconciliation_evidence(payload, failure_payload, inspection_payload)

    def test_extract_draft_id_prefers_submit_response(self) -> None:
        context = {
            "current_url": "https://offer-new.1688.com/popular/publish.htm?draftId=url-draft",
            "draft_submit_trace": {"responseJson": {"success": True, "data": {"draftId": "response-draft"}}},
        }
        self.assertEqual(extract_draft_id(context), "response-draft")

    def test_extract_draft_id_supports_offer_draft_id_response(self) -> None:
        context = {
            "draft_submit_trace": {
                "responseJson": {"success": True, "data": {"offerDraftId": "existing-draft"}}
            },
        }
        self.assertEqual(extract_draft_id(context), "existing-draft")

    def test_draft_repair_rejects_a_different_save_response_draft_id(self) -> None:
        payload = sample_payload()
        payload["workflow"]["pending_draft_id"] = "existing-draft"

        with self.assertRaisesRegex(
            ListingContractError,
            "expected existing-draft, got replacement-draft",
        ):
            assert_repaired_draft_id(payload, "replacement-draft")

    def test_new_draft_allows_the_save_response_draft_id(self) -> None:
        assert_repaired_draft_id(sample_payload(), "new-draft")

    def test_extract_offer_id_supports_submit_response_and_url(self) -> None:
        self.assertEqual(
            extract_offer_id({"submit_request_trace": {"responseJson": {"data": {"offerId": "123456"}}}}),
            "123456",
        )
        self.assertEqual(
            extract_offer_id({"current_url": "https://detail.1688.com/offer/987654.html"}),
            "987654",
        )


if __name__ == "__main__":
    unittest.main()
