from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
import sys
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
RPA_ROOT = PROJECT_ROOT / "rpa"
if str(RPA_ROOT) not in sys.path:
    sys.path.insert(0, str(RPA_ROOT))

from operation_saga import OperationSagaRepository, SagaOperation
from sku_operation_saga import (
    build_saga_operation,
    persist_ali1688_results,
    record_operation_key,
    task_operation_key,
)


class FakeSagaRepository:
    def __init__(self) -> None:
        self.results: list[dict] = []

    def record_ali1688_result(self, **kwargs) -> None:
        self.results.append(kwargs)


class OperationSagaTests(unittest.TestCase):
    def task(self, *, replacement_sku: str = "") -> SimpleNamespace:
        return SimpleNamespace(
            store_name="阿里巴巴-常州工莱家具",
            platform="Alibaba",
            product_id="1001",
            online_sku="OLD-1",
            replacement_sku=replacement_sku,
            platform_store_item_code="BAR-1",
            handling="全渠道替换" if replacement_sku else "全渠道下架",
            source_file="input.csv",
            source_row_number=3,
        )

    def test_task_and_report_resolve_same_operation_key(self) -> None:
        task = self.task(replacement_sku="NEW-1")
        key = task_operation_key("sku_replace", task)
        report_key = record_operation_key(
            "sku_replace",
            {
                "store_name": task.store_name,
                "product_id": task.product_id,
                "online_sku": task.online_sku,
                "replacement_sku": task.replacement_sku,
            },
        )
        self.assertEqual(key, report_key)

    def test_success_creates_outbox_payload_and_failure_does_not(self) -> None:
        task = self.task()
        operation = build_saga_operation("stop_sale", "run-1", "gonglai", task)
        repository = FakeSagaRepository()
        persist_ali1688_results(
            repository,  # type: ignore[arg-type]
            task_type="stop_sale",
            operations=[operation],
            records=[
                {
                    "store_name": task.store_name,
                    "product_id": task.product_id,
                    "online_sku": task.online_sku,
                    "platform_store_item_code": task.platform_store_item_code,
                    "status": "already_offline",
                }
            ],
            account_fencing_token=9,
        )
        result = repository.results[0]
        self.assertEqual(result["outbox_topic"], "jushuitan.cleanup_1688_link")
        self.assertEqual(result["outbox_payload"]["operation_key"], operation.operation_key)
        self.assertEqual(result["account_fencing_token"], 9)

    def test_outbox_keeps_business_operation_key_and_exact_jushuitan_store(self) -> None:
        task = self.task(replacement_sku="NEW-1")
        task.store_name = "新佰广1688"
        operation = build_saga_operation(
            "sku_replace",
            "run-xin",
            "xinbaiguang_shanzhu",
            task,
            jushuitan_store_name="阿里巴巴-新佰广",
        )
        self.assertEqual(operation.payload["store_name"], "新佰广1688")
        self.assertEqual(operation.payload["jushuitan_store_name"], "阿里巴巴-新佰广")
        self.assertEqual(operation.operation_key, task_operation_key("sku_replace", task))

    def test_erp_ddl_owns_only_saga_and_outbox(self) -> None:
        ddl = (PROJECT_ROOT / "sql" / "362_ali1688_operation_saga_outbox.sql").read_text(
            encoding="utf-8"
        )
        self.assertIn("app.ali1688_operation_saga", ddl)
        self.assertIn("app.ali1688_operation_outbox", ddl)
        self.assertIn("account_fencing_token", ddl)
        self.assertIn("READPAST", (PROJECT_ROOT / "rpa" / "operation_saga.py").read_text(encoding="utf-8"))
        self.assertNotIn("CREATE TABLE app.ali1688_runtime_lease", ddl)
        self.assertNotIn("CREATE PROCEDURE app.usp_ali1688_runtime_", ddl)


if __name__ == "__main__":
    unittest.main()


