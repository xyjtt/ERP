from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import DateTime, ForeignKey, Integer, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def utc_now() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    pass


class TaskRecord(Base):
    __tablename__ = "task"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    task_id: Mapped[str] = mapped_column(Text, unique=True, index=True)
    task_type: Mapped[str] = mapped_column(Text)
    platform: Mapped[str] = mapped_column(Text)
    shop_id: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(Text, index=True)
    total_count: Mapped[int] = mapped_column(Integer, default=0)
    success_count: Mapped[int] = mapped_column(Integer, default=0)
    failed_count: Mapped[int] = mapped_column(Integer, default=0)
    scheduled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    priority: Mapped[int] = mapped_column(Integer, default=0)
    next_retry_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    idempotency_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    executor_type: Mapped[str | None] = mapped_column(Text, nullable=True)
    adapter_code: Mapped[str | None] = mapped_column(Text, nullable=True)
    mapping_version: Mapped[str | None] = mapped_column(Text, nullable=True)
    payload_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    remark: Mapped[str | None] = mapped_column(Text, nullable=True)


class TaskItemRecord(Base):
    __tablename__ = "task_item"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    task_id: Mapped[int] = mapped_column(ForeignKey("task.id"), index=True)
    sku_id: Mapped[str] = mapped_column(Text, index=True)
    platform_sku_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    platform_item_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(Text, index=True)
    source_snapshot_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    payload_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    mapping_version: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_version: Mapped[str | None] = mapped_column(Text, nullable=True)
    attempt_count: Mapped[int] = mapped_column(Integer, default=0)
    result_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    verification_status: Mapped[str | None] = mapped_column(Text, nullable=True)
    verification_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class SourceSnapshotRecord(Base):
    __tablename__ = "source_snapshot"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    snapshot_id: Mapped[str] = mapped_column(Text, unique=True, index=True)
    task_id: Mapped[int | None] = mapped_column(ForeignKey("task.id"), nullable=True)
    task_item_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    sku_id: Mapped[str] = mapped_column(Text, index=True)
    source_type: Mapped[str] = mapped_column(Text)
    source_record_key: Mapped[str] = mapped_column(Text, index=True)
    snapshot_json: Mapped[str] = mapped_column(Text)
    version: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class TaskAttemptRecord(Base):
    __tablename__ = "task_attempt"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    attempt_id: Mapped[str] = mapped_column(Text, unique=True)
    task_id: Mapped[int] = mapped_column(ForeignKey("task.id"), index=True)
    task_item_id: Mapped[int | None] = mapped_column(ForeignKey("task_item.id"), nullable=True, index=True)
    attempt_no: Mapped[int] = mapped_column(Integer)
    adapter_code: Mapped[str] = mapped_column(Text)
    adapter_version: Mapped[str] = mapped_column(Text)
    mapping_version: Mapped[str] = mapped_column(Text)
    source_snapshot_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    source_version: Mapped[str] = mapped_column(Text)
    worker_node_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(Text, index=True)
    error_type: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    retry_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    raw_result_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)


class TaskArtifactRecord(Base):
    __tablename__ = "task_artifact"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    artifact_id: Mapped[str] = mapped_column(Text, unique=True)
    task_attempt_id: Mapped[int] = mapped_column(ForeignKey("task_attempt.id"), index=True)
    task_id: Mapped[int] = mapped_column(ForeignKey("task.id"), index=True)
    task_item_id: Mapped[int | None] = mapped_column(ForeignKey("task_item.id"), nullable=True, index=True)
    artifact_type: Mapped[str] = mapped_column(Text)
    artifact_path: Mapped[str] = mapped_column(Text)
    metadata_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class ReflowEventRecord(Base):
    __tablename__ = "reflow_event"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    event_id: Mapped[str] = mapped_column(Text, unique=True)
    event_type: Mapped[str] = mapped_column(Text)
    platform: Mapped[str] = mapped_column(Text, index=True)
    shop_id: Mapped[int] = mapped_column(Integer, index=True)
    task_id: Mapped[int | None] = mapped_column(ForeignKey("task.id"), nullable=True, index=True)
    task_item_id: Mapped[int | None] = mapped_column(ForeignKey("task_item.id"), nullable=True, index=True)
    sku_id: Mapped[str] = mapped_column(Text)
    platform_item_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    platform_sku_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    idempotency_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    payload_json: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, index=True)
    retry_count: Mapped[int] = mapped_column(Integer, default=0)
    next_retry_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

