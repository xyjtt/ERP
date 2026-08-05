"""Contracts for independent saved-draft review and submit authorization."""

from __future__ import annotations

from datetime import datetime
import hashlib
import json
import re
from typing import Any, Mapping

from operation_saga import build_operation_key


INSPECTION_ARTIFACT_VERSION = "listing_draft_inspection_v1"
REQUIRED_INSPECTION_CHECKS = frozenset(
    {
        "draft_id",
        "title",
        "price",
        "quantity",
        "main_image_present",
        "main_image_count",
        "main_image_square",
        "detail_image_count",
        "spec_color",
        "spec_size",
        "logistics",
        "send_address",
        "buyer_protection",
    }
)
_DRAFT_ID = re.compile(r"[A-Za-z0-9_-]+")
_FULL_SHA = re.compile(r"[0-9a-f]{40}")


class ListingReviewError(ValueError):
    pass


def canonical_sha256(value: Any) -> str:
    rendered = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(rendered.encode("utf-8")).hexdigest()


def existing_draft_ids(payload: Mapping[str, Any]) -> set[str]:
    workflow = dict(payload.get("workflow") or {})
    values = [
        workflow.get("pending_draft_id"),
        dict(workflow.get("draft") or {}).get("draft_id"),
    ]
    draft_ids = {str(value or "").strip() for value in values if str(value or "").strip()}
    if not draft_ids:
        historical = [
            dict(item.get("evidence") or {}).get("draft_id")
            for item in list(workflow.get("event_history") or [])
            if isinstance(item, Mapping)
            and str(item.get("event") or "").strip() == "draft_saved"
        ]
        draft_ids = {
            str(value or "").strip()
            for value in historical[-1:]
            if str(value or "").strip()
        }
    invalid = sorted(value for value in draft_ids if not _DRAFT_ID.fullmatch(value))
    if invalid:
        raise ListingReviewError("listing payload contains an invalid existing draft_id")
    return draft_ids


def require_unique_existing_draft_id(payload: Mapping[str, Any]) -> str:
    draft_ids = existing_draft_ids(payload)
    if len(draft_ids) != 1:
        raise ListingReviewError(
            "listing payload must reference exactly one existing draft_id; "
            f"found {len(draft_ids)}"
        )
    return next(iter(draft_ids))


def build_listing_operation_key(
    *,
    account_key: str,
    task_id: str,
    draft_id: str,
    mode: str,
) -> str:
    normalized_mode = str(mode or "").strip().lower()
    if normalized_mode not in {"draft", "submit"}:
        raise ListingReviewError("listing operation mode must be draft or submit")
    normalized_draft_id = str(draft_id or "").strip()
    if not _DRAFT_ID.fullmatch(normalized_draft_id):
        raise ListingReviewError("listing operation requires a valid existing draft_id")
    return build_operation_key(
        f"listing-{normalized_mode}",
        account_key,
        task_id,
        normalized_draft_id,
    )


def build_review_contract(payload: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "task_id": payload.get("task_id"),
        "schema_version": payload.get("schema_version"),
        "platform": payload.get("platform"),
        "shop": payload.get("shop") or {},
        "source": payload.get("source") or {},
        "product": payload.get("product") or {},
        "pricing": payload.get("pricing") or {},
        "inventory": payload.get("inventory") or {},
        "images": payload.get("images") or {},
        "sku": payload.get("sku") or {},
        "attributes": payload.get("attributes") or {},
        "logistics": payload.get("logistics") or {},
        "draft_id": require_unique_existing_draft_id(payload),
    }


def build_review_contract_sha256(payload: Mapping[str, Any]) -> str:
    return canonical_sha256(build_review_contract(payload))


