"""Safe daily orchestration for curated 1688 listing payloads.

The manager consumes complete, reviewed ``listing_task_payload_v1`` files. It
does not invent titles, images, shop identities, prices, or logistics from raw
source rows. Automatic execution stops at a saved draft pending independent
review; approval and submit remain separate guarded actions.
"""

from __future__ import annotations

import argparse
import copy
from collections import Counter
from datetime import date, datetime
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from typing import Any, Callable, Iterable


PROJECT_ROOT = Path(__file__).resolve().parents[1]
RPA_ROOT = PROJECT_ROOT / "rpa"
if str(RPA_ROOT) not in sys.path:
    sys.path.insert(0, str(RPA_ROOT))

from auto_listing import STATE_DRAFT_PENDING, STATE_DRAFT_PENDING_REVIEW, validate_listing_payload
from listing_audit import ListingAuditRepository, build_listing_task_record
from query_1688_listing_source import (
    DEFAULT_CREDENTIAL_REF,
    DEFAULT_DATABASE,
    DEFAULT_PORT,
    DEFAULT_SERVER,
    ListingSourceReader,
    build_source_config,
    source_gate_passed,
)
from stop_sale_audit import resolve_stop_sale_app_config
from listing_review import require_unique_existing_draft_id


DAILY_TASK_NAME = "YYDD-1688-Listing-Daily"
DEFAULT_ACCOUNT_KEY = "muke_lixiang"
SAFE_TASK_ID = re.compile(r"[A-Za-z0-9_.-]{1,100}")


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Daily guarded 1688 draft listing manager.")
    subparsers = parser.add_subparsers(dest="command", required=True)
    run = subparsers.add_parser("run")
    run.add_argument("--mode", choices=["preview", "execute"], default="preview")
    run.add_argument("--yes", action="store_true")
    run.add_argument("--business-date", default=date.today().isoformat())
    run.add_argument("--candidate-root", required=True)
    run.add_argument(
        "--output-root",
        default=str(PROJECT_ROOT / "logs" / "listing_daily" / "scheduler"),
    )
    run.add_argument(
        "--shared-runtime-root",
        default=os.getenv("YYDD_1688_RUNTIME_ROOT", "D:/script_1688"),
    )
    run.add_argument("--account-key", default=DEFAULT_ACCOUNT_KEY)
    run.add_argument("--operator", default=DAILY_TASK_NAME)
    run.add_argument("--max-items", type=int, default=50)
    run.add_argument("--task-timeout-seconds", type=int, default=7200)
    run.add_argument("--source-server", default=DEFAULT_SERVER)
    run.add_argument("--source-port", type=int, default=DEFAULT_PORT)
    run.add_argument("--source-database", default=DEFAULT_DATABASE)
    run.add_argument("--source-credential-ref", default=DEFAULT_CREDENTIAL_REF)
    run.add_argument("--source-driver", default="ODBC Driver 17 for SQL Server")
    return parser


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )


def _safe_name(value: str) -> str:
    normalized = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(value or "").strip())
    return normalized[:100] or "invalid"


def discover_candidate_files(candidate_root: Path) -> list[Path]:
    if not candidate_root.is_dir():
        raise FileNotFoundError(f"listing candidate root does not exist: {candidate_root}")
    return sorted(path for path in candidate_root.glob("*.json") if path.is_file())


def _source_value(row: dict[str, Any], columns: dict[str, str], name: str) -> Any:
    actual = columns.get(name)
    return row.get(actual) if actual else None


def prepare_scheduled_payload(
    payload: dict[str, Any],
    *,
    source_rows: list[dict[str, Any]],
    source_columns: dict[str, str],
    manager_run_id: str,
    business_date: str,
    candidate_path: Path,
) -> dict[str, Any]:
    prepared = copy.deepcopy(payload)
    latest = source_rows[0]
    source = prepared.setdefault("source", {})
    source["enabled"] = _source_value(latest, source_columns, "enabled")
    source["stock_disabled"] = _source_value(latest, source_columns, "stock_disabled")
    source["lifecycle_field"] = "other_5"
    source["lifecycle_status"] = str(
        _source_value(latest, source_columns, "other_5") or ""
    ).strip()
    source["item_type"] = str(
        _source_value(latest, source_columns, "item_type") or ""
    ).strip()
    prepared["schedule"] = {
        "task_name": DAILY_TASK_NAME,
        "manager_run_id": manager_run_id,
        "business_date": business_date,
        "candidate_file": candidate_path.name,
        "source_checked_at": datetime.now().astimezone().isoformat(),
    }
    prepared["preflight"] = validate_listing_payload(prepared, require_duplicate_clear=True)
    return prepared


