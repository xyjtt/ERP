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

    def get_saga_state(self, operation_key: str) -> dict[str, Any]:
        saga = self._table("ali1688_operation_saga")
        with self._connect(self.config) as connection:
            row = connection.cursor().execute(
                f"""
                SELECT run_id, task_type, account_key, business_key, state,
                       owner_token_hash, account_fencing_token,
                       browser_slot_key, browser_slot_fencing_token,
                       payload_json, evidence_json, error_code, error_summary,
                       prepared_at, ali1688_finished_at, finished_at, updated_at
                  FROM {saga} WITH (NOLOCK)
                 WHERE operation_key = ?
                """,
                (operation_key,),
            ).fetchone()
        if row is None:
            raise SagaReconcileRequiredError("saga_operation_missing")
        columns = (
            "run_id",
            "task_type",
            "account_key",
            "business_key",
            "state",
            "owner_token_hash",
            "account_fencing_token",
            "browser_slot_key",
            "browser_slot_fencing_token",
            "payload_json",
            "evidence_json",
            "error_code",
            "error_summary",
            "prepared_at",
            "ali1688_finished_at",
            "finished_at",
            "updated_at",
        )
        return {"operation_key": operation_key, **dict(zip(columns, row))}

    def recover_reconcile_required(
        self,
        operation_key: str,
        *,
        expected_error_code: str,
        expected_owner_token_hash: str,
        expected_account_fencing_token: int,
        expected_browser_slot_key: str,
        expected_browser_slot_fencing_token: int,
        evidence: Mapping[str, Any],
        reason: str,
    ) -> dict[str, Any]:
        if expected_error_code != "owner_changed_after_prepare":
            raise ValueError("Only owner_changed_after_prepare may use controlled recovery")
        if expected_account_fencing_token <= 0 or expected_browser_slot_fencing_token <= 0:
            raise ValueError("Controlled recovery requires positive fencing tokens")
        if not expected_owner_token_hash or not expected_browser_slot_key:
            raise ValueError("Controlled recovery requires the previous owner and browser slot")
        if not evidence or not str(reason or "").strip():
            raise ValueError("Controlled recovery requires evidence and a reason")

        saga = self._table("ali1688_operation_saga")
        outbox = self._table("ali1688_operation_outbox")
        evidence_json = json.dumps(dict(evidence), ensure_ascii=False, sort_keys=True, default=str)
        with self._connect(self.config) as connection:
            cursor = connection.cursor()
            row = cursor.execute(
                f"""
                SELECT state, error_code, owner_token_hash, account_fencing_token,
                       browser_slot_key, browser_slot_fencing_token,
                       evidence_json, ali1688_finished_at
                  FROM {saga} WITH (UPDLOCK, HOLDLOCK)
                 WHERE operation_key = ?
                """,
                (operation_key,),
            ).fetchone()
            if row is None:
                raise SagaReconcileRequiredError("saga_operation_missing")
            current = {
                "state": str(row[0] or ""),
                "error_code": str(row[1] or ""),
                "owner_token_hash": str(row[2] or ""),
                "account_fencing_token": int(row[3] or 0),
                "browser_slot_key": str(row[4] or ""),
                "browser_slot_fencing_token": int(row[5] or 0),
                "evidence_json": row[6],
                "ali1688_finished_at": row[7],
            }
            expected = {
                "state": "reconcile_required",
                "error_code": expected_error_code,
                "owner_token_hash": expected_owner_token_hash,
                "account_fencing_token": expected_account_fencing_token,
                "browser_slot_key": expected_browser_slot_key,
                "browser_slot_fencing_token": expected_browser_slot_fencing_token,
                "evidence_json": None,
                "ali1688_finished_at": None,
            }
            changed = [name for name, value in expected.items() if current[name] != value]
            if changed:
                raise SagaReconcileRequiredError(
                    "saga_recovery_precondition_changed:" + ",".join(changed)
                )
            outbox_count = int(
                cursor.execute(
                    f"SELECT COUNT_BIG(1) FROM {outbox} WITH (UPDLOCK, HOLDLOCK) WHERE operation_key = ?",
                    (operation_key,),
                ).fetchone()[0]
            )
            if outbox_count:
                raise SagaReconcileRequiredError("saga_recovery_outbox_exists")
            cursor.execute(
                f"""
                UPDATE {saga}
                   SET state = 'failed_retryable', evidence_json = ?,
                       error_code = 'controlled_reconcile', error_summary = ?,
                       finished_at = NULL, updated_at = SYSUTCDATETIME()
                 WHERE operation_key = ?
                   AND state = 'reconcile_required'
                   AND error_code = ?
                   AND owner_token_hash = ?
                   AND account_fencing_token = ?
                   AND browser_slot_key = ?
                   AND browser_slot_fencing_token = ?
                   AND evidence_json IS NULL
                   AND ali1688_finished_at IS NULL
                """,
                (
                    evidence_json,
                    str(reason).strip()[:2000],
                    operation_key,
                    expected_error_code,
                    expected_owner_token_hash,
                    expected_account_fencing_token,
                    expected_browser_slot_key,
                    expected_browser_slot_fencing_token,
                ),
            )
            if cursor.rowcount != 1:
                raise SagaFencingError("saga_recovery_compare_and_set_failed")
            connection.commit()
        return {
            "operation_key": operation_key,
            "previous_state": "reconcile_required",
            "state": "failed_retryable",
            "reason": str(reason).strip(),
            "evidence": dict(evidence),
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

    def prepare_interrupted_stop_sale_recovery_many(
        self,
        operations: Iterable[SagaOperation],
        *,
        source_run_id: str,
        approved_operation_keys: Iterable[str],
        approval_sha256: str,
        input_sha256: str,
        owner_token: str,
        account_fencing_token: int,
        browser_slot_key: str,
        browser_slot_fencing_token: int,
    ) -> dict[str, str]:
        operation_list = list(operations)
        operation_map = {operation.operation_key: operation for operation in operation_list}
        approved_keys = sorted(
            str(key or "").strip().lower() for key in approved_operation_keys
        )
        if not operation_list or len(operation_map) != len(operation_list):
            raise ValueError("Interrupted recovery requires unique Saga operations")
        if len(approved_keys) != len(set(approved_keys)):
            raise ValueError("Interrupted recovery approval contains duplicate operation_keys")
        if set(approved_keys) != set(operation_map):
            raise ValueError("Interrupted recovery operations do not match the approved scope")
        if any(
            len(key) != 64 or any(char not in "0123456789abcdef" for char in key)
            for key in approved_keys
        ):
            raise ValueError("Interrupted recovery requires lowercase SHA-256 operation_keys")
        if not str(source_run_id or "").strip():
            raise ValueError("Interrupted recovery requires a source_run_id")
        recovery_hashes = {
            "approval_sha256": str(approval_sha256 or "").strip(),
            "input_sha256": str(input_sha256 or "").strip(),
        }
        if any(
            len(value) != 64 or any(char not in "0123456789abcdef" for char in value)
            for value in recovery_hashes.values()
        ):
            raise ValueError("Interrupted recovery requires lowercase SHA-256 evidence hashes")
        if account_fencing_token <= 0 or browser_slot_fencing_token <= 0:
            raise SagaFencingError("saga_requires_positive_fencing_tokens")
        if not str(owner_token or "").strip() or not str(browser_slot_key or "").strip():
            raise SagaFencingError("saga_requires_runtime_ownership")
        if any(operation.task_type != "stop_sale" for operation in operation_list):
            raise ValueError("Interrupted recovery supports stop_sale operations only")
        recovery_run_ids = {str(operation.run_id or "").strip() for operation in operation_list}
        if len(recovery_run_ids) != 1 or source_run_id in recovery_run_ids:
            raise ValueError("Interrupted recovery requires one new recovery run_id")
        account_keys = {str(operation.account_key or "").strip() for operation in operation_list}
        if len(account_keys) != 1 or "" in account_keys:
            raise ValueError("Interrupted recovery requires one non-empty account_key")

        saga = self._table("ali1688_operation_saga")
        outbox = self._table("ali1688_operation_outbox")
        item = self._table("ali1688_stop_sale_item")
        expected_error_code = "interrupted_executor_process"
        expected_item_error_category = "automation_error"
        expected_item_error_prefix = "interrupted_executor_process:"
        with self._connect(self.config) as connection:
            cursor = connection.cursor()
            try:
                cursor.execute(
                    "SET XACT_ABORT ON; SET TRANSACTION ISOLATION LEVEL SERIALIZABLE;"
                )
                for key in approved_keys:
                    operation = operation_map[key]
                    row = cursor.execute(
                        f"""
                        SELECT saga_row.run_id, saga_row.task_type, saga_row.account_key,
                               saga_row.business_key,
                               saga_row.state, saga_row.ali1688_status,
                               saga_row.jushuitan_status, saga_row.error_code,
                               (SELECT COUNT_BIG(1)
                                  FROM {outbox} AS outbox_row WITH (UPDLOCK, HOLDLOCK)
                                 WHERE outbox_row.operation_key = saga_row.operation_key),
                               (SELECT COUNT_BIG(1)
                                  FROM {item} AS source_item WITH (UPDLOCK, HOLDLOCK)
                                 WHERE source_item.task_key = saga_row.operation_key
                                   AND source_item.run_id = ?),
                               (SELECT COUNT_BIG(1)
                                  FROM {item} AS source_item WITH (UPDLOCK, HOLDLOCK)
                                 WHERE source_item.task_key = saga_row.operation_key
                                   AND source_item.run_id = ?
                                   AND source_item.offline_status = 'failed'
                                   AND source_item.error_category = ?
                                   AND LEFT(source_item.error_message, ?) = ?),
                               (SELECT COUNT_BIG(1)
                                  FROM {item} AS successful_item WITH (UPDLOCK, HOLDLOCK)
                                 WHERE successful_item.task_key = saga_row.operation_key
                                   AND successful_item.offline_status IN ('success', 'already_offline'))
                          FROM {saga} AS saga_row WITH (UPDLOCK, HOLDLOCK)
                         WHERE saga_row.operation_key = ?
                        """,
                        (
                            source_run_id,
                            source_run_id,
                            expected_item_error_category,
                            len(expected_item_error_prefix),
                            expected_item_error_prefix,
                            key,
                        ),
                    ).fetchone()
                    if row is None:
                        raise SagaReconcileRequiredError(
                            f"interrupted_recovery_saga_missing:{key}"
                        )
                    observed = {
                        "run_id": str(row[0] or ""),
                        "task_type": str(row[1] or ""),
                        "account_key": str(row[2] or ""),
                        "business_key": str(row[3] or ""),
                        "state": str(row[4] or ""),
                        "ali1688_status": str(row[5] or ""),
                        "jushuitan_status": str(row[6] or ""),
                        "error_code": str(row[7] or ""),
                        "outbox_count": int(row[8] or 0),
                        "source_item_count": int(row[9] or 0),
                        "eligible_source_item_count": int(row[10] or 0),
                        "successful_item_count": int(row[11] or 0),
                    }
                    expected = {
                        "run_id": source_run_id,
                        "task_type": "stop_sale",
                        "account_key": operation.account_key,
                        "business_key": operation.business_key,
                        "state": "failed_terminal",
                        "ali1688_status": "failed",
                        "jushuitan_status": "",
                        "error_code": expected_error_code,
                        "outbox_count": 0,
                        "source_item_count": 1,
                        "eligible_source_item_count": 1,
                        "successful_item_count": 0,
                    }
                    changed = [name for name, value in expected.items() if observed[name] != value]
                    if changed:
                        raise SagaReconcileRequiredError(
                            "interrupted_recovery_precondition_changed:"
                            f"{key}:" + ",".join(changed)
                        )

                for key in approved_keys:
                    operation = operation_map[key]
                    payload = dict(operation.payload)
                    payload["targeted_recovery_source_run_id"] = source_run_id
                    payload["targeted_recovery_approval_sha256"] = recovery_hashes[
                        "approval_sha256"
                    ]
                    payload["targeted_recovery_input_sha256"] = recovery_hashes[
                        "input_sha256"
                    ]
                    cursor.execute(
                        f"""
                        UPDATE saga_row
                           SET run_id = ?, state = 'prepared', owner_token_hash = ?,
                               account_fencing_token = ?, browser_slot_key = ?,
                               browser_slot_fencing_token = ?, payload_json = ?,
                               evidence_json = NULL, error_code = NULL,
                               error_summary = NULL, prepared_at = SYSUTCDATETIME(),
                               ali1688_status = NULL, jushuitan_status = NULL,
                               ali1688_finished_at = NULL, finished_at = NULL,
                               updated_at = SYSUTCDATETIME()
                          FROM {saga} AS saga_row
                         WHERE saga_row.operation_key = ?
                           AND saga_row.run_id = ?
                           AND saga_row.task_type = 'stop_sale'
                           AND saga_row.account_key = ?
                           AND saga_row.state = 'failed_terminal'
                           AND saga_row.ali1688_status = 'failed'
                           AND saga_row.jushuitan_status IS NULL
                           AND saga_row.error_code = ?
                           AND NOT EXISTS (
                               SELECT 1 FROM {outbox} AS outbox_row
                                WHERE outbox_row.operation_key = saga_row.operation_key
                           )
                           AND EXISTS (
                               SELECT 1 FROM {item} AS source_item
                                WHERE source_item.task_key = saga_row.operation_key
                                  AND source_item.run_id = ?
                                  AND source_item.offline_status = 'failed'
                                  AND source_item.error_category = ?
                                  AND LEFT(source_item.error_message, ?) = ?
                           )
                           AND 1 = (
                               SELECT COUNT_BIG(1) FROM {item} AS source_item
                                WHERE source_item.task_key = saga_row.operation_key
                                  AND source_item.run_id = ?
                           )
                           AND NOT EXISTS (
                               SELECT 1 FROM {item} AS successful_item
                                WHERE successful_item.task_key = saga_row.operation_key
                                  AND successful_item.offline_status IN ('success', 'already_offline')
                           )
                        """,
                        (
                            operation.run_id,
                            owner_token_hash(owner_token),
                            account_fencing_token,
                            browser_slot_key,
                            browser_slot_fencing_token,
                            json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str),
                            key,
                            source_run_id,
                            operation.account_key,
                            expected_error_code,
                            source_run_id,
                            expected_item_error_category,
                            len(expected_item_error_prefix),
                            expected_item_error_prefix,
                            source_run_id,
                        ),
                    )
                    if cursor.rowcount != 1:
                        raise SagaFencingError(
                            f"interrupted_recovery_compare_and_set_failed:{key}"
                        )
                connection.commit()
            except Exception:
                rollback = getattr(connection, "rollback", None)
                if callable(rollback):
                    rollback()
                raise
        return {key: "prepared" for key in approved_keys}

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
                    finished_at = CASE
                        WHEN ? IN ('completed', 'failed_terminal')
                        THEN COALESCE(finished_at, SYSUTCDATETIME())
                        ELSE finished_at
                    END,
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

    def claim_outbox_exact(
        self,
        *,
        claim_owner: str,
        run_id: str,
        topic: str,
        operation_keys: Iterable[str],
        claim_seconds: int = 1800,
        expected_expired_claim_owner: str = "",
        expected_expired_claim_attempt_count: int | None = None,
        expired_claim_recovery_evidence: Mapping[str, Any] | None = None,
    ) -> list[OutboxItem]:
        approved_keys = sorted({str(key or "").strip().lower() for key in operation_keys})
        if not approved_keys:
            raise ValueError("Exact Outbox claim requires at least one operation_key")
        if any(
            len(key) != 64 or any(char not in "0123456789abcdef" for char in key)
            for key in approved_keys
        ):
            raise ValueError("Exact Outbox claim requires lowercase SHA-256 operation_keys")
        if not str(run_id or "").strip() or not str(topic or "").strip():
            raise ValueError("Exact Outbox claim requires run_id and topic")
        expected_expired_owner = str(expected_expired_claim_owner or "").strip()
        recovering_expired_claim = bool(
            expected_expired_owner
            or expected_expired_claim_attempt_count is not None
            or expired_claim_recovery_evidence
        )
        if recovering_expired_claim:
            if not expected_expired_owner:
                raise ValueError("Expired Outbox claim recovery requires the previous claim owner")
            if len(expected_expired_owner) > 100:
                raise ValueError("Expired Outbox claim owner is too long")
            if (
                expected_expired_claim_attempt_count is None
                or int(expected_expired_claim_attempt_count) < 1
            ):
                raise ValueError("Expired Outbox claim recovery requires a positive attempt count")
            if not expired_claim_recovery_evidence:
                raise ValueError("Expired Outbox claim recovery requires durable evidence")
            if topic == "jushuitan.cleanup_1688_link":
                recovery_task_type = "stop_sale"
                recovery_success_statuses = ("success", "already_offline")
            elif topic == "jushuitan.sync_1688_link":
                recovery_task_type = "sku_replace"
                recovery_success_statuses = ("success", "already_replaced")
            else:
                raise ValueError("Expired Outbox claim recovery does not support this topic")
        else:
            recovery_task_type = ""
            recovery_success_statuses = ("", "")
        recovery_summary = ""
        if recovering_expired_claim:
            recovery_summary = json.dumps(
                dict(expired_claim_recovery_evidence or {}),
                ensure_ascii=False,
                sort_keys=True,
                default=str,
            )
            if len(recovery_summary) > 2000:
                raise ValueError("Expired Outbox claim recovery evidence is too long")

        outbox = self._table("ali1688_operation_outbox")
        saga = self._table("ali1688_operation_saga")
        claim_token = uuid.uuid4().hex
        with self._connect(self.config) as connection:
            cursor = connection.cursor()
            try:
                cursor.execute(
                    "CREATE TABLE #approved_operation_keys "
                    "(operation_key CHAR(64) NOT NULL PRIMARY KEY)"
                )
                cursor.executemany(
                    "INSERT INTO #approved_operation_keys (operation_key) VALUES (?)",
                    [(key,) for key in approved_keys],
                )
                if recovering_expired_claim:
                    eligibility_sql = """
                           outbox_row.status = 'claimed'
                       AND outbox_row.attempt_count = ?
                       AND outbox_row.claim_owner = ?
                       AND outbox_row.claim_token IS NOT NULL
                       AND outbox_row.claim_until IS NOT NULL
                       AND outbox_row.claim_until < SYSUTCDATETIME()
                       AND saga_row.state = 'jushuitan_pending'
                       AND saga_row.task_type = ?
                       AND saga_row.ali1688_status IN (?, ?)
                       AND saga_row.jushuitan_status = 'pending'
                    """
                    eligibility_params: tuple[Any, ...] = (
                        int(expected_expired_claim_attempt_count),
                        expected_expired_owner,
                        recovery_task_type,
                        *recovery_success_statuses,
                    )
                else:
                    eligibility_sql = """
                           outbox_row.status IN ('pending', 'failed_retryable')
                       AND outbox_row.available_at <= SYSUTCDATETIME()
                       AND (outbox_row.claim_until IS NULL
                            OR outbox_row.claim_until < SYSUTCDATETIME())
                    """
                    eligibility_params = ()
                validation_query = f"""
                    SELECT approved.operation_key,
                           CASE WHEN outbox_row.outbox_id IS NULL THEN 0 ELSE 1 END AS exists_flag,
                           ISNULL(outbox_row.status, ''), ISNULL(outbox_row.topic, ''),
                           ISNULL(saga_row.run_id, ''),
                           CASE WHEN {eligibility_sql}
                                THEN 1 ELSE 0 END AS eligible_flag
                      FROM #approved_operation_keys AS approved
                      LEFT JOIN {outbox} AS outbox_row WITH (UPDLOCK, HOLDLOCK)
                        ON outbox_row.operation_key = approved.operation_key
                     LEFT JOIN {saga} AS saga_row WITH (UPDLOCK, HOLDLOCK)
                        ON saga_row.operation_key = approved.operation_key
                     ORDER BY approved.operation_key
                    """
                validation_cursor = (
                    cursor.execute(validation_query, eligibility_params)
                    if eligibility_params
                    else cursor.execute(validation_query)
                )
                validation_rows = validation_cursor.fetchall()
                observed_keys = {str(row[0]) for row in validation_rows}
                if observed_keys != set(approved_keys) or len(validation_rows) != len(approved_keys):
                    raise SagaReconcileRequiredError("outbox_approved_scope_query_mismatch")
                invalid = [
                    str(row[0])
                    for row in validation_rows
                    if int(row[1]) != 1
                    or str(row[3]) != topic
                    or str(row[4]) != run_id
                    or int(row[5]) != 1
                ]
                if invalid:
                    raise SagaReconcileRequiredError(
                        "outbox_approved_scope_not_claimable:" + ",".join(invalid)
                    )

                if recovering_expired_claim:
                    rows = cursor.execute(
                        f"""
                        UPDATE outbox_row
                           SET status = 'claimed', claim_owner = ?, claim_token = ?,
                               claim_until = DATEADD(SECOND, ?, SYSUTCDATETIME()),
                               attempt_count = attempt_count + 1,
                               last_error_code = 'expired_claim_recovered',
                               last_error_summary = ?, updated_at = SYSUTCDATETIME()
                        OUTPUT inserted.outbox_id, inserted.operation_key, inserted.topic,
                               inserted.payload_json, inserted.attempt_count, inserted.claim_token
                          FROM {outbox} AS outbox_row
                          JOIN #approved_operation_keys AS approved
                            ON approved.operation_key = outbox_row.operation_key
                          JOIN {saga} AS saga_row
                            ON saga_row.operation_key = outbox_row.operation_key
                         WHERE outbox_row.status = 'claimed'
                           AND outbox_row.attempt_count = ?
                           AND outbox_row.claim_owner = ?
                           AND outbox_row.claim_token IS NOT NULL
                           AND outbox_row.claim_until IS NOT NULL
                           AND outbox_row.claim_until < SYSUTCDATETIME()
                           AND outbox_row.topic = ?
                           AND saga_row.run_id = ?
                           AND saga_row.state = 'jushuitan_pending'
                           AND saga_row.task_type = ?
                           AND saga_row.ali1688_status IN (?, ?)
                           AND saga_row.jushuitan_status = 'pending'
                        """,
                        (
                            claim_owner[:100],
                            claim_token,
                            max(1, int(claim_seconds)),
                            recovery_summary,
                            int(expected_expired_claim_attempt_count),
                            expected_expired_owner,
                            topic,
                            run_id,
                            recovery_task_type,
                            *recovery_success_statuses,
                        ),
                    ).fetchall()
                else:
                    rows = cursor.execute(
                        f"""
                        UPDATE outbox_row
                           SET status = 'claimed', claim_owner = ?, claim_token = ?,
                               claim_until = DATEADD(SECOND, ?, SYSUTCDATETIME()),
                               attempt_count = attempt_count + 1,
                               updated_at = SYSUTCDATETIME()
                        OUTPUT inserted.outbox_id, inserted.operation_key, inserted.topic,
                               inserted.payload_json, inserted.attempt_count, inserted.claim_token
                          FROM {outbox} AS outbox_row
                          JOIN #approved_operation_keys AS approved
                            ON approved.operation_key = outbox_row.operation_key
                          JOIN {saga} AS saga_row
                            ON saga_row.operation_key = outbox_row.operation_key
                         WHERE outbox_row.status IN ('pending', 'failed_retryable')
                           AND outbox_row.available_at <= SYSUTCDATETIME()
                           AND (outbox_row.claim_until IS NULL
                                OR outbox_row.claim_until < SYSUTCDATETIME())
                           AND outbox_row.topic = ?
                           AND saga_row.run_id = ?
                        """,
                        (
                            claim_owner[:100],
                            claim_token,
                            max(1, int(claim_seconds)),
                            topic,
                            run_id,
                        ),
                    ).fetchall()
                claimed_keys = {str(row[1]) for row in rows}
                if claimed_keys != set(approved_keys) or len(rows) != len(approved_keys):
                    raise SagaFencingError("outbox_exact_claim_compare_and_set_failed")
                connection.commit()
            except Exception:
                rollback = getattr(connection, "rollback", None)
                if callable(rollback):
                    rollback()
                raise
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

    def get_outbox_state(self, operation_key: str) -> dict[str, Any]:
        outbox = self._table("ali1688_operation_outbox")
        saga = self._table("ali1688_operation_saga")
        with self._connect(self.config) as connection:
            row = connection.cursor().execute(
                f"""
                SELECT outbox_row.outbox_id, outbox_row.status, outbox_row.attempt_count,
                       outbox_row.last_error_code, outbox_row.last_error_summary,
                       saga_row.state, saga_row.run_id
                  FROM {outbox} AS outbox_row
                  JOIN {saga} AS saga_row ON saga_row.operation_key = outbox_row.operation_key
                 WHERE outbox_row.operation_key = ?
                """,
                (operation_key,),
            ).fetchone()
        if row is None:
            raise SagaReconcileRequiredError("outbox_operation_missing")
        return {
            "outbox_id": int(row[0]),
            "operation_key": operation_key,
            "status": str(row[1] or ""),
            "attempt_count": int(row[2] or 0),
            "last_error_code": str(row[3] or ""),
            "last_error_summary": str(row[4] or ""),
            "saga_state": str(row[5] or ""),
            "run_id": str(row[6] or ""),
        }

    def requeue_outbox(
        self,
        operation_key: str,
        *,
        expected_status: str,
        expected_error_code: str,
        expected_run_id: str,
        expected_attempt_count: int,
        expected_saga_state: str,
        reason: str,
    ) -> dict[str, Any]:
        if expected_status not in {"failed_retryable", "failed_terminal"}:
            raise ValueError("Only failed Outbox rows may be requeued")
        if expected_saga_state not in {"failed_retryable", "failed_terminal"}:
            raise ValueError("Only failed Saga rows may be requeued")
        if expected_attempt_count < 0:
            raise ValueError("Expected Outbox attempt_count must be non-negative")
        if not expected_error_code or not expected_run_id or not str(reason or "").strip():
            raise ValueError("Controlled requeue requires exact error, run, and reason values")
        outbox = self._table("ali1688_operation_outbox")
        saga = self._table("ali1688_operation_saga")
        with self._connect(self.config) as connection:
            cursor = connection.cursor()
            row = cursor.execute(
                f"""
                SELECT outbox_row.outbox_id, outbox_row.status, outbox_row.attempt_count,
                       outbox_row.last_error_code, outbox_row.last_error_summary,
                       saga_row.state, saga_row.run_id
                  FROM {outbox} AS outbox_row WITH (UPDLOCK, HOLDLOCK)
                  JOIN {saga} AS saga_row WITH (UPDLOCK, HOLDLOCK)
                    ON saga_row.operation_key = outbox_row.operation_key
                 WHERE outbox_row.operation_key = ?
                """,
                (operation_key,),
            ).fetchone()
            if row is None:
                raise SagaReconcileRequiredError("outbox_operation_missing")
            current_status = str(row[1] or "")
            current_attempt_count = int(row[2] or 0)
            current_error_code = str(row[3] or "")
            saga_state = str(row[5] or "")
            current_run_id = str(row[6] or "")
            if current_status != expected_status:
                raise SagaReconcileRequiredError(
                    f"outbox_status_changed:{current_status}"
                )
            if current_error_code != expected_error_code:
                raise SagaReconcileRequiredError(
                    f"outbox_error_changed:{current_error_code}"
                )
            if current_attempt_count != expected_attempt_count:
                raise SagaReconcileRequiredError(
                    f"outbox_attempt_count_changed:{current_attempt_count}"
                )
            if current_run_id != expected_run_id:
                raise SagaReconcileRequiredError(f"saga_run_id_changed:{current_run_id}")
            if saga_state != expected_saga_state:
                raise SagaReconcileRequiredError(f"saga_state_changed:{saga_state}")
            cursor.execute(
                f"""
                UPDATE {outbox}
                   SET status = 'failed_retryable', claim_owner = NULL, claim_token = NULL,
                       claim_until = NULL, available_at = SYSUTCDATETIME(),
                       completed_at = NULL, last_error_code = 'controlled_requeue',
                       last_error_summary = ?, updated_at = SYSUTCDATETIME()
                 WHERE operation_key = ? AND status = ? AND attempt_count = ?
                   AND ISNULL(last_error_code, '') = ?
                """,
                (
                    str(reason).strip()[:2000],
                    operation_key,
                    expected_status,
                    expected_attempt_count,
                    expected_error_code,
                ),
            )
            if cursor.rowcount != 1:
                raise SagaFencingError("outbox_requeue_compare_and_set_failed")
            cursor.execute(
                f"""
                UPDATE {saga}
                   SET state = 'failed_retryable', jushuitan_status = 'failed_retryable',
                       error_code = 'controlled_requeue', error_summary = ?,
                       finished_at = NULL, updated_at = SYSUTCDATETIME()
                 WHERE operation_key = ? AND state = ? AND run_id = ?
                """,
                (str(reason).strip()[:2000], operation_key, saga_state, expected_run_id),
            )
            if cursor.rowcount != 1:
                raise SagaFencingError("saga_requeue_compare_and_set_failed")
            connection.commit()
        return {
            "operation_key": operation_key,
            "previous_status": current_status,
            "previous_error_code": current_error_code,
            "previous_attempt_count": current_attempt_count,
            "run_id": current_run_id,
            "status": "failed_retryable",
            "reason": str(reason).strip(),
        }

    def terminalize_outbox(
        self,
        operation_key: str,
        *,
        expected_status: str,
        expected_error_code: str,
        expected_run_id: str,
        expected_attempt_count: int,
        expected_saga_state: str,
        reason: str,
    ) -> dict[str, Any]:
        """CAS a verified Jushuitan task absence to a terminal outcome.

        This operation is intentionally narrower than ``finish_outbox`` and
        ``requeue_outbox``.  It is for a previously observed, exact
        ``task_not_found`` result only; it must never turn a changed row into a
        terminal state based on a stale recovery report.
        """
        normalized_key = str(operation_key or "").strip().lower()
        if len(normalized_key) != 64 or any(
            char not in "0123456789abcdef" for char in normalized_key
        ):
            raise ValueError("operation_key must be a 64-character lowercase SHA-256 value")
        if expected_status != "failed_retryable":
            raise ValueError("Only failed_retryable Outbox rows may be terminalized")
        if expected_error_code != "task_not_found":
            raise ValueError("Only task_not_found rows may be terminalized")
        if expected_saga_state not in {"failed_retryable", "jushuitan_pending"}:
            raise ValueError("Unsupported Saga state for task_not_found terminalization")
        if expected_attempt_count < 0:
            raise ValueError("Expected Outbox attempt_count must be non-negative")
        normalized_run_id = str(expected_run_id or "").strip()
        normalized_reason = str(reason or "").strip()
        if not normalized_run_id or not normalized_reason:
            raise ValueError("Terminalization requires exact run_id and non-empty reason")

        outbox = self._table("ali1688_operation_outbox")
        saga = self._table("ali1688_operation_saga")
        with self._connect(self.config) as connection:
            cursor = connection.cursor()
            try:
                row = cursor.execute(
                    f"""
                    SELECT outbox_row.outbox_id, outbox_row.status,
                           outbox_row.attempt_count, outbox_row.last_error_code,
                           outbox_row.last_error_summary,
                           saga_row.state, saga_row.run_id
                      FROM {outbox} AS outbox_row WITH (UPDLOCK, HOLDLOCK)
                      JOIN {saga} AS saga_row WITH (UPDLOCK, HOLDLOCK)
                        ON saga_row.operation_key = outbox_row.operation_key
                     WHERE outbox_row.operation_key = ?
                    """,
                    (normalized_key,),
                ).fetchone()
                if row is None:
                    raise SagaReconcileRequiredError("outbox_operation_missing")

                current_status = str(row[1] or "")
                current_attempt_count = int(row[2] or 0)
                current_error_code = str(row[3] or "")
                current_error_summary = str(row[4] or "").strip()
                current_saga_state = str(row[5] or "")
                current_run_id = str(row[6] or "")
                if current_status != expected_status:
                    raise SagaReconcileRequiredError(
                        f"outbox_status_changed:{current_status}"
                    )
                if current_error_code != expected_error_code:
                    raise SagaReconcileRequiredError(
                        f"outbox_error_changed:{current_error_code}"
                    )
                if current_attempt_count != expected_attempt_count:
                    raise SagaReconcileRequiredError(
                        f"outbox_attempt_count_changed:{current_attempt_count}"
                    )
                if current_run_id != normalized_run_id:
                    raise SagaReconcileRequiredError(
                        f"saga_run_id_changed:{current_run_id}"
                    )
                if current_saga_state != expected_saga_state:
                    raise SagaReconcileRequiredError(
                        f"saga_state_changed:{current_saga_state}"
                    )

                terminal_summary = normalized_reason[:2000]
                if current_error_summary:
                    suffix = f" | terminalized: {normalized_reason}"
                    terminal_summary = (current_error_summary + suffix)[:2000]

                cursor.execute(
                    f"""
                    UPDATE {outbox}
                       SET status = 'failed_terminal',
                           claim_owner = NULL, claim_token = NULL, claim_until = NULL,
                           last_error_code = ?, last_error_summary = ?,
                           completed_at = SYSUTCDATETIME(),
                           updated_at = SYSUTCDATETIME()
                     WHERE operation_key = ?
                       AND status = ?
                       AND attempt_count = ?
                       AND ISNULL(last_error_code, '') = ?
                    """,
                    (
                        expected_error_code,
                        terminal_summary,
                        normalized_key,
                        expected_status,
                        expected_attempt_count,
                        expected_error_code,
                    ),
                )
                if cursor.rowcount != 1:
                    raise SagaFencingError("outbox_terminalize_compare_and_set_failed")

                cursor.execute(
                    f"""
                    UPDATE {saga}
                       SET state = 'failed_terminal',
                           jushuitan_status = 'failed_terminal',
                           error_code = ?, error_summary = ?,
                           finished_at = SYSUTCDATETIME(),
                           updated_at = SYSUTCDATETIME()
                     WHERE operation_key = ?
                       AND state = ?
                       AND run_id = ?
                    """,
                    (
                        expected_error_code,
                        terminal_summary,
                        normalized_key,
                        expected_saga_state,
                        normalized_run_id,
                    ),
                )
                if cursor.rowcount != 1:
                    raise SagaFencingError("saga_terminalize_compare_and_set_failed")
                connection.commit()
            except Exception:
                connection.rollback()
                raise

        return {
            "operation_key": normalized_key,
            "previous_status": current_status,
            "previous_error_code": current_error_code,
            "previous_error_summary": current_error_summary,
            "previous_attempt_count": current_attempt_count,
            "previous_saga_state": current_saga_state,
            "run_id": current_run_id,
            "status": "failed_terminal",
            "error_code": expected_error_code,
            "error_summary": terminal_summary,
            "reason": normalized_reason,
        }