def validate_independent_inspection(
    payload: Mapping[str, Any],
    artifact: Mapping[str, Any],
    *,
    expected_cdp_port: int,
) -> dict[str, Any]:
    expected_draft_id = require_unique_existing_draft_id(payload)
    account_key = str(dict(payload.get("shop") or {}).get("account_key") or "").strip()
    shop_name = str(dict(payload.get("shop") or {}).get("shop_name") or "").strip()
    checks = artifact.get("checks")
    if str(artifact.get("artifact_version") or "").strip() != INSPECTION_ARTIFACT_VERSION:
        raise ListingReviewError("independent inspection artifact_version is unsupported")
    inspector_build_sha = str(artifact.get("inspector_build_sha") or "").strip().lower()
    if not _FULL_SHA.fullmatch(inspector_build_sha):
        raise ListingReviewError("independent inspection requires a full inspector_build_sha")
    if str(artifact.get("status") or "").strip() != "passed":
        raise ListingReviewError("independent inspection status must be passed")
    if artifact.get("draft_saved") is not False or artifact.get("offer_submitted") is not False:
        raise ListingReviewError("independent inspection artifact must be read-only")
    if str(artifact.get("task_id") or "").strip() != str(payload.get("task_id") or "").strip():
        raise ListingReviewError("independent inspection task_id does not match payload")
    if str(artifact.get("draft_id") or "").strip() != expected_draft_id:
        raise ListingReviewError("independent inspection draft_id does not match payload")
    if str(artifact.get("account_key") or "").strip() != account_key:
        raise ListingReviewError("independent inspection account_key does not match payload")
    if str(artifact.get("shop_name") or "").strip() != shop_name:
        raise ListingReviewError("independent inspection shop_name does not match payload")
    if int(artifact.get("cdp_port") or 0) != int(expected_cdp_port):
        raise ListingReviewError("independent inspection CDP port does not match account binding")
    if not isinstance(checks, Mapping):
        raise ListingReviewError("independent inspection checks are missing")
    missing_checks = sorted(REQUIRED_INSPECTION_CHECKS - set(checks))
    failed_checks = sorted(str(name) for name, passed in checks.items() if passed is not True)
    if missing_checks or failed_checks:
        raise ListingReviewError(
            "independent inspection fields are not all passed: "
            + ", ".join(missing_checks + failed_checks)
        )
    contract_sha = build_review_contract_sha256(payload)
    if str(artifact.get("payload_contract_sha256") or "").strip().lower() != contract_sha:
        raise ListingReviewError("independent inspection payload contract is stale")
    checked_at = str(artifact.get("checked_at") or "").strip()
    try:
        checked_time = datetime.fromisoformat(checked_at.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ListingReviewError("independent inspection checked_at is invalid") from exc
    if checked_time.tzinfo is None:
        raise ListingReviewError("independent inspection checked_at must include a timezone")
    return {
        "artifact_version": INSPECTION_ARTIFACT_VERSION,
        "artifact_sha256": canonical_sha256(artifact),
        "inspector_build_sha": inspector_build_sha,
        "payload_contract_sha256": contract_sha,
        "task_id": str(payload.get("task_id") or "").strip(),
        "draft_id": expected_draft_id,
        "account_key": account_key,
        "shop_name": shop_name,
        "cdp_port": int(expected_cdp_port),
        "checked_at": checked_at,
        "required_checks": sorted(REQUIRED_INSPECTION_CHECKS),
    }


def assert_approval_binding(payload: Mapping[str, Any], evidence: Mapping[str, Any]) -> dict[str, Any]:
    approved_by = str(evidence.get("approved_by") or "").strip()
    binding = dict(evidence.get("inspection_binding") or {})
    if not approved_by:
        raise ListingReviewError("approved_by is required")
    draft_id = require_unique_existing_draft_id(payload)
    account_key = str(dict(payload.get("shop") or {}).get("account_key") or "").strip()
    expected = {
        "artifact_version": INSPECTION_ARTIFACT_VERSION,
        "task_id": str(payload.get("task_id") or "").strip(),
        "draft_id": draft_id,
        "account_key": account_key,
        "payload_contract_sha256": build_review_contract_sha256(payload),
    }
    for name, value in expected.items():
        if str(binding.get(name) or "").strip() != str(value):
            raise ListingReviewError(f"approval inspection binding has mismatched {name}")
    if not _FULL_SHA.fullmatch(str(binding.get("inspector_build_sha") or "").strip().lower()):
        raise ListingReviewError("approval inspection binding requires inspector_build_sha")
    if not re.fullmatch(r"[0-9a-f]{64}", str(binding.get("artifact_sha256") or "").strip().lower()):
        raise ListingReviewError("approval inspection binding requires artifact_sha256")
    if int(binding.get("cdp_port") or 0) <= 0:
        raise ListingReviewError("approval inspection binding requires CDP port")
    if set(binding.get("required_checks") or []) != REQUIRED_INSPECTION_CHECKS:
        raise ListingReviewError("approval inspection binding does not cover every required field")
    return binding


def assert_submit_binding(payload: Mapping[str, Any]) -> dict[str, Any]:
    workflow = dict(payload.get("workflow") or {})
    evidence = dict(workflow.get("last_event_evidence") or {})
    binding = assert_approval_binding(payload, evidence)
    draft = dict(workflow.get("draft") or {})
    if draft.get("post_save_verified") is not True:
        raise ListingReviewError("submit requires post_save_verified from independent inspection")
    if dict(draft.get("inspection_binding") or {}) != binding:
        raise ListingReviewError("submit inspection binding does not match the reviewed draft")
    return binding
