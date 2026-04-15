from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status

from platform_mediation.api.dependencies import get_dispatch_service, get_task_service
from platform_mediation.application.services.dispatch_service import DispatchService
from platform_mediation.application.services.task_service import TaskNotFoundError, TaskService
from platform_mediation.schemas.task import (
    ActionResultResponse,
    BatchCreateTaskRequest,
    BatchCreateTaskResponse,
    CreateTaskRequest,
    CreateTaskResponse,
    TaskArtifactResponse,
    TaskAttemptResponse,
    TaskDetailResponse,
    TaskItemResponse,
    TaskListQuery,
    TaskSummaryResponse,
)


router = APIRouter(prefix="/tasks", tags=["tasks"])


@router.post("", response_model=CreateTaskResponse, status_code=status.HTTP_201_CREATED)
def create_task(
    request: CreateTaskRequest,
    task_service: TaskService = Depends(get_task_service),
) -> CreateTaskResponse:
    task = task_service.create_task(request)
    return CreateTaskResponse(task_id=task.task_id, status=task.status, item_count=task.total_count)


@router.get("", response_model=list[TaskSummaryResponse])
def list_tasks(task_service: TaskService = Depends(get_task_service)) -> list[TaskSummaryResponse]:
    tasks = task_service.list_tasks()
    return [
        TaskSummaryResponse(
            task_id=task.task_id,
            task_type=task.task_type,
            platform=task.platform,
            shop_id=task.shop_id,
            status=task.status,
            total_count=task.total_count,
            success_count=task.success_count,
            failed_count=task.failed_count,
            mapping_version=task.mapping_version,
            executor_type=task.executor_type,
            created_at=task.created_at,
        )
        for task in tasks
    ]


@router.get("/{task_id}", response_model=TaskSummaryResponse)
def get_task(task_id: str, task_service: TaskService = Depends(get_task_service)) -> TaskSummaryResponse:
    try:
        task = task_service.get_task(task_id)
    except TaskNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="task not found") from exc

    return TaskSummaryResponse(
        task_id=task.task_id,
        task_type=task.task_type,
        platform=task.platform,
        shop_id=task.shop_id,
        status=task.status,
        total_count=task.total_count,
        success_count=task.success_count,
        failed_count=task.failed_count,
        mapping_version=task.mapping_version,
        executor_type=task.executor_type,
        created_at=task.created_at,
    )


@router.get("/{task_id}/items", response_model=list[TaskItemResponse])
def get_task_items(
    task_id: str,
    task_service: TaskService = Depends(get_task_service),
) -> list[TaskItemResponse]:
    try:
        items = task_service.get_task_items(task_id)
    except TaskNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="task not found") from exc

    return [
        TaskItemResponse(
            item_id=item.pk,
            sku_id=item.sku_id,
            status=item.status,
            attempt_count=item.attempt_count,
            platform_item_id=item.platform_item_id,
            platform_sku_id=item.platform_sku_id,
            error_message=item.error_message,
            mapping_version=item.mapping_version,
            source_version=item.source_version,
            verification_status=item.verification_status,
            verification_message=item.verification_message,
        )
        for item in items
    ]


@router.get("/{task_id}/attempts", response_model=list[TaskAttemptResponse])
def get_task_attempts(
    task_id: str,
    task_service: TaskService = Depends(get_task_service),
) -> list[TaskAttemptResponse]:
    try:
        attempts = task_service.get_task_attempts(task_id)
    except TaskNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="task not found") from exc

    return [
        TaskAttemptResponse(
            attempt_id=attempt.attempt_id,
            task_item_id=attempt.task_item_pk,
            attempt_no=attempt.attempt_no,
            adapter_code=attempt.adapter_code,
            adapter_version=attempt.adapter_version,
            mapping_version=attempt.mapping_version,
            source_version=attempt.source_version,
            status=attempt.status.value,
            error_type=attempt.error_type,
            error_message=attempt.error_message,
            duration_ms=attempt.duration_ms,
            started_at=attempt.started_at,
            completed_at=attempt.completed_at,
        )
        for attempt in attempts
    ]


@router.get("/{task_id}/artifacts", response_model=list[TaskArtifactResponse])
def get_task_artifacts(
    task_id: str,
    task_service: TaskService = Depends(get_task_service),
) -> list[TaskArtifactResponse]:
    try:
        artifacts = task_service.get_task_artifacts(task_id)
    except TaskNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="task not found") from exc

    return [
        TaskArtifactResponse(
            artifact_id=artifact.artifact_id,
            task_attempt_id=artifact.task_attempt_pk,
            task_item_id=artifact.task_item_pk,
            artifact_type=artifact.artifact_type,
            artifact_path=artifact.artifact_path,
            metadata_json=artifact.metadata_json,
            created_at=artifact.created_at,
        )
        for artifact in artifacts
    ]


@router.post("/{task_id}/cancel", response_model=ActionResultResponse)
def cancel_task(
    task_id: str,
    task_service: TaskService = Depends(get_task_service),
) -> ActionResultResponse:
    try:
        task = task_service.cancel_task(task_id)
    except TaskNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="task not found") from exc
    return ActionResultResponse(task_id=task.task_id, status=task.status, changed_count=0)


