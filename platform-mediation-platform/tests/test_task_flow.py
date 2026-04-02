from __future__ import annotations

from platform_mediation.bootstrap import get_container
from platform_mediation.schemas.task import CreateTaskItemRequest, CreateTaskRequest


def reset_container() -> None:
    get_container.cache_clear()


def test_create_and_dispatch_task() -> None:
    reset_container()
    container = get_container()

    task = container.task_service.create_task(
        CreateTaskRequest(
            task_type="listing",
            platform="1688",
            shop_id=10001,
            executor_type="rpa",
            priority=10,
            mapping_version="1688.v1",
            created_by="pytest",
            items=[
                CreateTaskItemRequest(
                    sku_id="SKU001",
                    payload={"sku_id": "SKU001", "title": "示例商品", "price": 199.0},
                    source_type="manual",
                )
            ],
        )
    )

    result = container.dispatch_service.dispatch_task(task.task_id)

    assert result.status.value == "success"
    assert result.success_count == 1
    assert result.failed_count == 0

    artifacts = container.task_service.get_task_artifacts(task.task_id)
    attempts = container.task_service.get_task_attempts(task.task_id)

    assert len(artifacts) == 1
    assert len(attempts) == 1
    assert attempts[0].adapter_code == "1688_adapter"


def test_retry_failed_items_keeps_success_tasks_clean() -> None:
    reset_container()
    container = get_container()

    task = container.task_service.create_task(
        CreateTaskRequest(
            task_type="listing",
            platform="1688",
            shop_id=10001,
            executor_type="rpa",
            priority=10,
            mapping_version="1688.v1",
            created_by="pytest",
            items=[CreateTaskItemRequest(sku_id="SKU001")],
        )
    )

    container.dispatch_service.dispatch_task(task.task_id)
    updated_task, retry_count = container.task_service.retry_failed_items(task.task_id)

    assert retry_count == 0
    assert updated_task.status.value == "success"

