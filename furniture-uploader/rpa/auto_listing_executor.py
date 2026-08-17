"""Guarded adapter from listing_task_payload_v1 to the existing browser uploader."""

from __future__ import annotations

import copy
from decimal import Decimal, InvalidOperation
import os
import re
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse

from auto_listing import (
    ListingContractError,
    STATE_DRAFT_PENDING,
    STATE_SUBMIT_PENDING,
    advance_listing_state,
    validate_listing_payload,
    validate_image_capacity_probe,
)
from variant_pipeline import ReleaseVariant, build_products_from_variants
from listing_review import (
    ListingReviewError,
    build_submit_reapply_contract,
    canonical_sha256,
)


ALI1688_CATEGORY_IDS = {
    "bedside_table": "122942001",
}

DRAFT_REPAIR_STEP_NAMES = {
    "main_image",
    "detail_images",
    "description",
    "buyer_protection_ship_time",
    "sanitize_spec_inputs",
    "assert_no_publish_errors",
    "logistics_dimensions",
}

FULL_DRAFT_REPAIR_STEP_NAMES = DRAFT_REPAIR_STEP_NAMES | {
    "title",
    "price",
    "price_begin_amount",
    "quantity",
    "brand",
    "material",
    "category_defaults",
    "spec_values",
    "ship_from_template",
    "freight_template",
    "ship_time_template",
}


def _enabled(name: str) -> bool:
    return str(os.getenv(name, "")).strip().lower() in {"1", "true", "yes", "on"}


def assert_execution_allowed(payload: dict[str, Any], mode: str) -> None:
    mode = str(mode or "").strip().lower()
    report = validate_listing_payload(payload, require_duplicate_clear=True)
    if report["status"] != "passed":
        raise ListingContractError("listing preflight is blocked: " + "; ".join(report["errors"]))
    state = str((payload.get("workflow") or {}).get("state") or "")
    if not _enabled("ENABLE_1688_LISTING_EXECUTION"):
        raise ListingContractError("ENABLE_1688_LISTING_EXECUTION is required for browser execution")
    if mode == "draft":
        if state != STATE_DRAFT_PENDING:
            raise ListingContractError(f"draft execution requires state {STATE_DRAFT_PENDING}")
        if _has_image_album_capacity_block(payload):
            raise ListingContractError(
                "image album capacity is blocked; a fresh passed 2-image capacity probe is required"
            )
        return
    if mode == "submit":
        if state != STATE_SUBMIT_PENDING:
            raise ListingContractError(f"submit execution requires state {STATE_SUBMIT_PENDING}")
        if not _enabled("ENABLE_1688_LISTING_SUBMIT"):
            raise ListingContractError("ENABLE_1688_LISTING_SUBMIT is required for submit")
        from listing_review import ListingReviewError, assert_submit_binding

        try:
            assert_submit_binding(payload)
        except ListingReviewError as exc:
            raise ListingContractError(str(exc)) from exc
        draft = (payload.get("workflow") or {}).get("draft") or {}
        if not str(draft.get("draft_id") or "").strip():
            raise ListingContractError("submit requires the reviewed draft_id")
        return
    raise ListingContractError("mode must be draft or submit")


