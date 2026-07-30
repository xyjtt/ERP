from __future__ import annotations

from pathlib import Path
import json
import sys
import tempfile
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_ROOT = PROJECT_ROOT / "scripts"
RPA_ROOT = PROJECT_ROOT / "rpa"
for path in (SCRIPTS_ROOT, RPA_ROOT):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from operation_saga import OutboxItem
from run_1688_jushuitan_outbox_worker import (
    build_node_command,
    finish_claimed_items,
    write_claimed_handoff,
)


class FakeRepository:
    def __init__(self) -> None:
        self.finished: list[tuple[str, str]] = []

    def finish_outbox(self, item, *, status, **_kwargs) -> None:
        self.finished.append((item.operation_key, status))


class JushuitanOutboxWorkerTests(unittest.TestCase):
    def item(self, key: str, attempts: int = 1) -> OutboxItem:
        return OutboxItem(1, key, "jushuitan.cleanup_1688_link", {"task_id": "old"}, attempts, "c" * 32)

    def test_handoff_forces_durable_operation_key(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "handoff.jsonl"
            write_claimed_handoff(path, [self.item("a" * 64)])
            payload = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(payload["task_id"], "a" * 64)
        self.assertEqual(payload["operation_key"], "a" * 64)

    def test_worker_maps_success_and_bounded_retry(self) -> None:
        repository = FakeRepository()
        first = self.item("a" * 64, attempts=1)
        last = self.item("b" * 64, attempts=3)
        counts = finish_claimed_items(
            repository,  # type: ignore[arg-type]
            [first, last],
            [{"task_id": first.operation_key, "status": "success"}],
            return_code=2,
            max_attempts=3,
            retry_delay_seconds=30,
        )
        self.assertEqual(repository.finished, [
            (first.operation_key, "failed_retryable"),
            (last.operation_key, "failed_terminal"),
        ])
        self.assertEqual(counts["failed_retryable"], 1)
        self.assertEqual(counts["failed_terminal"], 1)

    def test_node_command_is_no_notify_and_explicit_execute(self) -> None:
        command = build_node_command(
            "sync",
            handoff_path=Path("handoff.jsonl"),
            results_dir=Path("results"),
            run_id="run-1",
        )
        self.assertIn("sync:1688", command)
        self.assertIn("--yes", command)
        self.assertIn("--no-notify", command)


if __name__ == "__main__":
    unittest.main()
