"""Run a listing task in preflight mode or behind explicit production guards."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
RPA_ROOT = PROJECT_ROOT / "rpa"
if str(RPA_ROOT) not in sys.path:
    sys.path.insert(0, str(RPA_ROOT))

from auto_listing import advance_listing_state, validate_listing_payload
from auto_listing_executor import (
    assert_execution_allowed,
    build_execution_payload,
    execute_browser_task,
    extract_detail_upload_resume_evidence,
    extract_draft_reconciliation_evidence,
    extract_submit_reconciliation_evidence,
    restore_execution_only_detail_images,
)
from config_loader import load_json_with_local_override
from exceptions import ImageAlbumFullError
from listing_audit import ListingAuditRepository
from stop_sale_audit import resolve_stop_sale_app_config


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Guarded 1688 single-item listing worker.")
    parser.add_argument("--payload", required=True, help="listing_task_payload_v1 JSON file")
    parser.add_argument(
        "--mode",
        choices=[
            "preflight",
            "resume",
            "draft",
            "approve",
            "reject",
            "submit",
            "reconcile-draft",
            "reconcile-submit",
            "writeback",
        ],
        default="preflight",
    )
    parser.add_argument("--output", default="", help="updated payload output path")
    parser.add_argument("--skip-login", action="store_true")
    parser.add_argument("--operator", default="", help="operator recorded for resume evidence")
    parser.add_argument(
        "--draft-id",
        default="",
        help="known historical draft ID to rebind during an explicitly authorized review repair",
    )
    parser.add_argument(
        "--capacity-evidence",
        default="",
        help="fresh passed output from probe_1688_image_picker.py; required for resume",
    )
    parser.add_argument(
        "--detail-limit",
        type=int,
        default=0,
        help="draft-only real acceptance probe using the first N detail images",
    )
    parser.add_argument(
        "--detail-upload-resume-context",
        default="",
        help="failure-context JSON containing contiguous completed picker batches, including a full checkpoint",
    )
    parser.add_argument("--offer-url", default="", help="verified offer URL for writeback")
    parser.add_argument(
        "--draft-evidence",
        default="",
        help="failed draft context that proves 1688 returned a matching draft_id",
    )
    parser.add_argument(
        "--draft-inspection",
        default="",
        help="read-only independent inspection of the recovered draft",
    )
    parser.add_argument(
        "--submit-evidence",
        default="",
        help="failed submit context that proves the first click reached the 1688 success page",
    )
    parser.add_argument(
        "--shared-runtime-root",
        default=os.getenv("YYDD_1688_RUNTIME_ROOT", "D:/script_1688"),
        help="1688 runtime root used by the external secret provider",
    )
    return parser


def _write_result(payload: dict, output: str) -> None:
    rendered = json.dumps(payload, ensure_ascii=False, indent=2, default=str)
    if output:
        Path(output).write_text(rendered + "\n", encoding="utf-8")
    print(rendered)


def _write_failure_context(output: str, *, payload: dict, context: dict, error: Exception) -> str:
    if not output:
        return ""
    output_path = Path(output)
    failure_path = output_path.with_name(f"{output_path.stem}.failure-context.json")
    failure_payload = {
        "task_id": str(payload.get("task_id") or ""),
        "error_type": type(error).__name__,
        "error": str(error),
        "result_context": context,
    }
    failure_path.write_text(
        json.dumps(failure_payload, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    return str(failure_path)


def _load_listing_audit_repository(shared_runtime_root: str) -> ListingAuditRepository:
    config = resolve_stop_sale_app_config(shared_runtime_root)
    repository = ListingAuditRepository(config)
    contract = repository.check_contract()
    if not contract.get("ready"):
        missing = ", ".join(str(item) for item in contract.get("missing_tables") or [])
        raise RuntimeError(f"listing audit database contract is not ready: {missing}")
    return repository


def main() -> int:
    args = build_parser().parse_args()
    payload = json.loads(Path(args.payload).read_text(encoding="utf-8-sig"))
    if args.mode == "preflight":
        _write_result(validate_listing_payload(payload, require_duplicate_clear=True), args.output)
        return 0
    if args.mode == "resume":
        if not args.capacity_evidence:
            raise ValueError("--capacity-evidence is required for resume")
        capacity_probe = json.loads(
            Path(args.capacity_evidence).read_text(encoding="utf-8-sig")
        )
        current_state = str((payload.get("workflow") or {}).get("state") or "").strip()
        last_event = str((payload.get("workflow") or {}).get("last_event") or "").strip()
        if current_state == "draft_pending":
            event = "capacity_reverified"
        elif current_state == "blocked" and last_event == "review_rejected":
            event = "review_repair_resumed"
        else:
            event = "execution_resumed"
        operator_field = "verified_by" if event == "capacity_reverified" else "resumed_by"
        reason = (
            "review_required_fields_repair"
            if event == "review_repair_resumed"
            else "image_album_capacity_verified"
        )
        workflow = payload.get("workflow") or {}
        resume_draft_id = str(
            args.draft_id
            or workflow.get("pending_draft_id")
            or ((workflow.get("draft") or {}).get("draft_id") or "")
        ).strip()
        updated = advance_listing_state(
            payload,
            event,
            evidence={
                operator_field: args.operator,
                "reason": reason,
                "draft_id": resume_draft_id,
                "capacity_probe": capacity_probe,
            },
        )
        repository = _load_listing_audit_repository(args.shared_runtime_root)
        repository.upsert_task(updated)
        repository.record_latest_event(updated, operator_name=args.operator)
        _write_result(updated, args.output)
        return 0
    if args.mode == "writeback":
        updated = advance_listing_state(payload, "offer_written_back", evidence={"offer_url": args.offer_url})
        repository = _load_listing_audit_repository(args.shared_runtime_root)
        repository.upsert_task(updated)
        repository.record_latest_event(updated, operator_name=args.operator)
        _write_result(updated, args.output)
        return 0
    if args.mode == "reconcile-submit":
        if not args.submit_evidence:
            raise ValueError("--submit-evidence is required for reconcile-submit")
        failure_payload = json.loads(Path(args.submit_evidence).read_text(encoding="utf-8-sig"))
        evidence = extract_submit_reconciliation_evidence(payload, failure_payload)
        updated = advance_listing_state(payload, "submit_succeeded", evidence=evidence)
        repository = _load_listing_audit_repository(args.shared_runtime_root)
        repository.upsert_task(updated)
        repository.record_latest_event(updated, operator_name=args.operator)
        _write_result(updated, args.output)
        return 0
    if args.mode == "reconcile-draft":
        if not args.draft_evidence:
            raise ValueError("--draft-evidence is required for reconcile-draft")
        if not args.draft_inspection:
            raise ValueError("--draft-inspection is required for reconcile-draft")
        failure_payload = json.loads(Path(args.draft_evidence).read_text(encoding="utf-8-sig"))
        inspection_payload = json.loads(Path(args.draft_inspection).read_text(encoding="utf-8-sig"))
        evidence = extract_draft_reconciliation_evidence(payload, failure_payload, inspection_payload)
        updated = advance_listing_state(payload, "draft_saved", evidence=evidence)
        repository = _load_listing_audit_repository(args.shared_runtime_root)
        repository.upsert_task(updated)
        repository.record_latest_event(updated, operator_name=args.operator)
        _write_result(updated, args.output)
        return 0
    if args.mode in {"approve", "reject"}:
        if not str(args.operator or "").strip():
            raise ValueError("--operator is required for review decisions")
        if args.mode == "approve":
            event = "review_approved"
            evidence = {"approved_by": args.operator}
        else:
            event = "review_rejected"
            evidence = {"rejected_by": args.operator}
        updated = advance_listing_state(payload, event, evidence=evidence)
        repository = _load_listing_audit_repository(args.shared_runtime_root)
        repository.upsert_task(updated)
        repository.record_latest_event(updated, operator_name=args.operator)
        _write_result(updated, args.output)
        return 0

    assert_execution_allowed(payload, args.mode)
    execution_payload = build_execution_payload(
        payload,
        mode=args.mode,
        detail_limit=args.detail_limit,
    )
    if args.detail_upload_resume_context:
        failure_payload = json.loads(
            Path(args.detail_upload_resume_context).read_text(encoding="utf-8-sig")
        )
        detail_count = len(list(((execution_payload.get("images") or {}).get("detail_urls") or [])))
        resume_evidence = extract_detail_upload_resume_evidence(
            failure_payload,
            expected_task_id=str(execution_payload.get("task_id") or ""),
            expected_detail_count=detail_count,
            batch_size=3,
        )
        execution_images = execution_payload.setdefault("images", {})
        execution_images["detail_upload_resume_urls"] = resume_evidence["uploaded_urls"]
        execution_images["detail_upload_excluded_album_values"] = resume_evidence[
            "excluded_album_values"
        ]
    repository = _load_listing_audit_repository(args.shared_runtime_root)
    repository.upsert_task(payload)

    from browser_rpa import BrowserRPA

    config_dir = PROJECT_ROOT / "config"
    platform_config = load_json_with_local_override(config_dir / "platforms" / "1688.json")
    system_config = load_json_with_local_override(config_dir / "systems" / "1688_direct.json")
    operator_config = load_json_with_local_override(config_dir / "operator_config.json")
    category_config = load_json_with_local_override(config_dir / "furniture_categories.json")
    browser = BrowserRPA(operator_config.get("browser", {}), PROJECT_ROOT)
    execution_id = repository.start_execution(task_id=str(payload.get("task_id") or ""), mode=args.mode)
    try:
        browser.open()
        if not args.skip_login:
            browser.run_system_workflow(system_config, {})
        updated, _context = execute_browser_task(
            execution_payload,
            mode=args.mode,
            browser=browser,
            platform_config=platform_config,
            category_config=category_config,
            project_root=PROJECT_ROOT,
        )
        updated = restore_execution_only_detail_images(updated, payload)
    except ImageAlbumFullError as exc:
        context = dict(browser.last_result_context or {})
        evidence = {
            "reason": "image_album_full",
            "message": str(exc),
            "draft_id": str(((payload.get("workflow") or {}).get("pending_draft_id") or "")),
            "failed_album_values": list(context.get("detail_images_failed_album_values") or []),
            "platform_message": str(context.get("detail_images_album_full_message") or ""),
            "shop_skip_remaining": True,
        }
        updated = advance_listing_state(payload, "execution_blocked", evidence=evidence)
        repository.upsert_task(updated)
        repository.record_latest_event(updated, operator_name=args.operator)
        repository.finish_execution(
            execution_id=execution_id,
            status="blocked",
            result=updated,
            error_code="IMAGE_ALBUM_FULL",
            error_summary=str(exc),
        )
        _write_result(updated, args.output)
        return 2
    except Exception as exc:
        context = dict(browser.last_result_context or {})
        failure_context_path = _write_failure_context(
            args.output,
            payload=payload,
            context=context,
            error=exc,
        )
        repository.finish_execution(
            execution_id=execution_id,
            status="failed",
            error_code=type(exc).__name__,
            error_summary=(
                f"{exc}; failure_context={failure_context_path}"
                if failure_context_path
                else str(exc)
            ),
        )
        raise
    finally:
        browser.close()
    repository.upsert_task(updated)
    repository.record_latest_event(updated, operator_name=args.operator)
    repository.finish_execution(execution_id=execution_id, status="success", result=updated)
    _write_result(updated, args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