class _FakeCursor:
    def __init__(self, rowcount: int, current: tuple | None) -> None:
        self.rowcount = rowcount
        self._current = current
        self.executed: list[tuple] = []

    def execute(self, sql, params=None):
        self.executed.append((sql, params))
        return self

    def fetchone(self):
        return self._current


class _FakeConnection:
    def __init__(self, cursor: _FakeCursor) -> None:
        self._cursor = cursor
        self.commits = 0
        self.rollbacks = 0

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def cursor(self):
        return self._cursor

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1


class _FakeConfig:
    schema = "app"
    database = "JSReportReplica"


class RecordAli1688ResultIdempotencyTests(unittest.TestCase):
    def test_failed_terminal_result_sets_finished_at(self) -> None:
        cursor = _FakeCursor(rowcount=1, current=None)
        connection = _FakeConnection(cursor)
        repository = OperationSagaRepository(
            _FakeConfig(),
            connect=lambda _config: connection,
        )
        repository.record_ali1688_result(
            operation_key="k" * 64,
            account_fencing_token=19,
            status="failed",
            error_code="automation_error",
            error_summary="executor stopped",
        )
        update_sql = cursor.executed[0][0]
        self.assertIn("IN ('completed', 'failed_terminal')", update_sql)
        self.assertEqual(connection.commits, 1)

    def test_completed_state_replay_is_noop_despite_new_fencing(self) -> None:
        cursor = _FakeCursor(rowcount=0, current=("completed", 15))
        connection = _FakeConnection(cursor)
        repository = OperationSagaRepository(
            _FakeConfig(),
            connect=lambda _config: connection,
        )
        repository.record_ali1688_result(
            operation_key="k" * 64,
            account_fencing_token=19,
            status="already_offline",
            outbox_topic="jushuitan.cleanup_1688_link",
            outbox_payload={"operation_key": "k" * 64},
        )
        self.assertEqual(connection.commits, 1)
        insert_count = sum(1 for sql, _ in cursor.executed if "INSERT INTO" in sql)
        self.assertEqual(insert_count, 0)


class _RequeueCursor:
    def __init__(self) -> None:
        self.rowcount = -1
        self._row = (7, "failed_terminal", 3, "task_not_found", "missing", "failed_terminal", "run-1")
        self.executed: list[tuple] = []

    def execute(self, sql, params=None):
        self.executed.append((sql, params))
        self.rowcount = 1 if "UPDATE" in sql else -1
        return self

    def fetchone(self):
        return self._row


class OperationSagaOutboxRequeueTests(unittest.TestCase):
    def test_controlled_requeue_updates_outbox_and_saga_with_cas(self) -> None:
        cursor = _RequeueCursor()
        connection = _FakeConnection(cursor)  # type: ignore[arg-type]
        repository = OperationSagaRepository(
            _FakeConfig(),
            connect=lambda _config: connection,
        )
        result = repository.requeue_outbox(
            "a" * 64,
            expected_status="failed_terminal",
            expected_error_code="task_not_found",
            expected_run_id="run-1",
            expected_attempt_count=3,
            expected_saga_state="failed_terminal",
            reason="verified target absence classifier deployed",
        )
        self.assertEqual(result["status"], "failed_retryable")
        self.assertEqual(connection.commits, 1)
        updates = [sql for sql, _ in cursor.executed if "UPDATE" in sql]
        self.assertEqual(len(updates), 2)
        self.assertTrue(all("WHERE operation_key = ?" in sql for sql in updates))
        self.assertIn("attempt_count = ?", updates[0])
        self.assertIn("run_id = ?", updates[1])


class _TerminalizeCursor:
    def __init__(self, *, row=None, update_rowcounts=(1, 1)) -> None:
        self._row = row or (
            7,
            "failed_retryable",
            3,
            "task_not_found",
            "No exact row matched store/product/SKU/platform code",
            "jushuitan_pending",
            "run-1",
        )
        self._update_rowcounts = list(update_rowcounts)
        self.executed: list[tuple] = []
        self._update_index = 0
        self.rowcount = -1

    def execute(self, sql, params=None):
        self.executed.append((sql, params))
        if "UPDATE" in sql:
            self.rowcount = self._update_rowcounts[self._update_index]
            self._update_index += 1
        return self

    def fetchone(self):
        return self._row