def build_release_variant_payload(payload: dict[str, Any]) -> dict[str, Any]:
    product = payload.get("product") or {}
    source = payload.get("source") or {}
    shop = payload.get("shop") or {}
    pricing = payload.get("pricing") or {}
    inventory = payload.get("inventory") or {}
    images = payload.get("images") or {}
    sku = payload.get("sku") or {}
    specs = sku.get("sales_specifications") or {}
    attributes = dict(payload.get("attributes") or {})
    attributes.setdefault("sku_name", specs.get("sku_name", ""))
    attributes.setdefault("properties_value", specs.get("raw_properties_value", ""))
    logistics = payload.get("logistics") or {}
    draft = dict(dict(payload.get("workflow") or {}).get("draft") or {})
    result = {
        "variant_id": payload.get("task_id"),
        "source_product_id": product.get("spu_code"),
        "platform": "1688",
        "channel": "1688",
        "shop_name": shop.get("shop_name"),
        "title": product.get("selected_title"),
        "style_name": specs.get("sku_name") or product.get("product_name"),
        "sell_points": product.get("product_name"),
        "main_images": list(images.get("main_urls") or []),
        "sku_images": list(images.get("sku_urls") or []),
        "detail_images": list(images.get("detail_urls") or []),
        "attributes": attributes,
        "price_rule_id": pricing.get("price_rule_version"),
        "price_value": pricing.get("publish_price"),
        "publish_route": "platform-direct",
        "publish_mode": (payload.get("workflow") or {}).get("state"),
        "image_source_type": "task-detail",
        "image_source_spu": images.get("standard_model_spu"),
        "image_source_sku": source.get("company_sku"),
        "image_candidate_id": images.get("material_id"),
        "image_selection_rule": images.get("selection_policy"),
        "platform_category": product.get("category"),
        "quantity": str(inventory.get("quantity") or 999),
        "sku_rows": list(sku.get("rows") or []),
        "length_cm": logistics.get("length_cm", ""),
        "width_cm": logistics.get("width_cm", ""),
        "height_cm": logistics.get("height_cm", ""),
        "weight_g": logistics.get("weight_g", ""),
        "store_label": shop.get("shop_name"),
        "description": product.get("product_name"),
        "submit_reapply_required_fields": list(
            draft.get("submit_reapply_required_fields") or []
        ),
        "submit_reapply_evidence": dict(
            draft.get("submit_reapply_evidence") or {}
        ),
        "submit_reapply_contract_sha256": str(
            draft.get("submit_reapply_contract_sha256") or ""
        ).strip(),
    }
    return result


def build_product_record(payload: dict[str, Any], *, project_root: str | Path):
    variant = ReleaseVariant.from_payload(build_release_variant_payload(payload))
    products = build_products_from_variants(
        [variant],
        image_client=None,
        project_root=project_root,
    )
    if len(products) != 1:
        raise ListingContractError("one listing payload must produce exactly one product record")
    return products[0]


def resolve_1688_publish_url(payload: dict[str, Any], *, mode: str = "draft") -> tuple[str, str]:
    product = payload.get("product") or {}
    category_key = str(product.get("category") or "").strip()
    category_id = str(product.get("category_id") or ALI1688_CATEGORY_IDS.get(category_key, "")).strip()
    if not category_id:
        raise ListingContractError(f"1688 category id is not configured for '{category_key}'")
    if str(mode or "").strip().lower() == "submit":
        draft = (payload.get("workflow") or {}).get("draft") or {}
        draft_id = str(draft.get("draft_id") or "").strip()
        if not re.fullmatch(r"[A-Za-z0-9_-]+", draft_id):
            raise ListingContractError("submit requires a valid reviewed draft_id")
        query = urlencode({"operator": "draft2offer", "offerDraftId": draft_id})
        return f"https://offer.1688.com/offer/post/fillProductInfo.htm?{query}", category_id
    if str(mode or "").strip().lower() != "draft":
        raise ListingContractError("mode must be draft or submit")
    pending_draft_id = str(((payload.get("workflow") or {}).get("pending_draft_id") or "")).strip()
    if pending_draft_id:
        if not re.fullmatch(r"[A-Za-z0-9_-]+", pending_draft_id):
            raise ListingContractError("draft repair requires a valid pending_draft_id")
        query = urlencode(
            {
                "catId": category_id,
                "saleChannel": "default",
                "operator": "draft2offer",
                "draftId": pending_draft_id,
            }
        )
        return f"https://offer-new.1688.com/popular/publish.htm?{query}", category_id
    query = urlencode(
        {
            "catId": category_id,
            "saleChannel": "default",
            "operator": "new",
        }
    )
    return f"https://offer-new.1688.com/popular/publish.htm?{query}", category_id


def _nested_identifier(value: Any, names: set[str]) -> str:
    if isinstance(value, dict):
        for key, item in value.items():
            if str(key).lower() in names and str(item or "").strip():
                return str(item).strip()
        for item in value.values():
            found = _nested_identifier(item, names)
            if found:
                return found
    elif isinstance(value, list):
        for item in value:
            found = _nested_identifier(item, names)
            if found:
                return found
    return ""


def extract_draft_id(context: dict[str, Any]) -> str:
    trace = context.get("draft_submit_trace") or {}
    draft_id = _nested_identifier(
        (trace or {}).get("responseJson"),
        {"draftid", "offerdraftid", "draft_id"},
    )
    if draft_id:
        return draft_id
    for key in ("current_url", "publish_url"):
        query = parse_qs(urlparse(str(context.get(key) or "")).query)
        draft_id = str((query.get("draftId") or query.get("offerDraftId") or [""])[0]).strip()
        if draft_id:
            return draft_id
    return ""


