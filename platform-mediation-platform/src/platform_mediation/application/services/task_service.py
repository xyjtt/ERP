from __future__ import annotations

from datetime import UTC, datetime

from platform_mediation.domain.enums import TaskItemStatus, TaskStatus
from platform_mediation.domain.models import SourceSnapshot, Task, TaskItem
from platform_mediation.repositories.interfaces import PlatformMediationRepository
from platform_mediation.schemas.task import CreateTaskRequest
from platform_mediation.utils.ids import build_snapshot_id, build_task_id


class TaskNotFoundError(KeyError):
    """Raised when the requested task does not exist."""


class TaskService:
    def __init__(self, repository: PlatformMediationRepository) -> None:
        self._repository = repository

    def create_task(self, request: CreateTaskRequest) -> Task:
        task = Task(
            task_id=build_task_id(),
            task_type=request.task_type,
            platform=request.platform,
            shop_id=request.shop_id,
            status=TaskStatus.CREATED,
            created_by=request.created_by,
            executor_type=request.executor_type,
            priority=request.priority,
            scheduled_at=request.scheduled_at,
            idempotency_key=request.idempotency_key,
            adapter_code=request.adapter_code,
            mapping_version=request.mapping_version,
            payload_json=request.payload or {},
            remark=request.remark,
            total_count=len(request.items),
        )
        task = self._repository.save_task(task)

        for request_item in request.items:
            item = TaskItem(
                task_pk=task.pk,
                sku_id=request_item.sku_id,
                status=TaskItemStatus.CREATED,
                payload_json=request_item.payload or {},
                mapping_version=request_item.mapping_version or request.mapping_version,
                source_version=request_item.source_version or "v1",
            )
            item = self._repository.save_task_item(item)

            snapshot = SourceSnapshot(
                snapshot_id=build_snapshot_id(),
                task_pk=task.pk,
                task_item_pk=item.pk,
                sku_id=request_item.sku_id,
                source_type=request_item.source_type or "manual",
                source_record_key=request_item.source_record_key or request_item.sku_id,
                snapshot_json=request_item.payload or {"sku_id": request_item.sku_id},
                version=request_item.source_version or "v1",
            )
            snapshot = self._repository.save_snapshot(snapshot)
            item.source_snapshot_pk = snapshot.pk
            item.payload_json = snapshot.snapshot_json
            self._repository.save_task_item(item)

        return task

    def list_tasks(self) -> list[Task]:
        return self._repository.list_tasks()

    def get_task(self, task_id: str) -> Task:
        task = self._repository.get_task(task_id)
        if task is None:
            raise TaskNotFoundError(task_id)
        return task

    def get_task_items(self, task_id: str) -> list[TaskItem]:
        task = self.get_task(task_id)
        return self._repository.list_task_items(task.pk)

    def get_task_attempts(self, task_id: str):
        task = self.get_task(task_id)
        return self._repository.list_attempts(task.pk)

    def get_task_artifacts(self, task_id: str):
        task = self.get_task(task_id)
        return self._repository.list_artifacts(task.pk)

    def cancel_task(self, task_id: str) -> Task:
        task = self.get_task(task_id)
        if task.status in {TaskStatus.SUCCESS, TaskStatus.FAILED, TaskStatus.CANCELLED}:
            return task

        task.status = TaskStatus.CANCELLED
        task.completed_at = datetime.now(UTC)
        self._repository.save_task(task)

        for item in self._repository.list_task_items(task.pk):
            if item.status in {TaskItemStatus.CREATED, TaskItemStatus.QUEUED, TaskItemStatus.RUNNING}:
                item.status = TaskItemStatus.CANCELLED
                item.completed_at = datetime.now(UTC)
                self._repository.save_task_item(item)

        return task

    def retry_failed_items(self, task_id: str) -> tuple[Task, int]:
        task = self.get_task(task_id)
        retry_count = 0
        for item in self._repository.list_task_items(task.pk):
            if item.status == TaskItemStatus.FAILED:
                item.status = TaskItemStatus.QUEUED
                item.error_message = None
                item.completed_at = None
                item.verification_message = None
                self._repository.save_task_item(item)
                retry_count += 1

        if retry_count > 0:
            task.status = TaskStatus.QUEUED
            task.next_retry_at = None
            task.completed_at = None
            self._repository.save_task(task)

        return task, retry_count
