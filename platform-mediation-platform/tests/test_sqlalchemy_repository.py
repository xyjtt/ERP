from __future__ import annotations

from pathlib import Path

from platform_mediation.adapters.alibaba_1688_adapter import Alibaba1688Adapter
from platform_mediation.adapters.registry import AdapterRegistry
from platform_mediation.application.services.dispatch_service import DispatchService
from platform_mediation.application.services.lock_service import InMemoryLockService
from platform_mediation.application.services.task_service import TaskService
from platform_mediation.config import Settings
from platform_mediation.repositories.sqlalchemy_repo import SqlAlchemyPlatformMediationRepository
from platform_mediation.schemas.task import CreateTaskItemRequest, CreateTaskRequest


def build_settings(tmp_path: Path) -> Settings:
    return Settings(
        app_name="platform-mediation-platform",
        environment="test",
        api_prefix="/api/v1",
        adapter_mode="mock",
        repository_backend="sqlalchemy",
        database_url=f"sqlite:///{tmp_path / 'platform_mediation.db'}",
        auto_create_schema=True,
        enable_real_1688_execution=False,
        furniture_uploader_root=Path("D:/script_files/ERP/furniture-uploader"),
    )


def test_sqlalchemy_repository_supports_task_flow(tmp_path: Path) -> None:
    settings = build_settings(tmp_path)
    repository = SqlAlchemyPlatformMediationRepository.from_database_url(
        settings.database_url,
        auto_create_schema=True,
    )
    task_service = TaskService(repository)
    dispatch_service = DispatchService(
        repository,
        AdapterRegistry([Alibaba1688Adapter(settings)]),
        InMemoryLockService(),
    )

    task = task_service.create_task(
        CreateTaskRequest(
            task_type="listing",
            platform="1688",
            shop_id=20001,
            executor_type="rpa",
            priority=5,
            mapping_version="1688.v1",
            created_by="pytest",
            items=[
                CreateTaskItemRequest(
                    sku_id="SQL-SKU-001",
                    payload={"sku_id": "SQL-SKU-001", "title": "SQL 持久化验证"},
                    source_type="manual",
                )
            ],
        )
    )

    result = dispatch_service.dispatch_task(task.task_id)

    assert result.status.value == "success"
    assert result.success_count == 1
    assert len(task_service.get_task_attempts(task.task_id)) == 1
    assert len(task_service.get_task_artifacts(task.task_id)) == 1
    assert len(repository.list_reflow_events(result.pk)) == 1

