from __future__ import annotations

import signal
import time
from datetime import UTC, datetime, timedelta
from typing import Callable, Protocol

from platform_mediation.domain.enums import ReflowStatus
from platform_mediation.domain.models import ReflowEvent
from platform_mediation.repositories.interfaces import PlatformMediationRepository


class ReflowHandler(Protocol):
    def handle(self, event: ReflowEvent) -> tuple[bool, str | None]: ...


class MockReflowHandler:
    def handle(self, event: ReflowEvent) -> tuple[bool, str | None]:
        return True, None


class ReflowService:
    def __init__(
        self,
        repository: PlatformMediationRepository,
        handler: ReflowHandler,
        *,
        poll_interval_seconds: float = 5.0,
        max_iterations: int | None = None,
        max_retry_count: int = 3,
        retry_delay_seconds: int = 60,
        on_event_processed: Callable[[str, bool], None] | None = None,
    ) -> None:
        self._repository = repository
        self._handler = handler
        self._poll_interval_seconds = poll_interval_seconds
        self._max_iterations = max_iterations
        self._max_retry_count = max_retry_count
        self._retry_delay_seconds = retry_delay_seconds
        self._on_event_processed = on_event_processed
        self._running = False

    def start(self) -> None:
        self._running = True
        signal.signal(signal.SIGINT, self._handle_shutdown)
        signal.signal(signal.SIGTERM, self._handle_shutdown)
        iteration = 0
        while self._running:
            if self._max_iterations is not None and iteration >= self._max_iterations:
                break
            self._poll_and_process()
            time.sleep(self._poll_interval_seconds)
            iteration += 1

    def stop(self) -> None:
        self._running = False

    def _handle_shutdown(self, signum: int, frame: object) -> None:
        print(f"[Reflow] Received signal {signum}, shutting down...")
        self._running = False

    def _poll_and_process(self) -> None:
        pending_events = self._repository.list_reflow_events_by_status(ReflowStatus.PENDING)
        retry_events = self._repository.list_reflow_events_by_status(ReflowStatus.FAILED)
        retryable_events = [
            event
            for event in retry_events
            if event.retry_count < self._max_retry_count
            and (event.next_retry_at is None or event.next_retry_at <= datetime.now(UTC))
        ]
        all_events = pending_events + retryable_events
        if not all_events:
            return

        for event in all_events:
            if not self._running:
                break
            self._process_event(event)

    def _process_event(self, event: ReflowEvent) -> None:
        event.status = ReflowStatus.PROCESSING
        event.updated_at = datetime.now(UTC)
        self._repository.save_reflow_event(event)

        success, error_message = self._handler.handle(event)
        completed_at = datetime.now(UTC)

        if success:
            event.status = ReflowStatus.SUCCESS
            event.error_message = None
        else:
            event.retry_count += 1
            if event.retry_count >= self._max_retry_count:
                event.status = ReflowStatus.FAILED
                event.error_message = error_message or "Max retry count exceeded"
            else:
                event.status = ReflowStatus.PENDING
                event.next_retry_at = completed_at + timedelta(seconds=self._retry_delay_seconds)
                event.error_message = error_message

        event.updated_at = completed_at
        self._repository.save_reflow_event(event)

        if self._on_event_processed:
            self._on_event_processed(event.event_id, success)