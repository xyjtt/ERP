"""Terminalize one exact historical Jushuitan ``task_not_found`` outbox row."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
from typing import Any, Callable


PROJECT_ROOT = Path(__file__).resolve().parents[1]
RPA_ROOT = PROJECT_ROOT / "rpa"
if str(RPA_ROOT) not in sys.path:
    sys.path.insert(0, str(RPA_ROOT))

from operation_saga import OperationSagaRepository
from stop_sale_audit import resolve_stop_sale_app_config


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="CAS-terminalize one exact historical Jushuitan task_not_found row"
    )
    parser.add_argument("--operation-key", required=True)
    parser.add_argument(
        "--expected-status",
        choices=("failed_retryable",),
        required=True,
    )
    parser.add_argument(
        "--expected-error-code",
        choices=("task_not_found",),
        required=True,
    )
    parser.add_argument("--expected-run-id", required=True)
    parser.add_argument("--expected-attempt-count", required=True, type=int)
    parser.add_argument(
        "--expected-saga-state",
        choices=("failed_retryable", "jushuitan_pending"),
        required=True,
    )
    parser.add_argument("--reason", required=True)
    parser.add_argument(
        "--shared-runtime-root",
        default=os.getenv("SCRIPT_1688_ROOT", "D:/script_1688"),
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="Apply the exact CAS; without this flag the command is preview-only.",
    )
    return parser


def _normalize_operation_key(value: str) -> str:
    operation_key = str(value or "").strip().lower()
    if len(operation_key) != 64 or any(
        char not in "0123456789abcdef" for char in operation_key
    ):
        raise ValueError("operation_key must be a 64-character lowercase SHA-256 value")
    return operation_key


def run(
    args: argparse.Namespace,
    *,
    repository_factory: Callable[[str], OperationSagaRepository] | None = None,
) -> dict[str, Any]:
    operation_key = _normalize_operation_key(args.operation_key)
    expected_error_code = str(args.expected_error_code or "").strip()
    if expected_error_code != "task_not_found":
        raise ValueError("--expected-error-code must be task_not_found")
    expected = {
        "operation_key": operation_key,
        "status": str(args.expected_status or "").strip(),
        "last_error_code": expected_error_code,
        "run_id": str(args.expected_run_id or "").strip(),
        "attempt_count": int(args.expected_attempt_count),
        "saga_state": str(args.expected_saga_state or "").strip(),
    }
    reason = str(args.reason or "").strip()
    if not expected["run_id"] or not reason:
        raise ValueError("--expected-run-id and --reason must not be empty")
    repository = (
        repository_factory(str(args.shared_runtime_root))
        if repository_factory
        else OperationSagaRepository(
            resolve_stop_sale_app_config(str(args.shared_runtime_root))
        )
    )
    before = repository.get_outbox_state(operation_key)
    changed = [
        name
        for name, value in expected.items()
        if before.get(name) != value
    ]
    if changed:
        raise RuntimeError(
            "task_not_found_terminalize_precondition_changed:" + ",".join(changed)
        )
    if not args.yes:
        return {
            "status": "preview",
            "operation_key": operation_key,
            "before": before,
            "expected": expected,
            "reason": reason,
        }
    result = repository.terminalize_outbox(
        operation_key,
        expected_status=expected["status"],
        expected_error_code=expected["last_error_code"],
        expected_run_id=expected["run_id"],
        expected_attempt_count=expected["attempt_count"],
        expected_saga_state=expected["saga_state"],
        reason=reason,
    )
    return {
        "status": "applied",
        "operation_key": operation_key,
        "before": before,
        "result": result,
        "reason": reason,
    }


def main() -> int:
    result = run(build_parser().parse_args())
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
