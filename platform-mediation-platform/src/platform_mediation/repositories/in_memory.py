from __future__ import annotations

from collections import defaultdict

from platform_mediation.domain.models import (
    ReflowEvent,
    SourceSnapshot,
    Task,
    TaskArtifact,
    TaskAttempt,
    TaskItem,
)
from platform_mediation.repositories.interfaces import PlatformMediationRepository


class InMemoryPlatformMediationRepository(PlatformMediationRepository):
    def __init__(self) -> None:
        self._counters: dict[str, int] = defaultdict(int)
        self._tasks_by_pk: dict[int, Task] = {}
        self._task_pk_by_public_id: dict[str, int] = {}
        self._items_by_pk: dict[int, TaskItem] = {}
        self._item_pks_by_task_pk: dict[int, list[int]] = defaultdict(list)
        self._snapshots_by_pk: dict[int, SourceSnapshot] = {}
        self._attempts_by_pk: dict[int, TaskAttempt] = {}
        self._attempt_pks_by_task_pk: dict[int, list[int]] = defaultdict(list)
        self._artifacts_by_pk: dict[int, TaskArtifact] = {}
        self._artifact_pks_by_task_pk: dict[int, list[int]] = defaultdict(list)
        self._reflow_events_by_pk: dict[int, ReflowEvent] = {}
        self._reflow_event_pks_by_task_pk: dict[int, list[int]] = defaultdict(list)

    def next_pk(self, entity_name: str) -> int:
        self._counters[entity_name] += 1
        return self._counters[entity_name]

    def save_task(self, task: Task) -> Task:
        if task.pk == 0:
            task.pk = self.next_pk("task")
        self._tasks_by_pk[task.pk] = task
        self._task_pk_by_public_id[task.task_id] = task.pk
        return task

    def save_task_item(self, item: TaskItem) -> TaskItem:
        if item.pk == 0:
            item.pk = self.next_pk("task_item")
            self._item_pks_by_task_pk[item.task_pk].append(item.pk)
        self._items_by_pk[item.pk] = item
        return item

    def save_snapshot(self, snapshot: SourceSnapshot) -> SourceSnapshot:
        if snapshot.pk == 0:
            snapshot.pk = self.next_pk("source_snapshot")
        self._snapshots_by_pk[snapshot.pk] = snapshot
        return snapshot

    def save_attempt(self, attempt: TaskAttempt) -> TaskAttempt:
        if attempt.pk == 0:
            attempt.pk = self.next_pk("task_attempt")
            self._attempt_pks_by_task_pk[attempt.task_pk].append(attempt.pk)
        self._attempts_by_pk[attempt.pk] = attempt
        return attempt

    def save_artifact(self, artifact: TaskArtifact) -> TaskArtifact:
        if artifact.pk == 0:
            artifact.pk = self.next_pk("task_artifact")
            self._artifact_pks_by_task_pk[artifact.task_pk].append(artifact.pk)
        self._artifacts_by_pk[artifact.pk] = artifact
        return artifact

    def save_reflow_event(self, event: ReflowEvent) -> ReflowEvent:
        if event.pk == 0:
            event.pk = self.next_pk("reflow_event")
            if event.task_pk is not None:
                self._reflow_event_pks_by_task_pk[event.task_pk].append(event.pk)
        self._reflow_events_by_pk[event.pk] = event
        return event

    def get_task(self, task_id: str) -> Task | None:
        task_pk = self._task_pk_by_public_id.get(task_id)
        if task_pk is None:
            return None
        return self._tasks_by_pk.get(task_pk)

    def list_tasks(self) -> list[Task]:
        return sorted(self._tasks_by_pk.values(), key=lambda task: task.created_at, reverse=True)

    def list_tasks_by_status(self, status) -> list[Task]:
        return sorted(
            [task for task in self._tasks_by_pk.values() if task.status == status],
            key=lambda task: (task.priority, task.created_at),
        )

    def list_task_items(self, task_pk: int) -> list[TaskItem]:
        return [self._items_by_pk[item_pk] for item_pk in self._item_pks_by_task_pk.get(task_pk, [])]

    def get_task_item(self, item_pk: int) -> TaskItem | None:
        return self._items_by_pk.get(item_pk)

    def get_snapshot(self, snapshot_pk: int) -> SourceSnapshot | None:
        return self._snapshots_by_pk.get(snapshot_pk)

    def list_attempts(self, task_pk: int) -> list[TaskAttempt]:
        return [self._attempts_by_pk[attempt_pk] for attempt_pk in self._attempt_pks_by_task_pk.get(task_pk, [])]

    def list_artifacts(self, task_pk: int) -> list[TaskArtifact]:
        return [self._artifacts_by_pk[artifact_pk] for artifact_pk in self._artifact_pks_by_task_pk.get(task_pk, [])]

    def list_reflow_events(self, task_pk: int) -> list[ReflowEvent]:
        return [
            self._reflow_events_by_pk[event_pk]
            for event_pk in self._reflow_event_pks_by_task_pk.get(task_pk, [])
        ]

    def list_reflow_events_by_status(self, status) -> list[ReflowEvent]:
        return [
            event
            for event in self._reflow_events_by_pk.values()
            if event.status == status
        ]
