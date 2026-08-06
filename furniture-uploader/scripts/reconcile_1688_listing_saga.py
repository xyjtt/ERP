"""Recover a listing Saga only after proving a browser pre-action failure."""

from __future__ import annotations

import argparse
from datetime import date, datetime
import hashlib
import json
from pathlib import Path
import re
import sys
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
RPA_ROOT = PROJECT_ROOT / "rpa"
if str(RPA_ROOT) not in sys.path:
    sys.path.insert(0, str(RPA_ROOT))

from listing_review import build_listing_operation_key
from operation_saga import OperationSagaRepository
from stop_sale_audit import connect_app_database, resolve_stop_sale_app_config


SAFE_PRE_ACTION_ERROR = "SessionNotCreatedException"
EDGE_PRE_ATTACH_ENDPOINT_PATTERN = re.compile(
    r"cannot connect to microsoft edge at 127\.0\.0\.1:(?P<port>\d{1,5})"
)
CHROMEDRIVER_PRE_ATTACH_ENDPOINT_PATTERN = re.compile(
    r"cannot connect to chrome at 127\.0\.0\.1:(?P<port>\d{1,5})"
)
CHROMEDRIVER_EDGE_VERSION_PATTERN = re.compile(
    r"unrecognized Chrome version:\s*Edg/[0-9.]+",
    re.IGNORECASE,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Preview or apply a controlled listing Saga pre-action recovery."
    )
    parser.add_argument("--shared-runtime-root", required=True, type=Path)
    parser.add_argument("--task-id", required=True)
    parser.add_argument("--account-key", required=True)
    parser.add_argument("--draft-id", required=True)
    parser.add_argument("--operation-mode", choices=["draft", "submit"], required=True)
    parser.add_argument("--execution-id", required=True)
    parser.add_argument("--failure-context", required=True, type=Path)
    parser.add_argument("--expected-owner-token-hash", required=True)
    parser.add_argument("--expected-account-fencing-token", required=True, type=int)
    parser.add_argument("--expected-browser-slot-key", required=True)
    parser.add_argument("--expected-browser-slot-fencing-token", required=True, type=int)
    parser.add_argument("--reason", required=True)
    parser.add_argument("--apply", action="store_true")
    return parser


def _json_value(value: Any) -> Any:
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    return value


def _extract_pre_attach_evidence(error: str) -> tuple[int, str]:
    match = EDGE_PRE_ATTACH_ENDPOINT_PATTERN.search(error)
    signature_prefix = "edge"
    if match is None:
        match = CHROMEDRIVER_PRE_ATTACH_ENDPOINT_PATTERN.search(error)
        if match is None or CHROMEDRIVER_EDGE_VERSION_PATTERN.search(error) is None:
            raise RuntimeError("missing_pre_attach_signature")
        signature_prefix = "chromedriver_edge_version_mismatch"
    port = int(match.group("port"))
    if not 1 <= port <= 65535:
        raise RuntimeError("invalid_pre_attach_cdp_port")
    return port, f"{signature_prefix}_127.0.0.1_{port}_unreachable"


def _extract_pre_attach_cdp_port(error: str) -> int:
    return _extract_pre_attach_evidence(error)[0]


def _validate_failure_context(path: Path, *, task_id: str) -> dict[str, Any]:
    raw = path.read_bytes()
    payload = json.loads(raw.decode("utf-8-sig"))
    if str(payload.get("task_id") or "") != task_id:
        raise RuntimeError("failure_context_task_id_mismatch")
    if str(payload.get("error_type") or "") != SAFE_PRE_ACTION_ERROR:
        raise RuntimeError("failure_context_not_safe_pre_action_error")
    if dict(payload.get("result_context") or {}):
        raise RuntimeError("failure_context_contains_browser_action_evidence")
    error = str(payload.get("error") or "")
    try:
        cdp_port, pre_attach_signature = _extract_pre_attach_evidence(error)
    except RuntimeError as exc:
        raise RuntimeError("failure_context_missing_pre_attach_signature") from exc
    return {
        "path": str(path.resolve()),
        "sha256": hashlib.sha256(raw).hexdigest(),
        "error_type": SAFE_PRE_ACTION_ERROR,
        "result_context_empty": True,
        "pre_attach_signature": pre_attach_signature,
        "cdp_port": cdp_port,
    }


