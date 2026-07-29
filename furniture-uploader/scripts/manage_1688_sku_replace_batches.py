from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PIPELINE = PROJECT_ROOT / "scripts" / "run_1688_sku_replace_pipeline.py"
BUSINESS_TERMINAL_CATEGORIES = {
    "business_validation",
    "campaign_restriction",
    "delivery_service_backfill_failed",
    "product_unavailable",
    "replacement_sku_conflict",
    "replacement_verification_failed",
    "sku_not_found",
    "sole_sku_requires_product_offline",
    "submit_blocked_before_request",
    "task_not_found",
}
JUSHUITAN_BUSINESS_TERMINAL_CATEGORIES = {
    "task_not_found",
}
TERMINAL_CLASSIFICATIONS = {
    "success",
    "business_terminal",
    "notified_terminal",
}
QUEUE_BLOCKED_MARKERS = (
    "A recent SKU replacement audit run is still active for",
    "pyodbc.OperationalError: ('08001'",
)


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run a resumable queue of 1688 SKU replacement CSV batches."
    )
    parser.add_argument("--input-dir", required=True)
    parser.add_argument("--account-key", required=True)
    parser.add_argument("--queue-id", required=True)
    parser.add_argument("--jushuitan-root", required=True)
    parser.add_argument("--shared-runtime-root", required=True)
    parser.add_argument("--max-attempts", type=int, default=2)
    parser.add_argument("--lock-wait-seconds", type=int, default=3600)
    parser.add_argument("--jushuitan-lock-wait-seconds", type=int, default=3600)
    parser.add_argument("--crawler-task-wait-seconds", type=int, default=4800)
    parser.add_argument(
        "--1688-timeout-seconds",
        dest="timeout_1688_seconds",
        type=int,
        default=3600,
    )
    parser.add_argument(
        "--jushuitan-timeout-seconds",
        dest="timeout_jushuitan_seconds",
        type=int,
        default=1800,
    )
    parser.add_argument("--source-database", default="JSReportReplica")
    parser.add_argument("--source-table", default="app.op_stop_sale")
    parser.add_argument("--pipeline-script", default=str(DEFAULT_PIPELINE))
    parser.add_argument("--state-path", default="")
    parser.add_argument("--log-dir", default="")
    parser.add_argument("--fail-fast", action="store_true")
    return parser


def discover_batches(input_dir: Path) -> list[Path]:
    return sorted(path.resolve() for path in input_dir.glob("batch_*.csv") if path.is_file())


def load_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    records: list[dict[str, Any]] = []
    try:
        lines = path.read_text(encoding="utf-8-sig").splitlines()
    except OSError:
        return []
    for line in lines:
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            records.append(value)
    return records


def classify_pipeline_summary(summary: dict[str, Any]) -> str:
    if not summary:
        return "retryable_failure"
    notification_sent = summary.get("notification_sent")
    if str(summary.get("status", "")).strip() == "success" and notification_sent is not False:
        return "success"

    process_error = str(summary.get("error_message", "")).strip()
    if (
        "GlobalLockTimeoutError" in process_error
        or "Timed out waiting for global lock" in process_error
    ):
        return "queue_blocked"
    replace_return_code = summary.get("replace_return_code")
    jushuitan_return_code = summary.get("jushuitan_return_code")
    if process_error or replace_return_code not in {0, None}:
        return "retryable_failure"

    jushuitan_counts = summary.get("jushuitan_counts", {})
    if not isinstance(jushuitan_counts, dict):
        jushuitan_counts = {}
    jushuitan_failed = int(jushuitan_counts.get("failed", 0) or 0)
    if jushuitan_failed > 0:
        jushuitan_report_path = Path(str(summary.get("jushuitan_report_path", "")).strip())
        failed_jushuitan_records = [
            record
            for record in load_jsonl(jushuitan_report_path)
            if str(record.get("status", "")) == "failed"
        ]
        failed_jushuitan_categories = {
            str(record.get("category", record.get("error_category", ""))).strip()
            for record in failed_jushuitan_records
        }
        failed_jushuitan_categories.discard("")
        if (
            failed_jushuitan_records
            and failed_jushuitan_categories
            and failed_jushuitan_categories <= JUSHUITAN_BUSINESS_TERMINAL_CATEGORIES
            and notification_sent is True
        ):
            return "notified_terminal"
        return "retryable_failure"
    if jushuitan_return_code not in {0, None}:
        return "retryable_failure"

    report_path = Path(str(summary.get("replace_report_path", "")).strip())
    failed_records = [
        record for record in load_jsonl(report_path) if str(record.get("status", "")) == "failed"
    ]
    failed_categories = {
        str(record.get("error_category", record.get("page_error_category", ""))).strip()
        for record in failed_records
    }
    failed_categories.discard("")
    if (
        failed_records
        and failed_categories
        and failed_categories <= BUSINESS_TERMINAL_CATEGORIES
        and notification_sent is True
    ):
        return "business_terminal"

    replace_counts = summary.get("replace_counts", {})
    if not isinstance(replace_counts, dict):
        replace_counts = {}
    has_replace_result = sum(int(value or 0) for value in replace_counts.values()) > 0
    if has_replace_result and notification_sent is True:
        return "notified_terminal"
    return "retryable_failure"