def build_source_reader(args: argparse.Namespace) -> ListingSourceReader:
    config = build_source_config(
        shared_runtime_root=args.shared_runtime_root,
        server=args.source_server,
        port=args.source_port,
        database=args.source_database,
        credential_ref=args.source_credential_ref,
        driver=args.source_driver,
    )
    return ListingSourceReader(config)


def build_audit_repository(args: argparse.Namespace) -> ListingAuditRepository:
    return ListingAuditRepository(resolve_stop_sale_app_config(args.shared_runtime_root))


def build_task_command(
    args: argparse.Namespace,
    input_path: Path,
    output_path: Path,
    draft_id: str,
) -> list[str]:
    return [
        sys.executable,
        str(PROJECT_ROOT / "scripts" / "run_1688_listing_task.py"),
        "--payload",
        str(input_path),
        "--mode",
        "draft",
        "--output",
        str(output_path),
        "--operator",
        str(args.operator),
        "--draft-id",
        draft_id,
        "--shared-runtime-root",
        str(Path(args.shared_runtime_root).resolve()),
    ]


def _run_task(
    command: list[str],
    *,
    timeout_seconds: int,
    log_path: Path,
    command_runner: Callable[..., subprocess.CompletedProcess[Any]],
) -> subprocess.CompletedProcess[Any]:
    environment = dict(os.environ)
    environment["ENABLE_1688_LISTING_EXECUTION"] = "1"
    environment.pop("ENABLE_1688_LISTING_SUBMIT", None)
    try:
        completed = command_runner(
            command,
            cwd=str(PROJECT_ROOT),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_seconds,
            check=False,
            env=environment,
        )
    except subprocess.TimeoutExpired as exc:
        log_path.write_text(
            str(exc.stdout or "") + str(exc.stderr or ""),
            encoding="utf-8",
        )
        raise
    log_path.write_text(
        str(completed.stdout or "") + str(completed.stderr or ""),
        encoding="utf-8",
    )
    return completed


def _item_result(candidate_path: Path) -> dict[str, Any]:
    return {
        "candidate_file": candidate_path.name,
        "status": "pending",
        "task_id": "",
        "account_key": "",
        "shop_name": "",
        "company_sku": "",
        "workflow_state": "",
        "error_type": "",
        "error": "",
    }


