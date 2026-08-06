"""Preview or explicitly requeue one fenced ERP Jushuitan Outbox operation."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
RPA_ROOT = PROJECT_ROOT / "rpa"
if str(RPA_ROOT) not in sys.path:
    sys.path.insert(0, str(RPA_ROOT))

from operation_saga import OperationSagaRepository
from stop_sale_audit import resolve_stop_sale_app_config


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Controlled requeue for one ERP Jushuitan Outbox row")
    parser.add_argument("--operation-key", required=True)
    parser.add_argument(
        "--expected-status",
        choices=("failed_retryable", "failed_terminal"),
        required=True,
    )
    parser.add_argument("--expected-error-code", required=True)
    parser.add_argument("--expected-run-id", required=True)
    parser.add_argument("--expected-attempt-count", required=True, type=int)
    parser.add_argument(
        "--expected-saga-state",
        choices=("failed_retryable", "failed_terminal"),
        required=True,
    )
    parser.add_argument("--reason", required=True)
    parser.add_argument("--shared-runtime-root", default=os.getenv("SCRIPT_1688_ROOT", "D:/script_1688"))
    parser.add_argument("--yes", action="store_true")
    return parser


def run(args: argparse.Namespace) -> dict[str, object]:
    operation_key = str(args.operation_key or "").strip().lower()
    if len(operation_key) != 64 or any(char not in "0123456789abcdef" for char in operation_key):
        raise ValueError("operation_key must be a 64-character lowercase SHA-256 value")
    repository = OperationSagaRepository(resolve_stop_sale_app_config(args.shared_runtime_root))
    before = repository.get_outbox_state(operation_key)
    expected = {
        "status": str(args.expected_status),
        "last_error_code": str(args.expected_error_code or "").strip(),
        "run_id": str(args.expected_run_id or "").strip(),
        "attempt_count": int(args.expected_attempt_count),
        "saga_state": str(args.expected_saga_state),
    }
    changed = [name for name, value in expected.items() if before.get(name) != value]
    if changed:
        raise RuntimeError("outbox_preview_precondition_changed:" + ",".join(changed))
    reason = str(args.reason or "").strip()
    if not reason:
        raise ValueError("--reason must not be empty")
    if not args.yes:
        return {"status": "preview", "before": before, "expected": expected}
    result = repository.requeue_outbox(
        operation_key,
        expected_status=args.expected_status,
        expected_error_code=expected["last_error_code"],
        expected_run_id=expected["run_id"],
        expected_attempt_count=expected["attempt_count"],
        expected_saga_state=expected["saga_state"],
        reason=reason,
    )
    return {"status": "applied", "before": before, "result": result}


def main() -> int:
    result = run(build_parser().parse_args())
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