def assert_repaired_draft_id(payload: dict[str, Any], actual_draft_id: str) -> None:
    pending_draft_id = str(
        ((payload.get("workflow") or {}).get("pending_draft_id") or "")
    ).strip()
    if pending_draft_id and str(actual_draft_id or "").strip() != pending_draft_id:
        raise ListingContractError(
            "draft repair returned a different draft_id: "
            f"expected {pending_draft_id}, got {str(actual_draft_id or '').strip() or 'unavailable'}"
        )


def extract_offer_id(context: dict[str, Any]) -> str:
    offer_id = str(context.get("platform_link_id") or "").strip()
    if offer_id:
        return offer_id
    trace = context.get("submit_request_trace") or {}
    offer_id = _nested_identifier((trace or {}).get("responseJson"), {"offerid", "offer_id"})
    if offer_id:
        return offer_id
    for key in ("platform_link_url", "current_url"):
        url = str(context.get(key) or "").strip()
        match = re.search(r"/offer/(\d+)", url)
        if match:
            return match.group(1)
        query = parse_qs(urlparse(url).query)
        offer_id = str((query.get("offerId") or query.get("offer_id") or [""])[0]).strip()
        if offer_id:
            return offer_id
    return ""


def _configure_auto_listing_steps(publish: dict[str, Any]) -> None:
    steps = [
        step
        for step in list(publish.get("steps") or [])
        if str(step.get("action") or "").strip() != "manual"
    ]
    publish["steps"] = steps
    for step in steps:
        if str(step.get("name") or "").strip() != "detail_images":
            continue
        step["action"] = "tinymce_images"
        step["source"] = "detail_images"
        step["required"] = True
        step["multiple"] = True
        step["picker_batch_size"] = 3
        step["max_album_rotations_per_batch"] = 12
        step["append_mode"] = "replace"
        step["use_remote_detail_urls"] = False
        step["reload_page_before_picker_batch"] = True
        step["upload_timeout_seconds"] = max(180, int(step.get("upload_timeout_seconds", 0) or 0))
        step["per_file_upload_timeout_seconds"] = max(
            120, int(step.get("per_file_upload_timeout_seconds", 0) or 0)
        )


def _configure_draft_repair_steps(
    publish: dict[str, Any],
    *,
    skip_main_image: bool = False,
    full_rebuild: bool = False,
) -> None:
    allowed_names = set(FULL_DRAFT_REPAIR_STEP_NAMES if full_rebuild else DRAFT_REPAIR_STEP_NAMES)
    if skip_main_image:
        allowed_names.discard("main_image")
    publish["steps"] = [
        step
        for step in list(publish.get("steps") or [])
        if str(step.get("name") or "").strip() in allowed_names
    ]


def _configure_submit_steps(publish: dict[str, Any]) -> None:
    publish["steps"] = []


def build_execution_payload(
    payload: dict[str, Any],
    *,
    mode: str,
    detail_limit: int = 0,
) -> dict[str, Any]:
    execution_payload = copy.deepcopy(payload)
    if not detail_limit:
        return execution_payload
    if mode != "draft" or detail_limit < 1:
        raise ValueError("--detail-limit is only valid for draft mode with a positive value")
    detail_urls = list(((payload.get("images") or {}).get("detail_urls") or []))
    if detail_limit > len(detail_urls):
        raise ValueError("--detail-limit exceeds payload detail image count")
    execution_payload.setdefault("images", {})["detail_urls"] = detail_urls[:detail_limit]
    return execution_payload


def restore_execution_only_detail_images(
    updated_payload: dict[str, Any],
    source_payload: dict[str, Any],
) -> dict[str, Any]:
    restored = copy.deepcopy(updated_payload)
    restored.setdefault("images", {})["detail_urls"] = list(
        ((source_payload.get("images") or {}).get("detail_urls") or [])
    )
    source_images = source_payload.get("images") or {}
    for field_name in ("detail_upload_resume_urls", "detail_upload_excluded_album_values"):
        if field_name in source_images:
            restored["images"][field_name] = copy.deepcopy(source_images[field_name])
        else:
            restored["images"].pop(field_name, None)
    return restored


