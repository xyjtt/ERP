"""Refresh and inspect one saved 1688 draft without saving or submitting it."""

from __future__ import annotations

import argparse
import json
import os
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
from listing_browser_session import open_account_bound_listing_browser
from listing_duplicate_probe import ListingCandidate, LiveListingDuplicateProbe
from listing_review import (
    INSPECTION_ARTIFACT_VERSION,
    build_review_contract_sha256,
    build_submit_reapply_contract,
    build_submit_reapply_contract_sha256,
    require_unique_existing_draft_id,
)
from cross_project_runtime import resolve_build_sha
from sku_offline_browser import SkuOfflineBrowser


class InspectionProgressArtifact:
    def __init__(self, path: Path, *, output_path: Path) -> None:
        self.path = path
        self.output_path = output_path
        self.started_at = datetime.now(timezone.utc).isoformat()

    def write(self, stage: str, *, status: str = "running", **extra: object) -> None:
        updated_at = datetime.now(timezone.utc).isoformat()
        artifact = {
            "artifact_version": "erp_saved_draft_inspector_progress_v1",
            "status": status,
            "stage": stage,
            "started_at": self.started_at,
            "updated_at": updated_at,
            "finished_at": updated_at if status != "running" else None,
            "inspector_pid": os.getpid(),
            "output": str(self.output_path),
            "draft_saved": False,
            "offer_submitted": False,
            **extra,
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path = self.path.with_name(f".{self.path.name}.{os.getpid()}.tmp")
        temporary_path.write_text(
            json.dumps(artifact, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary_path, self.path)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Read-only independent refresh check for one 1688 draft.")
    parser.add_argument("--payload", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--progress-output", default="")
    parser.add_argument(
        "--draft-id",
        default="",
        help="confirm the payload's unique existing draft ID for read-only inspection",
    )
    parser.add_argument(
        "--publish-url",
        default="",
        help="exact saved offer-new publish URL; must match the expected draft ID and category",
    )
    parser.add_argument(
        "--offer-id",
        default="",
        help="existing numeric Offer ID to verify in the same read-only browser session",
    )
    parser.add_argument(
        "--offer-url",
        default="",
        help="existing detail.1688.com Offer URL; must match --offer-id",
    )
    parser.add_argument(
        "--open-from-management",
        action="store_true",
        help="open the draft by clicking its real product-management draft-box link",
    )
    parser.add_argument(
        "--offer-only",
        action="store_true",
        help="inspect an already-published Offer without requiring the historical draft row",
    )
    parser.add_argument("--expected-shop", default="")
    parser.add_argument("--account-key", default="muke_lixiang")
    parser.add_argument("--expected-cdp-port", type=int, default=9306)
    parser.add_argument("--lock-wait-seconds", type=int, default=0)
    parser.add_argument("--runtime-lease-wait-seconds", type=float, default=0)
    parser.add_argument("--login-timeout-seconds", type=int, default=300)
    parser.add_argument(
        "--shared-runtime-root",
        default=os.getenv("YYDD_1688_RUNTIME_ROOT", "D:/script_1688"),
    )
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


def _resolve_inspection_draft_id(payload: dict[str, object], requested_draft_id: str) -> str:
    expected_draft_id = require_unique_existing_draft_id(payload)
    requested = str(requested_draft_id or "").strip()
    if requested and requested != expected_draft_id:
        raise ValueError("--draft-id must match the payload's unique existing draft ID")
    return expected_draft_id


def _resolve_expected_shop(payload: dict[str, object], requested_shop: str) -> str:
    shop = payload.get("shop") or {}
    payload_shop = str(shop.get("shop_name") or payload.get("shop_name") or "").strip()
    if not payload_shop:
        raise ValueError("payload must provide shop.shop_name for account-bound inspection")
    requested = str(requested_shop or "").strip()
    if requested and requested != payload_shop:
        raise ValueError("--expected-shop must match payload shop.shop_name")
    return payload_shop


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


def _classify_field_outcomes(
    persisted_checks: dict[str, bool],
    *,
    submit_reapply_fields: set[str],
    submit_reapply_contract_sha256: str,
) -> tuple[dict[str, bool], dict[str, dict[str, object]]]:
    checks: dict[str, bool] = {}
    field_outcomes: dict[str, dict[str, object]] = {}
    for name, persisted in persisted_checks.items():
        if persisted:
            checks[name] = True
            field_outcomes[name] = {"status": "persisted"}
        elif name in submit_reapply_fields:
            checks[name] = True
            field_outcomes[name] = {
                "status": "submit_reapply_required",
                "contract_sha256": submit_reapply_contract_sha256,
            }
        else:
            checks[name] = False
            field_outcomes[name] = {"status": "failed"}
    return checks, field_outcomes


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


def _resolve_offer_target(raw_url: object, requested_offer_id: object) -> tuple[str, str]:
    url = str(raw_url or "").strip()
    offer_id = str(requested_offer_id or "").strip()
    if not url and not offer_id:
        return "", ""
    if not re.fullmatch(r"\d+", offer_id):
        raise ValueError("--offer-id must be numeric when Offer inspection is requested")
    if not url:
        url = f"https://detail.1688.com/offer/{offer_id}.html"
    parsed = urlparse(url)
    path_match = re.fullmatch(r"/offer/(\d+)\.html", parsed.path)
    if (
        parsed.scheme != "https"
        or parsed.hostname != "detail.1688.com"
        or parsed.username is not None
        or parsed.password is not None
        or path_match is None
        or path_match.group(1) != offer_id
        or bool(parsed.query)
        or bool(parsed.fragment)
    ):
        raise ValueError("Offer URL must be the exact detail.1688.com URL for --offer-id")
    return url, offer_id


def _inspect_offer_detail_page(
    browser: BrowserRPA,
    *,
    offer_url: str,
    expected_offer_id: str,
    expected_title: str,
    expected_shop: str,
    output_path: Path,
) -> dict[str, object]:
    driver = browser.driver
    if driver is None:
        raise RuntimeError("Browser has not been opened.")
    try:
        driver.get(offer_url)
        diagnostic: dict[str, object] = {}
        for _attempt in range(60):
            diagnostic = driver.execute_script(
                """
                const firstText = (selectors) => {
                  for (const selector of selectors) {
                    const node = document.querySelector(selector);
                    const value = String(
                      (node && (node.getAttribute('content') || node.textContent)) || ''
                    ).replace(/\\s+/g, ' ').trim();
                    if (value) return value;
                  }
                  return '';
                };
                return {
                  current_url: String(window.location.href || ''),
                  page_title: String(document.title || ''),
                  ready_state: String(document.readyState || ''),
                  body_text: String((document.body && document.body.innerText) || '').slice(0, 4000),
                  product_title: firstText([
                    'h1.title-text',
                    'h1[class*="title"]',
                    'h1',
                    'meta[property="og:title"]'
                  ]),
                  seller_text: firstText([
                    '[class*="company-name"]',
                    '[class*="shop-name"]',
                    '[class*="seller-name"]'
                  ]),
                };
                """
            ) or {}
            if str(diagnostic.get("ready_state") or "") == "complete" and str(
                diagnostic.get("body_text") or ""
            ).strip():
                break
            browser._pause(0.5)

        current_url = str(diagnostic.get("current_url") or "").strip()
        current_path = urlparse(current_url).path
        current_match = re.fullmatch(r"/offer/(\d+)\.html", current_path)
        actual_offer_id = current_match.group(1) if current_match else ""
        body_text = str(diagnostic.get("body_text") or "")
        page_title = str(diagnostic.get("page_title") or "").strip()
        product_title = str(diagnostic.get("product_title") or "").strip()
        seller_text = str(diagnostic.get("seller_text") or "").strip()
        page_product_title = re.sub(r"\s*-\s*阿里巴巴\s*$", "", page_title).strip()
        if product_title != expected_title and page_product_title == expected_title:
            product_title = page_product_title
        lowered_url = current_url.lower()
        auth_challenge = (
            any(host in lowered_url for host in ("login.1688.com", "login.alibaba.com"))
            or any(term in body_text for term in ("请登录", "滑块", "安全验证", "请输入验证码"))
        )
        unavailable = any(
            term in body_text
            for term in ("商品不存在", "商品已下架", "访问的商品不存在", "页面不存在")
        )
        seller_display_matches_task_shop = (
            bool(seller_text) and expected_shop in seller_text
        )
        identity_mismatch = False
        title_matched = bool(product_title) and product_title == expected_title
        if auth_challenge:
            status = "blocked_auth"
        elif actual_offer_id != expected_offer_id:
            status = "failed_offer_identity"
        elif unavailable:
            status = "offer_unavailable"
        elif not title_matched:
            status = "failed_title_mismatch"
        else:
            status = "passed"

        screenshot_path = output_path.with_suffix(".offer.png")
        screenshot_saved = bool(driver.save_screenshot(str(screenshot_path)))
        return {
            "status": status,
            "checked_at": datetime.now(timezone.utc).isoformat(),
            "requested_url": offer_url,
            "current_url": current_url,
            "expected_offer_id": expected_offer_id,
            "actual_offer_id": actual_offer_id,
            "offer_id_matched": actual_offer_id == expected_offer_id,
            "page_title": page_title,
            "ready_state": str(diagnostic.get("ready_state") or ""),
            "product_title": product_title,
            "expected_title": expected_title,
            "title_matched": title_matched,
            "seller_text": seller_text,
            "expected_shop": expected_shop,
            "identity_mismatch": identity_mismatch,
            "identity_source": "account_bound_payload",
            "seller_display_matches_task_shop": seller_display_matches_task_shop,
            "auth_challenge": auth_challenge,
            "unavailable": unavailable,
            "body_text": body_text,
            "screenshot_path": str(screenshot_path) if screenshot_saved else "",
            "read_only": True,
        }
    except Exception as exc:
        return {
            "status": "inspection_error",
            "checked_at": datetime.now(timezone.utc).isoformat(),
            "requested_url": offer_url,
            "expected_offer_id": expected_offer_id,
            "error_type": type(exc).__name__,
            "error": str(exc),
            "read_only": True,
        }


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
    output_path = Path(args.output).resolve()
    progress_path = (
        Path(args.progress_output).resolve()
        if args.progress_output
        else Path(f"{output_path}.progress.json")
    )
    progress = InspectionProgressArtifact(progress_path, output_path=output_path)
    progress.write("inspector_started")
    payload = json.loads(Path(args.payload).read_text(encoding="utf-8-sig"))
    expected_draft_id = _resolve_inspection_draft_id(payload, args.draft_id)
    expected_shop = _resolve_expected_shop(payload, args.expected_shop)
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
    submit_reapply_contract = build_submit_reapply_contract(payload)
    submit_reapply_contract_sha256 = build_submit_reapply_contract_sha256(payload)
    submit_reapply_fields = set(submit_reapply_contract["required_fields"])

    config_dir = PROJECT_ROOT / "config"
    operator_config = load_json_with_local_override(config_dir / "operator_config.json")
    system_config = load_json_with_local_override(config_dir / "systems" / "1688_sku_offline.json")
    offer_url, expected_offer_id = _resolve_offer_target(args.offer_url, args.offer_id)
    if args.offer_only and not offer_url:
        raise ValueError("--offer-only requires --offer-id or --offer-url")
    publish_url = ""
    entry_evidence: dict[str, object] = {"entry_mode": "direct_draft_url"}
    if args.offer_only:
        entry_evidence = {
            "entry_mode": "published_offer_detail",
            "offer_url": offer_url,
        }
    elif not args.open_from_management:
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
    browser_class = (
        SkuOfflineBrowser if args.open_from_management or args.offer_only else BrowserRPA
    )
    progress.write("preflight_complete")
    with open_account_bound_listing_browser(
        payload=payload,
        browser_class=browser_class,
        operator_config=operator_config,
        project_root=PROJECT_ROOT,
        shared_runtime_root=args.shared_runtime_root,
        expected_account_key=args.account_key,
        expected_shop_name=expected_shop,
        expected_cdp_port=args.expected_cdp_port,
        component="erp-listing-draft-inspector",
        task_type="listing",
        lock_wait_seconds=args.lock_wait_seconds,
        runtime_lease_wait_seconds=args.runtime_lease_wait_seconds,
        login_timeout_seconds=args.login_timeout_seconds,
        progress_callback=progress.write,
    ) as (browser, binding, identity):
        if args.offer_only:
            offer_evidence = _inspect_offer_detail_page(
                browser,
                offer_url=offer_url,
                expected_offer_id=expected_offer_id,
                expected_title=expected_title,
                expected_shop=identity.shop_name,
                output_path=output_path,
            )
            offer_passed = offer_evidence.get("status") == "passed"
            evidence = {
                "artifact_version": INSPECTION_ARTIFACT_VERSION,
                "inspector_build_sha": resolve_build_sha(PROJECT_ROOT.parent),
                "payload_contract_sha256": build_review_contract_sha256(payload),
                "status": "passed" if offer_passed else "failed",
                "checked_at": datetime.now(timezone.utc).isoformat(),
                "task_id": str(payload.get("task_id") or ""),
                "draft_id": expected_draft_id,
                "entry": {
                    "entry_mode": "published_offer_detail",
                    "offer_url": offer_url,
                },
                "requested_url": offer_url,
                "current_url": str(offer_evidence.get("current_url") or ""),
                "offer": offer_evidence,
                "checks": {
                    "offer_exists": offer_passed,
                    "offer_id": bool(offer_evidence.get("offer_id_matched")),
                    "title": bool(offer_evidence.get("title_matched")),
                    "shop_identity": not bool(offer_evidence.get("identity_mismatch")),
                },
                "draft_saved": False,
                "offer_submitted": False,
                "account_key": identity.account_key,
                "shop_name": identity.shop_name,
                "cdp_port": binding.cdp_port,
                "read_only": True,
            }
            rendered = json.dumps(evidence, ensure_ascii=False, indent=2)
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text(rendered + "\n", encoding="utf-8")
            print(rendered)
            exit_code = 0 if offer_passed else 2
            progress.write(
                "offer_inspection_completed",
                status="completed",
                exit_code=exit_code,
                inspection_status=evidence["status"],
            )
            return exit_code

        boot_network_probe_installed = _install_draft_boot_network_probe(browser)
        if args.open_from_management:
            entry_evidence = _open_draft_from_management(
                browser,
                system_config,
                payload,
                expected_draft_id=expected_draft_id,
                expected_shop=identity.shop_name,
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
            offer_evidence = (
                _inspect_offer_detail_page(
                    browser,
                    offer_url=offer_url,
                    expected_offer_id=expected_offer_id,
                    expected_title=expected_title,
                    expected_shop=identity.shop_name,
                    output_path=output_path,
                )
                if offer_url
                else None
            )
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
                "offer": offer_evidence,
                "draft_saved": False,
                "offer_submitted": False,
                "account_key": identity.account_key,
                "shop_name": identity.shop_name,
                "cdp_port": binding.cdp_port,
            }
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text(
                json.dumps(evidence, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            print(json.dumps(evidence, ensure_ascii=False, indent=2))
            progress.write(
                "inspection_unavailable",
                status="completed",
                exit_code=3,
                inspection_status="unavailable",
            )
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
        delivery_service = browser._draft_delivery_service_state()
        buyer_protection = browser._draft_selected_buyer_protection()
        buyer_schedule = browser._draft_selected_buyer_protection_schedule()
        assist_messages = browser._collect_assist_messages()
        boot_network_records = _collect_draft_boot_network_records(browser)
        draft_screenshot_path = output_path.with_suffix(".draft.png")
        draft_screenshot_saved = bool(browser.driver.save_screenshot(str(draft_screenshot_path)))
        offer_evidence = (
            _inspect_offer_detail_page(
                browser,
                offer_url=offer_url,
                expected_offer_id=expected_offer_id,
                expected_title=expected_title,
                expected_shop=identity.shop_name,
                output_path=output_path,
            )
            if offer_url
            else None
        )

    selected_delivery_ids = {
        int(item)
        for item in list(delivery_service.get("selectedServiceIds") or [])
        if str(item).strip().isdigit() and int(item) > 0
    }
    allowed_delivery_ids = {
        int(item)
        for item in list(delivery_service.get("allowedServiceIds") or [])
        if str(item).strip().isdigit() and int(item) > 0
    }
    persisted_checks = {
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
        "delivery_service": bool(selected_delivery_ids)
        and bool(allowed_delivery_ids)
        and selected_delivery_ids.issubset(allowed_delivery_ids),
        "logistics": all(str(logistics.get(key, "")).strip() == value for key, value in expected_logistics.items()),
        "send_address": bool(send_address),
        "buyer_protection": _buyer_protection_matches(
            buyer_protection,
            buyer_schedule,
            expected_value=expected_buyer_protection,
            expected_code=expected_buyer_protection_code,
        ),
    }
    checks, field_outcomes = _classify_field_outcomes(
        persisted_checks,
        submit_reapply_fields=submit_reapply_fields,
        submit_reapply_contract_sha256=submit_reapply_contract_sha256,
    )
    if offer_evidence is not None:
        checks["offer_exists"] = offer_evidence.get("status") == "passed"
        field_outcomes["offer_exists"] = {
            "status": "persisted" if checks["offer_exists"] else "failed"
        }
    evidence = {
        "artifact_version": INSPECTION_ARTIFACT_VERSION,
        "inspector_build_sha": resolve_build_sha(PROJECT_ROOT.parent),
        "payload_contract_sha256": build_review_contract_sha256(payload),
        "status": "passed" if all(checks.values()) else "failed",
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "task_id": str(payload.get("task_id") or ""),
        "draft_id": str(core.get("draft_id") or ""),
        "entry": entry_evidence,
        "requested_url": publish_url,
        "current_url": current_url,
        "draft_screenshot_path": str(draft_screenshot_path) if draft_screenshot_saved else "",
        "offer": offer_evidence,
        "checks": checks,
        "field_outcomes": field_outcomes,
        "submit_reapply_contract_sha256": submit_reapply_contract_sha256,
        "actual": {
            "title": str(core.get("title") or ""),
            "price": str(core.get("price") or ""),
            "quantity": int(core.get("quantity") or 0),
            "main_image": main_image,
            "detail_image_count": description_image_count,
            "spec_values": spec_values,
            "delivery_service": delivery_service,
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
            "submit_reapply_required_fields": sorted(submit_reapply_fields),
            "offer_id": expected_offer_id,
            "offer_url": offer_url,
        },
        "boot_network_probe_installed": boot_network_probe_installed,
        "boot_network_records": boot_network_records,
        "draft_saved": False,
        "offer_submitted": False,
        "account_key": identity.account_key,
        "shop_name": identity.shop_name,
        "cdp_port": binding.cdp_port,
    }
    rendered = json.dumps(evidence, ensure_ascii=False, indent=2)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    exit_code = 0 if evidence["status"] == "passed" else 2
    progress.write(
        "inspection_completed",
        status="completed",
        exit_code=exit_code,
        inspection_status=evidence["status"],
    )
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
