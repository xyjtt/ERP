"""Probe the real 1688 image picker without saving or submitting a listing."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse


PROJECT_ROOT = Path(__file__).resolve().parents[1]
RPA_ROOT = PROJECT_ROOT / "rpa"
if str(RPA_ROOT) not in sys.path:
    sys.path.insert(0, str(RPA_ROOT))

from auto_listing_executor import build_product_record, resolve_1688_publish_url
from browser_rpa import BrowserRPA
from config_loader import load_json_with_local_override
from exceptions import ImageAlbumFullError


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Upload a small capacity-aware real 1688 image probe batch.")
    parser.add_argument("--payload", required=True)
    parser.add_argument("--count", type=int, default=2)
    parser.add_argument("--force-new-album", action="store_true")
    parser.add_argument(
        "--evidence-output",
        default=str(PROJECT_ROOT / "logs" / "production" / "1688-image-picker-probe-latest.json"),
    )
    return parser


def emit_evidence(evidence: dict[str, object], output_path: str) -> None:
    payload = dict(evidence)
    payload.setdefault("checked_at", datetime.now(timezone.utc).isoformat())
    rendered = json.dumps(payload, ensure_ascii=False, indent=2)
    target = Path(output_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)


def collect_page_diagnostics(browser: BrowserRPA, evidence_output: str) -> dict[str, object]:
    driver = browser.driver
    if driver is None:
        return {"available": False}
    diagnostics: dict[str, object] = {
        "available": True,
        "current_url": str(driver.current_url or ""),
        "page_title": str(driver.title or ""),
        "tab_count": len(driver.window_handles),
    }
    try:
        page_state = driver.execute_script(
            """
            const selectors = [
              '#guid-title input[maxlength="60"]',
              '#guid-title input',
              '#saveDraftButton',
              '#submitFormButton',
              'input[maxlength="60"]',
            ];
            return {
              ready_state: document.readyState,
              body_text: String((document.body && document.body.innerText) || '').slice(0, 2000),
              selector_counts: Object.fromEntries(
                selectors.map((selector) => [selector, document.querySelectorAll(selector).length])
              ),
              frames: Array.from(document.querySelectorAll('iframe')).slice(0, 20).map((frame) => ({
                id: String(frame.id || ''),
                name: String(frame.name || ''),
                src: String(frame.src || ''),
              })),
            };
            """
        )
        if isinstance(page_state, dict):
            diagnostics.update(page_state)
    except Exception as exc:
        diagnostics["script_error"] = f"{type(exc).__name__}: {exc}"[:1000]

    screenshot_path = str(Path(evidence_output).with_suffix(".png"))
    try:
        diagnostics["screenshot_saved"] = bool(driver.save_screenshot(screenshot_path))
        diagnostics["screenshot_path"] = screenshot_path
    except Exception as exc:
        diagnostics["screenshot_saved"] = False
        diagnostics["screenshot_error"] = f"{type(exc).__name__}: {exc}"[:1000]
    return diagnostics


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    args = build_parser().parse_args()
    if args.count < 1 or args.count > 4:
        raise ValueError("probe count must be between 1 and 4")

    payload = json.loads(Path(args.payload).read_text(encoding="utf-8-sig"))
    draft_id = str((payload.get("workflow") or {}).get("pending_draft_id") or "").strip()
    product = build_product_record(payload, project_root=PROJECT_ROOT)
    local_images = [
        item.strip()
        for item in str(product.raw.get("detail_images", "")).split("|")
        if item.strip()
    ][: args.count]
    if len(local_images) != args.count:
        raise ValueError(f"payload resolved only {len(local_images)}/{args.count} probe images")

    config_dir = PROJECT_ROOT / "config"
    platform_config = load_json_with_local_override(config_dir / "platforms" / "1688.json")
    operator_config = load_json_with_local_override(config_dir / "operator_config.json")
    detail_step = next(
        step
        for step in list((platform_config.get("publish") or {}).get("steps") or [])
        if str(step.get("name") or "").strip() == "detail_images"
    )
    picker_selector = dict(detail_step.get("fallback_picker_selector") or {})
    if not picker_selector.get("value"):
        raise ValueError("detail_images fallback picker selector is not configured")

    browser = BrowserRPA(operator_config.get("browser", {}), PROJECT_ROOT)
    context: dict[str, object] = {}
    page_diagnostics: dict[str, object] = {}
    probe_error: Exception | None = None
    browser_opened = False
    try:
        browser.open()
        browser_opened = True
        publish_url, _category_id = resolve_1688_publish_url(payload, mode="draft")
        same_draft_page = str(
            payload.get("workflow", {}).get("pending_draft_id") or ""
        ) in str(browser.driver.current_url or "")
        if not same_draft_page:
            browser.driver.get(publish_url)
            browser._pause(float(browser.browser_config.get("page_load_wait_seconds", 2)))
        elif not browser._is_publish_form_ready():
            browser.driver.refresh()
        browser._wait_for_publish_runtime_ready(timeout_seconds=180)

        browser._run_picker_upload(
            {
                **detail_step,
                "name": "image_probe",
                "force_create_album": args.force_new_album,
                "auto_create_album_when_full": True,
                "require_album_ready": True,
                "capture_uploaded_urls_only": True,
                "auto_album_name_prefix": "AUTO_PROBE",
                "max_insert_count": args.count,
                "per_file_upload_timeout_seconds": 30,
            },
            picker_selector,
            local_images,
            context,
        )
    except Exception as exc:
        probe_error = exc
        page_diagnostics = collect_page_diagnostics(browser, args.evidence_output)
    finally:
        if browser_opened:
            browser.close()

    uploaded_urls = [
        str(item).strip()
        for item in list(context.get("image_probe_uploaded_urls") or [])
        if str(item).strip()
    ]
    if probe_error is not None:
        evidence = {
            "status": "blocked" if isinstance(probe_error, ImageAlbumFullError) else "failed",
            "error_type": type(probe_error).__name__,
            "error": str(probe_error),
            "probe_count": args.count,
            "draft_id": draft_id,
            "uploaded_count": len(uploaded_urls),
            "album_selected": dict(context.get("image_probe_album_selected") or {}),
            "album_rotation_count": int(context.get("image_probe_album_rotation_count") or 0),
            "failed_album_values": list(context.get("image_probe_failed_album_values") or []),
            "album_created": bool(context.get("image_probe_album_created")),
            "album_name": str(context.get("image_probe_album_name") or ""),
            "album_access": str(context.get("image_probe_album_access") or ""),
            "album_access_label": str(context.get("image_probe_album_access_label") or ""),
            "album_access_verified": bool(context.get("image_probe_album_access_verified")),
            "album_capacity": int(context.get("image_probe_album_capacity") or 0),
            "platform_message": str(context.get("image_probe_album_full_message") or "")[:1000],
            "page_diagnostics": page_diagnostics,
            "draft_saved": False,
            "offer_submitted": False,
        }
        emit_evidence(evidence, args.evidence_output)
        return 2

    if len(uploaded_urls) != args.count:
        raise RuntimeError(f"probe returned {len(uploaded_urls)}/{args.count} remote URLs")

    evidence = {
        "status": "passed",
        "probe_count": args.count,
        "draft_id": draft_id,
        "uploaded_count": len(uploaded_urls),
        "album_created": bool(context.get("image_probe_album_created")),
        "album_name": str(context.get("image_probe_album_name") or ""),
        "album_access": str(context.get("image_probe_album_access") or ""),
        "album_access_label": str(context.get("image_probe_album_access_label") or ""),
        "album_access_verified": bool(context.get("image_probe_album_access_verified")),
        "album_capacity": int(context.get("image_probe_album_capacity") or 0),
        "album_selected": dict(context.get("image_probe_album_selected") or {}),
        "album_rotation_count": int(context.get("image_probe_album_rotation_count") or 0),
        "remote_hosts": sorted({urlparse(url).hostname or "" for url in uploaded_urls}),
        "draft_saved": False,
        "offer_submitted": False,
    }
    emit_evidence(evidence, args.evidence_output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
