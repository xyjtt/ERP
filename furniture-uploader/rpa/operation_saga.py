"""Durable ERP-owned Saga and Jushuitan Outbox persistence."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from typing import Any, Callable, Iterable, Mapping
import uuid

from stop_sale_audit import StopSaleAppConfig, _validate_identifier, connect_app_database


SUCCESS_1688_STATUSES = {
    "success",
    "already_offline",
    "already_replaced",
    "draft_saved",
    "submit_succeeded",
}


class SagaReconcileRequiredError(RuntimeError):
    pass


class SagaFencingError(RuntimeError):
    pass


@dataclass(frozen=True)
class SagaOperation:
    operation_key: str
    run_id: str
    task_type: str
    account_key: str
    business_key: str
    payload: Mapping[str, Any]


@dataclass(frozen=True)
class OutboxItem:
    outbox_id: int
    operation_key: str
    topic: str
    payload: dict[str, Any]
    attempt_count: int
    claim_token: str


def build_operation_key(task_type: str, account_key: str, *identity_parts: Any) -> str:
    normalized = "|".join(
        [
            "ali1688-operation-v1",
            str(task_type or "").strip().lower(),
            str(account_key or "").strip().lower(),
            *("".join(str(part or "").split()).lower() for part in identity_parts),
        ]
    )
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def owner_token_hash(owner_token: str) -> str:
    return hashlib.sha256(str(owner_token).encode("utf-8")).hexdigest()


class OperationSagaRepository:
    REQUIRED_TABLES = ("ali1688_operation_saga", "ali1688_operation_outbox")

    def __init__(
        self,
        config: StopSaleAppConfig,
        *,
        connect: Callable[[StopSaleAppConfig], Any] = connect_app_database,
    ) -> None:
        self.config = config
        self.schema = _validate_identifier(config.schema, "schema")
        self._connect = connect

    def _table(self, name: str) -> str:
        return f"[{self.schema}].[{_validate_identifier(name, 'table')}]"

    def check_contract(self) -> dict[str, Any]:
        with self._connect(self.config) as connection:
            cursor = connection.cursor()
            database_name = str(cursor.execute("SELECT DB_NAME()").fetchone()[0])
            missing = [
                f"{self.schema}.{table}"
                for table in self.REQUIRED_TABLES
                if cursor.execute(
                    "SELECT OBJECT_ID(?, 'U')",
                    (f"{self.schema}.{table}",),
                ).fetchone()[0]
                is None
            ]
        if database_name != self.config.database:
            raise RuntimeError(f"Saga connected to unexpected database: {database_name}")
        return {
            "database": database_name,
            "schema": self.schema,
            "missing_tables": missing,
            "ready": not missing,
        }

    def prepare(
        self,
        operation: SagaOperation,
        *,
        owner_token: str,
        account_fencing_token: int,
        browser_slot_key: str,
        browser_slot_fencing_token: int,
    ) -> str:
        if account_fencing_token <= 0 or browser_slot_fencing_token <= 0:
            raise SagaFencingError("saga_requires_positive_fencing_tokens")
        saga = self._table("ali1688_operation_saga")
        payload_json = json.dumps(operation.payload, ensure_ascii=False, sort_keys=True, default=str)
        with self._connect(self.config) as connection:
            cursor = connection.cursor()
            existing = cursor.execute(
                f"""
                SELECT state, account_fencing_token, owner_token_hash
                FROM {saga} WITH (UPDLOCK, HOLDLOCK)
                WHERE operation_key = ?
                """,
                (operation.operation_key,),
            ).fetchone()
            if existing is not None:
                state = str(existing[0] or "")
                existing_fencing = int(existing[1] or 0)
                existing_owner_hash = str(existing[2] or "")
                if state in {"completed", "ali1688_success", "jushuitan_pending"}:
                    connection.commit()
                    return state
                if state == "prepared" and (
                    existing_fencing != account_fencing_token
                    or existing_owner_hash != owner_token_hash(owner_token)
                ):
                    cursor.execute(
                        f"""
                        UPDATE {saga}
                        SET state = 'reconcile_required', updated_at = SYSUTCDATETIME(),
                            error_code = 'owner_changed_after_prepare',
                            error_summary = 'Prepared operation must be reconciled before retry.'
                        WHERE operation_key = ? AND state = 'prepared'
                        """,
                        (operation.operation_key,),
                    )
                    connection.commit()
                    raise SagaReconcileRequiredError("owner_changed_after_prepare")
                cursor.execute(
                    f"""
                    UPDATE {saga}
                    SET run_id = ?, state = 'prepared', owner_token_hash = ?,
                        account_fencing_token = ?, browser_slot_key = ?,
                        browser_slot_fencing_token = ?, payload_json = ?,
                        evidence_json = NULL, error_code = NULL, error_summary = NULL,
                        prepared_at = SYSUTCDATETIME(), ali1688_finished_at = NULL,
                        finished_at = NULL, updated_at = SYSUTCDATETIME()
                    WHERE operation_key = ?
                    """,
                    (
                        operation.run_id,
                        owner_token_hash(owner_token),
                        account_fencing_token,
                        browser_slot_key,
                        browser_slot_fencing_token,
                        payload_json,
                        operation.operation_key,
                    ),
                )
            else:
                cursor.execute(
                    f"""
                    INSERT INTO {saga} (
                        operation_key, run_id, task_type, account_key, business_key,
                        state, owner_token_hash, account_fencing_token,
                        browser_slot_key, browser_slot_fencing_token, payload_json
                    ) VALUES (?, ?, ?, ?, ?, 'prepared', ?, ?, ?, ?, ?)
                    """,
                    (
                        operation.operation_key,
                        operation.run_id,
                        operation.task_type,
                        operation.account_key,
                        operation.business_key[:500],
                        owner_token_hash(owner_token),
                        account_fencing_token,
                        browser_slot_key,
                        browser_slot_fencing_token,
                        payload_json,
                    ),
                )
            connection.commit()
        return "prepared"

    def prepare_many(
        self,
        operations: Iterable[SagaOperation],
        **fencing: Any,
    ) -> dict[str, str]:
        return {
            operation.operation_key: self.prepare(operation, **fencing)
            for operation in operations
        }

    def record_ali1688_result(
        self,
        *,
        operation_key: str,
        account_fencing_token: int,
        status: str,
        evidence: Mapping[str, Any] | None = None,
        outbox_topic: str = "",
        outbox_payload: Mapping[str, Any] | None = None,
        error_code: str = "",
        error_summary: str = "",
    ) -> None:
        normalized_status = str(status or "failed").strip()
        success = normalized_status in SUCCESS_1688_STATUSES
        state = "jushuitan_pending" if success and outbox_topic else "completed" if success else "failed_terminal"
        saga = self._table("ali1688_operation_saga")
        outbox = self._table("ali1688_operation_outbox")
        with self._connect(self.config) as connection:
            cursor = connection.cursor()
            cursor.execute(
                f"""
                UPDATE {saga}
                SET state = ?, ali1688_status = ?,
                    jushuitan_status = CASE WHEN ? = 1 THEN 'pending' ELSE jushuitan_status END,
                    evidence_json = ?, error_code = ?, error_summary = ?,
                    ali1688_finished_at = SYSUTCDATETIME(),
                    finished_at = CASE WHEN ? = 'completed' THEN SYSUTCDATETIME() ELSE finished_at END,
                    updated_at = SYSUTCDATETIME()
                WHERE operation_key = ?
                  AND account_fencing_token = ?
                  AND state IN ('prepared', 'failed_retryable', 'failed_terminal')
                """,
                (
                    state,
                    normalized_status[:60],
                    int(bool(outbox_topic)),
                    json.dumps(dict(evidence or {}), ensure_ascii=False, default=str),
                    str(error_code or "")[:120] or None,
                    str(error_summary or "")[:2000] or None,
                    state,
                    operation_key,
                    account_fencing_token,
                ),
            )
            if cursor.rowcount == 0:
                current = cursor.execute(
                    f"SELECT state, account_fencing_token FROM {saga} WHERE operation_key = ?",
                    (operation_key,),
                ).fetchone()
                if current is None:
                    raise SagaFencingError("saga_operation_missing")
                # Idempotent replay of a terminal-success operation is a no-op
                # regardless of fencing: no state transition happens anyway.
                if str(current[0] or "") in {"completed", "jushuitan_pending", "ali1688_success"}:
                    connection.commit()
                    return
                if int(current[1] or 0) != account_fencing_token:
                    raise SagaFencingError("stale_account_fencing_token")
                raise SagaReconcileRequiredError(f"saga_state_rejected:{current[0]}")
            if success and outbox_topic:
                serialized = json.dumps(dict(outbox_payload or {}), ensure_ascii=False, sort_keys=True, default=str)
                cursor.execute(
                    f"""
                    IF NOT EXISTS (SELECT 1 FROM {outbox} WITH (UPDLOCK, HOLDLOCK) WHERE operation_key = ?)
                        INSERT INTO {outbox} (operation_key, topic, status, payload_json)
                        VALUES (?, ?, 'pending', ?)
                    """,
                    (operation_key, operation_key, outbox_topic[:100], serialized),
                )
            connection.commit()

    def claim_outbox(
        self,
        *,
        claim_owner: str,
        run_id: str = "",
        topic: str = "",
        limit: int = 100,
        claim_seconds: int = 1800,
    ) -> list[OutboxItem]:
        outbox = self._table("ali1688_operation_outbox")
        saga = self._table("ali1688_operation_saga")
        claim_token = uuid.uuid4().hex
        filters = [
            "outbox_row.status IN ('pending', 'failed_retryable')",
            "outbox_row.available_at <= SYSUTCDATETIME()",
            "(outbox_row.claim_until IS NULL OR outbox_row.claim_until < SYSUTCDATETIME())",
        ]
        params: list[Any] = []
        if run_id:
            filters.append("saga_row.run_id = ?")
            params.append(run_id)
        if topic:
            filters.append("outbox_row.topic = ?")
            params.append(topic)
        params.extend(
            [claim_owner[:100], claim_token, max(1, int(claim_seconds)), max(1, int(limit))]
        )
        with self._connect(self.config) as connection:
            cursor = connection.cursor()
            rows = cursor.execute(
                f"""
                ;WITH candidates AS (
                    SELECT TOP (?) outbox_row.outbox_id
                    FROM {outbox} AS outbox_row WITH (UPDLOCK, READPAST, ROWLOCK)
                    JOIN {saga} AS saga_row ON saga_row.operation_key = outbox_row.operation_key
                    WHERE {' AND '.join(filters)}
                    ORDER BY outbox_row.available_at, outbox_row.outbox_id
                )
                UPDATE outbox_row
                SET status = 'claimed', claim_owner = ?, claim_token = ?,
                    claim_until = DATEADD(SECOND, ?, SYSUTCDATETIME()),
                    attempt_count = attempt_count + 1, updated_at = SYSUTCDATETIME()
                OUTPUT inserted.outbox_id, inserted.operation_key, inserted.topic,
                       inserted.payload_json, inserted.attempt_count, inserted.claim_token
                FROM {outbox} AS outbox_row
                JOIN candidates ON candidates.outbox_id = outbox_row.outbox_id;
                """,
                tuple([max(1, int(limit)), *params[:-1]]),
            ).fetchall()
            connection.commit()
        return [
            OutboxItem(
                outbox_id=int(row[0]),
                operation_key=str(row[1]),
                topic=str(row[2]),
                payload=json.loads(str(row[3])),
                attempt_count=int(row[4]),
                claim_token=str(row[5]),
            )
            for row in rows
        ]

    def finish_outbox(
        self,
        item: OutboxItem,
        *,
        status: str,
        error_code: str = "",
        error_summary: str = "",
        retry_delay_seconds: int = 300,
    ) -> None:
        if status not in {"succeeded", "failed_retryable", "failed_terminal"}:
            raise ValueError(f"Unsupported outbox terminal status: {status}")
        outbox = self._table("ali1688_operation_outbox")
        saga = self._table("ali1688_operation_saga")
        saga_state = "completed" if status == "succeeded" else status
        jushuitan_status = "success" if status == "succeeded" else status
        with self._connect(self.config) as connection:
            cursor = connection.cursor()
            cursor.execute(
                f"""
                UPDATE {outbox}
                SET status = ?, claim_owner = NULL, claim_token = NULL, claim_until = NULL,
                    available_at = CASE WHEN ? = 'failed_retryable'
                        THEN DATEADD(SECOND, ?, SYSUTCDATETIME()) ELSE available_at END,
                    last_error_code = ?, last_error_summary = ?,
                    completed_at = CASE WHEN ? IN ('succeeded', 'failed_terminal')
                        THEN SYSUTCDATETIME() ELSE completed_at END,
                    updated_at = SYSUTCDATETIME()
                WHERE outbox_id = ? AND status = 'claimed' AND claim_token = ?
                """,
                (
                    status,
                    status,
                    max(0, int(retry_delay_seconds)),
                    error_code[:120] or None,
                    error_summary[:2000] or None,
                    status,
                    item.outbox_id,
                    item.claim_token,
                ),
            )
            if cursor.rowcount != 1:
                raise SagaFencingError("outbox_claim_compare_and_set_failed")
            cursor.execute(
                f"""
                UPDATE {saga}
                SET state = ?, jushuitan_status = ?, error_code = ?, error_summary = ?,
                    finished_at = CASE WHEN ? IN ('completed', 'failed_terminal')
                        THEN SYSUTCDATETIME() ELSE finished_at END,
                    updated_at = SYSUTCDATETIME()
                WHERE operation_key = ? AND state IN ('jushuitan_pending', 'failed_retryable')
                """,
                (
                    saga_state,
                    jushuitan_status,
                    error_code[:120] or None,
                    error_summary[:2000] or None,
                    saga_state,
                    item.operation_key,
                ),
            )
            connection.commit()
