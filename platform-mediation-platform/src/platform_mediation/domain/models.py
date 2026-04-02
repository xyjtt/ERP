from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from platform_mediation.domain.enums import (
    AttemptStatus,
    ExecutorType,
    ReflowStatus,
    TaskItemStatus,
    TaskStatus,
    TaskType,
)


JsonDict = dict[str, Any]


@dataclass
class Task:
    task_id: str
    task_type: TaskType
    platform: str
    shop_id: int
    status: TaskStatus
    created_by: str
    executor_type: ExecutorType
    priority: int = 0
    scheduled_at: datetime | None = None
    next_retry_at: datetime | None = None
    idempotency_key: str | None = None
    adapter_code: str | None = None
    mapping_version: str | None = None
    payload_json: JsonDict = field(default_factory=dict)
    remark: str | None = None
    total_count: int = 0
    success_count: int = 0
    failed_count: int = 0
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    started_at: datetime | None = None
    completed_at: datetime | None = None
    pk: int = 0


@dataclass
class TaskItem:
    sku_id: str
    status: TaskItemStatus
    task_pk: int = 0
    platform_sku_id: str | None = None
    platform_item_id: str | None = None
    source_snapshot_pk: int | None = None
    payload_json: JsonDict = field(default_factory=dict)
    mapping_version: str | None = None
    source_version: str | None = None
    result_json: JsonDict = field(default_factory=dict)
    verification_status: str | None = None
    verification_message: str | None = None
    error_message: str | None = None
    attempt_count: int = 0
    started_at: datetime | None = None
    completed_at: datetime | None = None
    pk: int = 0


@dataclass
class SourceSnapshot:
    snapshot_id: str
    sku_id: str
    source_type: str
    source_record_key: str
    snapshot_json: JsonDict
    version: str
    task_pk: int | None = None
    task_item_pk: int | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    pk: int = 0


@dataclass
class TaskAttempt:
    attempt_id: str
    task_pk: int
    task_item_pk: int | None
    attempt_no: int
    adapter_code: str
    adapter_version: str
    mapping_version: str
    source_version: str
    status: AttemptStatus
    source_snapshot_pk: int | None = None
    worker_node_pk: int | None = None
    error_type: str | None = None
    error_message: str | None = None
    retry_reason: str | None = None
    raw_result_json: JsonDict = field(default_factory=dict)
    started_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    completed_at: datetime | None = None
    duration_ms: int | None = None
    pk: int = 0


@dataclass
class TaskArtifact:
    artifact_id: str
    task_attempt_pk: int
    task_pk: int
    task_item_pk: int | None
    artifact_type: str
    artifact_path: str
    metadata_json: JsonDict = field(default_factory=dict)
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    pk: int = 0


@dataclass
class ReflowEvent:
    event_id: str
    event_type: str
    platform: str
    shop_id: int
    sku_id: str
    payload_json: JsonDict
    status: ReflowStatus
    task_pk: int | None = None
    task_item_pk: int | None = None
    platform_item_id: str | None = None
    platform_sku_id: str | None = None
    idempotency_key: str | None = None
    retry_count: int = 0
    next_retry_at: datetime | None = None
    error_message: str | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    pk: int = 0


@dataclass(frozen=True)
class AdapterArtifact:
    artifact_type: str
    artifact_path: str
    metadata: JsonDict = field(default_factory=dict)


@dataclass(frozen=True)
class AdapterExecutionRequest:
    task_id: str
    task_item_id: str
    platform: str
    action: TaskType
    executor_type: ExecutorType
    shop_id: int
    mapping_version: str
    source_snapshot_id: str
    payload: JsonDict


@dataclass(frozen=True)
class AdapterExecutionResult:
    status: str
    error_type: str | None
    error_message: str | None
    platform_item_id: str | None
    platform_sku_id: str | None
    result_payload: JsonDict
    artifacts: list[AdapterArtifact]
    raw_result: JsonDict
