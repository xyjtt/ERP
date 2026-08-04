"""Run a listing task in preflight mode or behind explicit production guards."""

from __future__ import annotations

import argparse
from contextlib import ExitStack
import json
import os
import re
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
RPA_ROOT = PROJECT_ROOT / "rpa"
if str(RPA_ROOT) not in sys.path:
    sys.path.insert(0, str(RPA_ROOT))

from auto_listing import (
    ListingContractError,
    advance_listing_state,
    validate_listing_payload,
)
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
from cross_project_runtime import (
    RuntimeLeaseGuard,
    RuntimeLeaseRepository,
    resolve_build_sha,
    resolve_executor_binding,
)
from operation_saga import OperationSagaRepository, SagaOperation, build_operation_key
from sku_offline_auth import (
    OfflineLoginRequiredError,
    OfflineRiskControlError,
    OfflineStoreMismatchError,
    ensure_1688_authenticated_session,
)
from stop_sale_audit import resolve_stop_sale_app_config


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Guarded 1688 single-item listing worker.")
    parser.add_argument("--payload", required=True, help="listing_task_payload_v1 JSON file")
    parser.add_argument(
        "--mode",
        choices=[
            "preflight",
            "resume",
            "rebuild",
            "invalidate-draft",
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
        help=(
            "known historical draft ID to rebind: required in draft mode when the task "
            "already has a historical draft but no pending_draft_id; also used by "
            "resume/invalidate-draft"
        ),
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
    parser.add_argument(
        "--deletion-evidence",
        default="",
        help="verified deletion report for every corrupt historical draft; required for rebuild",
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
    parser.add_argument("--lock-wait-seconds", type=int, default=0)
    parser.add_argument("--lock-stale-seconds", type=int, default=21600)
    parser.add_argument("--lock-poll-seconds", type=float, default=5.0)
    parser.add_argument("--runtime-lease-wait-seconds", type=float, default=4800)
    parser.add_argument("--runtime-lease-poll-seconds", type=float, default=5.0)
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


def _known_historical_draft_ids(payload: dict) -> set[str]:
    workflow = payload.get("workflow") or {}
    known: set[str] = set()
    current = str(((workflow.get("draft") or {}).get("draft_id") or "")).strip()
    if current:
        known.add(current)
    for item in list(workflow.get("event_history") or []):
        if not isinstance(item, dict):
            continue
        if str(item.get("event") or "").strip() != "draft_saved":
            continue
        draft_id = str(((item.get("evidence") or {}).get("draft_id") or "")).strip()
        if draft_id:
            known.add(draft_id)
    return known


def _apply_draft_rebind(
    args: argparse.Namespace,
    payload: dict,
    execution_payload: dict,
) -> dict:
    """Bind draft execution to an existing 1688 draft or fail closed.

    ``--draft-id`` is honored in draft mode only: it must reference a draft ID
    that this task has already saved (workflow.draft.draft_id or any draft_saved
    event), and it becomes the execution ``pending_draft_id`` so the publish URL
    and the draft-request patch both carry the existing draft identity.

    Without an explicit rebind, draft mode must never silently create a new
    draft for a task that already has a historical draft; the only exception is
    an explicitly authorized rebuild (last_event=authorized_draft_rebuild_resumed).
    """
    requested = str(getattr(args, "draft_id", "") or "").strip()
    if args.mode != "draft":
        if requested:
            raise ValueError("--draft-id rebind is only valid for draft mode")
        return execution_payload
    workflow = execution_payload.setdefault("workflow", {})
    if requested:
        if not re.fullmatch(r"[A-Za-z0-9_-]+", requested):
            raise ValueError("--draft-id requires a valid draft ID")
        if requested not in _known_historical_draft_ids(payload):
            raise ValueError(
                "--draft-id must reference a known historical draft ID for this task"
            )
        workflow["pending_draft_id"] = requested
        return execution_payload
    pending = str(workflow.get("pending_draft_id") or "").strip()
    if pending:
        return execution_payload
    last_event = str((workflow.get("last_event") or "")).strip()
    if last_event != "authorized_draft_rebuild_resumed" and _known_historical_draft_ids(payload):
        raise ListingContractError(
            "draft mode would create a new 1688 draft, but this task already has "
            "a historical draft; re-run with --draft-id <known draft_id> to explicitly "
            "rebind the existing draft"
        )
    return execution_payload


def _record_controlled_saga_failure(
    saga_repository: OperationSagaRepository,
    *,
    operation_key: str,
    account_fencing_token: int,
    exc: Exception,
) -> str:
    """Move the saga to failed_terminal for an expected, controlled failure.

    Returns a short audit note for execution records. Unexpected exceptions must
    not be routed here: they leave the saga in ``prepared`` so the next run is
    forced through reconcile instead of a blind retry.
    """
    try:
        saga_repository.record_ali1688_result(
            operation_key=operation_key,
            account_fencing_token=account_fencing_token,
            status="failed",
            error_code=type(exc).__name__,
            error_summary=str(exc),
        )
    except Exception as saga_exc:
        return f"saga_record_failed={type(saga_exc).__name__}: {saga_exc}"
    return "saga=failed_terminal"


def _resolve_listing_account_lock_path(args: argparse.Namespace, payload: dict) -> tuple[Path, str]:
    account_key = str(((payload.get("shop") or {}).get("account_key") or "")).strip()
    if not re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", account_key):
        raise ValueError("listing payload requires a valid shop.account_key for browser locking")
    runtime_root = Path(args.shared_runtime_root).resolve()
    return runtime_root / "artifacts" / "locks" / f"ali1688_account_{account_key}.lock", account_key


def _build_listing_account_lock(args: argparse.Namespace, payload: dict):
    lock_path, account_key = _resolve_listing_account_lock_path(args, payload)
    runtime_root = Path(args.shared_runtime_root).resolve()
    lock_module = runtime_root / "src" / "runtime" / "global_lock.py"
    if not lock_module.exists():
        raise FileNotFoundError(f"Shared 1688 runtime lock module not found: {lock_module}")
    if str(runtime_root) not in sys.path:
        sys.path.insert(0, str(runtime_root))

    from src.runtime.global_lock import GlobalFileLock

    return GlobalFileLock(
        lock_path,
        stale_after_seconds=args.lock_stale_seconds,
        wait_timeout_seconds=args.lock_wait_seconds,
        poll_interval_seconds=args.lock_poll_seconds,
        metadata={
            "cycle": "1688_listing_task",
            "task_id": str(payload.get("task_id") or ""),
            "account_key": account_key,
            "mode": str(args.mode or ""),
            "payload": str(Path(args.payload).resolve()),
        },
    )


def _open_authenticated_listing_browser(
    browser,
    *,
    shared_runtime_root: str,
    account_key: str,
    shop_name: str,
    system_config: dict,
    skip_login: bool,
) -> None:
    if skip_login:
        browser.open()
        return

    try:
        ensure_1688_authenticated_session(
            shared_runtime_root,
            account_key,
            shop_name,
        )
    except (OfflineRiskControlError, OfflineStoreMismatchError):
        raise
    except OfflineLoginRequiredError:
        browser.open()
        browser.run_system_workflow(system_config, {})
        return

    # The shared login flow owns startup of the account CDP runtime. Connect
    # BrowserRPA only after that runtime is ready.
    browser.open()


def _build_listing_browser_config(operator_config: dict, *, cdp_port: int) -> dict:
    browser_config = dict(operator_config.get("browser") or {})
    if int(cdp_port) <= 0:
        raise ValueError("cdp_port must be positive")
    browser_config["debugger_address"] = f"127.0.0.1:{int(cdp_port)}"
    return browser_config


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
        elif current_state == "blocked" and last_event == "draft_verification_failed":
            event = "draft_verification_repair_resumed"
        else:
            event = "execution_resumed"
        operator_field = "verified_by" if event == "capacity_reverified" else "resumed_by"
        reason = (
            "review_required_fields_repair"
            if event == "review_repair_resumed"
            else (
                "server_draft_fields_missing"
                if event == "draft_verification_repair_resumed"
                else "image_album_capacity_verified"
            )
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
    if args.mode == "invalidate-draft":
        if not args.draft_inspection:
            raise ValueError("--draft-inspection is required for invalidate-draft")
        inspection = json.loads(Path(args.draft_inspection).read_text(encoding="utf-8-sig"))
        checks = inspection.get("checks") or {}
        missing_checks = sorted(str(name) for name, passed in checks.items() if passed is not True)
        current_draft_id = str(
            args.draft_id
            or ((payload.get("workflow") or {}).get("draft") or {}).get("draft_id")
            or ""
        ).strip()
        if str(inspection.get("task_id") or "").strip() != str(payload.get("task_id") or "").strip():
            raise ValueError("draft inspection task_id does not match payload")
        if str(inspection.get("draft_id") or "").strip() != current_draft_id:
            raise ValueError("draft inspection draft_id does not match payload")
        if str(inspection.get("status") or "").strip() != "failed" or not missing_checks:
            raise ValueError("invalidate-draft requires a failed independent inspection")
        updated = advance_listing_state(
            payload,
            "draft_verification_failed",
            evidence={
                "reason": "server_draft_fields_missing",
                "draft_id": current_draft_id,
                "inspection_status": "failed",
                "inspection_checked_at": str(inspection.get("checked_at") or "").strip(),
                "missing_checks": missing_checks,
            },
        )
        repository = _load_listing_audit_repository(args.shared_runtime_root)
        repository.upsert_task(updated)
        repository.record_latest_event(updated, operator_name=args.operator)
        _write_result(updated, args.output)
        return 0
    if args.mode == "rebuild":
        if not args.deletion_evidence or not args.capacity_evidence:
            raise ValueError("--deletion-evidence and --capacity-evidence are required for rebuild")
        deletion_evidence = json.loads(
            Path(args.deletion_evidence).read_text(encoding="utf-8-sig")
        )
        capacity_probe = json.loads(
            Path(args.capacity_evidence).read_text(encoding="utf-8-sig")
        )
        updated = advance_listing_state(
            payload,
            "authorized_draft_rebuild_resumed",
            evidence={
                "authorized_by": args.operator,
                "reason": "authorized_corrupt_draft_rebuild",
                "deletion_evidence": deletion_evidence,
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
    execution_payload = _apply_draft_rebind(args, payload, execution_payload)
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

    from browser_rpa import BrowserRPA, PublishValidationError

    config_dir = PROJECT_ROOT / "config"
    platform_config = load_json_with_local_override(config_dir / "platforms" / "1688.json")
    system_config = load_json_with_local_override(config_dir / "systems" / "1688_direct.json")
    operator_config = load_json_with_local_override(config_dir / "operator_config.json")
    category_config = load_json_with_local_override(config_dir / "furniture_categories.json")
    app_config = resolve_stop_sale_app_config(args.shared_runtime_root)
    saga_repository = OperationSagaRepository(app_config)
    saga_contract = saga_repository.check_contract()
    if not saga_contract.get("ready"):
        raise RuntimeError(
            "ERP operation saga tables are missing: "
            + ", ".join(str(item) for item in saga_contract.get("missing_tables") or [])
        )
    task_id = str(payload.get("task_id") or "").strip()
    account_key = str(((payload.get("shop") or {}).get("account_key") or "")).strip()
    operation_key = build_operation_key("listing", account_key, task_id)
    build_sha = resolve_build_sha(PROJECT_ROOT.parent)
    binding = resolve_executor_binding(account_key)
    browser = BrowserRPA(
        _build_listing_browser_config(operator_config, cdp_port=binding.cdp_port),
        PROJECT_ROOT,
    )
    with ExitStack() as stack:
        stack.enter_context(_build_listing_account_lock(args, payload))
        runtime_guard = stack.enter_context(
            RuntimeLeaseGuard(
                RuntimeLeaseRepository(app_config),
                binding=binding,
                task_type="listing",
                run_id=task_id,
                request_key=operation_key,
                build_sha=build_sha,
                component="erp-listing",
                wait_timeout_seconds=args.runtime_lease_wait_seconds,
                poll_interval_seconds=args.runtime_lease_poll_seconds,
            )
        )
        browser.set_runtime_action_guard(runtime_guard.assert_active)
        runtime_guard.register_owned_browser_closer(browser.close)
        saga_repository.prepare(
            SagaOperation(
                operation_key=operation_key,
                run_id=task_id,
                task_type="listing",
                account_key=account_key,
                business_key=task_id,
                payload={
                    "task_id": task_id,
                    "mode": args.mode,
                    "shop_name": str(((payload.get("shop") or {}).get("shop_name") or "")),
                    "company_sku": str(((payload.get("source") or {}).get("company_sku") or "")),
                },
            ),
            owner_token=runtime_guard.owner_token,
            account_fencing_token=runtime_guard.account_fencing_token,
            browser_slot_key=runtime_guard.browser_slot_key,
            browser_slot_fencing_token=runtime_guard.browser_slot_fencing_token,
        )
        execution_id = repository.start_execution(task_id=task_id, mode=args.mode)
        try:
            _open_authenticated_listing_browser(
                browser,
                shared_runtime_root=args.shared_runtime_root,
                account_key=account_key,
                shop_name=str(((payload.get("shop") or {}).get("shop_name") or "")).strip(),
                system_config=system_config,
                skip_login=args.skip_login,
            )
            updated, _context = execute_browser_task(
                execution_payload,
                mode=args.mode,
                browser=browser,
                platform_config=platform_config,
                category_config=category_config,
                project_root=PROJECT_ROOT,
            )
            updated = restore_execution_only_detail_images(updated, payload)
            runtime_guard.assert_active()
            updated["runtime_lease"] = {
                "protocol_version": 1,
                "build_sha": build_sha,
                "account_fencing_token": runtime_guard.account_fencing_token,
                "browser_slot_key": runtime_guard.browser_slot_key,
                "browser_slot_fencing_token": runtime_guard.browser_slot_fencing_token,
            }
            saga_repository.record_ali1688_result(
                operation_key=operation_key,
                account_fencing_token=runtime_guard.account_fencing_token,
                status="draft_saved" if args.mode == "draft" else "submit_succeeded",
                evidence={"workflow": updated.get("workflow") or {}, "runtime": updated["runtime_lease"]},
            )
        except ImageAlbumFullError as exc:
            context = dict(browser.last_result_context or {})
            saga_note = _record_controlled_saga_failure(
                saga_repository,
                operation_key=operation_key,
                account_fencing_token=runtime_guard.account_fencing_token,
                exc=exc,
            )
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
                error_summary=f"{exc}; {saga_note}",
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
            saga_note = ""
            if isinstance(exc, (PublishValidationError, ListingContractError)):
                saga_note = "; " + _record_controlled_saga_failure(
                    saga_repository,
                    operation_key=operation_key,
                    account_fencing_token=runtime_guard.account_fencing_token,
                    exc=exc,
                )
            repository.finish_execution(
                execution_id=execution_id,
                status="failed",
                error_code=type(exc).__name__,
                error_summary=(
                    (
                        f"{exc}; failure_context={failure_context_path}"
                        if failure_context_path
                        else str(exc)
                    )
                    + saga_note
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
