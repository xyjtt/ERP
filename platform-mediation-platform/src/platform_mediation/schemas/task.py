from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from platform_mediation.domain.enums import ExecutorType, TaskItemStatus, TaskStatus, TaskType


class CreateTaskItemRequest(BaseModel):
    sku_id: str = Field(..., min_length=1)
    payload: dict[str, Any] | None = None
    source_type: str | None = None
    source_record_key: str | None = None
    source_version: str | None = None
    mapping_version: str | None = None


class CreateTaskRequest(BaseModel):
    task_type: TaskType
    platform: str
    shop_id: int
    executor_type: ExecutorType
    priority: int = 0
    mapping_version: str
    items: list[CreateTaskItemRequest]
    created_by: str
    adapter_code: str | None = "1688_adapter"
    scheduled_at: datetime | None = None
    idempotency_key: str | None = None
    payload: dict[str, Any] | None = None
    remark: str | None = None


class CreateTaskResponse(BaseModel):
    task_id: str
    status: TaskStatus
    item_count: int


class TaskSummaryResponse(BaseModel):
    task_id: str
    task_type: TaskType
    platform: str
    shop_id: int
    status: TaskStatus
    total_count: int
    success_count: int
    failed_count: int
    mapping_version: str | None = None
    executor_type: ExecutorType
    created_at: datetime


class TaskItemResponse(BaseModel):
    item_id: int
    sku_id: str
    status: TaskItemStatus
    attempt_count: int
    platform_item_id: str | None = None
    platform_sku_id: str | None = None
    error_message: str | None = None
    mapping_version: str | None = None
    source_version: str | None = None
    verification_status: str | None = None
    verification_message: str | None = None


class TaskAttemptResponse(BaseModel):
    attempt_id: str
    task_item_id: int | None
    attempt_no: int
    adapter_code: str
    adapter_version: str
    mapping_version: str
    source_version: str
    status: str
    error_type: str | None = None
    error_message: str | None = None
    duration_ms: int | None = None
    started_at: datetime
    completed_at: datetime | None = None


class TaskArtifactResponse(BaseModel):
    artifact_id: str
    task_attempt_id: int
    task_item_id: int | None = None
    artifact_type: str
    artifact_path: str
    metadata_json: dict[str, Any]
    created_at: datetime


class ActionResultResponse(BaseModel):
    task_id: str
    status: TaskStatus
    changed_count: int = 0


class BatchCreateTaskRequest(BaseModel):
    tasks: list[CreateTaskRequest]


class BatchCreateTaskResponse(BaseModel):
    task_ids: list[str]
    total_tasks: int
    total_items: int


class TaskListQuery(BaseModel):
    status: TaskStatus | None = None
    platform: str | None = None
    shop_id: int | None = None
    limit: int = Field(default=20, ge=1, le=100)
    offset: int = Field(default=0, ge=0)


class TaskDetailResponse(BaseModel):
    task_id: str
    task_type: TaskType
    platform: str
    shop_id: int
    status: TaskStatus
    total_count: int
    success_count: int
    failed_count: int
    mapping_version: str | None = None
    executor_type: ExecutorType
    created_at: datetime
    started_at: datetime | None = None
    completed_at: datetime | None = None
    created_by: str
    items: list[TaskItemResponse] = []
    attempts: list[TaskAttemptResponse] = []
    artifacts: list[TaskArtifactResponse] = []

