from __future__ import annotations

from abc import ABC, abstractmethod

from platform_mediation.domain.models import (
    ReflowEvent,
    SourceSnapshot,
    Task,
    TaskArtifact,
    TaskAttempt,
    TaskItem,
)


class PlatformMediationRepository(ABC):
    @abstractmethod
    def save_task(self, task: Task) -> Task: ...

    @abstractmethod
    def save_task_item(self, item: TaskItem) -> TaskItem: ...

    @abstractmethod
    def save_snapshot(self, snapshot: SourceSnapshot) -> SourceSnapshot: ...

    @abstractmethod
    def save_attempt(self, attempt: TaskAttempt) -> TaskAttempt: ...

    @abstractmethod
    def save_artifact(self, artifact: TaskArtifact) -> TaskArtifact: ...

    @abstractmethod
    def save_reflow_event(self, event: ReflowEvent) -> ReflowEvent: ...

    @abstractmethod
    def get_task(self, task_id: str) -> Task | None: ...

    @abstractmethod
    def list_tasks(self) -> list[Task]: ...

    @abstractmethod
    def list_task_items(self, task_pk: int) -> list[TaskItem]: ...

    @abstractmethod
    def get_task_item(self, item_pk: int) -> TaskItem | None: ...

    @abstractmethod
    def get_snapshot(self, snapshot_pk: int) -> SourceSnapshot | None: ...

    @abstractmethod
    def list_attempts(self, task_pk: int) -> list[TaskAttempt]: ...

    @abstractmethod
    def list_artifacts(self, task_pk: int) -> list[TaskArtifact]: ...

    @abstractmethod
    def list_reflow_events(self, task_pk: int) -> list[ReflowEvent]: ...

    @abstractmethod
    def next_pk(self, entity_name: str) -> int: ...