def extract_detail_upload_resume_evidence(
    failure_payload: dict[str, Any],
    *,
    expected_task_id: str,
    expected_detail_count: int,
    batch_size: int = 3,
) -> dict[str, Any]:
    if str(failure_payload.get("task_id") or "").strip() != str(expected_task_id or "").strip():
        raise ListingContractError("detail upload resume context task_id does not match payload")
    context = failure_payload.get("result_context") or {}
    if not isinstance(context, dict):
        raise ListingContractError("detail upload resume context is missing result_context")
    if len(list(context.get("detail_images_list") or [])) != int(expected_detail_count or 0):
        raise ListingContractError("detail upload resume context image count does not match payload")

    normalized_batch_size = max(1, int(batch_size or 0))
    uploaded_urls: list[str] = []
    batch_number = 1
    while len(uploaded_urls) < expected_detail_count:
        batch_name = f"detail_images_batch_{batch_number:02d}"
        batch_urls = [
            str(item).strip()
            for item in list(context.get(f"{batch_name}_uploaded_urls") or [])
            if str(item).strip()
        ]
        expected_batch_count = min(normalized_batch_size, expected_detail_count - len(uploaded_urls))
        if len(batch_urls) != expected_batch_count:
            break
        uploaded_urls.extend(batch_urls)
        batch_number += 1

    if not uploaded_urls or len(uploaded_urls) > expected_detail_count:
        raise ListingContractError("detail upload resume context has no contiguous upload checkpoint")
    failed_batch_name = f"detail_images_batch_{batch_number:02d}"
    excluded_album_values = [
        str(item).strip()
        for item in list(context.get(f"{failed_batch_name}_failed_album_values") or [])
        if str(item).strip()
    ]
    return {
        "uploaded_urls": uploaded_urls,
        "completed_count": len(uploaded_urls),
        "complete": len(uploaded_urls) == expected_detail_count,
        "next_batch_number": batch_number,
        "excluded_album_values": excluded_album_values,
    }


def extract_submit_reconciliation_evidence(
    payload: dict[str, Any],
    failure_payload: dict[str, Any],
    *,
    expected_buyer_protection: str = "24小时发货",
    expected_buyer_protection_code: str = "essxsfh",
) -> dict[str, Any]:
    task_id = str(payload.get("task_id") or "").strip()
    if str(failure_payload.get("task_id") or "").strip() != task_id:
        raise ListingContractError("submit reconciliation evidence task_id does not match payload")
    if str((payload.get("workflow") or {}).get("state") or "").strip() != STATE_SUBMIT_PENDING:
        raise ListingContractError("submit reconciliation requires submit_pending state")
    context = failure_payload.get("result_context") or {}
    if not isinstance(context, dict):
        raise ListingContractError("submit reconciliation evidence is missing result_context")
    result_url = str(context.get("current_url") or "").strip()
    page_title = str(context.get("page_title") or "").strip()
    parsed = urlparse(result_url)
    query = parse_qs(parsed.query)
    offer_id = str((query.get("offerId") or query.get("offer_id") or [""])[0]).strip()
    if not re.fullmatch(r"\d+", offer_id):
        raise ListingContractError("submit reconciliation evidence has no numeric offerId")
    if "/result.htm" not in parsed.path or "发布成功" not in page_title:
        raise ListingContractError("submit reconciliation evidence is not a 1688 success page")
    if context.get("submit_required_fields_verified") is not True:
        raise ListingContractError("submit reconciliation evidence lacks required-field verification")
    if not str(context.get("submit_send_address_value") or "").strip():
        raise ListingContractError("submit reconciliation evidence has an empty send address")
    if str(context.get("submit_buyer_protection_value") or "").strip() != expected_buyer_protection:
        raise ListingContractError("submit reconciliation evidence has the wrong buyer protection value")
    matching_schedule = [
        item
        for item in list(context.get("submit_buyer_protection_schedule") or [])
        if isinstance(item, dict)
        and int(item.get("from", 0) or 0) == 1
        and str(item.get("serviceName") or "").strip() == expected_buyer_protection
        and str(item.get("serviceCode") or "").strip() == expected_buyer_protection_code
    ]
    if not matching_schedule:
        raise ListingContractError("submit reconciliation evidence has the wrong buyer protection schedule")
    if list(context.get("submit_blocking_assist_messages") or []):
        raise ListingContractError("submit reconciliation evidence contains blocking assist messages")
    reapply_contract = build_submit_reapply_contract(payload)
    required_reapply_fields = set(reapply_contract["required_fields"])
    reapply_results = context.get("submit_reapply_results") or {}
    reapply_evidence_source = "recorded_reapply_results"
    if required_reapply_fields and not reapply_results:
        reapply_results = _reconstruct_submit_reapply_results(
            reapply_contract,
            context,
        )
        reapply_evidence_source = "reconstructed_from_failure_context"
    elif not required_reapply_fields:
        reapply_evidence_source = "not_required"
    if not isinstance(reapply_results, dict) or set(reapply_results) != required_reapply_fields:
        raise ListingContractError(
            "submit reconciliation evidence does not cover the reviewed replay contract"
        )
    if any(
        not isinstance(reapply_results.get(name), dict)
        or reapply_results[name].get("status") != "reapplied_and_read_back"
        for name in required_reapply_fields
    ):
        raise ListingContractError(
            "submit reconciliation evidence has an unverified replay result"
        )
    return {
        "offer_id": offer_id,
        "offer_url": f"https://detail.1688.com/offer/{offer_id}.html",
        "result_url": result_url,
        "post_submit_verified": "reconciled_success_page",
        "required_fields_verified": True,
        "submit_reapply_contract_sha256": canonical_sha256(reapply_contract),
        "submit_reapply_evidence_source": reapply_evidence_source,
        "submit_reapply_results": reapply_results,
    }


