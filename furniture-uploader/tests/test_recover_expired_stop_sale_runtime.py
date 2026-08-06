from __future__ import annotations

import copy
from datetime import datetime, timedelta, timezone
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

from recover_expired_stop_sale_runtime import (  # noqa: E402
    RECOVERY_PROCEDURES,
    RECOVERY_MODE_REQUEST_ONLY,
    RecoveryGateError,
    RuntimeRecoveryRepository,
    SNAPSHOT_VERSION,
    STOP_SALE_TASK_TYPE,
    _eligibility,
    apply_snapshot,
    _snapshot_fingerprint,
    main,
    validate_snapshot,
)


NOW = datetime.now(timezone.utc)


def snapshot_template() -> dict:
    owner = "a" * 32
    return {
        "snapshot_version": SNAPSHOT_VERSION,
        "artifact_type": "stop_sale_runtime_recovery_preview",
        "server_now": NOW,
        "hostname": "PC-20210622ARIU",
        "account_key": "muke_lixiang",
        "run_id": "stop_sale_system_prompt_canary_20260806",
        "request_key": "stop_sale:stop_sale_system_prompt_canary_20260806:muke_lixiang",
        "task_type": STOP_SALE_TASK_TYPE,
        "recovery_mode": "request_and_resources",
        "request": {
            "request_key": "stop_sale:stop_sale_system_prompt_canary_20260806:muke_lixiang",
            "account_key": "muke_lixiang",
            "task_type": STOP_SALE_TASK_TYPE,
            "run_id": "stop_sale_system_prompt_canary_20260806",
            "owner_token": owner,
            "hostname": "PC-20210622ARIU",
            "pid": 13128,
            "status": "running",
            "expires_at": NOW - timedelta(minutes=2),
            "completed_at": None,
        },
        "resources": [
            {
                "resource_type": "account",
                "resource_key": "muke_lixiang",
                "account_key": "muke_lixiang",
                "owner_token": owner,
                "fencing_token": 3060,
                "run_id": "stop_sale_system_prompt_canary_20260806",
                "task_type": STOP_SALE_TASK_TYPE,
                "hostname": "PC-20210622ARIU",
                "pid": 13128,
                "expires_at": NOW - timedelta(minutes=2),
            },
            {
                "resource_type": "browser_slot",
                "resource_key": "PC-20210622ARIU:2",
                "account_key": "muke_lixiang",
                "owner_token": owner,
                "fencing_token": 284,
                "run_id": "stop_sale_system_prompt_canary_20260806",
                "task_type": STOP_SALE_TASK_TYPE,
                "hostname": "PC-20210622ARIU",
                "pid": 13128,
                "expires_at": NOW - timedelta(minutes=2),
            },
        ],
        "foreign_account_requests": [],
        "foreign_account_leases": [],
        "active_crawler_tasks": [],
        "active_crawler_attempts": [],
        "active_stop_sale_runs": [],
        "procedures": list(RECOVERY_PROCEDURES),
        "pid_live": {"13128": False},
        "eligibility": {"eligible": True, "reasons": []},
    }


class FakeRepository:
    def __init__(self, snapshot: dict) -> None:
        self.snapshot = snapshot
        self.calls: list[tuple[str, object]] = []
        self.new_owner_token = ""

    def query_snapshot(self, **_kwargs):
        return copy.deepcopy(self.snapshot)

    def recover_expired_runtime(self, *, resources, **kwargs):
        self.new_owner_token = str(kwargs["new_owner_token"])
        self.calls.append(("atomic", [row["resource_type"] for row in resources]))
        self.calls.append(("request", kwargs["request_key"]))
        self.calls.append(("complete", kwargs["request_key"]))
        return [
            {"action": "recover_lease", "resource_type": row["resource_type"], "status": "recovered"}
            for row in resources
        ] + [
            {"action": "recover_request", "status": "recovered"},
            {"action": "complete_recovered_request", "status": "completed"},
        ]

    def query_post_state(self, **_kwargs):
        return {
            "request": [{
                "request_key": self.snapshot["request_key"],
                "account_key": self.snapshot["account_key"],
                "task_type": STOP_SALE_TASK_TYPE,
                "status": "cancelled",
                "owner_token": self.new_owner_token,
                "run_id": self.snapshot["run_id"],
                "completed_at": NOW,
            }],
            "leases": [],
        }


