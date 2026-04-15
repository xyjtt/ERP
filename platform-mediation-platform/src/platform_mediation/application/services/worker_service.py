from __future__ import annotations

import signal
import sys
import time
from datetime import UTC, datetime
from typing import Callable

from platform_mediation.adapters.registry import AdapterRegistry
from platform_mediation.application.services.dispatch_service import DispatchService
from platform_mediation.application.services.lock_service import InMemoryLockService
from platform_mediation.domain.enums import TaskStatus
from platform_mediation.repositories.interfaces import PlatformMediationRepository


class WorkerService:
    def __init__(
        self,
        repository: PlatformMediationRepository,
        adapter_registry: AdapterRegistry,
        lock_service: InMemoryLockService,
        *,
        poll_interval_seconds: float = 5.0,
        max_iterations: int | None = None,
        on_task_dispatched: Callable[[str], None] | None = None,
    ) -> None:
        self._repository = repository
        self._adapter_registry = adapter_registry
        self._lock_service = lock_service
        self._poll_interval_seconds = poll_interval_seconds
        self._max_iterations = max_iterations
        self._on_task_dispatched = on_task_dispatched
        self._running = False
        self._dispatch_service = DispatchService(
            repository,
            adapter_registry,
            lock_service,
        )

    def start(self) -> None:
        self._running = True
        signal.signal(signal.SIGINT, self._handle_shutdown)
        signal.signal(signal.SIGTERM, self._handle_shutdown)
        iteration = 0
        while self._running:
            if self._max_iterations is not None and iteration >= self._max_iterations:
                break
            self._poll_and_dispatch()
            time.sleep(self._poll_interval_seconds)
            iteration += 1

    def stop(self) -> None:
        self._running = False

    def _handle_shutdown(self, signum: int, frame: object) -> None:
        print(f"[Worker] Received signal {signum}, shutting down...")
        self._running = False

    def _poll_and_dispatch(self) -> None:
        queued_tasks = self._repository.list_tasks_by_status(TaskStatus.QUEUED)
        if not queued_tasks:
            return

        sorted_tasks = sorted(queued_tasks, key=lambda t: (t.priority, t.scheduled_at or datetime.min.replace(tzinfo=UTC)))

        for task in sorted_tasks:
            if not self._running:
                break
            try:
                self._dispatch_service.dispatch_task(task.task_id)
                if self._on_task_dispatched:
                    self._on_task_dispatched(task.task_id)
            except Exception as exc:
                print(f"[Worker] Failed to dispatch task {task.task_id}: {exc}")
                continue