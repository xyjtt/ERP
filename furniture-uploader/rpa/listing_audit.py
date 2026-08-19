"""Formal JSReportReplica.app persistence for guarded 1688 listing tasks."""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timezone
from typing import Any, Callable, Mapping

from stop_sale_audit import (
    StopSaleAppConfig,
    _validate_identifier,
    connect_app_database,
)
from listing_review import canonical_sha256


def _text(value: Any, limit: int) -> str:
    return str(value or "").strip()[:limit]


def _approval_details(payload: Mapping[str, Any]) -> tuple[str, str, datetime | None]:
    workflow = dict(payload.get("workflow") or {})
    status = "pending"
    approved_by = ""
    approved_at: datetime | None = None
    for event in list(workflow.get("event_history") or []):
        if not isinstance(event, Mapping):
            continue
        event_name = str(event.get("event") or "").strip()
        evidence = dict(event.get("evidence") or {})
        if event_name == "review_rejected":
            status = "rejected"
            approved_by = ""
            approved_at = None
        elif event_name == "review_approved":
            status = "approved"
            approved_by = _text(evidence.get("approved_by"), 150)
            recorded_at = str(event.get("recorded_at") or "").strip()
            try:
                approved_at = datetime.fromisoformat(recorded_at.replace("Z", "+00:00"))
                if approved_at.tzinfo is not None:
                    approved_at = approved_at.astimezone(timezone.utc).replace(tzinfo=None)
            except ValueError:
                approved_at = None
    return status, approved_by, approved_at


def build_listing_task_record(payload: Mapping[str, Any]) -> dict[str, Any]:
    task_id = _text(payload.get("task_id"), 100)
    shop = dict(payload.get("shop") or {})
    source = dict(payload.get("source") or {})
    product = dict(payload.get("product") or {})
    workflow = dict(payload.get("workflow") or {})
    approval_status, approved_by, approved_at = _approval_details(payload)
    identity = "|".join(
        [
            _text(payload.get("platform"), 30).lower(),
            _text(shop.get("account_key"), 100).lower(),
            _text(source.get("company_sku"), 150).upper(),
            _text(source.get("novelty_type"), 30).lower(),
            str(bool(workflow.get("independent_link_required"))).lower(),
        ]
    )
    return {
        "task_id": task_id,
        "schema_version": _text(payload.get("schema_version"), 50),
        "platform": _text(payload.get("platform"), 30),
        "shop_name": _text(shop.get("shop_name"), 200),
        "account_key": _text(shop.get("account_key"), 100),
        "company_sku": _text(source.get("company_sku"), 150),
        "company_spu": _text(source.get("company_spu"), 150),
        "novelty_type": _text(source.get("novelty_type"), 30),
        "selected_title": _text(product.get("selected_title"), 500),
        "workflow_state": _text(workflow.get("state"), 50),
        "approval_status": approval_status,
        "approved_by": approved_by or None,
        "approved_at": approved_at,
        "idempotency_key": hashlib.sha256(identity.encode("utf-8")).hexdigest(),
        "payload_json": json.dumps(payload, ensure_ascii=False, default=str),
        "preflight_json": json.dumps(payload.get("preflight") or {}, ensure_ascii=False, default=str),
    }