class OperationSagaTaskNotFoundTerminalizeTests(unittest.TestCase):
    def _repository(self, cursor: _TerminalizeCursor):
        return OperationSagaRepository(
            _FakeConfig(),
            connect=lambda _config: _FakeConnection(cursor),
        )

    def test_terminalize_preserves_task_not_found_and_updates_both_rows(self) -> None:
        cursor = _TerminalizeCursor()
        connection = _FakeConnection(cursor)
        repository = OperationSagaRepository(
            _FakeConfig(), connect=lambda _config: connection
        )

        result = repository.terminalize_outbox(
            "a" * 64,
            expected_status="failed_retryable",
            expected_error_code="task_not_found",
            expected_run_id="run-1",
            expected_attempt_count=3,
            expected_saga_state="jushuitan_pending",
            reason="历史精确查询确认无匹配任务",
        )

        self.assertEqual(result["status"], "failed_terminal")
        self.assertEqual(result["error_code"], "task_not_found")
        self.assertIn("No exact row matched", result["error_summary"])
        self.assertIn("terminalized:", result["error_summary"])
        self.assertEqual(connection.commits, 1)
        self.assertEqual(connection.rollbacks, 0)
        updates = [sql for sql, _ in cursor.executed if "UPDATE" in sql]
        self.assertEqual(len(updates), 2)
        self.assertIn("status = 'failed_terminal'", updates[0])
        self.assertIn("claim_owner = NULL", updates[0])
        self.assertIn("completed_at = SYSUTCDATETIME()", updates[0])
        self.assertIn("attempt_count = ?", updates[0])
        self.assertIn("ISNULL(last_error_code, '') = ?", updates[0])
        self.assertIn("jushuitan_status = 'failed_terminal'", updates[1])
        self.assertIn("finished_at = SYSUTCDATETIME()", updates[1])
        self.assertIn("state = ?", updates[1])
        self.assertIn("run_id = ?", updates[1])

    def test_terminalize_rejects_state_drift_and_rolls_back(self) -> None:
        cursor = _TerminalizeCursor(
            row=(
                7,
                "failed_retryable",
                4,
                "task_not_found",
                "No exact row matched store/product/SKU/platform code",
                "failed_terminal",
                "run-1",
            )
        )
        connection = _FakeConnection(cursor)
        repository = OperationSagaRepository(
            _FakeConfig(), connect=lambda _config: connection
        )

        with self.assertRaisesRegex(RuntimeError, "attempt_count_changed:4"):
            repository.terminalize_outbox(
                "a" * 64,
                expected_status="failed_retryable",
                expected_error_code="task_not_found",
                expected_run_id="run-1",
                expected_attempt_count=3,
                expected_saga_state="jushuitan_pending",
                reason="approved historical terminalization",
            )

        self.assertEqual(connection.commits, 0)
        self.assertEqual(connection.rollbacks, 1)

    def test_terminalize_rejects_already_terminal_input(self) -> None:
        cursor = _TerminalizeCursor()
        repository = self._repository(cursor)

        with self.assertRaisesRegex(ValueError, "Only failed_retryable"):
            repository.terminalize_outbox(
                "a" * 64,
                expected_status="failed_terminal",
                expected_error_code="task_not_found",
                expected_run_id="run-1",
                expected_attempt_count=3,
                expected_saga_state="jushuitan_pending",
                reason="approved historical terminalization",
            )
        self.assertFalse(any("UPDATE" in sql for sql, _ in cursor.executed))

    def test_terminalize_rolls_back_when_saga_cas_is_not_exactly_one(self) -> None:
        cursor = _TerminalizeCursor(update_rowcounts=(1, 0))
        connection = _FakeConnection(cursor)
        repository = OperationSagaRepository(
            _FakeConfig(), connect=lambda _config: connection
        )

        with self.assertRaisesRegex(RuntimeError, "saga_terminalize"):
            repository.terminalize_outbox(
                "a" * 64,
                expected_status="failed_retryable",
                expected_error_code="task_not_found",
                expected_run_id="run-1",
                expected_attempt_count=3,
                expected_saga_state="jushuitan_pending",
                reason="approved historical terminalization",
            )

        self.assertEqual(connection.commits, 0)
        self.assertEqual(connection.rollbacks, 1)



