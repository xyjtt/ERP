from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_ROOT = PROJECT_ROOT / "scripts"
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

from manage_1688_sku_replace_batches import (  # noqa: E402
    build_pipeline_command,
    classify_attempt,
    classify_persisted_batch,
    classify_pipeline_summary,
    discover_batches,
    refresh_attempt_classifications,
)


def test_discover_batches_uses_stable_name_order(tmp_path: Path) -> None:
    (tmp_path / "batch_010.csv").write_text("a\n", encoding="utf-8")
    (tmp_path / "batch_002.csv").write_text("a\n", encoding="utf-8")
    (tmp_path / "notes.csv").write_text("a\n", encoding="utf-8")

    assert [path.name for path in discover_batches(tmp_path)] == [
        "batch_002.csv",
        "batch_010.csv",
    ]


def test_classify_pipeline_summary_accepts_business_terminal(tmp_path: Path) -> None:
    report_path = tmp_path / "replace.jsonl"
    report_path.write_text(
        json.dumps({"status": "failed", "error_category": "business_validation"}) + "\n",
        encoding="utf-8",
    )

    assert classify_pipeline_summary(
        {
            "status": "failed",
            "replace_report_path": str(report_path),
            "replace_counts": {"failed": 1},
            "replace_return_code": 0,
            "notification_sent": True,
        }
    ) == "business_terminal"


def test_system_prompt_is_a_non_success_business_terminal(tmp_path: Path) -> None:
    report_path = tmp_path / "replace.jsonl"
    report_path.write_text(
        json.dumps(
            {
                "status": "failed",
                "error_category": "system_prompt",
                "system_prompt": "毛重必须为数字",
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )

    assert classify_pipeline_summary(
        {
            "status": "failed",
            "replace_report_path": str(report_path),
            "replace_counts": {"failed": 1},
            "replace_return_code": 0,
            "notification_sent": True,
        }
    ) == "business_terminal"


def test_classify_pipeline_summary_retries_process_failure() -> None:
    assert classify_pipeline_summary(
        {
            "status": "failed",
            "replace_report_path": "",
            "replace_counts": {},
            "replace_return_code": 120,
            "error_message": "OSError: invalid handle",
            "notification_sent": True,
        }
    ) == "retryable_failure"


def test_account_lock_timeout_pauses_queue_without_consuming_retry() -> None:
    assert classify_pipeline_summary(
        {
            "status": "failed",
            "replace_counts": {},
            "replace_return_code": 1,
            "error_message": (
                "GlobalLockTimeoutError: Timed out waiting for global lock "
                "ali1688_account_muke_lixiang.lock"
            ),
            "notification_sent": True,
        }
    ) == "queue_blocked"


def test_jushuitan_technical_failure_overrides_1688_business_terminal(tmp_path: Path) -> None:
    replace_report = tmp_path / "replace.jsonl"
    replace_report.write_text(
        json.dumps({"status": "failed", "error_category": "business_validation"}) + "\n",
        encoding="utf-8",
    )
    jushuitan_report = tmp_path / "jushuitan.jsonl"
    jushuitan_report.write_text(
        json.dumps({"status": "failed", "category": "action_unavailable"}) + "\n",
        encoding="utf-8",
    )

    summary = {
        "status": "partial",
        "replace_report_path": str(replace_report),
        "replace_counts": {"success": 1, "failed": 1},
        "replace_return_code": 0,
        "jushuitan_report_path": str(jushuitan_report),
        "jushuitan_counts": {"failed": 1},
        "jushuitan_return_code": 2,
        "notification_sent": True,
    }

    assert classify_pipeline_summary(summary) == "retryable_failure"


def test_persisted_batch_is_reclassified_from_latest_summary(tmp_path: Path) -> None:
    jushuitan_report = tmp_path / "jushuitan.jsonl"
    jushuitan_report.write_text(
        json.dumps({"status": "failed", "category": "action_unavailable"}) + "\n",
        encoding="utf-8",
    )
    summary_path = tmp_path / "summary.json"
    summary_path.write_text(
        json.dumps(
            {
                "status": "partial",
                "replace_counts": {"success": 1},
                "replace_return_code": 0,
                "jushuitan_report_path": str(jushuitan_report),
                "jushuitan_counts": {"failed": 1},
                "jushuitan_return_code": 2,
                "notification_sent": True,
            }
        ),
        encoding="utf-8",
    )

    assert classify_persisted_batch(
        {
            "classification": "business_terminal",
            "attempts": [{"summary_path": str(summary_path)}],
        }
    ) == "retryable_failure"


def test_active_audit_guard_does_not_consume_retry_budget(tmp_path: Path) -> None:
    log_path = tmp_path / "attempt.log"
    log_path.write_text(
        "RuntimeError: A recent SKU replacement audit run is still active for STORE-A\n",
        encoding="utf-8",
    )
    attempt = {
        "classification": "retryable_failure",
        "log_path": str(log_path),
        "summary_path": str(tmp_path / "missing.summary.json"),
    }

    assert classify_attempt(attempt) == "queue_blocked"
    previous = {"classification": "exhausted", "attempts": [attempt]}
    assert classify_persisted_batch(previous) == "queue_blocked"
    assert refresh_attempt_classifications(previous)[0]["classification"] == "queue_blocked"


def test_database_startup_failure_does_not_consume_retry_budget(tmp_path: Path) -> None:
    log_path = tmp_path / "attempt.log"
    log_path.write_text(
        "pyodbc.OperationalError: ('08001', 'SQL Server connection failed')\n",
        encoding="utf-8",
    )

    assert classify_attempt({"log_path": str(log_path)}) == "queue_blocked"


def test_build_pipeline_command_includes_account_locks_and_timeouts(tmp_path: Path) -> None:
    args = argparse.Namespace(
        pipeline_script=str(tmp_path / "pipeline.py"),
        jushuitan_root=str(tmp_path / "jst"),
        shared_runtime_root=str(tmp_path / "runtime"),
        account_key="gonglai",
        lock_wait_seconds=3600,
        jushuitan_lock_wait_seconds=3600,
        crawler_task_wait_seconds=4800,
        timeout_1688_seconds=3600,
        timeout_jushuitan_seconds=1800,
        crawler_worker_task_name="YYDD-1688-Crawler-Worker",
        source_database="JSReportReplica",
        source_table="app.op_stop_sale",
    )

    command = build_pipeline_command(args, tmp_path / "batch_001.csv", "queue_b001_a01")

    assert command[0] == sys.executable
    assert command[command.index("--account-key") + 1] == "gonglai"
    assert command[command.index("--lock-wait-seconds") + 1] == "3600"
    assert command[command.index("--jushuitan-lock-wait-seconds") + 1] == "3600"
    assert command[command.index("--source-table") + 1] == "app.op_stop_sale"
    assert command[command.index("--crawler-worker-task-name") + 1] == "YYDD-1688-Crawler-Worker"
