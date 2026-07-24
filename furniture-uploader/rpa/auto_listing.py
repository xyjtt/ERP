"""Safe, deterministic contract and state machine for the 1688 MVP listing flow."""

from __future__ import annotations

import json
import random
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_CEILING
from typing import Any, Iterable


SCHEMA_VERSION = "listing_task_payload_v1"
PRICE_RULE_VERSION = "jst_sale_price_multiplier_temp_v1"
PRICE_MULTIPLIER = Decimal("2.5")
DEFAULT_QUANTITY = 999
PLATFORM = "1688"
IMAGE_SOURCE = "yidian"
IMAGE_CAPACITY_PROBE_MAX_AGE_SECONDS = 30 * 60
SOURCE_LIFECYCLE_ACTIVE = "销售"

STATE_DRAFT_PENDING = "draft_pending"
STATE_DRAFT_PENDING_REVIEW = "draft_pending_review"
STATE_SUBMIT_PENDING = "submit_pending"
STATE_SUBMITTED = "submitted"
STATE_OFFER_WRITTEN_BACK = "offer_written_back"
STATE_BLOCKED = "blocked"

ALLOWED_STATES = {
    STATE_DRAFT_PENDING,
    STATE_DRAFT_PENDING_REVIEW,
    STATE_SUBMIT_PENDING,
    STATE_SUBMITTED,
    STATE_OFFER_WRITTEN_BACK,
    STATE_BLOCKED,
}


class ListingContractError(ValueError):
    """Raised when a listing payload cannot safely enter the workflow."""


def validate_image_capacity_probe(
    probe: dict[str, Any],
    *,
    expected_draft_id: str,
    now: datetime | None = None,
) -> list[str]:
    errors: list[str] = []
    if str(probe.get("status") or "").strip().lower() != "passed":
        errors.append("capacity probe status must be passed")
    try:
        probe_count = int(probe.get("probe_count") or 0)
        uploaded_count = int(probe.get("uploaded_count") or 0)
    except (TypeError, ValueError):
        probe_count = 0
        uploaded_count = 0
    if probe_count < 2 or uploaded_count != probe_count:
        errors.append("capacity probe must upload at least 2/2 images")
    remote_hosts = [
        str(item or "").strip().lower()
        for item in list(probe.get("remote_hosts") or [])
        if str(item or "").strip()
    ]
    if not remote_hosts or any(not host.endswith(".alicdn.com") for host in remote_hosts):
        errors.append("capacity probe must return Alibaba CDN URLs")
    if bool(probe.get("draft_saved")) or bool(probe.get("offer_submitted")):
        errors.append("capacity probe must not save or submit the listing")
    draft_id = str(probe.get("draft_id") or "").strip()
    if not expected_draft_id or draft_id != expected_draft_id:
        errors.append("capacity probe draft_id must match pending_draft_id")

    checked_at_raw = str(probe.get("checked_at") or "").strip()
    try:
        checked_at = datetime.fromisoformat(checked_at_raw.replace("Z", "+00:00"))
        if checked_at.tzinfo is None:
            raise ValueError("timezone is required")
        current = now or datetime.now(timezone.utc)
        age_seconds = (current.astimezone(timezone.utc) - checked_at.astimezone(timezone.utc)).total_seconds()
        if age_seconds < -300 or age_seconds > IMAGE_CAPACITY_PROBE_MAX_AGE_SECONDS:
            errors.append("capacity probe is stale")
    except ValueError:
        errors.append("capacity probe checked_at is invalid")
    return errors


@dataclass(frozen=True)
class PriceResult:
    sale_price: str
    multiplier: str
    raw_price: str
    publish_price: str
    rounding_policy: str = "ceiling_1dp"

    def to_dict(self) -> dict[str, str]:
        return {
            "price_rule_version": PRICE_RULE_VERSION,
            "sale_price": self.sale_price,
            "price_multiplier": self.multiplier,
            "temporary_price_raw": self.raw_price,
            "publish_price": self.publish_price,
            "rounding_policy": self.rounding_policy,
            "price_source": "JianSun.dbo.jst_sku_View.sale_price",
        }