class _ExactClaimCursor:
    def __init__(
        self,
        keys: list[str],
        *,
        invalid_key: str = "",
        topic: str = "jushuitan.cleanup_1688_link",
    ) -> None:
        self.keys = keys
        self.invalid_key = invalid_key
        self.topic = topic
        self.stage = ""
        self.executed: list[tuple] = []

    def execute(self, sql, params=None):
        self.executed.append((sql, params))
        if "SELECT approved.operation_key" in sql:
            self.stage = "validate"
        elif "UPDATE outbox_row" in sql:
            self.stage = "claim"
        return self

    def executemany(self, sql, params):
        self.executed.append((sql, list(params)))
        return self

    def fetchall(self):
        if self.stage == "validate":
            return [
                (key, 1, "failed_retryable", self.topic, "run-1", 0 if key == self.invalid_key else 1)
                for key in self.keys
            ]
        if self.stage == "claim":
            return [
                (index + 1, key, self.topic, "{}", 2, "c" * 32)
                for index, key in enumerate(self.keys)
            ]
        return []


class OperationSagaExactClaimTests(unittest.TestCase):
    def test_exact_claim_commits_only_the_approved_set(self) -> None:
        keys = ["a" * 64, "b" * 64]
        cursor = _ExactClaimCursor(keys)
        connection = _FakeConnection(cursor)  # type: ignore[arg-type]
        repository = OperationSagaRepository(_FakeConfig(), connect=lambda _config: connection)

        items = repository.claim_outbox_exact(
            claim_owner="worker-1",
            run_id="run-1",
            topic="jushuitan.cleanup_1688_link",
            operation_keys=keys,
        )

        self.assertEqual({item.operation_key for item in items}, set(keys))
        self.assertEqual(connection.commits, 1)
        self.assertEqual(connection.rollbacks, 0)

    def test_exact_claim_rolls_back_when_any_approved_key_is_not_claimable(self) -> None:
        keys = ["a" * 64, "b" * 64]
        cursor = _ExactClaimCursor(keys, invalid_key=keys[1])
        connection = _FakeConnection(cursor)  # type: ignore[arg-type]
        repository = OperationSagaRepository(_FakeConfig(), connect=lambda _config: connection)

        with self.assertRaisesRegex(RuntimeError, "not_claimable"):
            repository.claim_outbox_exact(
                claim_owner="worker-1",
                run_id="run-1",
                topic="jushuitan.cleanup_1688_link",
                operation_keys=keys,
            )

        self.assertEqual(connection.commits, 0)
        self.assertEqual(connection.rollbacks, 1)

    def test_exact_claim_can_take_over_only_the_expected_expired_claim_set(self) -> None:
        keys = ["a" * 64, "b" * 64]
        cursor = _ExactClaimCursor(keys)
        connection = _FakeConnection(cursor)  # type: ignore[arg-type]
        repository = OperationSagaRepository(_FakeConfig(), connect=lambda _config: connection)

        items = repository.claim_outbox_exact(
            claim_owner="executor:new-worker",
            run_id="run-1",
            topic="jushuitan.cleanup_1688_link",
            operation_keys=keys,
            expected_expired_claim_owner="executor:1234",
            expected_expired_claim_attempt_count=1,
            expired_claim_recovery_evidence={
                "approved_operation_keys_sha256": "c" * 64,
                "reason": "previous owner stopped after the claim expired",
            },
        )

        self.assertEqual({item.operation_key for item in items}, set(keys))
        self.assertEqual(connection.commits, 1)
        self.assertEqual(connection.rollbacks, 0)
        validation = next(entry for entry in cursor.executed if "CASE WHEN" in entry[0])
        self.assertEqual(
            validation[1],
            (1, "executor:1234", "stop_sale", "success", "already_offline"),
        )
        update = next(entry for entry in cursor.executed if "UPDATE outbox_row" in entry[0])
        self.assertIn("outbox_row.status = 'claimed'", update[0])
        self.assertIn("outbox_row.claim_owner = ?", update[0])
        self.assertIn("outbox_row.claim_until < SYSUTCDATETIME()", update[0])
        self.assertIn("saga_row.task_type = ?", update[0])
        self.assertIn("last_error_code = 'expired_claim_recovered'", update[0])
        self.assertIn("approved_operation_keys_sha256", str(update[1]))

    def test_exact_expired_claim_takeover_requires_complete_evidence(self) -> None:
        repository = OperationSagaRepository(
            _FakeConfig(),
            connect=lambda _config: _FakeConnection(_ExactClaimCursor(["a" * 64])),
        )

        with self.assertRaisesRegex(ValueError, "durable evidence"):
            repository.claim_outbox_exact(
                claim_owner="executor:new-worker",
                run_id="run-1",
                topic="jushuitan.cleanup_1688_link",
                operation_keys=["a" * 64],
                expected_expired_claim_owner="executor:1234",
                expected_expired_claim_attempt_count=1,
            )

    def test_exact_expired_claim_takeover_rolls_back_on_owner_or_state_drift(self) -> None:
        keys = ["a" * 64, "b" * 64]
        cursor = _ExactClaimCursor(keys, invalid_key=keys[1])
        connection = _FakeConnection(cursor)  # type: ignore[arg-type]
        repository = OperationSagaRepository(_FakeConfig(), connect=lambda _config: connection)

        with self.assertRaisesRegex(RuntimeError, "not_claimable"):
            repository.claim_outbox_exact(
                claim_owner="executor:new-worker",
                run_id="run-1",
                topic="jushuitan.cleanup_1688_link",
                operation_keys=keys,
                expected_expired_claim_owner="executor:1234",
                expected_expired_claim_attempt_count=1,
                expired_claim_recovery_evidence={"reason": "approved recovery"},
            )

        self.assertEqual(connection.commits, 0)
        self.assertEqual(connection.rollbacks, 1)
        self.assertFalse(any("UPDATE outbox_row" in sql for sql, _ in cursor.executed))

    def test_exact_expired_sync_claim_binds_sku_replace_success_states(self) -> None:
        key = "a" * 64
        cursor = _ExactClaimCursor([key], topic="jushuitan.sync_1688_link")
        repository = OperationSagaRepository(
            _FakeConfig(),
            connect=lambda _config: _FakeConnection(cursor),
        )

        repository.claim_outbox_exact(
            claim_owner="executor:new-worker",
            run_id="run-1",
            topic="jushuitan.sync_1688_link",
            operation_keys=[key],
            expected_expired_claim_owner="executor:1234",
            expected_expired_claim_attempt_count=1,
            expired_claim_recovery_evidence={"reason": "approved recovery"},
        )

        validation = next(entry for entry in cursor.executed if "CASE WHEN" in entry[0])
        self.assertEqual(
            validation[1],
            (1, "executor:1234", "sku_replace", "success", "already_replaced"),
        )


