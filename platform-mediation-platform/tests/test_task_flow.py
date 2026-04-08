from __future__ import annotations

import subprocess
from pathlib import Path

from platform_mediation.adapters.alibaba_1688_adapter import Alibaba1688Adapter
from platform_mediation.adapters.registry import AdapterRegistry
from platform_mediation.bootstrap import get_container
from platform_mediation.application.services.dispatch_service import DispatchService
from platform_mediation.application.services.lock_service import InMemoryLockService
from platform_mediation.application.services.task_service import TaskService
from platform_mediation.config import Settings
from platform_mediation.repositories.in_memory import InMemoryPlatformMediationRepository
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


def test_validate_bridge_does_not_emit_reflow_event(tmp_path: Path, monkeypatch) -> None:
    import platform_mediation.adapters.alibaba_1688_adapter as adapter_module

    monkeypatch.setattr(adapter_module, "PROJECT_ROOT", tmp_path)
    furniture_root = tmp_path / "furniture-uploader"
    (furniture_root / "rpa").mkdir(parents=True, exist_ok=True)
    (furniture_root / "rpa" / "main.py").write_text("print('stub')\n", encoding="utf-8")

    def fake_runner(command: list[str], cwd: Path, timeout_seconds: int) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            args=command,
            returncode=0,
            stdout="[INFO] Validation completed.\n",
            stderr="",
        )

    settings = Settings(
        app_name="platform-mediation-platform",
        environment="test",
        api_prefix="/api/v1",
        adapter_mode="validate",
        repository_backend="memory",
        database_url=f"sqlite:///{tmp_path / 'platform_mediation.db'}",
        auto_create_schema=True,
        enable_real_1688_execution=False,
        furniture_uploader_root=furniture_root,
    )
    repository = InMemoryPlatformMediationRepository()
    task_service = TaskService(repository)
    dispatch_service = DispatchService(
        repository,
        AdapterRegistry([Alibaba1688Adapter(settings, runner=fake_runner, python_executable="python-test")]),
        InMemoryLockService(),
    )

    task = task_service.create_task(
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
                    sku_id="SKU-VALIDATE-001",
                    payload={
                        "sku_id": "SKU-VALIDATE-001",
                        "title": "验证商品",
                        "price": 188,
                        "platform_category": "corner_table",
                        "shop_name": "demo_shop",
                    },
                    source_type="manual",
                )
            ],
        )
    )

    result = dispatch_service.dispatch_task(task.task_id)
    items = task_service.get_task_items(task.task_id)

    assert result.status.value == "success"
    assert items[0].verification_status == "validate"
    assert repository.list_reflow_events(result.pk) == []