def _load_execution_evidence(
    config: Any,
    *,
    operation_key: str,
    task_id: str,
    execution_id: str,
    expected_cdp_port: int,
) -> dict[str, Any]:
    with connect_app_database(config) as connection:
        cursor = connection.cursor()
        row = cursor.execute(
            """
            SELECT execution_id, task_id, execution_mode, status, offer_id,
                   offer_url, result_json, error_code, error_summary,
                   started_at, finished_at
              FROM app.ali1688_listing_execution WITH (NOLOCK)
             WHERE execution_id = ? AND task_id = ?
            """,
            (execution_id, task_id),
        ).fetchone()
        if row is None:
            raise RuntimeError("listing_execution_missing")
        columns = (
            "execution_id",
            "task_id",
            "execution_mode",
            "status",
            "offer_id",
            "offer_url",
            "result_json",
            "error_code",
            "error_summary",
            "started_at",
            "finished_at",
        )
        execution = dict(zip(columns, row))
        if str(execution["status"] or "") != "failed":
            raise RuntimeError("listing_execution_not_failed")
        if str(execution["error_code"] or "") != SAFE_PRE_ACTION_ERROR:
            raise RuntimeError("listing_execution_not_safe_pre_action_error")
        if any(execution[name] is not None for name in ("offer_id", "offer_url", "result_json")):
            raise RuntimeError("listing_execution_contains_side_effect_result")
        try:
            execution_cdp_port = _extract_pre_attach_cdp_port(
                str(execution["error_summary"] or "")
            )
        except RuntimeError as exc:
            raise RuntimeError("listing_execution_missing_pre_attach_signature") from exc
        if execution_cdp_port != expected_cdp_port:
            raise RuntimeError("listing_execution_pre_attach_endpoint_mismatch")
        audit_count = int(
            cursor.execute(
                """
                SELECT COUNT_BIG(1)
                  FROM app.ali1688_listing_audit WITH (NOLOCK)
                 WHERE task_id = ? AND created_at >= ?
                """,
                (task_id, execution["started_at"]),
            ).fetchone()[0]
        )
        outbox_count = int(
            cursor.execute(
                """
                SELECT COUNT_BIG(1)
                  FROM app.ali1688_operation_outbox WITH (NOLOCK)
                 WHERE operation_key = ?
                """,
                (operation_key,),
            ).fetchone()[0]
        )
    if audit_count:
        raise RuntimeError("listing_audit_exists_after_failed_execution_started")
    if outbox_count:
        raise RuntimeError("listing_outbox_exists")
    return {
        "execution_id": str(execution["execution_id"]),
        "execution_mode": str(execution["execution_mode"] or ""),
        "status": "failed",
        "error_code": SAFE_PRE_ACTION_ERROR,
        "started_at": _json_value(execution["started_at"]),
        "finished_at": _json_value(execution["finished_at"]),
        "offer_id": None,
        "offer_url": None,
        "result_json": None,
        "cdp_port": execution_cdp_port,
        "audit_events_since_start": 0,
        "outbox_count": 0,
    }


def main() -> int:
    args = build_parser().parse_args()
    config = resolve_stop_sale_app_config(args.shared_runtime_root)
    repository = OperationSagaRepository(config)
    operation_key = build_listing_operation_key(
        account_key=args.account_key,
        task_id=args.task_id,
        draft_id=args.draft_id,
        mode=args.operation_mode,
    )
    saga = repository.get_saga_state(operation_key)
    expected = {
        "state": "reconcile_required",
        "error_code": "owner_changed_after_prepare",
        "owner_token_hash": args.expected_owner_token_hash,
        "account_fencing_token": args.expected_account_fencing_token,
        "browser_slot_key": args.expected_browser_slot_key,
        "browser_slot_fencing_token": args.expected_browser_slot_fencing_token,
        "ali1688_finished_at": None,
        "evidence_json": None,
    }
    changed = [name for name, value in expected.items() if saga.get(name) != value]
    if changed:
        raise RuntimeError("saga_preview_precondition_changed:" + ",".join(changed))
    if str(saga.get("run_id") or "") != args.task_id:
        raise RuntimeError("saga_run_id_mismatch")
    if str(saga.get("account_key") or "") != args.account_key:
        raise RuntimeError("saga_account_key_mismatch")

    failure_context = _validate_failure_context(args.failure_context, task_id=args.task_id)
    execution = _load_execution_evidence(
        config,
        operation_key=operation_key,
        task_id=args.task_id,
        execution_id=args.execution_id,
        expected_cdp_port=int(failure_context["cdp_port"]),
    )
    evidence = {
        "classification": "verified_browser_pre_action_failure",
        "failure_context": failure_context,
        "execution": execution,
        "saga_precondition": {
            name: _json_value(saga.get(name))
            for name in (
                "state",
                "error_code",
                "owner_token_hash",
                "account_fencing_token",
                "browser_slot_key",
                "browser_slot_fencing_token",
                "prepared_at",
                "ali1688_finished_at",
            )
        },
    }
    result: dict[str, Any] = {
        "status": "ready",
        "mode": "apply" if args.apply else "preview",
        "operation_key": operation_key,
        "evidence": evidence,
    }
    if args.apply:
        result["recovery"] = repository.recover_reconcile_required(
            operation_key,
            expected_error_code="owner_changed_after_prepare",
            expected_owner_token_hash=args.expected_owner_token_hash,
            expected_account_fencing_token=args.expected_account_fencing_token,
            expected_browser_slot_key=args.expected_browser_slot_key,
            expected_browser_slot_fencing_token=args.expected_browser_slot_fencing_token,
            evidence=evidence,
            reason=args.reason,
        )
        result["status"] = "applied"
        result["saga_after"] = repository.get_saga_state(operation_key)
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