@router.post("/{task_id}/retry", response_model=ActionResultResponse)
def retry_task(
    task_id: str,
    task_service: TaskService = Depends(get_task_service),
) -> ActionResultResponse:
    try:
        task, retry_count = task_service.retry_failed_items(task_id)
    except TaskNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="task not found") from exc
    return ActionResultResponse(task_id=task.task_id, status=task.status, changed_count=retry_count)


@router.post("/{task_id}/dispatch", response_model=TaskSummaryResponse)
def dispatch_task(
    task_id: str,
    dispatch_service: DispatchService = Depends(get_dispatch_service),
) -> TaskSummaryResponse:
    try:
        task = dispatch_service.dispatch_task(task_id)
    except KeyError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="task not found") from exc

    return TaskSummaryResponse(
        task_id=task.task_id,
        task_type=task.task_type,
        platform=task.platform,
        shop_id=task.shop_id,
        status=task.status,
        total_count=task.total_count,
        success_count=task.success_count,
        failed_count=task.failed_count,
        mapping_version=task.mapping_version,
        executor_type=task.executor_type,
        created_at=task.created_at,
    )


@router.post("/batch", response_model=BatchCreateTaskResponse, status_code=status.HTTP_201_CREATED)
def batch_create_tasks(
    request: BatchCreateTaskRequest,
    task_service: TaskService = Depends(get_task_service),
) -> BatchCreateTaskResponse:
    task_ids: list[str] = []
    total_items = 0
    for task_request in request.tasks:
        task = task_service.create_task(task_request)
        task_ids.append(task.task_id)
        total_items += task.total_count
    return BatchCreateTaskResponse(task_ids=task_ids, total_tasks=len(task_ids), total_items=total_items)


@router.get("/query", response_model=list[TaskSummaryResponse])
def query_tasks(
    status: str | None = None,
    platform: str | None = None,
    shop_id: int | None = None,
    limit: int = 20,
    offset: int = 0,
    task_service: TaskService = Depends(get_task_service),
) -> list[TaskSummaryResponse]:
    all_tasks = task_service.list_tasks()
    filtered = all_tasks
    if status:
        filtered = [t for t in filtered if t.status.value == status]
    if platform:
        filtered = [t for t in filtered if t.platform == platform]
    if shop_id:
        filtered = [t for t in filtered if t.shop_id == shop_id]
    paginated = filtered[offset:offset + limit]
    return [
        TaskSummaryResponse(
            task_id=task.task_id,
            task_type=task.task_type,
            platform=task.platform,
            shop_id=task.shop_id,
            status=task.status,
            total_count=task.total_count,
            success_count=task.success_count,
            failed_count=task.failed_count,
            mapping_version=task.mapping_version,
            executor_type=task.executor_type,
            created_at=task.created_at,
        )
        for task in paginated
    ]


@router.get("/{task_id}/detail", response_model=TaskDetailResponse)
def get_task_detail(
    task_id: str,
    task_service: TaskService = Depends(get_task_service),
) -> TaskDetailResponse:
    try:
        task = task_service.get_task(task_id)
        items = task_service.get_task_items(task_id)
        attempts = task_service.get_task_attempts(task_id)
        artifacts = task_service.get_task_artifacts(task_id)
    except TaskNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="task not found") from exc

    return TaskDetailResponse(
        task_id=task.task_id,
        task_type=task.task_type,
        platform=task.platform,
        shop_id=task.shop_id,
        status=task.status,
        total_count=task.total_count,
        success_count=task.success_count,
        failed_count=task.failed_count,
        mapping_version=task.mapping_version,
        executor_type=task.executor_type,
        created_at=task.created_at,
        started_at=task.started_at,
        completed_at=task.completed_at,
        created_by=task.created_by,
        items=[
            TaskItemResponse(
                item_id=item.pk,
                sku_id=item.sku_id,
                status=item.status,
                attempt_count=item.attempt_count,
                platform_item_id=item.platform_item_id,
                platform_sku_id=item.platform_sku_id,
                error_message=item.error_message,
                mapping_version=item.mapping_version,
                source_version=item.source_version,
                verification_status=item.verification_status,
                verification_message=item.verification_message,
            )
            for item in items
        ],
        attempts=[
            TaskAttemptResponse(
                attempt_id=attempt.attempt_id,
                task_item_id=attempt.task_item_pk,
                attempt_no=attempt.attempt_no,
                adapter_code=attempt.adapter_code,
                adapter_version=attempt.adapter_version,
                mapping_version=attempt.mapping_version,
                source_version=attempt.source_version,
                status=attempt.status.value,
                error_type=attempt.error_type,
                error_message=attempt.error_message,
                duration_ms=attempt.duration_ms,
                started_at=attempt.started_at,
                completed_at=attempt.completed_at,
            )
            for attempt in attempts
        ],
        artifacts=[
            TaskArtifactResponse(
                artifact_id=artifact.artifact_id,
                task_attempt_id=artifact.task_attempt_pk,
                task_item_id=artifact.task_item_pk,
                artifact_type=artifact.artifact_type,
                artifact_path=artifact.artifact_path,
                metadata_json=artifact.metadata_json,
                created_at=artifact.created_at,
            )
            for artifact in artifacts
        ],
    )

