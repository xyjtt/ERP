from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_ROOT = PROJECT_ROOT / "scripts"
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

from recover_interrupted_stop_sale_daily_manager import (
    _discover_child_ids,
    _load_manager_lock,
    _validate_children,
    recover,
)


class InterruptedDailyManagerRecoveryTests(unittest.TestCase):
    def write_lock(self, path: Path, *, manager_run_id: str = "daily_1", pid: int = 123) -> None:
        path.write_text(
            json.dumps(
                {
                    "pid": pid,
                    "token": "token-1",
                    "metadata": {
                        "cycle": "1688_stop_sale_daily_manager",
                        "manager_run_id": manager_run_id,
                    },
                }
            ),
            encoding="utf-8",
        )

    def test_lock_requires_manager_cycle_and_dead_owner(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "manager.lock"
            self.write_lock(path)
            with patch(
                "recover_interrupted_stop_sale_daily_manager._is_process_running",
                return_value=False,
            ):
                payload = _load_manager_lock(path, "daily_1")
            self.assertEqual(payload["token"], "token-1")

            with patch(
                "recover_interrupted_stop_sale_daily_manager._is_process_running",
                return_value=True,
            ):
                with self.assertRaisesRegex(RuntimeError, "still active"):
                    _load_manager_lock(path, "daily_1")

    def test_child_validation_rejects_scope_or_nonterminal_state(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "scope mismatch"):
            _validate_children("daily_1", {"daily_1_s01_b001"}, [])
        with self.assertRaisesRegex(RuntimeError, "not terminal"):
            _validate_children(
                "daily_1",
                {"daily_1_s01_b001"},
                [
                    {
                        "run_id": "daily_1_s01_b001",
                        "status": "running",
                        "finished_at": None,
                        "summary_path": "",
                    }
                ],
            )

    def test_discovery_keeps_pre_audit_rejection_out_of_child_scope(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            manager_id = "daily_1"
            manager_dir = Path(temp_dir) / manager_id
            manager_dir.mkdir()
            pre_audit_run_id = f"{manager_id}_s02_b002"
            audited_retry_run_id = f"{manager_id}_s02_b002_a02"
            (manager_dir / f"{audited_retry_run_id}.summary.json").write_text(
                json.dumps({"run_id": audited_retry_run_id, "audit_status": "failed"}),
                encoding="utf-8",
            )
            (manager_dir / f"STORE-A_{audited_retry_run_id}.log").write_text(
                "normal audited pipeline output",
                encoding="utf-8",
            )
            pre_audit_log = manager_dir / f"STORE-A_{pre_audit_run_id}.log"
            pre_audit_log.write_text(
                json.dumps(
                    {
                        "run_id": pre_audit_run_id,
                        "reason": "higher_priority_browser_write",
                    }
                ),
                encoding="utf-8",
            )

            discovery = _discover_child_ids(
                manager_id,
                manager_dir,
                database_ids={audited_retry_run_id},
            )

            self.assertEqual(set(discovery.child_ids), {audited_retry_run_id})
            self.assertEqual(len(discovery.pre_audit_evidence), 1)
            evidence = discovery.pre_audit_evidence[0]
            self.assertEqual(evidence["run_id"], pre_audit_run_id)
            self.assertEqual(evidence["classification"], "orphan")
            self.assertEqual(evidence["evidence_type"], "pre_audit_rejection")
            self.assertEqual(evidence["reason"], "higher_priority_browser_write")
            self.assertFalse(evidence["summary_present"])
            self.assertFalse(evidence["database_row_present"])

    def test_discovery_rejects_unknown_child_log(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            manager_id = "daily_1"
            manager_dir = Path(temp_dir) / manager_id
            manager_dir.mkdir()
            child_id = f"{manager_id}_s02_b002"
            (manager_dir / f"STORE-A_{child_id}.log").write_text(
                json.dumps({"run_id": child_id, "reason": "unknown_resource_failure"}),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(RuntimeError, "Unknown child log evidence"):
                _discover_child_ids(manager_id, manager_dir, database_ids=set())

    def test_child_validation_rejects_finished_at_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            summary_path = Path(temp_dir) / "child.summary.json"
            summary_path.write_text(
                json.dumps(
                    {
                        "run_id": "daily_1_s01_b001",
                        "audit_status": "failed",
                        "finished_at": "2026-08-04T15:00:01",
                    }
                ),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(RuntimeError, "finished_at mismatch"):
                _validate_children(
                    "daily_1",
                    {"daily_1_s01_b001"},
                    [
                        {
                            "run_id": "daily_1_s01_b001",
                            "status": "failed",
                            "finished_at": "2026-08-04 06:00:00",
                            "summary_path": str(summary_path),
                        }
                    ],
                )

    def test_recovery_writes_summary_before_matching_token_release(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            manager_id = "daily_1"
            manager_dir = root / manager_id
            manager_dir.mkdir()
            child_id = f"{manager_id}_s01_b001"
            (manager_dir / f"{child_id}.summary.json").write_text(
                json.dumps({"run_id": child_id, "audit_status": "failed"}),
                encoding="utf-8",
            )
            pre_audit_run_id = f"{manager_id}_s02_b002"
            (manager_dir / f"STORE-A_{pre_audit_run_id}.log").write_text(
                json.dumps(
                    {
                        "run_id": pre_audit_run_id,
                        "reason": "higher_priority_browser_write",
                    }
                ),
                encoding="utf-8",
            )
            runtime = root / "runtime"
            lock = runtime / "artifacts" / "locks" / "ali1688_stop_sale_daily.lock"
            lock.parent.mkdir(parents=True)
            self.write_lock(lock, manager_run_id=manager_id)
            child_summary = root / "child.summary.json"
            child_summary.write_text(
                json.dumps(
                    {
                        "run_id": f"{manager_id}_s01_b001",
                        "audit_status": "failed",
                        "finished_at": "2026-08-04T15:00:00",
                    }
                ),
                encoding="utf-8",
            )
            args = SimpleNamespace(
                manager_run_id=manager_id,
                manager_dir=str(manager_dir),
                shared_runtime_root=str(runtime),
                shared_lock_path=str(lock),
                reason="executor resource exhaustion",
                no_notify=True,
                yes=True,
            )
            child_rows = [
                {
                    "run_id": f"{manager_id}_s01_b001",
                    "status": "failed",
                    "finished_at": "2026-08-04T07:00:00",
                    "summary_path": str(child_summary),
                }
            ]

            with (
                patch(
                    "recover_interrupted_stop_sale_daily_manager._is_process_running",
                    return_value=False,
                ),
                patch(
                    "recover_interrupted_stop_sale_daily_manager._query_child_runs",
                    return_value=child_rows,
                ),
                patch(
                    "recover_interrupted_stop_sale_daily_manager.send_manager_notification",
                    return_value=False,
                ),
            ):
                result = recover(args)

            self.assertFalse(lock.exists())
            self.assertTrue((manager_dir / "summary.json").is_file())
            self.assertTrue(result["lock_removed"])
            self.assertEqual(result["recovery_status"], "finalized")
            self.assertTrue((manager_dir.parent / "latest.summary.json").is_file())
            self.assertEqual(result["child_status_counts"], {"failed": 1})
            self.assertEqual(result["pre_audit_evidence_count"], 1)
            self.assertEqual(result["pre_audit_evidence"][0]["run_id"], pre_audit_run_id)
            self.assertTrue(result["lock_snapshot_sha256"])

    def test_token_change_after_summary_preserves_lock(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            manager_id = "daily_1"
            manager_dir = root / manager_id
            manager_dir.mkdir()
            runtime = root / "runtime"
            lock = runtime / "artifacts" / "locks" / "ali1688_stop_sale_daily.lock"
            lock.parent.mkdir(parents=True)
            self.write_lock(lock, manager_run_id=manager_id)
            args = SimpleNamespace(
                manager_run_id=manager_id,
                manager_dir=str(manager_dir),
                shared_runtime_root=str(runtime),
                shared_lock_path=str(lock),
                reason="interrupted",
                no_notify=True,
                yes=True,
            )

            def change_token(*_args, **_kwargs):
                payload = json.loads(lock.read_text(encoding="utf-8"))
                payload["token"] = "token-2"
                lock.write_text(json.dumps(payload), encoding="utf-8")
                return False

            with (
                patch(
                    "recover_interrupted_stop_sale_daily_manager._is_process_running",
                    return_value=False,
                ),
                patch(
                    "recover_interrupted_stop_sale_daily_manager._query_child_runs",
                    return_value=[],
                ),
                patch(
                    "recover_interrupted_stop_sale_daily_manager.send_manager_notification",
                    side_effect=change_token,
                ),
            ):
                with self.assertRaisesRegex(RuntimeError, "snapshot changed"):
                    recover(args)

            self.assertTrue(lock.exists())
            self.assertTrue((manager_dir / "summary.json").is_file())
            blocked = json.loads((manager_dir / "summary.json").read_text(encoding="utf-8"))
            self.assertEqual(blocked["recovery_status"], "blocked_lock_revalidation")

    def test_lock_metadata_change_after_summary_preserves_lock(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            manager_id = "daily_1"
            manager_dir = root / manager_id
            manager_dir.mkdir()
            runtime = root / "runtime"
            lock = runtime / "artifacts" / "locks" / "ali1688_stop_sale_daily.lock"
            lock.parent.mkdir(parents=True)
            self.write_lock(lock, manager_run_id=manager_id)
            args = SimpleNamespace(
                manager_run_id=manager_id,
                manager_dir=str(manager_dir),
                shared_runtime_root=str(runtime),
                shared_lock_path=str(lock),
                reason="interrupted",
                no_notify=True,
                yes=True,
            )

            def change_metadata(*_args, **_kwargs):
                payload = json.loads(lock.read_text(encoding="utf-8"))
                payload["created_at_epoch"] = 123.0
                lock.write_text(json.dumps(payload), encoding="utf-8")
                return False

            with (
                patch(
                    "recover_interrupted_stop_sale_daily_manager._is_process_running",
                    return_value=False,
                ),
                patch(
                    "recover_interrupted_stop_sale_daily_manager._query_child_runs",
                    return_value=[],
                ),
                patch(
                    "recover_interrupted_stop_sale_daily_manager.send_manager_notification",
                    side_effect=change_metadata,
                ),
            ):
                with self.assertRaisesRegex(RuntimeError, "snapshot changed"):
                    recover(args)

            self.assertTrue(lock.exists())

    def test_atomic_release_failure_preserves_current_lock(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            manager_id = "daily_1"
            manager_dir = root / manager_id
            manager_dir.mkdir()
            runtime = root / "runtime"
            lock = runtime / "artifacts" / "locks" / "ali1688_stop_sale_daily.lock"
            lock.parent.mkdir(parents=True)
            self.write_lock(lock, manager_run_id=manager_id)
            args = SimpleNamespace(
                manager_run_id=manager_id,
                manager_dir=str(manager_dir),
                shared_runtime_root=str(runtime),
                shared_lock_path=str(lock),
                reason="interrupted",
                no_notify=True,
                yes=True,
            )

            with (
                patch(
                    "recover_interrupted_stop_sale_daily_manager._is_process_running",
                    return_value=False,
                ),
                patch(
                    "recover_interrupted_stop_sale_daily_manager._query_child_runs",
                    return_value=[],
                ),
                patch(
                    "recover_interrupted_stop_sale_daily_manager.send_manager_notification",
                    return_value=False,
                ),
                patch(
                    "recover_interrupted_stop_sale_daily_manager._remove_windows_lock_if_unchanged",
                    side_effect=RuntimeError("exclusive lock unavailable"),
                ),
            ):
                with self.assertRaisesRegex(RuntimeError, "exclusive lock unavailable"):
                    recover(args)

            self.assertTrue(lock.exists())
            blocked = json.loads((manager_dir / "summary.json").read_text(encoding="utf-8"))
            self.assertEqual(blocked["recovery_status"], "blocked_lock_revalidation")

    def test_recovery_resumes_after_summary_write_with_identical_preconditions(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            manager_id = "daily_1"
            manager_dir = root / manager_id
            manager_dir.mkdir()
            runtime = root / "runtime"
            lock = runtime / "artifacts" / "locks" / "ali1688_stop_sale_daily.lock"
            lock.parent.mkdir(parents=True)
            self.write_lock(lock, manager_run_id=manager_id)
            args = SimpleNamespace(
                manager_run_id=manager_id,
                manager_dir=str(manager_dir),
                shared_runtime_root=str(runtime),
                shared_lock_path=str(lock),
                reason="interrupted",
                no_notify=True,
                yes=True,
            )
            original_lock = lock.read_text(encoding="utf-8")

            def change_token(*_args, **_kwargs):
                payload = json.loads(original_lock)
                payload["token"] = "temporary-token"
                lock.write_text(json.dumps(payload), encoding="utf-8")
                return False

            common_patches = (
                patch(
                    "recover_interrupted_stop_sale_daily_manager._is_process_running",
                    return_value=False,
                ),
                patch(
                    "recover_interrupted_stop_sale_daily_manager._query_child_runs",
                    return_value=[],
                ),
            )
            with (
                common_patches[0],
                common_patches[1],
                patch(
                    "recover_interrupted_stop_sale_daily_manager.send_manager_notification",
                    side_effect=change_token,
                ),
            ):
                with self.assertRaisesRegex(RuntimeError, "snapshot changed"):
                    recover(args)

            lock.write_text(original_lock, encoding="utf-8")
            with (
                patch(
                    "recover_interrupted_stop_sale_daily_manager._is_process_running",
                    return_value=False,
                ),
                patch(
                    "recover_interrupted_stop_sale_daily_manager._query_child_runs",
                    return_value=[],
                ),
                patch(
                    "recover_interrupted_stop_sale_daily_manager.send_manager_notification"
                ) as notification,
            ):
                result = recover(args)

            notification.assert_not_called()
            self.assertTrue(result["recovery_resumed"])
            self.assertTrue(result["lock_removed"])
            self.assertFalse(lock.exists())


if __name__ == "__main__":
    unittest.main()