def _decimal(value: Any, field_name: str) -> Decimal:
    try:
        parsed = Decimal(str(value).strip())
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ListingContractError(f"{field_name} must be a decimal value") from exc
    if not parsed.is_finite() or parsed <= 0:
        raise ListingContractError(f"{field_name} must be greater than zero")
    return parsed


def _positive_text(value: Any, field_name: str) -> str:
    parsed = _decimal(value, field_name)
    return format(parsed.normalize(), "f")


def _weight_grams(product: dict[str, Any]) -> str:
    grams = product.get("weight_g")
    if grams not in (None, ""):
        return _positive_text(grams, "weight_g")
    kilograms = product.get("weight")
    if kilograms in (None, ""):
        raise ListingContractError("weight or weight_g is required")
    return _positive_text(_decimal(kilograms, "weight") * Decimal("1000"), "weight_g")


def _format_decimal(value: Decimal, places: int = 1) -> str:
    quantizer = Decimal(1).scaleb(-places)
    return format(value.quantize(quantizer), f".{places}f")


def calculate_publish_price(sale_price: Any, multiplier: Any = PRICE_MULTIPLIER) -> PriceResult:
    sale = _decimal(sale_price, "sale_price")
    factor = _decimal(multiplier, "price_multiplier")
    raw = sale * factor
    publish = (raw * Decimal("10")).to_integral_value(rounding=ROUND_CEILING) / Decimal("10")
    return PriceResult(
        sale_price=format(sale, "f"),
        multiplier=format(factor, "f"),
        raw_price=format(raw, "f"),
        publish_price=_format_decimal(publish, 1),
    )


def novelty_note(novelty_type: str) -> str:
    normalized = str(novelty_type or "").strip().lower().replace("-", "_")
    if normalized not in {"new_spu", "new_sku"}:
        raise ListingContractError("novelty_type must be new_spu or new_sku")
    return "\u8d27\u53f7\uff1a\u65b0 SPU" if normalized == "new_spu" else "\u8d27\u53f7\uff1a\u65b0 SKU"


def parse_sales_specifications(raw_value: Any, product_name: str = "") -> dict[str, Any]:
    """Preserve company SKU naming while exposing stable dimensions for review."""
    text = str(raw_value or "").strip()
    tokens = [item.strip() for item in re.split(r"[;|]+", text) if item.strip()]
    name = str(product_name or "").strip()
    dimension_tokens = re.findall(r"\d+(?:\.\d+)?(?:\s*[xX*/]\s*\d+(?:\.\d+)?){1,2}", name)
    return {
        "sku_name": name,
        "raw_properties_value": text,
        "matched_tokens": tokens,
        "dimensions_from_name": dimension_tokens,
        "match_method": "jst_sku_name_and_properties_value",
    }