def _reconstruct_submit_reapply_results(
    contract: dict[str, Any],
    context: dict[str, Any],
) -> dict[str, Any]:
    fields = dict(contract.get("fields") or {})
    required_fields = [str(item).strip() for item in contract.get("required_fields") or []]
    results: dict[str, Any] = {}
    for name in required_fields:
        evidence = dict(fields.get(name) or {})
        if name == "send_address":
            expected = str(
                evidence.get("expected_value") or evidence.get("requested_value") or ""
            ).strip()
            actual = str(context.get("submit_send_address_value") or "").strip()
            if not expected or actual != expected:
                raise ListingContractError(
                    "submit reconciliation send address does not match the reviewed replay contract"
                )
            results[name] = {"status": "reapplied_and_read_back", "actual_value": actual}
            continue

        if name == "delivery_service":
            state = dict(context.get("submit_delivery_service_state") or {})
            expected_ids = _positive_integer_set(evidence.get("requested_ids") or [])
            actual_ids = _positive_integer_set(state.get("selectedServiceIds") or [])
            allowed_ids = _positive_integer_set(state.get("allowedServiceIds") or [])
            if not expected_ids or actual_ids != expected_ids or not expected_ids.issubset(allowed_ids):
                raise ListingContractError(
                    "submit reconciliation delivery service does not match the reviewed replay contract"
                )
            results[name] = {
                "status": "reapplied_and_read_back",
                "actual_ids": sorted(actual_ids),
                "allowed_ids": sorted(allowed_ids),
            }
            continue

        if name == "logistics":
            expected_values = dict(evidence.get("expected_values") or {})
            actual_values = dict(context.get("submit_logistics_dimensions") or {})
            required_dimensions = {"length", "width", "height", "weight"}
            if set(expected_values) != required_dimensions or any(
                not _decimal_values_equal(actual_values.get(key), expected_values.get(key))
                for key in required_dimensions
            ):
                raise ListingContractError(
                    "submit reconciliation logistics do not match the reviewed replay contract"
                )
            results[name] = {
                "status": "reapplied_and_read_back",
                "actual_values": actual_values,
            }
            continue

        if name == "buyer_protection":
            expected_name = str(evidence.get("service_name") or "").strip()
            expected_code = str(evidence.get("service_code") or "").strip()
            actual_name = str(context.get("submit_buyer_protection_value") or "").strip()
            schedule = list(context.get("submit_buyer_protection_schedule") or [])
            matching = [
                item
                for item in schedule
                if isinstance(item, dict)
                and int(item.get("from", 0) or 0) == 1
                and str(item.get("serviceName") or "").strip() == expected_name
                and str(item.get("serviceCode") or "").strip() == expected_code
            ]
            if not expected_name or not expected_code or actual_name != expected_name or not matching:
                raise ListingContractError(
                    "submit reconciliation buyer protection does not match the reviewed replay contract"
                )
            results[name] = {
                "status": "reapplied_and_read_back",
                "service_name": actual_name,
                "service_code": expected_code,
                "schedule": schedule,
            }
            continue

        raise ListingContractError(
            f"submit reconciliation cannot reconstruct unsupported replay field: {name}"
        )
    return results


