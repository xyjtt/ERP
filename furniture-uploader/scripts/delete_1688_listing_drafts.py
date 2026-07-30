"""Delete explicitly authorized 1688 drafts and verify their rows disappeared."""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

from selenium.common.exceptions import NoAlertPresentException, StaleElementReferenceException
from selenium.webdriver.common.by import By


PROJECT_ROOT = Path(__file__).resolve().parents[1]
RPA_ROOT = PROJECT_ROOT / "rpa"
if str(RPA_ROOT) not in sys.path:
    sys.path.insert(0, str(RPA_ROOT))

from config_loader import load_json_with_local_override
from listing_duplicate_probe import ListingCandidate, LiveListingDuplicateProbe
from sku_offline_browser import SkuOfflineBrowser


DRAFT_ID_RE = re.compile(r"[A-Za-z0-9_-]+")
CONFIRM_LABELS = ("确定", "确认", "删除")
CANCEL_LABELS = ("取消", "关闭", "返回")
PRIMARY_CLASS_MARKERS = ("next-btn-primary", "ant-btn-primary", "btn-primary")
CONFIRM_ATTRIBUTE_VALUES = ("confirm", "ok", "delete", "remove")


def normalize_draft_ids(values: Iterable[str]) -> list[str]:
    result: list[str] = []
    for value in values:
        draft_id = str(value or "").strip()
        if not DRAFT_ID_RE.fullmatch(draft_id):
            raise ValueError(f"invalid draft ID: {draft_id or '<empty>'}")
        if draft_id not in result:
            result.append(draft_id)
    if not result:
        raise ValueError("at least one --draft-id is required")
    return result


def is_delete_confirmation(text: object) -> bool:
    normalized = re.sub(r"\s+", "", str(text or ""))
    return "删除" in normalized and ("草稿" in normalized or "商品" in normalized)


def choose_confirm_label(labels: Iterable[str]) -> str:
    normalized = [str(label or "").strip() for label in labels]
    return next((label for label in CONFIRM_LABELS if label in normalized), "")


def choose_confirm_control_index(controls: Iterable[Mapping[str, object]]) -> int | None:
    evidence = list(controls)
    explicit_matches = [
        index
        for index, item in enumerate(evidence)
        if str(item.get("text") or "").strip() in CONFIRM_LABELS
    ]
    if len(explicit_matches) == 1:
        return explicit_matches[0]
    if explicit_matches:
        return None

    semantic_matches: list[int] = []
    for index, item in enumerate(evidence):
        searchable = " ".join(
            str(item.get(key) or "").strip().lower()
            for key in ("text", "title", "aria_label", "data_role", "data_action", "data_type")
        )
        if any(label in searchable for label in CANCEL_LABELS):
            continue
        class_name = str(item.get("class") or "").lower()
        has_primary_class = any(marker in class_name for marker in PRIMARY_CLASS_MARKERS)
        attribute_values = {
            str(item.get(key) or "").strip().lower()
            for key in ("data_role", "data_action", "data_type")
        }
        has_confirm_attribute = bool(attribute_values.intersection(CONFIRM_ATTRIBUTE_VALUES))
        aria_or_title = " ".join(
            str(item.get(key) or "").strip().lower()
            for key in ("aria_label", "title")
        )
        has_confirm_description = any(label in aria_or_title for label in CONFIRM_LABELS)
        if has_primary_class or has_confirm_attribute or has_confirm_description:
            semantic_matches.append(index)
    return semantic_matches[0] if len(semantic_matches) == 1 else None