class FakeCursor:
    def __init__(self, statuses: list[object]) -> None:
        self.statuses = iter(statuses)
        self.executions: list[tuple[str, tuple[object, ...]]] = []

    def execute(self, sql: str, params: tuple[object, ...]) -> None:
        self.executions.append((sql, params))

    def fetchone(self) -> tuple[object, ...]:
        result = next(self.statuses)
        return result if isinstance(result, tuple) else (result,)


class FakeConnection:
    def __init__(self, statuses: list[object]) -> None:
        self._cursor = FakeCursor(statuses)
        self.commit_count = 0
        self.rollback_count = 0

    def __enter__(self):
        return self

    def __exit__(self, *_args) -> None:
        return None

    def cursor(self) -> FakeCursor:
        return self._cursor

    def commit(self) -> None:
        self.commit_count += 1

    def rollback(self) -> None:
        self.rollback_count += 1


class RecoverExpiredStopSaleRuntimeTests(unittest.TestCase):
    def test_expired_dead_owner_snapshot_is_eligible(self) -> None:
        snapshot = snapshot_template()
        eligible, reasons = _eligibility(
            snapshot,
            expected_hostname="PC-20210622ARIU",
            process_checker=lambda _pid: False,
        )
        self.assertTrue(eligible)
        self.assertEqual(reasons, [])

    def test_live_owner_blocks_recovery(self) -> None:
        snapshot = snapshot_template()
        eligible, reasons = _eligibility(
            snapshot,
            expected_hostname="PC-20210622ARIU",
            process_checker=lambda _pid: True,
        )
        self.assertFalse(eligible)
        self.assertIn("request_owner_pid_is_alive", reasons)

    def test_dead_owner_with_all_resources_missing_is_request_only_eligible(self) -> None:
        snapshot = snapshot_template()
        snapshot["resources"] = []
        snapshot["recovery_mode"] = RECOVERY_MODE_REQUEST_ONLY
        eligible, reasons = _eligibility(
            snapshot,
            expected_hostname="PC-20210622ARIU",
            process_checker=lambda _pid: False,
        )
        self.assertTrue(eligible)
        self.assertEqual(reasons, [])

    def test_partial_resource_loss_is_not_eligible(self) -> None:
        snapshot = snapshot_template()
        snapshot["resources"] = snapshot["resources"][:1]
        snapshot["recovery_mode"] = "invalid_resource_set"
        eligible, reasons = _eligibility(
            snapshot,
            expected_hostname="PC-20210622ARIU",
            process_checker=lambda _pid: False,
        )
        self.assertFalse(eligible)
        self.assertIn("target_resource_set_incomplete", reasons)

    def test_request_only_with_foreign_same_account_lease_is_not_eligible(self) -> None:
        snapshot = snapshot_template()
        snapshot["resources"] = []
        snapshot["recovery_mode"] = RECOVERY_MODE_REQUEST_ONLY
        snapshot["foreign_runtime_leases"] = [{
            "resource_type": "browser_slot",
            "resource_key": "PC-20210622ARIU:3",
            "account_key": "muke_lixiang",
            "owner_token": "c" * 32,
            "run_id": "other-run",
        }]
        eligible, reasons = _eligibility(
            snapshot,
            expected_hostname="PC-20210622ARIU",
            process_checker=lambda _pid: False,
        )
        self.assertFalse(eligible)
        self.assertIn("foreign_runtime_lease_present", reasons)

    def test_non_positive_owner_pid_blocks_recovery(self) -> None:
        snapshot = snapshot_template()
        snapshot["request"]["pid"] = 0
        for resource in snapshot["resources"]:
            resource["pid"] = 0
        eligible, reasons = _eligibility(
            snapshot,
            expected_hostname="PC-20210622ARIU",
            process_checker=lambda _pid: False,
        )
        self.assertFalse(eligible)
        self.assertIn("request_owner_pid_invalid", reasons)

    def test_foreign_account_lease_blocks_recovery(self) -> None:
        snapshot = snapshot_template()
        snapshot["foreign_account_leases"] = [{"resource_key": "muke_lixiang"}]
        eligible, reasons = _eligibility(
            snapshot,
            expected_hostname="PC-20210622ARIU",
            process_checker=lambda _pid: False,
        )
        self.assertFalse(eligible)
        self.assertIn("foreign_account_lease_present", reasons)

    def test_foreign_same_account_browser_lease_blocks_recovery(self) -> None:
        snapshot = snapshot_template()
        snapshot["foreign_runtime_leases"] = [{
            "resource_type": "browser_slot",
            "resource_key": "PC-20210622ARIU:3",
            "account_key": "muke_lixiang",
            "owner_token": "c" * 32,
            "run_id": "other-run",
        }]
        eligible, reasons = _eligibility(
            snapshot,
            expected_hostname="PC-20210622ARIU",
            process_checker=lambda _pid: False,
        )
        self.assertFalse(eligible)
        self.assertIn("foreign_runtime_lease_present", reasons)

    def test_active_same_account_crawler_attempt_blocks_recovery(self) -> None:
        snapshot = snapshot_template()
        snapshot["active_crawler_attempts"] = [{
            "attempt_id": "attempt_active",
            "task_id": "task_active",
            "status": "running",
        }]
        eligible, reasons = _eligibility(
            snapshot,
            expected_hostname="PC-20210622ARIU",
            process_checker=lambda _pid: False,
        )
        self.assertFalse(eligible)
        self.assertIn("active_crawler_attempt_present", reasons)

    def test_apply_uses_lease_then_request_cas_and_completion(self) -> None:
        snapshot = snapshot_template()
        from recover_expired_stop_sale_runtime import _snapshot_fingerprint

        snapshot["fingerprint"] = _snapshot_fingerprint(snapshot)
        snapshot["eligibility"] = {"eligible": True, "reasons": []}
        repository = FakeRepository(snapshot)
        result = apply_snapshot(
            snapshot,
            repository,  # type: ignore[arg-type]
            hostname="PC-20210622ARIU",
            process_checker=lambda _pid: False,
            yes=True,
        )
        self.assertEqual(
            repository.calls,
            [
                ("atomic", ["browser_slot", "account"]),
                ("request", snapshot["request_key"]),
                ("complete", snapshot["request_key"]),
            ],
        )
        self.assertEqual(result["post_state"]["request"][0]["status"], "cancelled")

    def test_apply_request_only_uses_request_cas_and_completion(self) -> None:
        snapshot = snapshot_template()
        snapshot["resources"] = []
        snapshot["recovery_mode"] = RECOVERY_MODE_REQUEST_ONLY
        snapshot["fingerprint"] = _snapshot_fingerprint(snapshot)
        repository = FakeRepository(snapshot)
        result = apply_snapshot(
            snapshot,
            repository,  # type: ignore[arg-type]
            hostname="PC-20210622ARIU",
            process_checker=lambda _pid: False,
            yes=True,
        )
        self.assertEqual(
            repository.calls,
            [
                ("atomic", []),
                ("request", snapshot["request_key"]),
                ("complete", snapshot["request_key"]),
            ],
        )
        self.assertEqual(result["recovery_mode"], RECOVERY_MODE_REQUEST_ONLY)

    def test_wrong_task_type_is_not_eligible(self) -> None:
        snapshot = snapshot_template()
        snapshot["request"]["task_type"] = "listing"
        eligible, reasons = _eligibility(
            snapshot,
            expected_hostname="PC-20210622ARIU",
            process_checker=lambda _pid: False,
        )
        self.assertFalse(eligible)
        self.assertIn("request_task_type_mismatch", reasons)

    def test_snapshot_scope_and_task_type_are_fingerprinted(self) -> None:
        snapshot = snapshot_template()
        fingerprint = _snapshot_fingerprint(snapshot)
        changed = copy.deepcopy(snapshot)
        changed["run_id"] = "other-run"
        self.assertNotEqual(fingerprint, _snapshot_fingerprint(changed))
        changed = copy.deepcopy(snapshot)
        changed["request"]["task_type"] = "listing"
        self.assertNotEqual(fingerprint, _snapshot_fingerprint(changed))
        changed = copy.deepcopy(snapshot)
        changed["resources"][0]["task_type"] = "listing"
        self.assertNotEqual(fingerprint, _snapshot_fingerprint(changed))
        changed = copy.deepcopy(snapshot)
        changed["procedures"] = []
        self.assertNotEqual(fingerprint, _snapshot_fingerprint(changed))
        changed = copy.deepcopy(snapshot)
        changed["eligibility"] = {"eligible": False, "reasons": ["request_not_expired"]}
        self.assertNotEqual(fingerprint, _snapshot_fingerprint(changed))
        changed = copy.deepcopy(snapshot)
        changed["pid_live"] = {"13128": True}
        self.assertNotEqual(fingerprint, _snapshot_fingerprint(changed))
        changed = copy.deepcopy(snapshot)
        changed["recovery_mode"] = RECOVERY_MODE_REQUEST_ONLY
        self.assertNotEqual(fingerprint, _snapshot_fingerprint(changed))

    def test_validate_snapshot_rejects_cross_account_resource(self) -> None:
        snapshot = snapshot_template()
        snapshot["resources"][0]["account_key"] = "other-account"
        snapshot["fingerprint"] = _snapshot_fingerprint(snapshot)
        with self.assertRaisesRegex(RecoveryGateError, "account_key mismatch"):
            validate_snapshot(snapshot)

    def test_validate_snapshot_rejects_resource_key_outside_scope(self) -> None:
        snapshot = snapshot_template()
        snapshot["resources"][1]["resource_key"] = "PC-20210622ARIU:99"
        snapshot["fingerprint"] = _snapshot_fingerprint(snapshot)
        with self.assertRaisesRegex(RecoveryGateError, "resource_key scope"):
            validate_snapshot(snapshot)

    def test_eligibility_rejects_non_recoverable_resource_type(self) -> None:
        snapshot = snapshot_template()
        snapshot["resources"][1]["resource_type"] = "jushuitan"
        eligible, reasons = _eligibility(
            snapshot,
            expected_hostname="PC-20210622ARIU",
            process_checker=lambda _pid: False,
        )
        self.assertFalse(eligible)
        self.assertIn("unknown_resource_type", reasons)

    def test_atomic_recovery_commits_once_after_all_cas_steps(self) -> None:
        snapshot = snapshot_template()
        connection = FakeConnection([
            "1",
            (0, 0, 0, 0),
            "recovered",
            "recovered",
            "0",
            "recovered",
            "completed",
            (1, 0),
        ])
        repository = RuntimeRecoveryRepository(
            object(), connect=lambda _config: connection
        )

        actions = repository.recover_expired_runtime(
            resources=snapshot["resources"],
            account_key=snapshot["account_key"],
            run_id=snapshot["run_id"],
            request_key=snapshot["request_key"],
            expected_owner_token=snapshot["request"]["owner_token"],
            expected_owner_pid=snapshot["request"]["pid"],
            new_owner_token="b" * 32,
            hostname=snapshot["hostname"],
            pid=24680,
            actor="test",
        )

        self.assertEqual(connection.commit_count, 1)
        self.assertEqual(connection.rollback_count, 0)
        self.assertEqual(len(connection._cursor.executions), 8)
        self.assertEqual(actions[-1]["action"], "verify_transactional_post_state")
        self.assertEqual(actions[-1]["status"], "passed")

    def test_request_only_recovery_checks_related_leases_in_same_transaction(self) -> None:
        snapshot = snapshot_template()
        connection = FakeConnection([
            1,
            (0, 0, 0, 0),
            0,
            "recovered",
            "completed",
            (1, 0),
        ])
        repository = RuntimeRecoveryRepository(
            object(), connect=lambda _config: connection
        )

        actions = repository.recover_expired_runtime(
            resources=[],
            account_key=snapshot["account_key"],
            run_id=snapshot["run_id"],
            request_key=snapshot["request_key"],
            expected_owner_token=snapshot["request"]["owner_token"],
            expected_owner_pid=snapshot["request"]["pid"],
            new_owner_token="b" * 32,
            hostname=snapshot["hostname"],
            pid=24680,
            actor="test",
        )

        self.assertEqual(connection.commit_count, 1)
        self.assertEqual(connection.rollback_count, 0)
        self.assertEqual(len(connection._cursor.executions), 6)
        self.assertEqual(actions[0]["action"], "verify_request_identity")
        self.assertEqual(actions[1]["action"], "verify_active_work_absent")
        self.assertEqual(actions[2]["action"], "verify_related_leases_absent")
        self.assertEqual(actions[2]["recovery_mode"], RECOVERY_MODE_REQUEST_ONLY)
        executed_sql = [sql for sql, _params in connection._cursor.executions]
        self.assertFalse(any("lease_recover_expired" in sql for sql in executed_sql))
        self.assertTrue(any("request_recover_expired" in sql for sql in executed_sql))
        self.assertTrue(any("request_complete" in sql for sql in executed_sql))

    def test_request_only_recovery_rolls_back_when_terminal_request_is_not_exact(self) -> None:
        snapshot = snapshot_template()
        connection = FakeConnection([
            1,
            (0, 0, 0, 0),
            0,
            "recovered",
            "completed",
            (0, 0),
        ])
        repository = RuntimeRecoveryRepository(
            object(), connect=lambda _config: connection
        )

        with self.assertRaisesRegex(RecoveryGateError, "transactional post-state gate"):
            repository.recover_expired_runtime(
                resources=[],
                account_key=snapshot["account_key"],
                run_id=snapshot["run_id"],
                request_key=snapshot["request_key"],
                expected_owner_token=snapshot["request"]["owner_token"],
                expected_owner_pid=snapshot["request"]["pid"],
                new_owner_token="b" * 32,
                hostname=snapshot["hostname"],
                pid=24680,
                actor="test",
            )

        self.assertEqual(connection.commit_count, 0)
        self.assertEqual(connection.rollback_count, 1)
        self.assertEqual(len(connection._cursor.executions), 6)

    def test_request_only_recovery_rolls_back_when_post_state_lease_exists(self) -> None:
        snapshot = snapshot_template()
        connection = FakeConnection([
            1,
            (0, 0, 0, 0),
            0,
            "recovered",
            "completed",
            (1, 1),
        ])
        repository = RuntimeRecoveryRepository(
            object(), connect=lambda _config: connection
        )

        with self.assertRaisesRegex(RecoveryGateError, "related_lease_count=1"):
            repository.recover_expired_runtime(
                resources=[],
                account_key=snapshot["account_key"],
                run_id=snapshot["run_id"],
                request_key=snapshot["request_key"],
                expected_owner_token=snapshot["request"]["owner_token"],
                expected_owner_pid=snapshot["request"]["pid"],
                new_owner_token="b" * 32,
                hostname=snapshot["hostname"],
                pid=24680,
                actor="test",
            )

        self.assertEqual(connection.commit_count, 0)
        self.assertEqual(connection.rollback_count, 1)
        self.assertEqual(len(connection._cursor.executions), 6)

    def test_request_only_recovery_rolls_back_when_related_lease_appears(self) -> None:
        snapshot = snapshot_template()
        connection = FakeConnection([1, (0, 0, 0, 0), 1])
        repository = RuntimeRecoveryRepository(
            object(), connect=lambda _config: connection
        )

        with self.assertRaisesRegex(RecoveryGateError, "related lease recovery gate"):
            repository.recover_expired_runtime(
                resources=[],
                account_key=snapshot["account_key"],
                run_id=snapshot["run_id"],
                request_key=snapshot["request_key"],
                expected_owner_token=snapshot["request"]["owner_token"],
                expected_owner_pid=snapshot["request"]["pid"],
                new_owner_token="b" * 32,
                hostname=snapshot["hostname"],
                pid=24680,
                actor="test",
            )

        self.assertEqual(connection.commit_count, 0)
        self.assertEqual(connection.rollback_count, 1)
        self.assertEqual(len(connection._cursor.executions), 3)

    def test_request_only_recovery_rolls_back_when_active_work_appears(self) -> None:
        snapshot = snapshot_template()
        connection = FakeConnection([1, (1, 0, 0, 0)])
        repository = RuntimeRecoveryRepository(
            object(), connect=lambda _config: connection
        )

        with self.assertRaisesRegex(RecoveryGateError, "active work recovery gate"):
            repository.recover_expired_runtime(
                resources=[],
                account_key=snapshot["account_key"],
                run_id=snapshot["run_id"],
                request_key=snapshot["request_key"],
                expected_owner_token=snapshot["request"]["owner_token"],
                expected_owner_pid=snapshot["request"]["pid"],
                new_owner_token="b" * 32,
                hostname=snapshot["hostname"],
                pid=24680,
                actor="test",
            )

        self.assertEqual(connection.commit_count, 0)
        self.assertEqual(connection.rollback_count, 1)
        self.assertEqual(len(connection._cursor.executions), 2)

    def test_request_only_recovery_rolls_back_when_request_identity_drifted(self) -> None:
        snapshot = snapshot_template()
        connection = FakeConnection([0])
        repository = RuntimeRecoveryRepository(
            object(), connect=lambda _config: connection
        )

        with self.assertRaisesRegex(RecoveryGateError, "request identity recovery gate"):
            repository.recover_expired_runtime(
                resources=[],
                account_key=snapshot["account_key"],
                run_id=snapshot["run_id"],
                request_key=snapshot["request_key"],
                expected_owner_token=snapshot["request"]["owner_token"],
                expected_owner_pid=snapshot["request"]["pid"],
                new_owner_token="b" * 32,
                hostname=snapshot["hostname"],
                pid=24680,
                actor="test",
            )

        self.assertEqual(connection.commit_count, 0)
        self.assertEqual(connection.rollback_count, 1)
        self.assertEqual(len(connection._cursor.executions), 1)

    def test_atomic_recovery_rolls_back_when_request_recovery_fails(self) -> None:
        snapshot = snapshot_template()
        connection = FakeConnection([
            "1",
            (0, 0, 0, 0),
            "recovered",
            "recovered",
            "0",
            "owner_mismatch",
        ])
        repository = RuntimeRecoveryRepository(
            object(), connect=lambda _config: connection
        )

        with self.assertRaisesRegex(RecoveryGateError, "request recovery failed"):
            repository.recover_expired_runtime(
                resources=snapshot["resources"],
                account_key=snapshot["account_key"],
                run_id=snapshot["run_id"],
                request_key=snapshot["request_key"],
                expected_owner_token=snapshot["request"]["owner_token"],
                expected_owner_pid=snapshot["request"]["pid"],
                new_owner_token="b" * 32,
                hostname=snapshot["hostname"],
                pid=24680,
                actor="test",
            )

        self.assertEqual(connection.commit_count, 0)
        self.assertEqual(connection.rollback_count, 1)
        self.assertEqual(len(connection._cursor.executions), 6)

    def test_apply_rejects_incomplete_post_state(self) -> None:
        snapshot = snapshot_template()
        snapshot["fingerprint"] = _snapshot_fingerprint(snapshot)
        repository = FakeRepository(snapshot)
        repository.query_post_state = lambda **_kwargs: {
            "request": [{
                "request_key": snapshot["request_key"],
                "account_key": snapshot["account_key"],
                "task_type": STOP_SALE_TASK_TYPE,
                "status": "cancelled",
                "owner_token": repository.new_owner_token,
                "run_id": snapshot["run_id"],
                "completed_at": None,
            }],
            "leases": [],
        }
        with self.assertRaisesRegex(RecoveryGateError, "completed_at"):
            apply_snapshot(
                snapshot,
                repository,  # type: ignore[arg-type]
                hostname="PC-20210622ARIU",
                process_checker=lambda _pid: False,
                yes=True,
            )

    def test_apply_requires_exact_snapshot_fingerprint(self) -> None:
        snapshot = snapshot_template()
        snapshot["fingerprint"] = "0" * 64
        with self.assertRaises(RecoveryGateError):
            validate_snapshot(snapshot)

    def test_apply_rejects_a_preview_that_was_not_eligible(self) -> None:
        snapshot = snapshot_template()
        snapshot["eligibility"] = {"eligible": False, "reasons": ["request_not_expired"]}
        snapshot["fingerprint"] = _snapshot_fingerprint(snapshot)
        with self.assertRaisesRegex(RecoveryGateError, "was not eligible"):
            validate_snapshot(snapshot)

    def test_configuration_failure_writes_structured_blocked_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            output_path = Path(temp_dir) / "blocked.json"
            with patch(
                "recover_expired_stop_sale_runtime.resolve_stop_sale_app_config",
                side_effect=RuntimeError("credential provider unavailable"),
            ):
                exit_code = main([
                    "--mode", "preview",
                    "--account-key", "muke_lixiang",
                    "--run-id", "stop_sale_system_prompt_canary_20260806",
                    "--request-key", "stop_sale:stop_sale_system_prompt_canary_20260806:muke_lixiang",
                    "--shared-runtime-root", temp_dir,
                    "--output", str(output_path),
                ])

            result = json.loads(output_path.read_text(encoding="utf-8"))

        self.assertEqual(exit_code, 2)
        self.assertEqual(result["artifact_type"], "stop_sale_runtime_recovery_error")
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["error_type"], "RuntimeError")
        self.assertEqual(result["error"], "credential provider unavailable")

    def test_cli_hostname_must_match_local_machine(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            output_path = Path(temp_dir) / "blocked.json"
            exit_code = main([
                "--mode", "preview",
                "--account-key", "muke_lixiang",
                "--run-id", "stop_sale_system_prompt_canary_20260806",
                "--request-key", "stop_sale:stop_sale_system_prompt_canary_20260806:muke_lixiang",
                "--shared-runtime-root", temp_dir,
                "--hostname", "definitely-not-the-local-host",
                "--output", str(output_path),
            ])

            result = json.loads(output_path.read_text(encoding="utf-8"))

        self.assertEqual(exit_code, 2)
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["error_type"], "RecoveryGateError")
        self.assertIn("hostname", result["error"])


if __name__ == "__main__":
    unittest.main()