class _InterruptedRecoveryCursor:
    def __init__(
        self,
        *,
        successful_item_count: int = 0,
        eligible_source_item_count: int = 1,
        saga_state: str = "failed_terminal",
        invalid_key: str = "",
    ) -> None:
        self.successful_item_count = successful_item_count
        self.eligible_source_item_count = eligible_source_item_count
        self.saga_state = saga_state
        self.invalid_key = invalid_key
        self.current_key = ""
        self.rowcount = -1
        self.stage = ""
        self.executed: list[tuple] = []

    def execute(self, sql, params=None):
        self.executed.append((sql, params))
        if "SELECT saga_row.run_id" in sql:
            self.stage = "validate"
            self.current_key = str(params[-1])
            self.rowcount = -1
        elif "UPDATE saga_row" in sql:
            self.stage = "update"
            self.rowcount = 1
        return self

    def fetchone(self):
        if self.stage != "validate":
            return None
        return (
            "source-run",
            "stop_sale",
            "gonglai",
            "store|product|sku|item",
            self.saga_state,
            "failed",
            None,
            "interrupted_executor_process",
            0,
            1,
            self.eligible_source_item_count,
            1 if self.current_key == self.invalid_key else self.successful_item_count,
        )


class OperationSagaInterruptedRecoveryTests(unittest.TestCase):
    def operation(self, key: str = "a" * 64) -> SagaOperation:
        return SagaOperation(
            operation_key=key,
            run_id="recovery-run",
            task_type="stop_sale",
            account_key="gonglai",
            business_key="store|product|sku|item",
            payload={"operation_key": key},
        )

    def test_exact_interrupted_recovery_prepares_only_after_all_gates_match(self) -> None:
        cursor = _InterruptedRecoveryCursor()
        connection = _FakeConnection(cursor)  # type: ignore[arg-type]
        repository = OperationSagaRepository(_FakeConfig(), connect=lambda _config: connection)
        operation = self.operation()

        result = repository.prepare_interrupted_stop_sale_recovery_many(
            [operation],
            source_run_id="source-run",
            approved_operation_keys=[operation.operation_key],
            approval_sha256="c" * 64,
            input_sha256="d" * 64,
            owner_token="owner-1",
            account_fencing_token=11,
            browser_slot_key="host:1",
            browser_slot_fencing_token=22,
        )

        self.assertEqual(result, {operation.operation_key: "prepared"})
        self.assertEqual(connection.commits, 1)
        self.assertEqual(connection.rollbacks, 0)
        updates = [entry for entry in cursor.executed if "UPDATE saga_row" in entry[0]]
        self.assertEqual(len(updates), 1)
        self.assertIn("NOT EXISTS", updates[0][0])
        self.assertIn("offline_status IN ('success', 'already_offline')", updates[0][0])
        self.assertIn("LEFT(source_item.error_message", updates[0][0])
        self.assertIn("targeted_recovery_source_run_id", str(updates[0][1]))
        self.assertIn("targeted_recovery_approval_sha256", str(updates[0][1]))

    def test_successful_item_evidence_blocks_entire_recovery(self) -> None:
        cursor = _InterruptedRecoveryCursor(successful_item_count=1)
        connection = _FakeConnection(cursor)  # type: ignore[arg-type]
        repository = OperationSagaRepository(_FakeConfig(), connect=lambda _config: connection)
        operation = self.operation()

        with self.assertRaisesRegex(RuntimeError, "successful_item_count"):
            repository.prepare_interrupted_stop_sale_recovery_many(
                [operation],
                source_run_id="source-run",
                approved_operation_keys=[operation.operation_key],
                approval_sha256="c" * 64,
                input_sha256="d" * 64,
                owner_token="owner-1",
                account_fencing_token=11,
                browser_slot_key="host:1",
                browser_slot_fencing_token=22,
            )

        self.assertEqual(connection.commits, 0)
        self.assertEqual(connection.rollbacks, 1)
        self.assertFalse(any("UPDATE saga_row" in sql for sql, _ in cursor.executed))

    def test_non_interrupted_source_item_blocks_entire_recovery(self) -> None:
        cursor = _InterruptedRecoveryCursor(eligible_source_item_count=0)
        connection = _FakeConnection(cursor)  # type: ignore[arg-type]
        repository = OperationSagaRepository(_FakeConfig(), connect=lambda _config: connection)
        operation = self.operation()

        with self.assertRaisesRegex(RuntimeError, "eligible_source_item_count"):
            repository.prepare_interrupted_stop_sale_recovery_many(
                [operation],
                source_run_id="source-run",
                approved_operation_keys=[operation.operation_key],
                approval_sha256="c" * 64,
                input_sha256="d" * 64,
                owner_token="owner-1",
                account_fencing_token=11,
                browser_slot_key="host:1",
                browser_slot_fencing_token=22,
            )

        self.assertEqual(connection.commits, 0)
        self.assertEqual(connection.rollbacks, 1)
        self.assertFalse(any("UPDATE saga_row" in sql for sql, _ in cursor.executed))

    def test_second_key_drift_blocks_before_any_update(self) -> None:
        first = self.operation("a" * 64)
        second = self.operation("b" * 64)
        cursor = _InterruptedRecoveryCursor(invalid_key=second.operation_key)
        connection = _FakeConnection(cursor)  # type: ignore[arg-type]
        repository = OperationSagaRepository(_FakeConfig(), connect=lambda _config: connection)

        with self.assertRaisesRegex(RuntimeError, "successful_item_count"):
            repository.prepare_interrupted_stop_sale_recovery_many(
                [first, second],
                source_run_id="source-run",
                approved_operation_keys=[first.operation_key, second.operation_key],
                approval_sha256="c" * 64,
                input_sha256="d" * 64,
                owner_token="owner-1",
                account_fencing_token=11,
                browser_slot_key="host:1",
                browser_slot_fencing_token=22,
            )

        self.assertEqual(connection.commits, 0)
        self.assertEqual(connection.rollbacks, 1)
        self.assertFalse(any("UPDATE saga_row" in sql for sql, _ in cursor.executed))

    def test_terminal_state_drift_blocks_entire_recovery(self) -> None:
        cursor = _InterruptedRecoveryCursor(saga_state="completed")
        connection = _FakeConnection(cursor)  # type: ignore[arg-type]
        repository = OperationSagaRepository(_FakeConfig(), connect=lambda _config: connection)
        operation = self.operation()

        with self.assertRaisesRegex(RuntimeError, ":state"):
            repository.prepare_interrupted_stop_sale_recovery_many(
                [operation],
                source_run_id="source-run",
                approved_operation_keys=[operation.operation_key],
                approval_sha256="c" * 64,
                input_sha256="d" * 64,
                owner_token="owner-1",
                account_fencing_token=11,
                browser_slot_key="host:1",
                browser_slot_fencing_token=22,
            )

        self.assertEqual(connection.commits, 0)
        self.assertEqual(connection.rollbacks, 1)

    def test_approved_key_scope_must_equal_operation_scope(self) -> None:
        cursor = _InterruptedRecoveryCursor()
        connection = _FakeConnection(cursor)  # type: ignore[arg-type]
        repository = OperationSagaRepository(_FakeConfig(), connect=lambda _config: connection)

        with self.assertRaisesRegex(ValueError, "approved scope"):
            repository.prepare_interrupted_stop_sale_recovery_many(
                [self.operation()],
                source_run_id="source-run",
                approved_operation_keys=["b" * 64],
                approval_sha256="c" * 64,
                input_sha256="d" * 64,
                owner_token="owner-1",
                account_fencing_token=11,
                browser_slot_key="host:1",
                browser_slot_fencing_token=22,
            )

        self.assertEqual(cursor.executed, [])

    def test_source_run_cannot_equal_recovery_run(self) -> None:
        cursor = _InterruptedRecoveryCursor()
        connection = _FakeConnection(cursor)  # type: ignore[arg-type]
        repository = OperationSagaRepository(_FakeConfig(), connect=lambda _config: connection)

        with self.assertRaisesRegex(ValueError, "new recovery run_id"):
            repository.prepare_interrupted_stop_sale_recovery_many(
                [self.operation()],
                source_run_id="recovery-run",
                approved_operation_keys=["a" * 64],
                approval_sha256="c" * 64,
                input_sha256="d" * 64,
                owner_token="owner-1",
                account_fencing_token=11,
                browser_slot_key="host:1",
                browser_slot_fencing_token=22,
            )

        self.assertEqual(cursor.executed, [])