def _positive_integer_set(values: Any) -> set[int]:
    normalized: set[int] = set()
    for value in list(values or []):
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            continue
        if parsed > 0:
            normalized.add(parsed)
    return normalized


def _decimal_values_equal(left: Any, right: Any) -> bool:
    try:
        return Decimal(str(left).strip()) == Decimal(str(right).strip())
    except (InvalidOperation, ValueError):
        return False


def _validated_submit_reapply_evidence(
    payload: dict[str, Any],
    *,
    draft_id: str,
    required_fields: Any,
    raw_evidence: Any,
) -> tuple[list[str], dict[str, Any], str]:
    fields = [
        str(item or "").strip()
        for item in list(required_fields or [])
        if str(item or "").strip()
    ]
    if raw_evidence and not isinstance(raw_evidence, dict):
        raise ListingContractError("submit reapply evidence must be an object")
    evidence = dict(raw_evidence or {})
    if not fields:
        if evidence:
            raise ListingContractError(
                "submit reapply evidence is present without required fields"
            )
        return [], {}, ""

    contract_hash = canonical_sha256(evidence)
    candidate = copy.deepcopy(payload)
    workflow = candidate.setdefault("workflow", {})
    workflow["pending_draft_id"] = draft_id
    draft = dict(workflow.get("draft") or {})
    draft.update(
        {
            "draft_id": draft_id,
            "submit_reapply_required_fields": fields,
            "submit_reapply_evidence": evidence,
            "submit_reapply_contract_sha256": contract_hash,
        }
    )
    workflow["draft"] = draft
    try:
        normalized = build_submit_reapply_contract(candidate)
    except ListingReviewError as exc:
        raise ListingContractError(str(exc)) from exc
    normalized_hash = canonical_sha256(normalized)
    return fields, normalized, normalized_hash


