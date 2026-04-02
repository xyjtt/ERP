from __future__ import annotations

import json
from contextlib import contextmanager

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from platform_mediation.domain.enums import (
    AttemptStatus,
    ExecutorType,
    ReflowStatus,
    TaskItemStatus,
    TaskStatus,
    TaskType,
)
from platform_mediation.domain.models import (
    ReflowEvent,
    SourceSnapshot,
    Task,
    TaskArtifact,
    TaskAttempt,
    TaskItem,
)
from platform_mediation.repositories.interfaces import PlatformMediationRepository
from platform_mediation.repositories.sqlalchemy_models import (
    Base,
    ReflowEventRecord,
    SourceSnapshotRecord,
    TaskArtifactRecord,
    TaskAttemptRecord,
    TaskItemRecord,
    TaskRecord,
)


def _dump_json(value) -> str | None:
    if value is None:
        return None
    return json.dumps(value, ensure_ascii=False)


def _load_json(value: str | None) -> dict:
    if not value:
        return {}
    return json.loads(value)


class SqlAlchemyPlatformMediationRepository(PlatformMediationRepository):
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    @classmethod
    def from_database_url(
        cls,
        database_url: str,
        *,
        auto_create_schema: bool = False,
    ) -> "SqlAlchemyPlatformMediationRepository":
        connect_args = {"check_same_thread": False} if database_url.startswith("sqlite") else {}
        engine = create_engine(database_url, future=True, connect_args=connect_args)
        if auto_create_schema:
            Base.metadata.create_all(engine)
        session_factory = sessionmaker(bind=engine, expire_on_commit=False, future=True)
        return cls(session_factory)

    @contextmanager
    def _session_scope(self):
        session = self._session_factory()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def next_pk(self, entity_name: str) -> int:
        return 0

    def save_task(self, task: Task) -> Task:
        with self._session_scope() as session:
            record = session.get(TaskRecord, task.pk) if task.pk else TaskRecord()
            record.task_id = task.task_id
            record.task_type = task.task_type.value
            record.platform = task.platform
            record.shop_id = task.shop_id
            record.status = task.status.value
            record.total_count = task.total_count
            record.success_count = task.success_count
            record.failed_count = task.failed_count
            record.scheduled_at = task.scheduled_at
            record.priority = task.priority
            record.next_retry_at = task.next_retry_at
            record.idempotency_key = task.idempotency_key
            record.executor_type = task.executor_type.value
            record.adapter_code = task.adapter_code
            record.mapping_version = task.mapping_version
            record.payload_json = _dump_json(task.payload_json)
            record.created_by = task.created_by
            record.created_at = task.created_at
            record.started_at = task.started_at
            record.completed_at = task.completed_at
            record.remark = task.remark
            session.add(record)
            session.flush()
            task.pk = record.id
            return self._to_task(record)

    def save_task_item(self, item: TaskItem) -> TaskItem:
        with self._session_scope() as session:
            record = session.get(TaskItemRecord, item.pk) if item.pk else TaskItemRecord()
            record.task_id = item.task_pk
            record.sku_id = item.sku_id
            record.platform_sku_id = item.platform_sku_id
            record.platform_item_id = item.platform_item_id
            record.status = item.status.value
            record.source_snapshot_id = item.source_snapshot_pk
            record.payload_json = _dump_json(item.payload_json)
            record.mapping_version = item.mapping_version
            record.source_version = item.source_version
            record.attempt_count = item.attempt_count
            record.result_json = _dump_json(item.result_json)
            record.verification_status = item.verification_status
            record.verification_message = item.verification_message
            record.error_message = item.error_message
            record.started_at = item.started_at
            record.completed_at = item.completed_at
            session.add(record)
            session.flush()
            item.pk = record.id
            return self._to_task_item(record)

    def save_snapshot(self, snapshot: SourceSnapshot) -> SourceSnapshot:
        with self._session_scope() as session:
            record = session.get(SourceSnapshotRecord, snapshot.pk) if snapshot.pk else SourceSnapshotRecord()
            record.snapshot_id = snapshot.snapshot_id
            record.task_id = snapshot.task_pk
            record.task_item_id = snapshot.task_item_pk
            record.sku_id = snapshot.sku_id
            record.source_type = snapshot.source_type
            record.source_record_key = snapshot.source_record_key
            record.snapshot_json = _dump_json(snapshot.snapshot_json) or "{}"
            record.version = snapshot.version
            record.created_at = snapshot.created_at
            session.add(record)
            session.flush()
            snapshot.pk = record.id
            return self._to_snapshot(record)

    def save_attempt(self, attempt: TaskAttempt) -> TaskAttempt:
        with self._session_scope() as session:
            record = session.get(TaskAttemptRecord, attempt.pk) if attempt.pk else TaskAttemptRecord()
            record.attempt_id = attempt.attempt_id
            record.task_id = attempt.task_pk
            record.task_item_id = attempt.task_item_pk
            record.attempt_no = attempt.attempt_no
            record.adapter_code = attempt.adapter_code
            record.adapter_version = attempt.adapter_version
            record.mapping_version = attempt.mapping_version
            record.source_snapshot_id = attempt.source_snapshot_pk
            record.source_version = attempt.source_version
            record.worker_node_id = attempt.worker_node_pk
            record.status = attempt.status.value
            record.error_type = attempt.error_type
            record.error_message = attempt.error_message
            record.retry_reason = attempt.retry_reason
            record.raw_result_json = _dump_json(attempt.raw_result_json)
            record.started_at = attempt.started_at
            record.completed_at = attempt.completed_at
            record.duration_ms = attempt.duration_ms
            session.add(record)
            session.flush()
            attempt.pk = record.id
            return self._to_attempt(record)

    def save_artifact(self, artifact: TaskArtifact) -> TaskArtifact:
        with self._session_scope() as session:
            record = session.get(TaskArtifactRecord, artifact.pk) if artifact.pk else TaskArtifactRecord()
            record.artifact_id = artifact.artifact_id
            record.task_attempt_id = artifact.task_attempt_pk
            record.task_id = artifact.task_pk
            record.task_item_id = artifact.task_item_pk
            record.artifact_type = artifact.artifact_type
            record.artifact_path = artifact.artifact_path
            record.metadata_json = _dump_json(artifact.metadata_json)
            record.created_at = artifact.created_at
            session.add(record)
            session.flush()
            artifact.pk = record.id
            return self._to_artifact(record)

    def save_reflow_event(self, event: ReflowEvent) -> ReflowEvent:
        with self._session_scope() as session:
            record = session.get(ReflowEventRecord, event.pk) if event.pk else ReflowEventRecord()
            record.event_id = event.event_id
            record.event_type = event.event_type
            record.platform = event.platform
            record.shop_id = event.shop_id
            record.task_id = event.task_pk
            record.task_item_id = event.task_item_pk
            record.sku_id = event.sku_id
            record.platform_item_id = event.platform_item_id
            record.platform_sku_id = event.platform_sku_id
            record.idempotency_key = event.idempotency_key
            record.payload_json = _dump_json(event.payload_json) or "{}"
            record.status = event.status.value
            record.retry_count = event.retry_count
            record.next_retry_at = event.next_retry_at
            record.error_message = event.error_message
            record.created_at = event.created_at
            record.updated_at = event.updated_at
            session.add(record)
            session.flush()
            event.pk = record.id
            return self._to_reflow_event(record)

    def get_task(self, task_id: str) -> Task | None:
        with self._session_scope() as session:
            statement = select(TaskRecord).where(TaskRecord.task_id == task_id)
            record = session.execute(statement).scalar_one_or_none()
            return self._to_task(record) if record else None

    def list_tasks(self) -> list[Task]:
        with self._session_scope() as session:
            statement = select(TaskRecord).order_by(TaskRecord.created_at.desc())
            return [self._to_task(record) for record in session.execute(statement).scalars()]

    def list_task_items(self, task_pk: int) -> list[TaskItem]:
        with self._session_scope() as session:
            statement = select(TaskItemRecord).where(TaskItemRecord.task_id == task_pk).order_by(TaskItemRecord.id.asc())
            return [self._to_task_item(record) for record in session.execute(statement).scalars()]

    def get_task_item(self, item_pk: int) -> TaskItem | None:
        with self._session_scope() as session:
            record = session.get(TaskItemRecord, item_pk)
            return self._to_task_item(record) if record else None

    def get_snapshot(self, snapshot_pk: int) -> SourceSnapshot | None:
        with self._session_scope() as session:
            record = session.get(SourceSnapshotRecord, snapshot_pk)
            return self._to_snapshot(record) if record else None

    def list_attempts(self, task_pk: int) -> list[TaskAttempt]:
        with self._session_scope() as session:
            statement = select(TaskAttemptRecord).where(TaskAttemptRecord.task_id == task_pk).order_by(TaskAttemptRecord.id.asc())
            return [self._to_attempt(record) for record in session.execute(statement).scalars()]

    def list_artifacts(self, task_pk: int) -> list[TaskArtifact]:
        with self._session_scope() as session:
            statement = select(TaskArtifactRecord).where(TaskArtifactRecord.task_id == task_pk).order_by(TaskArtifactRecord.id.asc())
            return [self._to_artifact(record) for record in session.execute(statement).scalars()]

    def list_reflow_events(self, task_pk: int) -> list[ReflowEvent]:
        with self._session_scope() as session:
            statement = select(ReflowEventRecord).where(ReflowEventRecord.task_id == task_pk).order_by(ReflowEventRecord.id.asc())
            return [self._to_reflow_event(record) for record in session.execute(statement).scalars()]

    def _to_task(self, record: TaskRecord) -> Task:
        return Task(
            task_id=record.task_id,
            task_type=TaskType(record.task_type),
            platform=record.platform,
            shop_id=record.shop_id,
            status=TaskStatus(record.status),
            created_by=record.created_by,
            executor_type=ExecutorType(record.executor_type or "rpa"),
            priority=record.priority,
            scheduled_at=record.scheduled_at,
            next_retry_at=record.next_retry_at,
            idempotency_key=record.idempotency_key,
            adapter_code=record.adapter_code,
            mapping_version=record.mapping_version,
            payload_json=_load_json(record.payload_json),
            remark=record.remark,
            total_count=record.total_count,
            success_count=record.success_count,
            failed_count=record.failed_count,
            created_at=record.created_at,
            started_at=record.started_at,
            completed_at=record.completed_at,
            pk=record.id,
        )

    def _to_task_item(self, record: TaskItemRecord) -> TaskItem:
        return TaskItem(
            sku_id=record.sku_id,
            status=TaskItemStatus(record.status),
            task_pk=record.task_id,
            platform_sku_id=record.platform_sku_id,
            platform_item_id=record.platform_item_id,
            source_snapshot_pk=record.source_snapshot_id,
            payload_json=_load_json(record.payload_json),
            mapping_version=record.mapping_version,
            source_version=record.source_version,
            result_json=_load_json(record.result_json),
            verification_status=record.verification_status,
            verification_message=record.verification_message,
            error_message=record.error_message,
            attempt_count=record.attempt_count,
            started_at=record.started_at,
            completed_at=record.completed_at,
            pk=record.id,
        )

    def _to_snapshot(self, record: SourceSnapshotRecord) -> SourceSnapshot:
        return SourceSnapshot(
            snapshot_id=record.snapshot_id,
            task_pk=record.task_id,
            task_item_pk=record.task_item_id,
            sku_id=record.sku_id,
            source_type=record.source_type,
            source_record_key=record.source_record_key,
            snapshot_json=_load_json(record.snapshot_json),
            version=record.version,
            created_at=record.created_at,
            pk=record.id,
        )

    def _to_attempt(self, record: TaskAttemptRecord) -> TaskAttempt:
        return TaskAttempt(
            attempt_id=record.attempt_id,
            task_pk=record.task_id,
            task_item_pk=record.task_item_id,
            attempt_no=record.attempt_no,
            adapter_code=record.adapter_code,
            adapter_version=record.adapter_version,
            mapping_version=record.mapping_version,
            source_snapshot_pk=record.source_snapshot_id,
            source_version=record.source_version,
            worker_node_pk=record.worker_node_id,
            status=AttemptStatus(record.status),
            error_type=record.error_type,
            error_message=record.error_message,
            retry_reason=record.retry_reason,
            raw_result_json=_load_json(record.raw_result_json),
            started_at=record.started_at,
            completed_at=record.completed_at,
            duration_ms=record.duration_ms,
            pk=record.id,
        )

    def _to_artifact(self, record: TaskArtifactRecord) -> TaskArtifact:
        return TaskArtifact(
            artifact_id=record.artifact_id,
            task_attempt_pk=record.task_attempt_id,
            task_pk=record.task_id,
            task_item_pk=record.task_item_id,
            artifact_type=record.artifact_type,
            artifact_path=record.artifact_path,
            metadata_json=_load_json(record.metadata_json),
            created_at=record.created_at,
            pk=record.id,
        )

    def _to_reflow_event(self, record: ReflowEventRecord) -> ReflowEvent:
        return ReflowEvent(
            event_id=record.event_id,
            event_type=record.event_type,
            platform=record.platform,
            shop_id=record.shop_id,
            task_pk=record.task_id,
            task_item_pk=record.task_item_id,
            sku_id=record.sku_id,
            platform_item_id=record.platform_item_id,
            platform_sku_id=record.platform_sku_id,
            idempotency_key=record.idempotency_key,
            payload_json=_load_json(record.payload_json),
            status=ReflowStatus(record.status),
            retry_count=record.retry_count,
            next_retry_at=record.next_retry_at,
            error_message=record.error_message,
            created_at=record.created_at,
            updated_at=record.updated_at,
            pk=record.id,
        )