class ListingAuditRepository:
    REQUIRED_TABLES = (
        "ali1688_listing_task",
        "ali1688_listing_execution",
        "ali1688_listing_audit",
    )

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
            missing = []
            for table in self.REQUIRED_TABLES:
                object_name = f"{self.schema}.{table}"
                if cursor.execute("SELECT OBJECT_ID(?, 'U')", (object_name,)).fetchone()[0] is None:
                    missing.append(object_name)
        if database_name != self.config.database:
            raise RuntimeError(f"Listing audit connected to unexpected database: {database_name}")
        return {
            "database": database_name,
            "schema": self.schema,
            "missing_tables": missing,
            "ready": not missing,
        }

    def count_active_drafts(self, *, account_key: str) -> int:
        """Count drafts already created (saved/pending review/blocked) for a shop.

        Business rule: per-shop draft cap (default 20); once reached, new
        candidates for that shop are skipped instead of creating more drafts.
        """
        table = self._table("ali1688_listing_task")
        with self._connect(self.config) as connection:
            cursor = connection.cursor()
            row = cursor.execute(
                f"""
                SELECT COUNT_BIG(1)
                FROM {table}
                WHERE account_key = ?
                  AND workflow_state IN (N'draft_saved', N'draft_pending_review', N'blocked')
                """,
                (_text(account_key, 100),),
            ).fetchone()
        return int(row[0] or 0)

    def find_existing_task(
        self,
        *,
        task_id: str,
        idempotency_key: str,
    ) -> dict[str, Any] | None:
        table = self._table("ali1688_listing_task")
        with self._connect(self.config) as connection:
            cursor = connection.cursor()
            row = cursor.execute(
                f"""
                SELECT TOP 1 task_id, idempotency_key, account_key, shop_name,
                    company_sku, workflow_state, approval_status, updated_at
                FROM {table}
                WHERE task_id = ? OR idempotency_key = ?
                ORDER BY CASE WHEN task_id = ? THEN 0 ELSE 1 END, updated_at DESC
                """,
                (
                    _text(task_id, 100),
                    _text(idempotency_key, 200),
                    _text(task_id, 100),
                ),
            ).fetchone()
            if row is None:
                return None
            columns = [str(item[0]) for item in cursor.description]
        return dict(zip(columns, row))

    def claim_scheduled_task(
        self,
        payload: Mapping[str, Any],
        *,
        claim_owner: str,
        claim_seconds: int,
    ) -> dict[str, Any]:
        record = build_listing_task_record(payload)
        owner = _text(claim_owner, 150)
        if not owner or int(claim_seconds) <= 0:
            raise ValueError("scheduled listing claim requires an owner and positive TTL")
        table = self._table("ali1688_listing_task")
        with self._connect(self.config) as connection:
            cursor = connection.cursor()
            row = cursor.execute(
                f"""
                SELECT TOP 1 task_id, idempotency_key, workflow_state, claimed_by,
                    claim_until, payload_json,
                    CASE WHEN claim_until > SYSUTCDATETIME() THEN 1 ELSE 0 END AS claim_active
                FROM {table} WITH (UPDLOCK, HOLDLOCK)
                WHERE task_id = ? OR idempotency_key = ?
                ORDER BY CASE WHEN task_id = ? THEN 0 ELSE 1 END, updated_at DESC
                """,
                (record["task_id"], record["idempotency_key"], record["task_id"]),
            ).fetchone()
            if row is not None:
                same_task = str(row[0] or "") == record["task_id"]
                same_idempotency = str(row[1] or "") == record["idempotency_key"]
                reclaimable = (
                    same_task
                    and same_idempotency
                    and str(row[2] or "") == "draft_pending"
                    and not bool(row[6])
                )
                if reclaimable:
                    cursor.execute(
                        f"""
                        UPDATE {table}
                        SET payload_json = ?, preflight_json = ?, claimed_by = ?,
                            claim_until = DATEADD(SECOND, ?, SYSUTCDATETIME()),
                            updated_at = SYSUTCDATETIME()
                        WHERE task_id = ? AND idempotency_key = ?
                          AND workflow_state = 'draft_pending'
                          AND (claim_until IS NULL OR claim_until <= SYSUTCDATETIME())
                        """,
                        (
                            record["payload_json"],
                            record["preflight_json"],
                            owner,
                            int(claim_seconds),
                            record["task_id"],
                            record["idempotency_key"],
                        ),
                    )
                    if cursor.rowcount == 1:
                        connection.commit()
                        return {
                            "status": "claimed",
                            "task_id": record["task_id"],
                            "idempotency_key": record["idempotency_key"],
                            "claimed_by": owner,
                            "payload_sha256": canonical_sha256(payload),
                            "reclaimed": True,
                        }
                connection.commit()
                return {
                    "status": "duplicate_existing",
                    "task_id": str(row[0] or ""),
                    "idempotency_key": str(row[1] or ""),
                    "workflow_state": str(row[2] or ""),
                    "claimed_by": str(row[3] or ""),
                    "claim_until": row[4],
                }
            cursor.execute(
                f"""
                INSERT INTO {table} (
                    task_id, schema_version, platform, shop_name, account_key,
                    company_sku, company_spu, novelty_type, selected_title,
                    workflow_state, approval_status, approved_by, approved_at,
                    idempotency_key, payload_json, preflight_json, claimed_by, claim_until
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                    DATEADD(SECOND, ?, SYSUTCDATETIME()))
                """,
                tuple(
                    record[name]
                    for name in (
                        "task_id",
                        "schema_version",
                        "platform",
                        "shop_name",
                        "account_key",
                        "company_sku",
                        "company_spu",
                        "novelty_type",
                        "selected_title",
                        "workflow_state",
                        "approval_status",
                        "approved_by",
                        "approved_at",
                        "idempotency_key",
                        "payload_json",
                        "preflight_json",
                    )
                )
                + (owner, int(claim_seconds)),
            )
            connection.commit()
        return {
            "status": "claimed",
            "task_id": record["task_id"],
            "idempotency_key": record["idempotency_key"],
            "claimed_by": owner,
            "payload_sha256": canonical_sha256(payload),
        }

    def assert_execution_payload_current(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        task_id = _text(payload.get("task_id"), 100)
        if not task_id:
            raise ValueError("listing execution payload requires task_id")
        schedule = dict(payload.get("schedule") or {})
        expected_claim_owner = _text(schedule.get("claim_owner"), 150)
        table = self._table("ali1688_listing_task")
        with self._connect(self.config) as connection:
            cursor = connection.cursor()
            row = cursor.execute(
                f"""
                SELECT task_id, idempotency_key, workflow_state, claimed_by,
                    claim_until, payload_json,
                    CASE WHEN claim_until > SYSUTCDATETIME() THEN 1 ELSE 0 END AS claim_active
                FROM {table} WITH (UPDLOCK, HOLDLOCK)
                WHERE task_id = ?
                """,
                (task_id,),
            ).fetchone()
            if row is None:
                raise RuntimeError("listing execution task is missing from the formal audit table")
            try:
                current_payload = json.loads(str(row[5] or ""))
            except json.JSONDecodeError as exc:
                raise RuntimeError("formal listing task payload_json is invalid") from exc
            if canonical_sha256(current_payload) != canonical_sha256(payload):
                raise RuntimeError("listing execution payload is stale; reload the formal task before retry")
            actual_claim_owner = str(row[3] or "").strip()
            if expected_claim_owner and actual_claim_owner != expected_claim_owner:
                raise RuntimeError("listing execution claim owner does not match the formal task")
            if expected_claim_owner and not bool(row[6]):
                raise RuntimeError("listing execution claim has expired")
            connection.commit()
        return {
            "task_id": str(row[0] or ""),
            "idempotency_key": str(row[1] or ""),
            "workflow_state": str(row[2] or ""),
            "claimed_by": actual_claim_owner,
            "claim_until": row[4],
            "payload_sha256": canonical_sha256(current_payload),
        }

    def register_execution_payload(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        record = build_listing_task_record(payload)
        table = self._table("ali1688_listing_task")
        with self._connect(self.config) as connection:
            cursor = connection.cursor()
            row = cursor.execute(
                f"""
                SELECT task_id, idempotency_key, payload_json
                FROM {table} WITH (UPDLOCK, HOLDLOCK)
                WHERE task_id = ? OR idempotency_key = ?
                ORDER BY CASE WHEN task_id = ? THEN 0 ELSE 1 END, updated_at DESC
                """,
                (record["task_id"], record["idempotency_key"], record["task_id"]),
            ).fetchone()
            if row is not None:
                try:
                    current_payload = json.loads(str(row[2] or ""))
                except json.JSONDecodeError as exc:
                    raise RuntimeError("formal listing task payload_json is invalid") from exc
                if str(row[0] or "") != record["task_id"]:
                    raise RuntimeError("listing idempotency key belongs to a different task_id")
                if canonical_sha256(current_payload) != canonical_sha256(payload):
                    raise RuntimeError(
                        "listing execution payload is stale; reload the formal task before retry"
                    )
                connection.commit()
                return {
                    "status": "current",
                    "task_id": record["task_id"],
                    "payload_sha256": canonical_sha256(current_payload),
                }
            cursor.execute(
                f"""
                INSERT INTO {table} (
                    task_id, schema_version, platform, shop_name, account_key,
                    company_sku, company_spu, novelty_type, selected_title,
                    workflow_state, approval_status, approved_by, approved_at,
                    idempotency_key, payload_json, preflight_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                tuple(
                    record[name]
                    for name in (
                        "task_id",
                        "schema_version",
                        "platform",
                        "shop_name",
                        "account_key",
                        "company_sku",
                        "company_spu",
                        "novelty_type",
                        "selected_title",
                        "workflow_state",
                        "approval_status",
                        "approved_by",
                        "approved_at",
                        "idempotency_key",
                        "payload_json",
                        "preflight_json",
                    )
                ),
            )
            connection.commit()
        return {
            "status": "registered",
            "task_id": record["task_id"],
            "payload_sha256": canonical_sha256(payload),
        }

    def upsert_task(self, payload: Mapping[str, Any]) -> None:
        record = build_listing_task_record(payload)
        table = self._table("ali1688_listing_task")
        with self._connect(self.config) as connection:
            cursor = connection.cursor()
            cursor.execute(
                f"""
                UPDATE {table}
                SET schema_version = ?, platform = ?, shop_name = ?, account_key = ?,
                    company_sku = ?, company_spu = ?, novelty_type = ?, selected_title = ?,
                    workflow_state = ?, approval_status = ?, approved_by = ?, approved_at = ?,
                    idempotency_key = ?, payload_json = ?, preflight_json = ?,
                    updated_at = SYSUTCDATETIME()
                WHERE task_id = ?
                """,
                (
                    record["schema_version"],
                    record["platform"],
                    record["shop_name"],
                    record["account_key"],
                    record["company_sku"],
                    record["company_spu"],
                    record["novelty_type"],
                    record["selected_title"],
                    record["workflow_state"],
                    record["approval_status"],
                    record["approved_by"],
                    record["approved_at"],
                    record["idempotency_key"],
                    record["payload_json"],
                    record["preflight_json"],
                    record["task_id"],
                ),
            )
            if cursor.rowcount == 0:
                cursor.execute(
                    f"""
                    INSERT INTO {table} (
                        task_id, schema_version, platform, shop_name, account_key,
                        company_sku, company_spu, novelty_type, selected_title,
                        workflow_state, approval_status, approved_by, approved_at,
                        idempotency_key, payload_json, preflight_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    tuple(
                        record[name]
                        for name in (
                            "task_id",
                            "schema_version",
                            "platform",
                            "shop_name",
                            "account_key",
                            "company_sku",
                            "company_spu",
                            "novelty_type",
                            "selected_title",
                            "workflow_state",
                            "approval_status",
                            "approved_by",
                            "approved_at",
                            "idempotency_key",
                            "payload_json",
                            "preflight_json",
                        )
                    ),
                )
            connection.commit()

    def record_latest_event(self, payload: Mapping[str, Any], *, operator_name: str = "") -> None:
        workflow = dict(payload.get("workflow") or {})
        events = list(workflow.get("event_history") or [])
        if not events:
            return
        event = dict(events[-1] or {})
        table = self._table("ali1688_listing_audit")
        with self._connect(self.config) as connection:
            connection.cursor().execute(
                f"""
                INSERT INTO {table} (
                    task_id, event_type, from_state, to_state, operator_name, evidence_json
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    _text(payload.get("task_id"), 100),
                    _text(event.get("event"), 60),
                    _text(event.get("from_state"), 50) or None,
                    _text(event.get("to_state"), 50) or None,
                    _text(operator_name, 150) or None,
                    json.dumps(event.get("evidence") or {}, ensure_ascii=False, default=str),
                ),
            )
            connection.commit()

    def start_execution(self, *, task_id: str, mode: str) -> str:
        execution_id = str(uuid.uuid4())
        table = self._table("ali1688_listing_execution")
        with self._connect(self.config) as connection:
            connection.cursor().execute(
                f"""
                INSERT INTO {table} (
                    task_id, execution_id, execution_mode, status, started_at
                ) VALUES (?, ?, ?, 'running', SYSUTCDATETIME())
                """,
                (
                    _text(task_id, 100),
                    execution_id,
                    _text(mode, 30),
                ),
            )
            connection.commit()
        return execution_id

    def finish_execution(
        self,
        *,
        execution_id: str,
        status: str,
        result: Mapping[str, Any] | None = None,
        error_code: str = "",
        error_summary: str = "",
    ) -> None:
        payload = dict(result or {})
        workflow = dict(payload.get("workflow") or {})
        offer = dict(workflow.get("offer") or {})
        table = self._table("ali1688_listing_execution")
        with self._connect(self.config) as connection:
            connection.cursor().execute(
                f"""
                UPDATE {table}
                SET status = ?, offer_id = ?, offer_url = ?, result_json = ?,
                    error_code = ?, error_summary = ?, finished_at = SYSUTCDATETIME()
                WHERE execution_id = ?
                """,
                (
                    _text(status, 40),
                    _text(offer.get("offer_id"), 100) or None,
                    _text(offer.get("offer_url"), 1000) or None,
                    json.dumps(payload, ensure_ascii=False, default=str) if payload else None,
                    _text(error_code, 100) or None,
                    _text(error_summary, 1000) or None,
                    execution_id,
                ),
            )
            connection.commit()