def extract_draft_reconciliation_evidence(
    payload: dict[str, Any],
    failure_payload: dict[str, Any],
    inspection_payload: dict[str, Any],
) -> dict[str, Any]:
    task_id = str(payload.get("task_id") or "").strip()
    if str(failure_payload.get("task_id") or "").strip() != task_id:
        raise ListingContractError("draft reconciliation evidence task_id does not match payload")
    if str((payload.get("workflow") or {}).get("state") or "").strip() != STATE_DRAFT_PENDING:
        raise ListingContractError("draft reconciliation requires draft_pending state")
    if str(failure_payload.get("error_type") or "").strip() != "PublishValidationError":
        raise ListingContractError("draft reconciliation requires a post-save validation failure")

    context = failure_payload.get("result_context") or {}
    if not isinstance(context, dict):
        raise ListingContractError("draft reconciliation evidence is missing result_context")
    trace = context.get("draft_submit_trace") or {}
    if not isinstance(trace, dict):
        raise ListingContractError("draft reconciliation evidence is missing draft_submit_trace")
    if int(trace.get("status") or context.get("draft_submit_response_status") or 0) != 200:
        raise ListingContractError("draft reconciliation evidence has a non-200 save response")
    response_json = trace.get("responseJson") or {}
    if not isinstance(response_json, dict) or response_json.get("success") is not True:
        raise ListingContractError("draft reconciliation evidence has no successful save response")
    response_draft_id = _nested_identifier(
        response_json,
        {"draftid", "offerdraftid", "draft_id"},
    )
    if not re.fullmatch(r"[A-Za-z0-9_-]+", response_draft_id):
        raise ListingContractError("draft reconciliation evidence has no valid response draft_id")

    current_url = str(context.get("current_url") or "").strip()
    current_parsed = urlparse(current_url)
    if current_parsed.hostname != "offer-new.1688.com" or current_parsed.path != "/popular/publish.htm":
        raise ListingContractError("draft reconciliation evidence is not a 1688 draft page")
    trace_parsed = urlparse(str(trace.get("url") or ""))
    if trace_parsed.hostname != "offer-new.1688.com" or not trace_parsed.path.endswith("/draftSubmit.htm"):
        raise ListingContractError("draft reconciliation evidence is not a 1688 draft save request")
    current_query = parse_qs(current_parsed.query)
    page_draft_id = str(
        (current_query.get("draftId") or current_query.get("offerDraftId") or [""])[0]
    ).strip()
    if page_draft_id != response_draft_id:
        raise ListingContractError("draft reconciliation page draft_id does not match save response")

    if str(inspection_payload.get("task_id") or "").strip() != task_id:
        raise ListingContractError("draft inspection task_id does not match payload")
    if str(inspection_payload.get("draft_id") or "").strip() != response_draft_id:
        raise ListingContractError("draft inspection draft_id does not match save response")
    if inspection_payload.get("draft_saved") is not False or inspection_payload.get("offer_submitted") is not False:
        raise ListingContractError("draft inspection must be read-only")
    inspection_status = str(inspection_payload.get("status") or "").strip()
    checks = inspection_payload.get("checks") or {}
    if inspection_status not in {"passed", "failed"} or not isinstance(checks, dict) or not checks:
        raise ListingContractError("draft inspection status or checks are invalid")
    if checks.get("draft_id") is not True:
        raise ListingContractError("draft inspection did not verify the draft_id")

    missing_checks = sorted(str(name) for name, passed in checks.items() if passed is not True)
    full_rebuild_checks = {
        "title",
        "price",
        "quantity",
        "main_image_present",
        "main_image_square",
        "detail_image_count",
        "spec_color",
        "logistics",
    }
    repair_scope = "full" if full_rebuild_checks.intersection(missing_checks) else "required_fields"
    (
        submit_reapply_required_fields,
        submit_reapply_evidence,
        submit_reapply_contract_sha256,
    ) = _validated_submit_reapply_evidence(
        payload,
        draft_id=response_draft_id,
        required_fields=context.get("draft_submit_reapply_required_fields"),
        raw_evidence=context.get("draft_submit_reapply_evidence"),
    )
    result = {
        "draft_id": response_draft_id,
        "draft_url": (
            "https://offer.1688.com/offer/post/fillProductInfo.htm?"
            + urlencode({"operator": "draft2offer", "offerDraftId": response_draft_id})
        ),
        "publish_url": current_url,
        "draft_response_status": 200,
        "post_save_verified": inspection_status == "passed" and not missing_checks,
        "reconciled_after_validation_failure": True,
        "repair_scope": repair_scope,
        "missing_checks": missing_checks,
        "inspection_checked_at": str(inspection_payload.get("checked_at") or "").strip(),
        "validation_error": str(failure_payload.get("error") or "").strip(),
        "submit_reapply_required_fields": submit_reapply_required_fields,
        "submit_reapply_evidence": submit_reapply_evidence,
    }
    if submit_reapply_required_fields:
        result["submit_reapply_contract_sha256"] = submit_reapply_contract_sha256
    return result


def _has_image_album_capacity_block(payload: dict[str, Any]) -> bool:
    workflow = payload.get("workflow") or {}
    events = list(workflow.get("event_history") or [])
    if workflow.get("last_event") and (
        not events or str(events[-1].get("event") or "") != str(workflow.get("last_event") or "")
    ):
        events.append(
            {
                "event": workflow.get("last_event"),
                "evidence": workflow.get("last_event_evidence") or {},
            }
        )
    blocked = False
    for item in events:
        if not isinstance(item, dict):
            continue
        event = str(item.get("event") or "").strip()
        reason = str((item.get("evidence") or {}).get("reason") or "").strip()
        if event == "execution_blocked" and reason == "image_album_full":
            blocked = True
        elif event in {"execution_resumed", "capacity_reverified"} and blocked:
            evidence = dict(item.get("evidence") or {})
            workflow_draft_id = str(workflow.get("pending_draft_id") or "").strip()
            if (
                reason == "image_album_capacity_verified"
                and not validate_image_capacity_probe(
                    dict(evidence.get("capacity_probe") or {}),
                    expected_draft_id=workflow_draft_id,
                )
            ):
                blocked = False
    return blocked