def classify_attempt(attempt: dict[str, Any]) -> str:
    summary_text = str(attempt.get("summary_path", "")).strip()
    if summary_text:
        summary = load_json(Path(summary_text))
        if summary:
            return classify_pipeline_summary(summary)

    log_text = str(attempt.get("log_path", "")).strip()
    if log_text:
        try:
            output = Path(log_text).read_text(encoding="utf-8-sig", errors="replace")
        except OSError:
            output = ""
        if any(marker in output for marker in QUEUE_BLOCKED_MARKERS):
            return "queue_blocked"
    return str(attempt.get("classification", "")).strip() or "retryable_failure"


def refresh_attempt_classifications(previous: dict[str, Any]) -> list[dict[str, Any]]:
    persisted = previous.get("attempts", [])
    attempts = list(persisted) if isinstance(persisted, list) else []
    for attempt in attempts:
        if isinstance(attempt, dict):
            attempt["classification"] = classify_attempt(attempt)
    return attempts


def classify_persisted_batch(previous: dict[str, Any]) -> str:
    attempts = refresh_attempt_classifications(previous)
    if not attempts:
        return str(previous.get("classification", ""))
    latest_attempt = attempts[-1]
    if not isinstance(latest_attempt, dict):
        return str(previous.get("classification", ""))
    return classify_attempt(latest_attempt)


def atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_suffix(path.suffix + ".tmp")
    temp_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temp_path.replace(path)


def build_pipeline_command(
    args: argparse.Namespace,
    batch_path: Path,
    run_id: str,
) -> list[str]:
    return [
        sys.executable,
        str(Path(args.pipeline_script).resolve()),
        "--file",
        str(batch_path),
        "--mode",
        "execute",
        "--yes",
        "--skip-login",
        "--run-id",
        run_id,
        "--jushuitan-root",
        str(Path(args.jushuitan_root).resolve()),
        "--shared-runtime-root",
        str(Path(args.shared_runtime_root).resolve()),
        "--account-key",
        args.account_key,
        "--lock-wait-seconds",
        str(args.lock_wait_seconds),
        "--jushuitan-lock-wait-seconds",
        str(args.jushuitan_lock_wait_seconds),
        "--crawler-task-wait-seconds",
        str(args.crawler_task_wait_seconds),
        "--1688-timeout-seconds",
        str(args.timeout_1688_seconds),
        "--jushuitan-timeout-seconds",
        str(args.timeout_jushuitan_seconds),
        "--source-database",
        args.source_database,
        "--source-table",
        args.source_table,
    ]