def _control_evidence(control: Any) -> dict[str, str]:
    return {
        "text": str(control.text or "").strip(),
        "tag": str(control.tag_name or "").strip(),
        "class": str(control.get_attribute("class") or "").strip(),
        "type": str(control.get_attribute("type") or "").strip(),
        "title": str(control.get_attribute("title") or "").strip(),
        "aria_label": str(control.get_attribute("aria-label") or "").strip(),
        "data_role": str(control.get_attribute("data-role") or "").strip(),
        "data_action": str(control.get_attribute("data-action") or "").strip(),
        "data_type": str(control.get_attribute("data-type") or "").strip(),
        "outer_html": str(control.get_attribute("outerHTML") or "")[:2000],
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Delete exact authorized 1688 draft IDs.")
    parser.add_argument("--payload", required=True)
    parser.add_argument("--draft-id", action="append", required=True)
    parser.add_argument("--expected-shop", default="木刻理想")
    parser.add_argument("--output", required=True)
    parser.add_argument("--yes", action="store_true")
    return parser


def _visible_delete_dialog(driver: Any) -> Any | None:
    selectors = (
        ".next-dialog",
        ".ant-modal",
        "[role='dialog']",
        "[class*='popconfirm']",
        "[class*='popover']",
    )
    for selector in selectors:
        for element in driver.find_elements(By.CSS_SELECTOR, selector):
            try:
                if element.is_displayed() and is_delete_confirmation(element.text):
                    return element
            except StaleElementReferenceException:
                continue
    return None


def _delete_one_draft(driver: Any, draft_id: str, output_path: Path) -> dict[str, Any]:
    row_selector = f'tr[data-row-key="{draft_id}"]'
    rows = [row for row in driver.find_elements(By.CSS_SELECTOR, row_selector) if row.is_displayed()]
    if len(rows) != 1:
        raise RuntimeError(f"expected exactly one visible draft row for {draft_id}, got {len(rows)}")
    row = rows[0]
    delete_links = [
        element
        for element in row.find_elements(By.CSS_SELECTOR, "a,button,[role='button']")
        if element.is_displayed() and str(element.text or "").strip() == "删除草稿"
    ]
    if len(delete_links) != 1:
        raise RuntimeError(f"expected exactly one delete control for {draft_id}, got {len(delete_links)}")
    driver.execute_script("arguments[0].click();", delete_links[0])

    deadline = time.monotonic() + 15.0
    confirmation_text = ""
    confirmation_mode = ""
    while time.monotonic() < deadline:
        try:
            alert = driver.switch_to.alert
            confirmation_text = str(alert.text or "").strip()
            if not is_delete_confirmation(confirmation_text):
                raise RuntimeError(f"unexpected browser confirmation for {draft_id}: {confirmation_text}")
            screenshot_path = output_path.with_name(f"{output_path.stem}.{draft_id}.before-delete.png")
            driver.save_screenshot(str(screenshot_path))
            alert.accept()
            confirmation_mode = "browser_alert"
            break
        except NoAlertPresentException:
            pass

        dialog = _visible_delete_dialog(driver)
        if dialog is not None:
            confirmation_text = str(dialog.text or "").strip()
            controls = [
                control
                for control in dialog.find_elements(
                    By.CSS_SELECTOR,
                    "button,a,[role='button'],.next-btn",
                )
                if control.is_displayed() and str(control.text or "").strip()
            ]
            control_evidence = [_control_evidence(control) for control in controls]
            confirm_index = choose_confirm_control_index(control_evidence)
            screenshot_path = output_path.with_name(f"{output_path.stem}.{draft_id}.before-delete.png")
            screenshot_saved = bool(driver.save_screenshot(str(screenshot_path)))
            diagnostic_path = output_path.with_name(
                f"{output_path.stem}.{draft_id}.delete-confirmation.json"
            )
            diagnostic_path.parent.mkdir(parents=True, exist_ok=True)
            diagnostic_path.write_text(
                json.dumps(
                    {
                        "draft_id": draft_id,
                        "dialog_text": confirmation_text,
                        "delete_semantics": is_delete_confirmation(confirmation_text),
                        "controls": control_evidence,
                        "selected_index": confirm_index,
                        "screenshot_path": str(screenshot_path) if screenshot_saved else "",
                    },
                    ensure_ascii=False,
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )
            if confirm_index is None:
                raise RuntimeError(
                    f"delete confirmation has no unique confirm control for {draft_id}: "
                    + json.dumps(control_evidence, ensure_ascii=False)
                )
            driver.execute_script("arguments[0].click();", controls[confirm_index])
            confirmation_mode = "dom_dialog"
            break
        time.sleep(0.25)
    if not confirmation_mode:
        raise RuntimeError(f"delete confirmation did not appear for {draft_id}")

    deadline = time.monotonic() + 30.0
    while time.monotonic() < deadline:
        if not driver.find_elements(By.CSS_SELECTOR, row_selector):
            return {
                "draft_id": draft_id,
                "status": "deleted",
                "confirmation_mode": confirmation_mode,
                "confirmation_text": confirmation_text,
            }
        time.sleep(0.5)
    raise RuntimeError(f"draft row remained visible after confirmed deletion: {draft_id}")


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    args = build_parser().parse_args()
    if not args.yes:
        raise ValueError("--yes is required for destructive draft deletion")
    target_ids = normalize_draft_ids(args.draft_id)
    payload = json.loads(Path(args.payload).read_text(encoding="utf-8-sig"))
    product = payload.get("product") or {}
    candidate = ListingCandidate(
        sku=str(product.get("sku_code") or "").strip(),
        spu=str(product.get("spu_code") or "").strip(),
    )
    if not candidate.sku or not candidate.spu:
        raise ValueError("payload product sku_code and spu_code are required")

    config_dir = PROJECT_ROOT / "config"
    operator_config = load_json_with_local_override(config_dir / "operator_config.json")
    system_config = load_json_with_local_override(config_dir / "systems" / "1688_sku_offline.json")
    output_path = Path(args.output)
    browser = SkuOfflineBrowser(operator_config.get("browser", {}), PROJECT_ROOT)
    deletion_results: list[dict[str, Any]] = []
    before_report: dict[str, Any] = {}
    after_evidence: dict[str, Any] = {}
    browser.open()
    try:
        probe = LiveListingDuplicateProbe(
            browser,
            system_config,
            expected_shop=args.expected_shop,
            draft_limit=20,
            expected_draft_ids=target_ids,
        )
        before_report = probe.run([candidate])
        identity = before_report.get("shop_identity") or {}
        draft_identity = before_report.get("draft_identity_evidence") or {}
        if (
            before_report.get("tab_all") is not True
            or identity.get("matched") is not True
            or draft_identity.get("status") != "passed"
        ):
            raise RuntimeError("shop, tab=all, or target draft identity gate did not pass")

        driver = browser.driver
        if driver is None:
            raise RuntimeError("Browser has not been opened.")
        for draft_id in target_ids:
            deletion_results.append(_delete_one_draft(driver, draft_id, output_path))
        time.sleep(2.0)
        after_evidence = probe._draft_page_evidence()
        remaining_ids = [
            draft_id
            for draft_id in target_ids
            if draft_id in list(after_evidence.get("draft_ids") or [])
        ]
        screenshot_path = output_path.with_suffix(".after-delete.png")
        screenshot_saved = bool(driver.save_screenshot(str(screenshot_path)))
    finally:
        browser.close()

    deleted_ids = [item["draft_id"] for item in deletion_results if item.get("status") == "deleted"]
    evidence = {
        "status": (
            "passed"
            if set(deleted_ids) == set(target_ids) and not remaining_ids
            else "failed"
        ),
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "read_only": False,
        "authorized_delete": True,
        "expected_shop": args.expected_shop,
        "shop_identity": before_report.get("shop_identity") or {},
        "management_url": before_report.get("management_url") or "",
        "tab_all": before_report.get("tab_all") is True,
        "target_draft_ids": target_ids,
        "deleted_draft_ids": deleted_ids,
        "remaining_target_ids": remaining_ids,
        "deletions": deletion_results,
        "before_draft_count": before_report.get("draft_count"),
        "after_observed_draft_ids": after_evidence.get("draft_ids") or [],
        "screenshot_path": str(screenshot_path) if screenshot_saved else "",
        "draft_saved": False,
        "offer_submitted": False,
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(evidence, ensure_ascii=False, indent=2))
    return 0 if evidence["status"] == "passed" else 2


if __name__ == "__main__":
    raise SystemExit(main())
