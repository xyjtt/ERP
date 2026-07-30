"""Refresh and inspect one saved 1688 draft without saving or submitting it."""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from selenium.common.exceptions import StaleElementReferenceException, TimeoutException
from selenium.webdriver.common.by import By


PROJECT_ROOT = Path(__file__).resolve().parents[1]
RPA_ROOT = PROJECT_ROOT / "rpa"
if str(RPA_ROOT) not in sys.path:
    sys.path.insert(0, str(RPA_ROOT))

from auto_listing_executor import resolve_1688_publish_url
from browser_rpa import BrowserRPA
from config_loader import load_json_with_local_override
from listing_duplicate_probe import ListingCandidate, LiveListingDuplicateProbe
from sku_offline_browser import SkuOfflineBrowser


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Read-only independent refresh check for one 1688 draft.")
    parser.add_argument("--payload", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--draft-id", default="", help="override the payload draft ID for read-only inspection")
    parser.add_argument(
        "--publish-url",
        default="",
        help="exact saved offer-new publish URL; must match the expected draft ID and category",
    )
    parser.add_argument(
        "--open-from-management",
        action="store_true",
        help="open the draft by clicking its real product-management draft-box link",
    )
    parser.add_argument("--expected-shop", default="木刻理想")
    return parser


def _decimal_equal(left: object, right: object) -> bool:
    try:
        return Decimal(str(left)) == Decimal(str(right))
    except (InvalidOperation, TypeError, ValueError):
        return False


def _spec_equal(actual: object, expected: object) -> bool:
    expected_value = str(expected or "").strip()
    return bool(expected_value) and str(actual or "").strip() == expected_value


def _expected_main_image_count(payload: dict[str, object]) -> int:
    main_urls = list(((payload.get("images") or {}).get("main_urls") or []))
    return max(1, min(4, len(main_urls)))


def _buyer_protection_matches(
    actual_value: object,
    actual_schedule: list[dict[str, object]],
    *,
    expected_value: str,
    expected_code: str,
) -> bool:
    if str(actual_value or "").strip() != expected_value:
        return False
    return any(
        int(item.get("from", 0) or 0) == 1
        and str(item.get("serviceName", "")).strip() == expected_value
        and str(item.get("serviceCode", "")).strip() == expected_code
        for item in actual_schedule
        if isinstance(item, dict)
    )


def _validate_publish_url_override(raw_url: object, expected_draft_id: str) -> str:
    url = str(raw_url or "").strip()
    parsed = urlparse(url)
    query = parse_qs(parsed.query)
    draft_id = str((query.get("draftId") or [""])[0]).strip()
    category_id = str((query.get("catId") or [""])[0]).strip()
    if (
        parsed.scheme != "https"
        or parsed.hostname != "offer-new.1688.com"
        or parsed.path != "/popular/publish.htm"
        or draft_id != expected_draft_id
        or not category_id
    ):
        raise ValueError("publish URL must be the saved 1688 URL for the expected draft and category")
    return url


def _assert_management_inspection_gate(report: dict[str, object]) -> None:
    identity = report.get("shop_identity") or {}
    draft_identity = report.get("draft_identity_evidence") or {}
    if (
        report.get("tab_all") is not True
        or identity.get("matched") is not True
        or draft_identity.get("status") != "passed"
    ):
        raise ValueError(
            "management draft identity gate did not pass: "
            + json.dumps(
                {
                    "tab_all": report.get("tab_all"),
                    "shop_identity": identity,
                    "draft_identity_evidence": draft_identity,
                },
                ensure_ascii=False,
            )
        )


def _open_draft_from_management(
    browser: BrowserRPA,
    system_config: dict[str, object],
    payload: dict[str, object],
    *,
    expected_draft_id: str,
    expected_shop: str,
) -> dict[str, object]:
    product = payload.get("product") or {}
    sku = str(product.get("sku_code") or "").strip()
    spu = str(product.get("spu_code") or "").strip()
    if not sku or not spu:
        raise ValueError("management draft inspection requires product sku_code and spu_code")
    probe = LiveListingDuplicateProbe(
        browser,
        system_config,
        expected_shop=expected_shop,
        draft_limit=20,
        expected_draft_ids=[expected_draft_id],
    )
    report = probe.run([ListingCandidate(sku=sku, spu=spu)])
    _assert_management_inspection_gate(report)

    driver = browser.driver
    if driver is None:
        raise RuntimeError("Browser has not been opened.")
    row_selector = f'tr[data-row-key="{expected_draft_id}"]'
    link_selector = 'a[data-click="btn-继续发布商品"]'

    def find_link():
        row = driver.find_element(By.CSS_SELECTOR, row_selector)
        return row.find_element(By.CSS_SELECTOR, link_selector)

    link = find_link()
    href = str(link.get_attribute("href") or "").strip()
    if expected_draft_id not in href:
        raise ValueError("management draft link does not carry the expected draft ID")
    existing_handles = set(driver.window_handles)
    try:
        driver.execute_script("arguments[0].click();", link)
    except StaleElementReferenceException:
        driver.execute_script("arguments[0].click();", find_link())

    deadline = time.monotonic() + 30.0
    while time.monotonic() < deadline:
        new_handles = [handle for handle in driver.window_handles if handle not in existing_handles]
        candidate_handles = [*new_handles, *list(driver.window_handles)]
        for handle in candidate_handles:
            try:
                driver.switch_to.window(handle)
                if expected_draft_id in str(driver.current_url or ""):
                    return {
                        "entry_mode": "management_draft_link_click",
                        "management_url": str(report.get("management_url") or ""),
                        "draft_count": report.get("draft_count"),
                        "draft_identity_evidence": report.get("draft_identity_evidence") or {},
                        "clicked_href": href,
                    }
            except Exception:
                continue
        time.sleep(0.25)
    raise TimeoutException("management draft link did not open the expected draft page")


def _raise_for_fatal_runtime_page(browser: BrowserRPA) -> None:
    driver = browser.driver
    if driver is None:
        raise RuntimeError("Browser has not been opened.")
    diagnostic = driver.execute_script(
        """
        return {
          ready_state: String(document.readyState || ''),
          body_text: String((document.body && document.body.innerText) || '').slice(0, 4000),
        };
        """
    )
    body_text = str((diagnostic or {}).get("body_text") or "")
    fatal_error = "SYS_ERROR" in body_text or (
        "出错啦" in body_text and "系统错误" in body_text
    )
    if str((diagnostic or {}).get("ready_state") or "") == "complete" and fatal_error:
        raise TimeoutException("platform returned SYS_ERROR before the publish runtime became available")


def _wait_for_inspection_runtime(browser: BrowserRPA, *, timeout_seconds: float) -> None:
    readiness_check = getattr(browser, "_is_publish_form_ready", None)
    for _attempt in range(20):
        _raise_for_fatal_runtime_page(browser)
        if callable(readiness_check) and readiness_check():
            return
        browser._pause(0.5)
    browser._wait_for_publish_runtime_ready(timeout_seconds=timeout_seconds)


def _install_draft_boot_network_probe(browser: BrowserRPA) -> bool:
    driver = browser.driver
    if driver is None:
        return False
    try:
        driver.execute_cdp_cmd(
            "Page.addScriptToEvaluateOnNewDocument",
            {
                "source": r"""
(() => {
  if (window.__codexDraftBootProbeInstalled) return;
  window.__codexDraftBootProbeInstalled = true;
  window.__codexDraftBootRecords = [];
  const records = window.__codexDraftBootRecords;
  const relevant = (rawUrl) => {
    try {
      const parsed = new URL(String(rawUrl || ''), window.location.href);
      return parsed.hostname === 'offer-new.1688.com' &&
        (parsed.pathname.includes('/popular/') || parsed.pathname.includes('/processing/'));
    } catch (error) {
      return false;
    }
  };
  const append = (record) => {
    records.push(record);
    if (records.length > 100) records.splice(0, records.length - 100);
  };
  const preview = (value) => String(value == null ? '' : value).slice(0, 4000);

  const originalOpen = XMLHttpRequest.prototype.open;
  XMLHttpRequest.prototype.open = function(method, url) {
    this.__codexDraftBootMeta = { method: String(method || ''), url: String(url || '') };
    return originalOpen.apply(this, arguments);
  };
  const originalSend = XMLHttpRequest.prototype.send;
  XMLHttpRequest.prototype.send = function() {
    const meta = this.__codexDraftBootMeta || {};
    if (relevant(meta.url)) {
      this.addEventListener('loadend', () => {
        let responseText = '';
        try { responseText = preview(this.responseText); } catch (error) {}
        append({
          transport: 'xhr',
          method: meta.method,
          url: meta.url,
          status: Number(this.status || 0),
          responseText,
        });
      }, { once: true });
    }
    return originalSend.apply(this, arguments);
  };

  const originalFetch = window.fetch;
  if (typeof originalFetch === 'function') {
    window.fetch = function() {
      const args = Array.from(arguments);
      const input = args[0];
      const url = typeof input === 'string' ? input : String((input && input.url) || '');
      const method = String(((args[1] || {}).method) || (input && input.method) || 'GET');
      const promise = originalFetch.apply(window, args);
      if (relevant(url)) {
        promise.then(async (response) => {
          let responseText = '';
          try { responseText = preview(await response.clone().text()); } catch (error) {}
          append({
            transport: 'fetch',
            method,
            url,
            status: Number(response.status || 0),
            responseText,
          });
        }).catch((error) => {
          append({ transport: 'fetch', method, url, status: 0, responseText: preview(error) });
        });
      }
      return promise;
    };
  }
})();
""",
            },
        )
        return True
    except Exception:
        return False


def _collect_draft_boot_network_records(browser: BrowserRPA) -> list[dict[str, object]]:
    driver = browser.driver
    if driver is None:
        return []
    try:
        records = driver.execute_script(
            "return Array.isArray(window.__codexDraftBootRecords) "
            "? window.__codexDraftBootRecords.map((item) => ({...item})) : [];"
        )
    except Exception:
        return []
    if not isinstance(records, list):
        return []
    return [dict(item) for item in records if isinstance(item, dict)]


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    args = build_parser().parse_args()
    payload = json.loads(Path(args.payload).read_text(encoding="utf-8-sig"))
    expected_draft_id = str(
        args.draft_id
        or ((payload.get("workflow") or {}).get("draft") or {}).get("draft_id")
        or ""
    ).strip()
    if not re.fullmatch(r"[A-Za-z0-9_-]+", expected_draft_id):
        raise ValueError("read-only draft inspection requires a valid draft ID")
    expected_detail_count = len(list(((payload.get("images") or {}).get("detail_urls") or [])))
    expected_main_image_count = _expected_main_image_count(payload)
    expected_title = str((payload.get("product") or {}).get("selected_title") or "").strip()
    expected_price = str((payload.get("pricing") or {}).get("publish_price") or "").strip()
    expected_quantity = int((payload.get("inventory") or {}).get("quantity") or 0)
    expected_specs = {
        "颜色": str((payload.get("attributes") or {}).get("color") or "").strip(),
        "尺寸": str((payload.get("attributes") or {}).get("size") or "").strip(),
    }
    expected_logistics = {
        "length": str((payload.get("logistics") or {}).get("length_cm") or "").strip(),
        "width": str((payload.get("logistics") or {}).get("width_cm") or "").strip(),
        "height": str((payload.get("logistics") or {}).get("height_cm") or "").strip(),
        "weight": str((payload.get("logistics") or {}).get("weight_g") or "").strip(),
    }
    expected_buyer_protection = "24小时发货"
    expected_buyer_protection_code = "essxsfh"

    config_dir = PROJECT_ROOT / "config"
    operator_config = load_json_with_local_override(config_dir / "operator_config.json")
    system_config = load_json_with_local_override(config_dir / "systems" / "1688_sku_offline.json")
    output_path = Path(args.output)
    publish_url = ""
    entry_evidence: dict[str, object] = {"entry_mode": "direct_draft_url"}
    if not args.open_from_management:
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
        if args.publish_url:
            publish_url = _validate_publish_url_override(args.publish_url, expected_draft_id)
            entry_evidence = {
                "entry_mode": "saved_publish_url",
                "publish_url": publish_url,
            }
    browser_class = SkuOfflineBrowser if args.open_from_management else BrowserRPA
    browser = browser_class(operator_config.get("browser", {}), PROJECT_ROOT)
    browser.open()
    boot_network_probe_installed = _install_draft_boot_network_probe(browser)
    try:
        if args.open_from_management:
            entry_evidence = _open_draft_from_management(
                browser,
                system_config,
                payload,
                expected_draft_id=expected_draft_id,
                expected_shop=args.expected_shop,
            )
            publish_url = str(entry_evidence.get("clicked_href") or publish_url)
        elif expected_draft_id not in str(browser.driver.current_url or ""):
            browser.driver.get(publish_url)
        else:
            browser.driver.refresh()
        try:
            _wait_for_inspection_runtime(browser, timeout_seconds=180)
        except TimeoutException as exc:
            boot_network_records = _collect_draft_boot_network_records(browser)
            diagnostic = browser.driver.execute_script(
                """
                return {
                  current_url: String(window.location.href || ''),
                  page_title: String(document.title || ''),
                  ready_state: String(document.readyState || ''),
                  sell_publish_sdk_present: Boolean(window.SellPublishSdk),
                  body_text: String((document.body && document.body.innerText) || '').slice(0, 4000),
                };
                """
            )
            screenshot_path = output_path.with_suffix(".load-failure.png")
            screenshot_saved = bool(browser.driver.save_screenshot(str(screenshot_path)))
            evidence = {
                "status": "unavailable",
                "checked_at": datetime.now(timezone.utc).isoformat(),
                "task_id": str(payload.get("task_id") or ""),
                "draft_id": expected_draft_id,
                "entry": entry_evidence,
                "requested_url": publish_url,
                "current_url": str((diagnostic or {}).get("current_url") or ""),
                "page_title": str((diagnostic or {}).get("page_title") or ""),
                "ready_state": str((diagnostic or {}).get("ready_state") or ""),
                "sell_publish_sdk_present": bool(
                    (diagnostic or {}).get("sell_publish_sdk_present")
                ),
                "body_text": str((diagnostic or {}).get("body_text") or ""),
                "error_type": type(exc).__name__,
                "error": str(exc),
                "boot_network_probe_installed": boot_network_probe_installed,
                "boot_network_records": boot_network_records,
                "screenshot_path": str(screenshot_path) if screenshot_saved else "",
                "draft_saved": False,
                "offer_submitted": False,
            }
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text(
                json.dumps(evidence, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            print(json.dumps(evidence, ensure_ascii=False, indent=2))
            return 3
        browser._pause(2.0)
        current_url = str(browser.driver.current_url or "").strip()
        core = browser.driver.execute_script(
            """
            const sdk = window.SellPublishSdk;
            const state = sdk && sdk.engine && sdk.engine.getJsonState ? sdk.engine.getJsonState() : {};
            const components = (state || {}).components || {};
            const props = (name) => ((components[name] || {}).props || {});
            const value = (name) => props(name).value;
            const priceRange = Array.isArray(value('priceRange')) ? value('priceRange') : [];
            const skuTable = Array.isArray(value('skuTable')) ? value('skuTable') : [];
            const totalSales = value('totalSales');
            const titleValue = value('subject') || value('title');
            const draftId = String(
              new URL(window.location.href).searchParams.get('draftId') ||
              new URL(window.location.href).searchParams.get('offerDraftId') ||
              (((state || {}).global || {}).renderData || {}).draftId ||
              (((state || {}).global || {}).systemParam || {}).draftId ||
              ''
            ).trim();
            return {
              draft_id: draftId,
              title: String(
                titleValue && typeof titleValue === 'object'
                  ? (titleValue.value || titleValue.text || titleValue.title || '')
                  : (titleValue || '')
              ).trim(),
              price: String(((priceRange[0] || {}).pricerange_price) || ((skuTable[0] || {}).sku_price) || '').trim(),
              quantity: Number(
                ((skuTable[0] || {}).sku_amountOnSale) ||
                (totalSales && typeof totalSales === 'object' ? totalSales.value : totalSales) ||
                0
              ),
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
        boot_network_records = _collect_draft_boot_network_records(browser)
    finally:
        browser.close()

    checks = {
        "draft_id": str(core.get("draft_id") or "") == expected_draft_id,
        "title": str(core.get("title") or "") == expected_title,
        "price": _decimal_equal(core.get("price"), expected_price),
        "quantity": int(core.get("quantity") or 0) == expected_quantity,
        "main_image_present": bool(main_image.get("present")),
        "main_image_count": int(main_image.get("count") or 0) >= expected_main_image_count,
        "main_image_square": bool(main_image.get("square")),
        "detail_image_count": description_image_count == expected_detail_count,
        "spec_color": _spec_equal(spec_values.get("颜色"), expected_specs["颜色"]),
        "spec_size": _spec_equal(spec_values.get("尺寸"), expected_specs["尺寸"]),
        "logistics": all(str(logistics.get(key, "")).strip() == value for key, value in expected_logistics.items()),
        "send_address": bool(send_address),
        "buyer_protection": _buyer_protection_matches(
            buyer_protection,
            buyer_schedule,
            expected_value=expected_buyer_protection,
            expected_code=expected_buyer_protection_code,
        ),
    }
    evidence = {
        "status": "passed" if all(checks.values()) else "failed",
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "task_id": str(payload.get("task_id") or ""),
        "draft_id": str(core.get("draft_id") or ""),
        "entry": entry_evidence,
        "requested_url": publish_url,
        "current_url": current_url,
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
        "expected": {
            "title": expected_title,
            "price": expected_price,
            "quantity": expected_quantity,
            "main_image_count": expected_main_image_count,
            "spec_values": expected_specs,
            "logistics": expected_logistics,
            "detail_image_count": expected_detail_count,
            "buyer_protection": expected_buyer_protection,
            "buyer_protection_code": expected_buyer_protection_code,
        },
        "boot_network_probe_installed": boot_network_probe_installed,
        "boot_network_records": boot_network_records,
        "draft_saved": False,
        "offer_submitted": False,
    }
    rendered = json.dumps(evidence, ensure_ascii=False, indent=2)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    return 0 if evidence["status"] == "passed" else 2


if __name__ == "__main__":
    raise SystemExit(main())
