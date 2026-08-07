from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_ROOT = PROJECT_ROOT / "scripts"
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

from recover_interrupted_stop_sale_run import (  # noqa: E402
    _load_completed_recovery_summary,
    _load_recorded_terminal_failures,
    _load_owned_interrupted_lock,
    _reconcile_interrupted_audit,
    build_argument_parser,
    stop_sale_task_key,
)


class _RecoveryCursor:
    def __init__(self, operation_key: str = "a" * 64) -> None:
        self.rowcount = -1
        self._result = None
        self.executed: list[tuple[str, object]] = []
        self.operation_key = operation_key

    def execute(self, sql, params=None):
        self.executed.append((sql, params))
        if "SELECT item.id" in sql:
            self._result = [
                (
                    7,
                    self.operation_key,
                    "pending",
                    0,
                    None,
                    None,
                    self.operation_key,
                    "RUN-1",
                    "stop_sale",
                    "muke_lixiang",
                    "prepared",
                    "failed",
                    31,
                    "{}",
                    None,
                    None,
                    0,
                )
            ]
            self.rowcount = -1
        elif "COUNT(DISTINCT CASE" in sql:
            self._result = (0, 0, 1, 0, 0, 0, 0)
            self.rowcount = -1
        elif "UPDATE" in sql:
            self._result = None
            self.rowcount = 1
        return self

    def fetchall(self):
        return list(self._result or [])

    def fetchone(self):
        return self._result


class _RecoveryConnection:
    def __init__(self, operation_key: str = "a" * 64) -> None:
        self._cursor = _RecoveryCursor(operation_key)
        self.commits = 0

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def cursor(self):
        return self._cursor

    def commit(self):
        self.commits += 1


class _RecoveryRepository:
    config = object()

    @staticmethod
    def _table(name: str) -> str:
        return f"[app].[{name}]"


