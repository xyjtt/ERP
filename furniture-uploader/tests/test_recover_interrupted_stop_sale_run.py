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
    _load_owned_interrupted_lock,
    _reconcile_interrupted_audit,
    build_argument_parser,
)


class _RecoveryCursor:
    def __init__(self) -> None:
        self.rowcount = -1
        self._result = None
        self.executed: list[tuple[str, object]] = []

    def execute(self, sql, params=None):
        self.executed.append((sql, params))
        if "SELECT item.id" in sql:
            self._result = [
                (
                    7,
                    "a" * 64,
                    "pending",
                    0,
                    None,
                    None,
                    "a" * 64,
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
    def __init__(self) -> None:
        self._cursor = _RecoveryCursor()
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
