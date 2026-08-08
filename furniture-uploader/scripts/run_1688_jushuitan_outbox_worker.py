"""Drain ERP 1688 Jushuitan outbox under the shared global Jushuitan lease."""

from __future__ import annotations

import argparse
import ctypes
import errno
import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
from typing import Any, Callable


PROJECT_ROOT = Path(__file__).resolve().parents[1]
ERP_ROOT = PROJECT_ROOT.parent
RPA_ROOT = PROJECT_ROOT / "rpa"
if str(RPA_ROOT) not in sys.path:
    sys.path.insert(0, str(RPA_ROOT))
if str(PROJECT_ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

from cross_project_runtime import JushuitanRuntimeGuard, RuntimeLeaseRepository, resolve_build_sha
from operation_saga import OperationSagaRepository, OutboxItem
from run_1688_stop_sale_pipeline import (
    DEFAULT_JST_LOGIN_URL,
    DEFAULT_JST_PRODUCT_URL,
    run_stage_command,
)
from stop_sale_audit import load_jsonl_records, resolve_stop_sale_app_config


TOPICS = {
    "cleanup": "jushuitan.cleanup_1688_link",
    "sync": "jushuitan.sync_1688_link",
}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Process durable 1688 Jushuitan outbox rows")
    parser.add_argument("--action", choices=tuple(TOPICS), required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--approved-operation-keys-file", required=True)
    parser.add_argument("--approved-operation-keys-sha256", required=True)
    parser.add_argument("--yes", action="store_true")
    parser.add_argument("--shared-runtime-root", default=os.getenv("SCRIPT_1688_ROOT", "D:/script_1688"))
    parser.add_argument("--jushuitan-root", default=str(ERP_ROOT / "jushuitan-sku-offline-batch"))
    parser.add_argument("--timeout-seconds", type=int, default=1200)
    parser.add_argument("--lease-wait-seconds", type=float, default=3600)
    parser.add_argument("--lease-poll-seconds", type=float, default=5)
    parser.add_argument("--claim-seconds", type=int, default=1800)
    parser.add_argument("--max-attempts", type=int, default=3)
    parser.add_argument("--retry-delay-seconds", type=int, default=300)
    parser.add_argument("--recover-expired-claim-owner", default="")
    parser.add_argument("--recover-expired-claim-attempt-count", type=int, default=None)
    parser.add_argument("--recover-expired-claim-reason", default="")
    parser.add_argument("--handoff-out", default="")
    parser.add_argument("--results-dir", default="")
    return parser


def _is_process_running(pid: int) -> bool:
    if int(pid or 0) <= 0:
        return False
    if os.name == "nt":
        handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, int(pid))  # type: ignore[attr-defined]
        if not handle:
            try:
                last_error = int(ctypes.windll.kernel32.GetLastError())  # type: ignore[attr-defined]
            except Exception:
                last_error = 0
            return last_error == 5
        ctypes.windll.kernel32.CloseHandle(handle)  # type: ignore[attr-defined]
        return True
    try:
        os.kill(int(pid), 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError as exc:
        return exc.errno != errno.ESRCH
    return True


def build_expired_claim_recovery_evidence(
    args: argparse.Namespace,
    *,
    hostname: str = "",
    process_checker: Callable[[int], bool] = _is_process_running,
) -> dict[str, Any] | None:
    expected_owner = str(getattr(args, "recover_expired_claim_owner", "") or "").strip()
    expected_attempt_count = getattr(args, "recover_expired_claim_attempt_count", None)
    reason = str(getattr(args, "recover_expired_claim_reason", "") or "").strip()
    requested = bool(expected_owner or expected_attempt_count is not None or reason)
    if not requested:
        return None
    if not expected_owner or expected_attempt_count is None or not reason:
        raise ValueError(
            "Expired claim recovery requires owner, attempt count, and reason together"
        )
    if int(expected_attempt_count) < 1:
        raise ValueError("Expired claim recovery attempt count must be positive")
    if len(reason) > 1000:
        raise ValueError("Expired claim recovery reason is too long")
    owner_hostname, separator, owner_pid_text = expected_owner.rpartition(":")
    if not separator or not owner_hostname or not owner_pid_text.isdigit():
        raise ValueError("Expired claim owner must use hostname:pid format")
    current_hostname = str(hostname or socket.gethostname()).strip()
    if owner_hostname.casefold() != current_hostname.casefold():
        raise RuntimeError("Expired claim owner hostname does not match this executor")
    owner_pid = int(owner_pid_text)
    if owner_pid <= 0:
        raise ValueError("Expired claim owner PID must be positive")
    if process_checker(owner_pid):
        raise RuntimeError("Expired claim owner process is still running")
    return {
        "action": "expired_claim_recovery",
        "approved_operation_keys_sha256": str(
            getattr(args, "approved_operation_keys_sha256", "") or ""
        ).strip(),
        "expected_claim_owner": expected_owner,
        "expected_attempt_count": int(expected_attempt_count),
        "previous_owner_pid_confirmed_inactive": True,
        "executor_hostname": current_hostname,
        "reason": reason,
    }


def load_approved_operation_keys(
    path: Path,
    expected_sha256: str,
    run_id: str,
) -> list[str]:
    if not path.is_file():
        raise FileNotFoundError(f"Approved operation-key file does not exist: {path}")
    raw = path.read_bytes()
    observed_sha256 = hashlib.sha256(raw).hexdigest()
    if len(expected_sha256) != 64 or any(char not in "0123456789abcdef" for char in expected_sha256):
        raise ValueError("--approved-operation-keys-sha256 must be a lowercase SHA-256 value")
    if observed_sha256 != expected_sha256:
        raise RuntimeError("Approved operation-key file SHA-256 does not match")

    keys: list[str]
    if path.suffix.lower() == ".jsonl":
        records = load_jsonl_records(path)
        keys = [
            str(record.get("operation_key") or record.get("task_id") or "").strip().lower()
            for record in records
        ]
    else:
        payload = json.loads(raw.decode("utf-8-sig"))
        if not isinstance(payload, dict) or int(payload.get("version") or 0) != 1:
            raise RuntimeError("Approved operation-key JSON must be a version 1 object")
        if str(payload.get("run_id") or "").strip() != run_id:
            raise RuntimeError("Approved operation-key run_id does not match")
        raw_keys = payload.get("operation_keys")
        if not isinstance(raw_keys, list):
            raise RuntimeError("Approved operation-key JSON is missing operation_keys")
        keys = [str(key or "").strip().lower() for key in raw_keys]
    if not keys:
        raise RuntimeError("Approved operation-key scope is empty")
    if len(set(keys)) != len(keys):
        raise RuntimeError("Approved operation-key scope contains duplicates")
    if any(len(key) != 64 or any(char not in "0123456789abcdef" for char in key) for key in keys):
        raise RuntimeError("Approved operation-key scope contains an invalid key")
    return sorted(keys)


def build_node_command(
    action: str,
    *,
    handoff_path: Path,
    results_dir: Path,
    run_id: str,
) -> list[str]:
    script = "cleanup:1688" if action == "cleanup" else "sync:1688"
    return [
        "npm.cmd" if sys.platform == "win32" else "npm",
        "run",
        script,
        "--",
        "--mode",
        "execute",
        "--file",
        str(handoff_path),
        "--results-dir",
        str(results_dir),
        "--run-id",
        run_id,
        "--yes",
        "--no-notify",
    ]


def build_node_environment(handoff_path: Path) -> dict[str, str]:
    environment = os.environ.copy()
    environment.setdefault("JST_LOGIN_URL", DEFAULT_JST_LOGIN_URL)
    environment.setdefault("JST_PRODUCT_URL", DEFAULT_JST_PRODUCT_URL)
    environment["EXCEL_PATH"] = str(handoff_path.resolve())
    return environment


def write_claimed_handoff(path: Path, items: list[OutboxItem]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for item in items:
            payload = dict(item.payload)
            payload["task_id"] = item.operation_key
            payload["operation_key"] = item.operation_key
            handle.write(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n")


def finish_claimed_items(
    repository: OperationSagaRepository,
    items: list[OutboxItem],
    records: list[dict[str, Any]],
    *,
    return_code: int,
    max_attempts: int,
    retry_delay_seconds: int,
) -> dict[str, int]:
    by_key = {
        str(record.get("operation_key") or record.get("task_id") or "").strip(): record
        for record in records
    }
    counts = {"succeeded": 0, "failed_retryable": 0, "failed_terminal": 0}
    for item in items:
        record = by_key.get(item.operation_key, {})
        result_status = str(record.get("status") or "").strip()
        # The Node CLI returns a batch-level non-zero code when any item fails.
        # Its per-item success rows have already passed live-page verification and
        # must not be rolled back to failed_retryable by an unrelated item.
        if result_status in {"success", "already_cleared", "already_synced"}:
            status = "succeeded"
            error_code = ""
            error_summary = ""
        else:
            terminal = item.attempt_count >= max(1, int(max_attempts))
            status = "failed_terminal" if terminal else "failed_retryable"
            error_code = str(
                record.get("category")
                or record.get("error_category")
                or ("jushuitan_process_failed" if return_code else "jushuitan_result_missing")
            )
            error_summary = str(
                record.get("message")
                or record.get("error_message")
                or f"Jushuitan CLI exited with code {return_code}"
            )
        repository.finish_outbox(
            item,
            status=status,
            error_code=error_code,
            error_summary=error_summary,
            retry_delay_seconds=retry_delay_seconds,
        )
        counts[status] += 1
    return counts


def run(
    args: argparse.Namespace,
    *,
    stage_runner: Callable[..., subprocess.CompletedProcess[Any]] = run_stage_command,
) -> dict[str, Any]:
    if not args.yes:
        raise ValueError("Outbox execute requires --yes")
    if args.timeout_seconds <= 0 or args.claim_seconds <= 0:
        raise ValueError("timeout values must be positive")
    approved_keys = load_approved_operation_keys(
        Path(args.approved_operation_keys_file).resolve(),
        str(args.approved_operation_keys_sha256 or "").strip(),
        str(args.run_id or "").strip(),
    )
    expired_claim_recovery_evidence = build_expired_claim_recovery_evidence(args)
    jushuitan_root = Path(args.jushuitan_root).resolve()
    if not (jushuitan_root / "package.json").exists():
        raise FileNotFoundError(f"Jushuitan project not found: {jushuitan_root}")

    app_config = resolve_stop_sale_app_config(args.shared_runtime_root)
    repository = OperationSagaRepository(app_config)
    contract = repository.check_contract()
    if not contract.get("ready"):
        raise RuntimeError(
            "ERP operation saga tables are missing: "
            + ", ".join(str(item) for item in contract.get("missing_tables") or [])
        )
    build_sha = resolve_build_sha(ERP_ROOT)
    runtime_repository = RuntimeLeaseRepository(app_config)
    worker_id = f"{socket.gethostname()}:{os.getpid()}"
    effective_run_id = str(args.run_id or "outbox").strip()
    with JushuitanRuntimeGuard(
        runtime_repository,
        run_id=effective_run_id,
        build_sha=build_sha,
        wait_timeout_seconds=args.lease_wait_seconds,
        poll_interval_seconds=args.lease_poll_seconds,
    ) as lease_guard:
        items = repository.claim_outbox_exact(
            claim_owner=worker_id,
            run_id=str(args.run_id).strip(),
            topic=TOPICS[args.action],
            operation_keys=approved_keys,
            claim_seconds=args.claim_seconds,
            expected_expired_claim_owner=str(
                getattr(args, "recover_expired_claim_owner", "") or ""
            ).strip(),
            expected_expired_claim_attempt_count=getattr(
                args, "recover_expired_claim_attempt_count", None
            ),
            expired_claim_recovery_evidence=expired_claim_recovery_evidence,
        )
        claimed_keys = {item.operation_key for item in items}
        if claimed_keys != set(approved_keys) or len(items) != len(approved_keys):
            raise RuntimeError("Claimed Outbox scope does not match approved operation_keys")
        worker_run_id = str(args.run_id or f"outbox_{items[0].outbox_id}").strip()
        work_dir = PROJECT_ROOT / "logs" / "sku_offline" / "outbox" / worker_run_id
        handoff_path = (
            Path(args.handoff_out).resolve()
            if str(args.handoff_out or "").strip()
            else work_dir / f"{worker_run_id}.jsonl"
        )
        results_dir = (
            Path(args.results_dir).resolve()
            if str(args.results_dir or "").strip()
            else work_dir / "results"
        )
        result_path = results_dir / f"{worker_run_id}.jsonl"
        write_claimed_handoff(handoff_path, items)
        completed = stage_runner(
            build_node_command(
                args.action,
                handoff_path=handoff_path,
                results_dir=results_dir,
                run_id=worker_run_id,
            ),
            cwd=jushuitan_root,
            timeout_seconds=args.timeout_seconds,
            stage=f"jushuitan_outbox_{args.action}",
            env=build_node_environment(handoff_path),
            heartbeat=lease_guard.assert_active,
            heartbeat_interval_seconds=10,
        )
        lease_guard.assert_active()
        records = load_jsonl_records(result_path)
        counts = finish_claimed_items(
            repository,
            items,
            records,
            return_code=completed.returncode,
            max_attempts=args.max_attempts,
            retry_delay_seconds=args.retry_delay_seconds,
        )
    return {
        "status": "success" if not counts["failed_retryable"] and not counts["failed_terminal"] else "partial",
        "claimed_count": len(items),
        "counts": counts,
        "run_id": worker_run_id,
        "handoff_path": str(handoff_path),
        "result_path": str(result_path),
        "expired_claim_recovered": expired_claim_recovery_evidence is not None,
    }


def main() -> int:
    args = build_parser().parse_args()
    result = run(args)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] in {"success", "empty"} else 2


if __name__ == "__main__":
    raise SystemExit(main())
