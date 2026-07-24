"""Refresh and inspect one saved 1688 draft without saving or submitting it."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
RPA_ROOT = PROJECT_ROOT / "rpa"
if str(RPA_ROOT) not in sys.path:
    sys.path.insert(0, str(RPA_ROOT))

from auto_listing_executor import resolve_1688_publish_url
from browser_rpa import BrowserRPA
from config_loader import load_json_with_local_override


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Read-only independent refresh check for one 1688 draft.")
    parser.add_argument("--payload", required=True)
    parser.add_argument("--output", required=True)
    return parser


def _decimal_equal(left: object, right: object) -> bool:
    try:
        return Decimal(str(left)) == Decimal(str(right))
    except (InvalidOperation, TypeError, ValueError):
        return False


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    args = build_parser().parse_args()
    payload = json.loads(Path(args.payload).read_text(encoding="utf-8-sig"))
    expected_draft_id = str(((payload.get("workflow") or {}).get("draft") or {}).get("draft_id") or "").strip()
    expected_detail_count = len(list(((payload.get("images") or {}).get("detail_urls") or [])))
    expected_title = str((payload.get("product") or {}).get("selected_title") or "").strip()
    expected_price = str((payload.get("pricing") or {}).get("publish_price") or "").strip()
    expected_quantity = int((payload.get("inventory") or {}).get("quantity") or 0)
    expected_logistics = {
        "length": str((payload.get("logistics") or {}).get("length_cm") or "").strip(),
        "width": str((payload.get("logistics") or {}).get("width_cm") or "").strip(),
        "height": str((payload.get("logistics") or {}).get("height_cm") or "").strip(),
        "weight": str((payload.get("logistics") or {}).get("weight_g") or "").strip(),
    }
    reapply_fields = list(
        (((payload.get("workflow") or {}).get("draft") or {}).get("submit_reapply_required_fields") or [])
    )

    config_dir = PROJECT_ROOT / "config"
    operator_config = load_json_with_local_override(config_dir / "operator_config.json")
    browser = BrowserRPA(operator_config.get("browser", {}), PROJECT_ROOT)
    browser.open()
    try:
        publish_url, _category_id = resolve_1688_publish_url(
            {
                **payload,
                "workflow": {
                    **(payload.get("workflow") or {}),
                    "pending_draft_id": expected_draft_id,
                },
            },
            mode="draft",
        )
        if expected_draft_id not in str(browser.driver.current_url or ""):
            browser.driver.get(publish_url)
        else:
            browser.driver.refresh()
        browser._wait_for_publish_runtime_ready(timeout_seconds=180)
        browser._pause(2.0)
        core = browser.driver.execute_script(
            """
            const sdk = window.SellPublishSdk;
            const state = sdk && sdk.engine && sdk.engine.getJsonState ? sdk.engine.getJsonState() : {};
            const components = (state || {}).components || {};
            const props = (name) => ((components[name] || {}).props || {});
            const value = (name) => props(name).value;
            const priceRange = Array.isArray(value('priceRange')) ? value('priceRange') : [];
            const skuTable = Array.isArray(value('skuTable')) ? value('skuTable') : [];
            const draftId = String(
              new URL(window.location.href).searchParams.get('draftId') ||
              (((state || {}).global || {}).renderData || {}).draftId ||
              ''
            ).trim();
            return {
              draft_id: draftId,
              title: String(value('title') || '').trim(),
              price: String(((priceRange[0] || {}).pricerange_price) || ((skuTable[0] || {}).sku_price) || '').trim(),
              quantity: Number(((skuTable[0] || {}).sku_amountOnSale) || 0),
            };
            """
        )
        main_image = browser._draft_main_image_state()
        description_image_count = browser._draft_description_image_count()
        spec_values = browser._collect_spec_values()
        logistics = browser._draft_logistics_dimension_values()
        send_address = browser._draft_selected_send_address()
        buyer_protection = browser._draft_selected_buyer_protection()
        buyer_schedule = browser._draft_selected_buyer_protection_schedule()
        assist_messages = browser._collect_assist_messages()
    finally:
        browser.close()

    checks = {
        "draft_id": str(core.get("draft_id") or "") == expected_draft_id,
        "title": str(core.get("title") or "") == expected_title,
        "price": _decimal_equal(core.get("price"), expected_price),
        "quantity": int(core.get("quantity") or 0) == expected_quantity,
        "main_image_present": bool(main_image.get("present")),
        "main_image_square": bool(main_image.get("square")),
        "detail_image_count": description_image_count == expected_detail_count,
        "spec_color": bool(str(spec_values.get("颜色", "")).strip()),
        "logistics": all(str(logistics.get(key, "")).strip() == value for key, value in expected_logistics.items()),
        "send_address_reapply_recorded": bool(send_address) or "send_address" in reapply_fields,
        "buyer_protection_reapply_recorded": bool(buyer_protection) or "buyer_protection" in reapply_fields,
    }
    evidence = {
        "status": "passed" if all(checks.values()) else "failed",
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "task_id": str(payload.get("task_id") or ""),
        "draft_id": str(core.get("draft_id") or ""),
        "checks": checks,
        "actual": {
            "title": str(core.get("title") or ""),
            "price": str(core.get("price") or ""),
            "quantity": int(core.get("quantity") or 0),
            "main_image": main_image,
            "detail_image_count": description_image_count,
            "spec_values": spec_values,
            "logistics": logistics,
            "send_address": send_address,
            "buyer_protection": buyer_protection,
            "buyer_protection_schedule": buyer_schedule,
            "assist_messages": assist_messages,
        },
        "submit_reapply_required_fields": reapply_fields,
        "draft_saved": False,
        "offer_submitted": False,
    }
    rendered = json.dumps(evidence, ensure_ascii=False, indent=2)
    target = Path(args.output)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    return 0 if evidence["status"] == "passed" else 2


if __name__ == "__main__":
    raise SystemExit(main())
