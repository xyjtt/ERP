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

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def cursor(self):
        return self._cursor

    def commit(self):
        self.commits += 1


class _FakeConfig:
    schema = "app"
    database = "JSReportReplica"


class RecordAli1688ResultIdempotencyTests(unittest.TestCase):
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