class RecoverInterruptedStopSaleRunTests(unittest.TestCase):
    def test_completed_recovery_backfills_finished_at_from_recovered_at(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            lock_path = Path(temp_dir) / "shared.lock"
            summary_path = Path(temp_dir) / "recovery.summary.json"
            summary_path.write_text(
                json.dumps(
                    {
                        "run_id": "RUN-1",
                        "error_message": "executor stopped",
                        "shared_lock_path": str(lock_path),
                        "recovered_at": "2026-08-07T14:46:40",
                        "lock_removed": True,
                    }
                ),
                encoding="utf-8",
            )

            summary, resolved_path = _load_completed_recovery_summary(
                {
                    "status": "failed",
                    "finished_at": "2026-08-07T06:46:40",
                    "error_message": "executor stopped",
                    "summary_path": str(summary_path),
                    "notification_sent": True,
                },
                run_id="RUN-1",
                reason="executor stopped",
                lock_path=lock_path.resolve(),
            )

        self.assertEqual(summary["finished_at"], "2026-08-07T14:46:40")
        self.assertEqual(resolved_path, summary_path.resolve())

    def test_reconcile_terminalizes_pending_item_and_prepared_saga(self) -> None:
        connection = _RecoveryConnection()
        with patch(
            "recover_interrupted_stop_sale_run.connect_app_database",
            return_value=connection,
        ):
            result = _reconcile_interrupted_audit(
                _RecoveryRepository(),
                run_id="RUN-1",
                reason="executor stopped",
            )

        self.assertEqual(result["item_terminalized_count"], 1)
        self.assertEqual(result["saga_terminalized_count"], 1)
        self.assertEqual(result["outbox_count"], 0)
        self.assertEqual(connection.commits, 1)
        updates = [sql for sql, _ in connection._cursor.executed if "UPDATE" in sql]
        self.assertEqual(len(updates), 3)
        self.assertTrue(any("offline_status = 'failed'" in sql for sql in updates))
        self.assertTrue(any("state = 'failed_terminal'" in sql for sql in updates))
        saga_update = next(sql for sql in updates if "state = 'failed_terminal'" in sql)
        self.assertIn("run_id = ?", saga_update)
        self.assertIn("account_fencing_token = ?", saga_update)

    def test_reconcile_preserves_recorded_business_terminal_failure(self) -> None:
        record = {
            "operation": "offline",
            "status": "failed",
            "store_name": "阿里巴巴-常州工莱家具",
            "product_id": "1022984903964",
            "online_sku": "AD006825N486V01",
            "platform_store_item_code": "6200210923101",
            "attempts": 1,
            "error_category": "sole_sku_requires_product_offline",
            "error_message": "1688 requires one online SKU",
            "screenshot_path": "D:/evidence.png",
            "html_snapshot_path": "D:/evidence.html",
        }
        operation_key = stop_sale_task_key(
            record["store_name"],
            record["product_id"],
            record["online_sku"],
            record["platform_store_item_code"],
        )
        connection = _RecoveryConnection(operation_key)
        with tempfile.TemporaryDirectory() as temp_dir:
            report_path = Path(temp_dir) / "RUN-1.jsonl"
            report_path.write_text(json.dumps(record) + "\n", encoding="utf-8")
            with patch(
                "recover_interrupted_stop_sale_run.connect_app_database",
                return_value=connection,
            ):
                result = _reconcile_interrupted_audit(
                    _RecoveryRepository(),
                    run_id="RUN-1",
                    reason="executor stopped",
                    report_path=report_path,
                )

        self.assertEqual(result["recorded_terminal_failure_count"], 1)
        self.assertEqual(result["interrupted_failure_count"], 0)
        updates = [entry for entry in connection._cursor.executed if "UPDATE" in entry[0]]
        item_update = next(entry for entry in updates if "offline_status = 'failed'" in entry[0])
        saga_update = next(entry for entry in updates if "state = 'failed_terminal'" in entry[0])
        self.assertEqual(item_update[1][2], "sole_sku_requires_product_offline")
        self.assertEqual(item_update[1][3], "1688 requires one online SKU")
        self.assertEqual(saga_update[1][1], "sole_sku_requires_product_offline")
        self.assertEqual(saga_update[1][2], "1688 requires one online SKU")

    def test_recorded_success_blocks_automatic_interrupted_recovery(self) -> None:
        record = {
            "operation": "offline",
            "status": "success",
            "store_name": "store-a",
            "product_id": "product-a",
            "online_sku": "sku-a",
            "platform_store_item_code": "item-a",
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            report_path = Path(temp_dir) / "RUN-1.jsonl"
            report_path.write_text(json.dumps(record) + "\n", encoding="utf-8")
            with self.assertRaisesRegex(RuntimeError, "recorded successful 1688 action"):
                _load_recorded_terminal_failures(report_path)

    def test_accepts_matching_stop_sale_lock_with_dead_owner(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            lock_path = Path(temp_dir) / "shared.lock"
            lock_path.write_text(
                json.dumps(
                    {
                        "pid": 12345,
                        "token": "token-1",
                        "metadata": {
                            "cycle": "1688_stop_sale_pipeline",
                            "run_id": "RUN-1",
                        },
                    }
                ),
                encoding="utf-8",
            )

            with patch("recover_interrupted_stop_sale_run._is_process_running", return_value=False):
                payload = _load_owned_interrupted_lock(lock_path, "RUN-1")

        self.assertEqual(payload["token"], "token-1")

    def test_rejects_lock_for_different_run(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            lock_path = Path(temp_dir) / "shared.lock"
            lock_path.write_text(
                json.dumps(
                    {
                        "pid": 12345,
                        "metadata": {
                            "cycle": "1688_stop_sale_pipeline",
                            "run_id": "RUN-2",
                        },
                    }
                ),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(RuntimeError, "run_id"):
                _load_owned_interrupted_lock(lock_path, "RUN-1")

    def test_rejects_live_lock_owner(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            lock_path = Path(temp_dir) / "shared.lock"
            lock_path.write_text(
                json.dumps(
                    {
                        "pid": 12345,
                        "metadata": {
                            "cycle": "1688_stop_sale_pipeline",
                            "run_id": "RUN-1",
                        },
                    }
                ),
                encoding="utf-8",
            )

            with patch("recover_interrupted_stop_sale_run._is_process_running", return_value=True):
                with self.assertRaisesRegex(RuntimeError, "still active"):
                    _load_owned_interrupted_lock(lock_path, "RUN-1")

    def test_parser_requires_explicit_confirmation_at_runtime(self) -> None:
        args = build_argument_parser().parse_args(
            [
                "--run-id",
                "RUN-1",
                "--shared-runtime-root",
                "E:/runtime",
                "--shared-lock-path",
                "E:/runtime/lock",
                "--reason",
                "executor stopped",
            ]
        )

        self.assertFalse(args.yes)


if __name__ == "__main__":
    unittest.main()