def execute_browser_task(
    payload: dict[str, Any],
    *,
    mode: str,
    browser: Any,
    platform_config: dict[str, Any],
    category_config: dict[str, Any],
    project_root: str | Path,
) -> tuple[dict[str, Any], dict[str, Any]]:
    assert_execution_allowed(payload, mode)
    effective_config = copy.deepcopy(platform_config)
    publish = effective_config.setdefault("publish", {})
    publish_url, expected_category_id = resolve_1688_publish_url(payload, mode=mode)
    effective_config["publish_url"] = publish_url
    publish["expected_category_id"] = expected_category_id
    pending_draft_id = str(((payload.get("workflow") or {}).get("pending_draft_id") or "")).strip()
    if mode == "submit" or pending_draft_id:
        publish["expected_draft_id"] = str(
            (
                ((payload.get("workflow") or {}).get("draft") or {}).get("draft_id")
                if mode == "submit"
                else pending_draft_id
            )
            or ""
        ).strip()
    publish["pause_before_submit"] = False
    publish["auto_save_draft"] = mode == "draft"
    publish["auto_submit"] = mode == "submit"
    if mode == "submit":
        _configure_submit_steps(publish)
    else:
        _configure_auto_listing_steps(publish)
        if pending_draft_id:
            draft_evidence = ((payload.get("workflow") or {}).get("draft") or {})
            _configure_draft_repair_steps(
                publish,
                full_rebuild=str(draft_evidence.get("repair_scope") or "").strip() == "full",
            )
    detail_image_count = len(list(((payload.get("images") or {}).get("detail_urls") or [])))
    if detail_image_count:
        publish.setdefault("draft_verification", {})["minimum_description_image_count"] = detail_image_count
    product = build_product_record(payload, project_root=project_root)
    product.raw["detail_upload_resume_urls"] = list(
        ((payload.get("images") or {}).get("detail_upload_resume_urls") or [])
    )
    product.raw["detail_upload_excluded_album_values"] = list(
        ((payload.get("images") or {}).get("detail_upload_excluded_album_values") or [])
    )
    category_entry = category_config.get(product.platform_category, {})
    publish["expected_category_path"] = str(
        (category_entry.get("platform_categories") or {}).get("1688") or ""
    ).strip()
    context = browser.publish_product(effective_config, product, category_config)
    if mode == "draft":
        draft_id = extract_draft_id(context)
        if not draft_id:
            raise ListingContractError("draft save completed without an extractable draft_id")
        assert_repaired_draft_id(payload, draft_id)
        (
            submit_reapply_required_fields,
            submit_reapply_evidence,
            submit_reapply_contract_sha256,
        ) = _validated_submit_reapply_evidence(
            payload,
            draft_id=draft_id,
            required_fields=context.get("draft_submit_reapply_required_fields"),
            raw_evidence=context.get("draft_submit_reapply_evidence"),
        )
        evidence = {
            "draft_id": draft_id,
            "draft_url": (
                "https://offer.1688.com/offer/post/fillProductInfo.htm?"
                + urlencode({"operator": "draft2offer", "offerDraftId": draft_id})
            ),
            "publish_url": str(context.get("current_url") or context.get("platform_link_url") or ""),
            "draft_response_status": context.get("draft_submit_response_status"),
            "draft_response_draft_id": context.get("draft_submit_response_draft_id"),
            "draft_identity_evidence": context.get("draft_submit_identity_evidence"),
            "detail_image_delivery_mode": context.get("detail_images_delivery_mode"),
            "detail_image_count": context.get("draft_description_image_count"),
            "browser_post_save_verified": True,
            "post_save_verified": False,
            "submit_reapply_required_fields": submit_reapply_required_fields,
            "submit_reapply_evidence": submit_reapply_evidence,
            "nonpersistent_assist_messages_ignored": list(
                context.get("draft_nonpersistent_assist_messages_ignored", []) or []
            ),
        }
        if submit_reapply_required_fields:
            evidence["submit_reapply_contract_sha256"] = submit_reapply_contract_sha256
        return advance_listing_state(payload, "draft_saved", evidence=evidence), context

    offer_id = extract_offer_id(context)
    offer_url = str(context.get("platform_link_url") or context.get("current_url") or "").strip()
    if not offer_id:
        raise ListingContractError("submit completed without an extractable offer_id")
    if "/offer/" not in offer_url:
        offer_url = f"https://detail.1688.com/offer/{offer_id}.html"
    evidence = {"offer_id": offer_id, "offer_url": offer_url, "post_submit_verified": context.get("post_submit_verified")}
    return advance_listing_state(payload, "submit_succeeded", evidence=evidence), context