def run(args: argparse.Namespace) -> int:
    input_dir = Path(args.input_dir).resolve()
    batches = discover_batches(input_dir)
    if not batches:
        raise FileNotFoundError(f"No batch_*.csv files found in {input_dir}")
    if args.max_attempts < 1:
        raise ValueError("--max-attempts must be at least 1")

    log_dir = Path(args.log_dir).resolve() if args.log_dir else (
        PROJECT_ROOT / "logs" / "sku_replace" / "queues" / args.queue_id
    )
    state_path = Path(args.state_path).resolve() if args.state_path else log_dir / "queue.summary.json"
    state = load_json(state_path)
    previous_batches = state.get("batches", {})
    if not isinstance(previous_batches, dict):
        previous_batches = {}
    state = {
        "queue_id": args.queue_id,
        "account_key": args.account_key,
        "input_dir": str(input_dir),
        "started_at": state.get("started_at") or datetime.now().isoformat(timespec="seconds"),
        "updated_at": datetime.now().isoformat(timespec="seconds"),
        "status": "running",
        "batch_count": len(batches),
        "batches": previous_batches,
    }
    atomic_write_json(state_path, state)

    exhausted = False
    for batch_index, batch_path in enumerate(batches, start=1):
        batch_key = batch_path.name
        previous = previous_batches.get(batch_key, {})
        if not isinstance(previous, dict):
            previous = {}
        previous_classification = classify_persisted_batch(previous)
        if previous:
            previous["classification"] = previous_classification
            state["batches"][batch_key] = previous
            state["updated_at"] = datetime.now().isoformat(timespec="seconds")
            atomic_write_json(state_path, state)
        if previous_classification in TERMINAL_CLASSIFICATIONS:
            continue

        batch_done = False
        attempts = refresh_attempt_classifications(previous)
        retry_attempt_count = sum(
            1
            for attempt in attempts
            if isinstance(attempt, dict)
            and str(attempt.get("classification", "")) != "queue_blocked"
        )
        while retry_attempt_count < args.max_attempts:
            attempt_number = len(attempts) + 1
            run_id = f"{args.queue_id}_b{batch_index:03d}_a{attempt_number:02d}"
            output_path = log_dir / f"{run_id}.log"
            summary_path = PROJECT_ROOT / "logs" / "sku_replace" / "pipelines" / f"{run_id}.summary.json"
            command = build_pipeline_command(args, batch_path, run_id)
            output_path.parent.mkdir(parents=True, exist_ok=True)
            attempt_state = {
                "attempt": attempt_number,
                "run_id": run_id,
                "started_at": datetime.now().isoformat(timespec="seconds"),
                "log_path": str(output_path),
                "summary_path": str(summary_path),
            }
            attempts.append(attempt_state)
            state["batches"][batch_key] = {
                "input_file": str(batch_path),
                "classification": "running",
                "attempts": attempts,
            }
            state["updated_at"] = datetime.now().isoformat(timespec="seconds")
            atomic_write_json(state_path, state)

            with output_path.open("w", encoding="utf-8", newline="") as output:
                completed = subprocess.run(
                    command,
                    cwd=PROJECT_ROOT,
                    stdout=output,
                    stderr=subprocess.STDOUT,
                    check=False,
                )
            summary = load_json(summary_path)
            classification = (
                classify_pipeline_summary(summary)
                if summary
                else classify_attempt(attempt_state)
            )
            attempt_state.update(
                {
                    "finished_at": datetime.now().isoformat(timespec="seconds"),
                    "return_code": completed.returncode,
                    "classification": classification,
                }
            )
            state["batches"][batch_key]["classification"] = classification
            state["batches"][batch_key]["attempts"] = attempts
            state["updated_at"] = datetime.now().isoformat(timespec="seconds")
            atomic_write_json(state_path, state)
            if classification in TERMINAL_CLASSIFICATIONS:
                batch_done = True
                break
            if classification == "queue_blocked":
                state["status"] = "blocked"
                state["blocked_at"] = datetime.now().isoformat(timespec="seconds")
                state["updated_at"] = state["blocked_at"]
                state["blocked_batch"] = batch_key
                state["blocked_run_id"] = run_id
                state["batches"][batch_key]["classification"] = "queue_blocked"
                atomic_write_json(state_path, state)
                print(json.dumps(state, ensure_ascii=False, indent=2))
                return 3
            retry_attempt_count += 1

        if not batch_done:
            exhausted = True
            state["batches"][batch_key]["classification"] = "exhausted"
            state["updated_at"] = datetime.now().isoformat(timespec="seconds")
            atomic_write_json(state_path, state)
            if args.fail_fast:
                break

    classifications = [
        str(value.get("classification", ""))
        for value in state["batches"].values()
        if isinstance(value, dict)
    ]
    state["finished_at"] = datetime.now().isoformat(timespec="seconds")
    state["updated_at"] = state["finished_at"]
    state["status"] = "completed_with_exhausted" if exhausted or "exhausted" in classifications else "completed"
    atomic_write_json(state_path, state)
    print(json.dumps(state, ensure_ascii=False, indent=2))
    return 2 if state["status"] == "completed_with_exhausted" else 0


def main() -> int:
    return run(build_argument_parser().parse_args())


if __name__ == "__main__":
    raise SystemExit(main())