def _candidate_value(candidate: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if candidate.get(key) not in (None, "", []):
            return candidate[key]
    return None


def _source_flag_matches(value: Any, expected: bool) -> bool:
    if isinstance(value, bool):
        return value is expected
    normalized = str(value if value is not None else "").strip().lower()
    if expected:
        return normalized in {"1", "true", "yes"}
    return normalized in {"0", "false", "no"}


def select_latest_complete_yidian_bundle(candidates: Iterable[dict[str, Any]]) -> dict[str, Any]:
    normalized: list[dict[str, Any]] = []
    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        main_urls = list(_candidate_value(candidate, "main_urls", "mainUrls") or [])
        sku_urls = list(_candidate_value(candidate, "sku_urls", "skuUrls") or [])
        detail_urls = list(_candidate_value(candidate, "detail_urls", "detailUrls") or [])
        complete = bool(_candidate_value(candidate, "is_full_set", "isFullSet"))
        if not complete or not main_urls or not sku_urls or not detail_urls:
            continue
        normalized.append({
            **candidate,
            "material_id": str(_candidate_value(candidate, "material_id", "materialId") or "").strip(),
            "uploaded_at": str(_candidate_value(candidate, "uploaded_at", "uploadedAt") or "").strip(),
            "main_urls": [str(item).strip() for item in main_urls if str(item).strip()],
            "sku_urls": [str(item).strip() for item in sku_urls if str(item).strip()],
            "detail_urls": [str(item).strip() for item in detail_urls if str(item).strip()],
            "is_full_set": True,
        })
    if not normalized:
        raise ListingContractError("No complete Yidian image bundle is available")
    normalized.sort(key=lambda item: (item["uploaded_at"], item["material_id"]), reverse=True)
    return normalized[0]


def _preflight_check(name: str, passed: bool, reason: str = "") -> dict[str, Any]:
    return {"name": name, "status": "passed" if passed else "blocked", "reason": reason}


def validate_listing_payload(payload: dict[str, Any], *, require_duplicate_clear: bool = True) -> dict[str, Any]:
    errors: list[str] = []
    checks: list[dict[str, Any]] = []
    if payload.get("schema_version") != SCHEMA_VERSION:
        errors.append("schema_version must be listing_task_payload_v1")
    if payload.get("platform") != PLATFORM:
        errors.append("platform must be 1688")

    shop = payload.get("shop") or {}
    product = payload.get("product") or {}
    pricing = payload.get("pricing") or {}
    images = payload.get("images") or {}
    sku = payload.get("sku") or {}
    source = payload.get("source") or {}
    logistics = payload.get("logistics") or {}

    checks.append(_preflight_check("shop", bool(shop.get("shop_name") and shop.get("account_key")), "shop mapping is required"))
    checks.append(_preflight_check(
        "source_eligibility",
        _source_flag_matches(source.get("enabled"), True)
        and _source_flag_matches(source.get("stock_disabled"), False)
        and str(source.get("lifecycle_status") or "").strip() == SOURCE_LIFECYCLE_ACTIVE,
        "source product must have enabled=1, stock_disabled=0 and other_5=销售",
    ))
    checks.append(_preflight_check("selected_title", bool(str(product.get("selected_title") or "").strip()), "manual title selection is required"))
    checks.append(_preflight_check("category", bool(str(product.get("category") or "").strip()), "category is required"))
    checks.append(_preflight_check("price", bool(pricing.get("publish_price")), "publish price is required"))
    checks.append(_preflight_check("inventory", int((payload.get("inventory") or {}).get("quantity") or 0) == DEFAULT_QUANTITY, "quantity must be 999"))
    checks.append(_preflight_check("yidian_images", images.get("source") == IMAGE_SOURCE and bool(images.get("material_id")), "latest complete Yidian bundle is required"))
    checks.append(_preflight_check("sku_sales_row", bool(sku.get("rows")) and len(sku.get("rows")) == 1 and bool(sku["rows"][0].get("sku_name")), "one independent SKU sales row is required"))
    checks.append(_preflight_check(
        "logistics",
        all(logistics.get(key) not in (None, "") for key in ("length_cm", "width_cm", "height_cm", "weight_g")),
        "length_cm, width_cm, height_cm and weight_g are required",
    ))
    duplicate_status = str((source.get("duplicate_check") or {}).get("status") or "pending").lower()
    checks.append(_preflight_check("live_duplicate_check", duplicate_status == "clear" if require_duplicate_clear else duplicate_status in {"clear", "pending"}, "live duplicate check must be clear"))

    errors.extend(check["reason"] for check in checks if check["status"] != "passed")
    return {
        "status": "passed" if not errors else "blocked",
        "errors": list(dict.fromkeys(errors)),
        "checks": checks,
        "checked_at": datetime.now(timezone.utc).isoformat(),
    }


def build_listing_payload(
    product: dict[str, Any],
    *,
    shop_name: str,
    account_key: str,
    novelty_type: str,
    selected_title: str,
    yidian_candidates: Iterable[dict[str, Any]],
    duplicate_status: str = "pending",
    duplicate_evidence: dict[str, Any] | None = None,
    selected_title_metadata: dict[str, Any] | None = None,
    require_duplicate_clear: bool = False,
) -> dict[str, Any]:
    sku_code = str(product.get("sku_id") or product.get("sku_code") or "").strip()
    spu_code = str(product.get("spu") or product.get("spu_code") or "").strip()
    product_name = str(product.get("name") or product.get("product_name") or "").strip()
    if not sku_code or not spu_code or not product_name:
        raise ListingContractError("sku_id, spu and name are required")
    title = str(selected_title or "").strip()
    if not title:
        raise ListingContractError("selected_title is required")
    price = calculate_publish_price(product.get("sale_price"))
    lifecycle_status = str(product.get("other_5") or product.get("lifecycle_status") or "").strip()
    if not _source_flag_matches(product.get("enabled"), True):
        raise ListingContractError("source product enabled must be 1")
    if not _source_flag_matches(product.get("stock_disabled"), False):
        raise ListingContractError("source product stock_disabled must be 0")
    if lifecycle_status != SOURCE_LIFECYCLE_ACTIVE:
        raise ListingContractError("source product other_5 must be 销售")
    bundle = select_latest_complete_yidian_bundle(yidian_candidates)
    novelty = str(novelty_type).strip().lower().replace("-", "_")
    sales_specs = parse_sales_specifications(product.get("properties_value"), product_name)
    payload = {
        "schema_version": SCHEMA_VERSION,
        "task_id": f"1688-listing-{sku_code}",
        "platform": PLATFORM,
        "shop": {"shop_name": shop_name, "account_key": account_key},
        "source": {
            "novelty_type": novelty,
            "novelty_note": novelty_note(novelty),
            "source_table": "JSDataMiddlePlatform.dbo.jst_sku",
            "enabled": 1,
            "stock_disabled": 0,
            "lifecycle_field": "other_5",
            "lifecycle_status": lifecycle_status,
            "company_sku": sku_code,
            "company_spu": spu_code,
            "duplicate_check": {"status": duplicate_status, **(duplicate_evidence or {})},
        },
        "product": {
            "sku_code": sku_code,
            "spu_code": spu_code,
            "product_name": product_name,
            "category": str(product.get("category") or "").strip(),
            "selected_title": title,
            "selected_title_metadata": selected_title_metadata or {},
        },
        "pricing": price.to_dict(),
        "inventory": {"quantity": DEFAULT_QUANTITY, "quantity_source": "business_default"},
        "logistics": {
            "length_cm": _positive_text(product.get("l") or product.get("length_cm"), "length_cm"),
            "width_cm": _positive_text(product.get("w") or product.get("width_cm"), "width_cm"),
            "height_cm": _positive_text(product.get("h") or product.get("height_cm"), "height_cm"),
            "weight_g": _weight_grams(product),
            "source": "JSDataMiddlePlatform.dbo.jst_sku.l_w_h_weight",
        },
        "images": {
            "source": IMAGE_SOURCE,
            "company_sku": sku_code,
            "company_spu": spu_code,
            "standard_model_spu": str(product.get("standard_model_spu") or spu_code).strip(),
            "resource_type": "task-detail",
            "material_id": bundle["material_id"],
            "uploaded_at": bundle["uploaded_at"],
            "selection_policy": "latest_complete",
            "main_urls": bundle["main_urls"],
            "sku_urls": bundle["sku_urls"],
            "detail_urls": bundle["detail_urls"],
        },
        "attributes": dict(product.get("attributes") or {}),
        "sku": {
            "naming_source": "JianSun.dbo.jst_sku.name",
            "sales_specifications": sales_specs,
            "rows": [{
                "sku_code": sku_code,
                "sku_name": product_name,
                "properties_value": str(product.get("properties_value") or "").strip(),
                "price": price.publish_price,
                "quantity": DEFAULT_QUANTITY,
                "specifications": sales_specs,
            }],
        },
        "workflow": {
            "state": STATE_DRAFT_PENDING,
            "approval_required": True,
            "batch_count_unit": "1688_spu_link",
            "independent_link_required": True,
            "submit_mode": "single_offer",
        },
    }
    payload["preflight"] = validate_listing_payload(payload, require_duplicate_clear=require_duplicate_clear)
    return payload


def advance_listing_state(payload: dict[str, Any], event: str, *, evidence: dict[str, Any] | None = None) -> dict[str, Any]:
    current = str((payload.get("workflow") or {}).get("state") or "").strip()
    event = str(event or "").strip()
    evidence = evidence or {}
    transitions = {
        (STATE_DRAFT_PENDING, "draft_saved"): STATE_DRAFT_PENDING_REVIEW,
        (STATE_DRAFT_PENDING_REVIEW, "review_approved"): STATE_SUBMIT_PENDING,
        (STATE_DRAFT_PENDING_REVIEW, "review_rejected"): STATE_BLOCKED,
        (STATE_SUBMIT_PENDING, "submit_succeeded"): STATE_SUBMITTED,
        (STATE_SUBMITTED, "offer_written_back"): STATE_OFFER_WRITTEN_BACK,
        (STATE_DRAFT_PENDING, "execution_blocked"): STATE_BLOCKED,
        (STATE_DRAFT_PENDING_REVIEW, "execution_blocked"): STATE_BLOCKED,
        (STATE_SUBMIT_PENDING, "execution_blocked"): STATE_BLOCKED,
        (STATE_BLOCKED, "execution_resumed"): STATE_DRAFT_PENDING,
        (STATE_BLOCKED, "review_repair_resumed"): STATE_DRAFT_PENDING,
        (STATE_DRAFT_PENDING, "capacity_reverified"): STATE_DRAFT_PENDING,
    }
    next_state = transitions.get((current, event))
    if next_state is None:
        raise ListingContractError(f"invalid listing state transition: {current} -> {event}")
    if event == "review_approved" and not evidence.get("approved_by"):
        raise ListingContractError("approved_by is required")
    if event == "submit_succeeded" and not evidence.get("offer_id"):
        raise ListingContractError("offer_id is required after submit")
    if event == "offer_written_back" and not evidence.get("offer_url"):
        raise ListingContractError("offer_url is required for writeback")
    review_repair_draft_id = ""
    if event in {"execution_resumed", "review_repair_resumed", "capacity_reverified"}:
        workflow = payload.get("workflow") or {}
        blocked_reason = str((workflow.get("last_event_evidence") or {}).get("reason") or "").strip()
        pending_draft_id = str(workflow.get("pending_draft_id") or "").strip()
        if event == "execution_resumed" and blocked_reason != "image_album_full":
            raise ListingContractError("only image_album_full blocks can be resumed automatically")
        if event == "review_repair_resumed":
            if str(workflow.get("last_event") or "").strip() != "review_rejected":
                raise ListingContractError("review repair requires the latest event to be review_rejected")
            review_repair_draft_id = str(((workflow.get("draft") or {}).get("draft_id") or "")).strip()
            pending_draft_id = review_repair_draft_id
            if str(evidence.get("reason") or "").strip() != "review_required_fields_repair":
                raise ListingContractError("review repair requires an explicit required-fields repair reason")
        if not pending_draft_id:
            raise ListingContractError("image capacity verification requires the existing pending_draft_id")
        operator_field = "resumed_by" if event in {"execution_resumed", "review_repair_resumed"} else "verified_by"
        if not str(evidence.get(operator_field) or "").strip():
            raise ListingContractError(f"{operator_field} is required")
        if event != "review_repair_resumed" and str(evidence.get("reason") or "").strip() != "image_album_capacity_verified":
            raise ListingContractError("image capacity verification requires verified image album capacity")
        probe_errors = validate_image_capacity_probe(
            dict(evidence.get("capacity_probe") or {}),
            expected_draft_id=pending_draft_id,
        )
        if probe_errors:
            raise ListingContractError("invalid image capacity probe: " + "; ".join(probe_errors))
    next_payload = json.loads(json.dumps(payload, ensure_ascii=False, default=str))
    workflow = next_payload.setdefault("workflow", {})
    workflow["state"] = next_state
    workflow["last_event"] = event
    workflow["last_event_evidence"] = evidence
    event_history = list(workflow.get("event_history") or [])
    event_history.append({
        "event": event,
        "from_state": current,
        "to_state": next_state,
        "evidence": evidence,
        "recorded_at": datetime.now(timezone.utc).isoformat(),
    })
    workflow["event_history"] = event_history
    if event == "review_repair_resumed":
        workflow["pending_draft_id"] = review_repair_draft_id
    if event == "draft_saved":
        workflow["draft"] = dict(evidence)
        workflow.pop("pending_draft_id", None)
    elif event == "submit_succeeded":
        workflow["offer"] = dict(evidence)
    return next_payload


def allocate_category_counts(categories: Iterable[str], total: int, *, seed: int) -> dict[str, int]:
    category_list = [str(item).strip() for item in categories if str(item).strip()]
    if total < 0 or not category_list:
        raise ListingContractError("categories and non-negative total are required")
    shuffled = list(category_list)
    random.Random(seed).shuffle(shuffled)
    base, remainder = divmod(total, len(shuffled))
    allocation = {category: base for category in category_list}
    for category in shuffled[:remainder]:
        allocation[category] += 1
    return allocation
