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


@dataclass(frozen=True)
class AppContainer:
    settings: Settings
    repository: InMemoryPlatformMediationRepository
    task_service: TaskService
    dispatch_service: DispatchService
    adapter_registry: AdapterRegistry
    lock_service: InMemoryLockService


@lru_cache(maxsize=1)
def get_container() -> AppContainer:
    settings = get_settings()
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