def _load_candidate(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(payload, dict):
        raise ValueError("listing candidate must be a JSON object")
    return payload


def _record_identity(item: dict[str, Any], payload: dict[str, Any]) -> None:
    shop = payload.get("shop") or {}
    source = payload.get("source") or {}
    item.update(
        {
            "task_id": str(payload.get("task_id") or "").strip(),
            "account_key": str(shop.get("account_key") or "").strip(),
            "shop_name": str(shop.get("shop_name") or "").strip(),
            "company_sku": str(source.get("company_sku") or "").strip(),
        }
    )


def _summary_status(mode: str, counts: Counter[str]) -> tuple[str, int]:
    if not counts:
        return "no_candidates", 0
    if any(counts.get(status) for status in ("failed", "invalid", "identity_mismatch")):
        return "completed_with_failures", 1
    if mode == "preview":
        if counts.get("preview_ready"):
            return "preview_ready", 0
        return "no_eligible_candidates", 0
    if counts.get("draft_saved_pending_review"):
        return "success", 0
    return "no_eligible_candidates", 0


def run_daily(
    args: argparse.Namespace,
    *,
    source_reader_factory: Callable[[argparse.Namespace], Any] = build_source_reader,
    audit_repository_factory: Callable[[argparse.Namespace], Any] = build_audit_repository,
    command_runner: Callable[..., subprocess.CompletedProcess[Any]] = subprocess.run,
) -> tuple[int, dict[str, Any]]:
    if args.mode == "execute" and not args.yes:
        raise ValueError("execute mode requires --yes")
    if args.max_items <= 0:
        raise ValueError("--max-items must be positive")
    if args.task_timeout_seconds <= 0:
        raise ValueError("--task-timeout-seconds must be positive")
    account_key = str(args.account_key or "").strip()
    if account_key != DEFAULT_ACCOUNT_KEY:
        raise ValueError(f"daily listing account must be {DEFAULT_ACCOUNT_KEY}")

    manager_run_id = "listing_daily_" + datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    output_root = Path(args.output_root).resolve()
    manager_dir = output_root / manager_run_id
    task_dir = manager_dir / "tasks"
    log_dir = manager_dir / "logs"
    task_dir.mkdir(parents=True, exist_ok=False)
    log_dir.mkdir(parents=True, exist_ok=True)
    summary: dict[str, Any] = {
        "manager_run_id": manager_run_id,
        "task_name": DAILY_TASK_NAME,
        "business_date": args.business_date,
        "mode": args.mode,
        "account_key": account_key,
        "started_at": datetime.now().astimezone().isoformat(),
        "status": "running",
        "candidate_root": str(Path(args.candidate_root).resolve()),
        "manager_dir": str(manager_dir),
        "items": [],
        "counts": {},
        "auto_submit": False,
    }

    seen_task_ids: set[str] = set()
    seen_idempotency_keys: set[str] = set()
    infrastructure_error: Exception | None = None
    return_code = 1
    try:
        candidates = discover_candidate_files(Path(args.candidate_root).resolve())[: args.max_items]
        repository = audit_repository_factory(args)
        contract = repository.check_contract()
        summary["audit_contract"] = contract
        if not contract.get("ready"):
            raise RuntimeError("listing audit database contract is not ready")
        with source_reader_factory(args) as source_reader:
            for index, candidate_path in enumerate(candidates, start=1):
                item = _item_result(candidate_path)
                summary["items"].append(item)
                try:
                    payload = _load_candidate(candidate_path)
                    _record_identity(item, payload)
                    task_id = item["task_id"]
                    if not SAFE_TASK_ID.fullmatch(task_id):
                        raise ValueError("candidate requires a safe task_id")
                    if item["account_key"] != account_key:
                        raise ValueError("candidate account_key does not match daily account")
                    workflow_state = str((payload.get("workflow") or {}).get("state") or "").strip()
                    if workflow_state != STATE_DRAFT_PENDING:
                        raise ValueError("daily candidate must be in draft_pending state")
                    draft_id = require_unique_existing_draft_id(payload)
                    item["draft_id"] = draft_id
                    validation = validate_listing_payload(payload, require_duplicate_clear=True)
                    if validation.get("status") != "passed":
                        raise ValueError("candidate preflight is blocked: " + "; ".join(validation["errors"]))

                    task_record = build_listing_task_record(payload)
                    idempotency_key = task_record["idempotency_key"]
                    if task_id in seen_task_ids or idempotency_key in seen_idempotency_keys:
                        item["status"] = "duplicate_in_batch"
                        continue
                    seen_task_ids.add(task_id)
                    seen_idempotency_keys.add(idempotency_key)

                    if args.mode == "preview":
                        existing = repository.find_existing_task(
                            task_id=task_id,
                            idempotency_key=idempotency_key,
                        )
                        if existing:
                            item.update(
                                {
                                    "status": "duplicate_existing",
                                    "workflow_state": str(existing.get("workflow_state") or ""),
                                    "existing_task_id": str(existing.get("task_id") or ""),
                                }
                            )
                            continue

                    source_rows, source_columns = source_reader.read(item["company_sku"])
                    if not source_gate_passed(source_rows, source_columns):
                        item["status"] = "source_ineligible"
                        continue
                    prepared = prepare_scheduled_payload(
                        payload,
                        source_rows=source_rows,
                        source_columns=source_columns,
                        manager_run_id=manager_run_id,
                        business_date=args.business_date,
                        candidate_path=candidate_path,
                    )
                    if prepared["preflight"].get("status") != "passed":
                        raise ValueError(
                            "refreshed candidate preflight is blocked: "
                            + "; ".join(prepared["preflight"].get("errors") or [])
                        )
                    claim_owner = f"{manager_run_id}:{task_id}"[:150]
                    prepared.setdefault("schedule", {})["claim_owner"] = claim_owner
                    if args.mode == "execute":
                        claim = repository.claim_scheduled_task(
                            prepared,
                            claim_owner=claim_owner,
                            claim_seconds=args.task_timeout_seconds + 300,
                        )
                        item["claim"] = claim
                        if claim.get("status") != "claimed":
                            item.update(
                                {
                                    "status": "duplicate_existing",
                                    "workflow_state": str(claim.get("workflow_state") or ""),
                                    "existing_task_id": str(claim.get("task_id") or ""),
                                }
                            )
                            continue
                    input_path = task_dir / f"{index:03d}_{_safe_name(task_id)}.input.json"
                    output_path = task_dir / f"{index:03d}_{_safe_name(task_id)}.result.json"
                    _write_json(input_path, prepared)
                    item["input_path"] = str(input_path)
                    item["result_path"] = str(output_path)
                    if args.mode == "preview":
                        item["status"] = "preview_ready"
                        item["workflow_state"] = STATE_DRAFT_PENDING
                        continue

                    command = build_task_command(args, input_path, output_path, draft_id)
                    completed = _run_task(
                        command,
                        timeout_seconds=args.task_timeout_seconds,
                        log_path=log_dir / f"{index:03d}_{_safe_name(task_id)}.log",
                        command_runner=command_runner,
                    )
                    item["return_code"] = int(completed.returncode)
                    if int(completed.returncode) != 0:
                        raise RuntimeError(f"listing child exited with code {completed.returncode}")
                    if not output_path.is_file():
                        raise RuntimeError("listing child did not write its result payload")
                    result_payload = _load_candidate(output_path)
                    result_state = str((result_payload.get("workflow") or {}).get("state") or "").strip()
                    if result_state != STATE_DRAFT_PENDING_REVIEW:
                        raise RuntimeError(
                            f"listing child returned unexpected workflow state: {result_state or 'missing'}"
                        )
                    draft_id = str(
                        (((result_payload.get("workflow") or {}).get("draft") or {}).get("draft_id") or "")
                    ).strip()
                    if not draft_id:
                        raise RuntimeError("listing child returned no draft_id")
                    item.update(
                        {
                            "status": "draft_saved_pending_review",
                            "workflow_state": result_state,
                            "draft_id": draft_id,
                        }
                    )
                except subprocess.TimeoutExpired as exc:
                    item.update(
                        {
                            "status": "failed",
                            "error_type": type(exc).__name__,
                            "error": f"listing child timed out after {args.task_timeout_seconds} seconds",
                        }
                    )
                except Exception as exc:
                    if "candidate account_key" in str(exc):
                        status = "identity_mismatch"
                    elif isinstance(exc, ValueError) and not item.get("input_path"):
                        status = "invalid"
                    else:
                        status = "failed"
                    item.update(
                        {
                            "status": status,
                            "error_type": type(exc).__name__,
                            "error": str(exc)[:1000],
                        }
                    )
    except Exception as exc:
        infrastructure_error = exc
    finally:
        if infrastructure_error is not None:
            summary["status"] = "infrastructure_failed"
            summary["error_type"] = type(infrastructure_error).__name__
            summary["error"] = str(infrastructure_error)[:1000]
            return_code = 1
        else:
            counts = Counter(str(item.get("status") or "unknown") for item in summary["items"])
            status, return_code = _summary_status(args.mode, counts)
            summary["counts"] = dict(sorted(counts.items()))
            summary["status"] = status
        summary["finished_at"] = datetime.now().astimezone().isoformat()
        _write_json(manager_dir / "summary.json", summary)
        _write_json(output_root / "latest.summary.json", summary)

    return return_code, summary


def main() -> int:
    args = build_argument_parser().parse_args()
    return_code, summary = run_daily(args)
    print(json.dumps(summary, ensure_ascii=False, indent=2, default=str))
    return return_code


if __name__ == "__main__":
    raise SystemExit(main())
