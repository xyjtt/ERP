from __future__ import annotations

from datetime import UTC, datetime

from platform_mediation.adapters.registry import AdapterRegistry
from platform_mediation.application.services.lock_service import InMemoryLockService
from platform_mediation.domain.enums import AttemptStatus, ReflowStatus, TaskItemStatus, TaskStatus
from platform_mediation.domain.models import (
    AdapterExecutionRequest,
    ReflowEvent,
    Task,
    TaskArtifact,
    TaskAttempt,
    TaskItem,
)
from platform_mediation.repositories.interfaces import PlatformMediationRepository
from platform_mediation.utils.ids import build_artifact_id, build_attempt_id, build_event_id


class DispatchService:
    def __init__(
        self,
        repository: PlatformMediationRepository,
        adapter_registry: AdapterRegistry,
        lock_service: InMemoryLockService,
    ) -> None:
        self._repository = repository
        self._adapter_registry = adapter_registry
        self._lock_service = lock_service

    def dispatch_task(self, task_id: str) -> Task:
        task = self._repository.get_task(task_id)
        if task is None:
            raise KeyError(task_id)

        owner = task.task_id
        resource_key = f"{task.platform}:{task.shop_id}"
        self._lock_service.acquire("shop", resource_key, owner)
        try:
            return self._run_task(task)
        finally:
            self._lock_service.release("shop", resource_key, owner)

    def _run_task(self, task: Task) -> Task:
        items = [
            item
            for item in self._repository.list_task_items(task.pk)
            if item.status in {TaskItemStatus.CREATED, TaskItemStatus.QUEUED}
        ]
        if not items:
            return self._finalize_task(task)

        task.status = TaskStatus.RUNNING
        task.started_at = task.started_at or datetime.now(UTC)
        self._repository.save_task(task)

        adapter = self._adapter_registry.resolve(
            platform=task.platform,
            executor_type=task.executor_type.value,
            adapter_code=task.adapter_code,
        )

        for item in items:
            self._run_item(task, item, adapter)

        return self._finalize_task(task)

    def _run_item(self, task: Task, item: TaskItem, adapter) -> None:
        item.status = TaskItemStatus.RUNNING
        item.started_at = item.started_at or datetime.now(UTC)
        self._repository.save_task_item(item)

        snapshot = self._repository.get_snapshot(item.source_snapshot_pk or 0)
        attempt_no = item.attempt_count + 1
        attempt = TaskAttempt(
            attempt_id=build_attempt_id(),
            task_pk=task.pk,
            task_item_pk=item.pk,
            attempt_no=attempt_no,
            adapter_code=task.adapter_code or adapter.adapter_code,
            adapter_version=adapter.version,
            mapping_version=item.mapping_version or task.mapping_version or "unbound",
            source_snapshot_pk=item.source_snapshot_pk,
            source_version=item.source_version or "v1",
            status=AttemptStatus.RUNNING,
        )
        attempt = self._repository.save_attempt(attempt)

        request = AdapterExecutionRequest(
            task_id=task.task_id,
            task_item_id=str(item.pk),
            platform=task.platform,
            action=task.task_type,
            executor_type=task.executor_type,
            shop_id=task.shop_id,
            mapping_version=item.mapping_version or task.mapping_version or "unbound",
            source_snapshot_id=snapshot.snapshot_id if snapshot else "missing-snapshot",
            payload=snapshot.snapshot_json if snapshot else item.payload_json,
        )

        result = adapter.execute(request)
        completed_at = datetime.now(UTC)
        attempt.completed_at = completed_at
        attempt.duration_ms = int((completed_at - attempt.started_at).total_seconds() * 1000)
        attempt.error_type = result.error_type
        attempt.error_message = result.error_message
        attempt.raw_result_json = result.raw_result
        execution_mode = str(result.result_payload.get("execution_mode", "")).strip().lower()

        if result.status == "success":
            attempt.status = AttemptStatus.SUCCESS
            item.status = TaskItemStatus.SUCCESS
            item.platform_item_id = result.platform_item_id
            item.platform_sku_id = result.platform_sku_id
            item.result_json = result.result_payload
            if execution_mode:
                item.verification_status = execution_mode
                item.verification_message = f"adapter finished in {execution_mode} mode"
        else:
            attempt.status = AttemptStatus.FAILED
            item.status = TaskItemStatus.FAILED
            item.error_message = result.error_message or result.error_type or "unknown adapter error"

        item.attempt_count = attempt_no
        item.completed_at = completed_at
        self._repository.save_attempt(attempt)
        self._repository.save_task_item(item)

        for adapter_artifact in result.artifacts:
            self._repository.save_artifact(
                TaskArtifact(
                    artifact_id=build_artifact_id(),
                    task_attempt_pk=attempt.pk,
                    task_pk=task.pk,
                    task_item_pk=item.pk,
                    artifact_type=adapter_artifact.artifact_type,
                    artifact_path=adapter_artifact.artifact_path,
                    metadata_json=adapter_artifact.metadata,
                )
            )

        if item.status == TaskItemStatus.SUCCESS and execution_mode not in {"preview", "validate"}:
            self._repository.save_reflow_event(
                ReflowEvent(
                    event_id=build_event_id(),
                    event_type="item_listed",
                    platform=task.platform,
                    shop_id=task.shop_id,
                    task_pk=task.pk,
                    task_item_pk=item.pk,
                    sku_id=item.sku_id,
                    platform_item_id=item.platform_item_id,
                    platform_sku_id=item.platform_sku_id,
                    idempotency_key=f"{task.task_id}:{item.sku_id}:item_listed",
                    payload_json={
                        "task_id": task.task_id,
                        "sku_id": item.sku_id,
                        "platform_item_id": item.platform_item_id,
                    },
                    status=ReflowStatus.PENDING,
                )
            )

    def _finalize_task(self, task: Task) -> Task:
        items = self._repository.list_task_items(task.pk)
        success_count = sum(1 for item in items if item.status == TaskItemStatus.SUCCESS)
        failed_count = sum(1 for item in items if item.status == TaskItemStatus.FAILED)
        cancelled_count = sum(1 for item in items if item.status == TaskItemStatus.CANCELLED)

        task.total_count = len(items)
        task.success_count = success_count
        task.failed_count = failed_count

        if items and success_count == len(items):
            task.status = TaskStatus.SUCCESS
        elif success_count > 0 and failed_count > 0:
            task.status = TaskStatus.PARTIAL_SUCCESS
        elif failed_count == len(items) and items:
            task.status = TaskStatus.FAILED
        elif cancelled_count == len(items) and items:
            task.status = TaskStatus.CANCELLED
        elif any(item.status in {TaskItemStatus.CREATED, TaskItemStatus.QUEUED, TaskItemStatus.RUNNING} for item in items):
            task.status = TaskStatus.RUNNING
        else:
            task.status = TaskStatus.CREATED

        if task.status in {TaskStatus.SUCCESS, TaskStatus.PARTIAL_SUCCESS, TaskStatus.FAILED, TaskStatus.CANCELLED}:
            task.completed_at = datetime.now(UTC)

        return self._repository.save_task(task)
