from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache

from platform_mediation.adapters.alibaba_1688_adapter import Alibaba1688Adapter
from platform_mediation.adapters.registry import AdapterRegistry
from platform_mediation.application.services.dispatch_service import DispatchService
from platform_mediation.application.services.lock_service import InMemoryLockService
from platform_mediation.application.services.task_service import TaskService
from platform_mediation.config import Settings, get_settings
from platform_mediation.repositories.in_memory import InMemoryPlatformMediationRepository
from platform_mediation.repositories.interfaces import PlatformMediationRepository
from platform_mediation.repositories.sqlalchemy_repo import SqlAlchemyPlatformMediationRepository


@dataclass(frozen=True)
class AppContainer:
    settings: Settings
    repository: PlatformMediationRepository
    task_service: TaskService
    dispatch_service: DispatchService
    adapter_registry: AdapterRegistry
    lock_service: InMemoryLockService


@lru_cache(maxsize=1)
def get_container() -> AppContainer:
    settings = get_settings()
    if settings.repository_backend == "sqlalchemy":
        repository = SqlAlchemyPlatformMediationRepository.from_database_url(
            settings.database_url,
            auto_create_schema=settings.auto_create_schema,
        )
    else:
        repository = InMemoryPlatformMediationRepository()
    lock_service = InMemoryLockService()
    adapter_registry = AdapterRegistry([Alibaba1688Adapter(settings)])
    task_service = TaskService(repository)
    dispatch_service = DispatchService(repository, adapter_registry, lock_service)
    return AppContainer(
        settings=settings,
        repository=repository,
        task_service=task_service,
        dispatch_service=dispatch_service,
        adapter_registry=adapter_registry,
        lock_service=lock_service,
    )
