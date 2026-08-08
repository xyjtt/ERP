from __future__ import annotations

from pathlib import Path
import hashlib
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
    build_expired_claim_recovery_evidence,
    finish_claimed_items,
    load_approved_operation_keys,
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

    def test_worker_preserves_verified_item_success_when_batch_exit_is_partial(self) -> None:
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
            (first.operation_key, "succeeded"),
            (last.operation_key, "failed_terminal"),
        ])
        self.assertEqual(counts["succeeded"], 1)
        self.assertEqual(counts["failed_terminal"], 1)

    def test_worker_retries_missing_item_result_even_when_batch_exit_is_zero(self) -> None:
        repository = FakeRepository()
        item = self.item("a" * 64, attempts=1)
        counts = finish_claimed_items(
            repository,  # type: ignore[arg-type]
            [item],
            [],
            return_code=0,
            max_attempts=3,
            retry_delay_seconds=30,
        )
        self.assertEqual(repository.finished, [(item.operation_key, "failed_retryable")])
        self.assertEqual(counts["failed_retryable"], 1)

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

    def test_jsonl_approval_requires_exact_hash_and_unique_operation_keys(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "approved.jsonl"
            path.write_text(
                "\n".join(
                    [
                        json.dumps({"operation_key": "b" * 64}),
                        json.dumps({"task_id": "a" * 64}),
                    ]
                ),
                encoding="utf-8",
            )
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            self.assertEqual(
                load_approved_operation_keys(path, digest, "run-1"),
                ["a" * 64, "b" * 64],
            )
            with self.assertRaisesRegex(RuntimeError, "SHA-256"):
                load_approved_operation_keys(path, "0" * 64, "run-1")

    def test_json_approval_requires_matching_run_id(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "approved.json"
            path.write_text(
                json.dumps(
                    {
                        "version": 1,
                        "run_id": "run-2",
                        "operation_keys": ["a" * 64],
                    }
                ),
                encoding="utf-8",
            )
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            with self.assertRaisesRegex(RuntimeError, "run_id"):
                load_approved_operation_keys(path, digest, "run-1")

    def test_expired_claim_recovery_requires_dead_owner_on_this_executor(self) -> None:
        args = type(
            "Args",
            (),
            {
                "recover_expired_claim_owner": "executor-1:1234",
                "recover_expired_claim_attempt_count": 1,
                "recover_expired_claim_reason": "controlled interrupted-run recovery",
                "approved_operation_keys_sha256": "c" * 64,
            },
        )()

        evidence = build_expired_claim_recovery_evidence(
            args,
            hostname="EXECUTOR-1",
            process_checker=lambda pid: False,
        )

        self.assertIsNotNone(evidence)
        assert evidence is not None
        self.assertEqual(evidence["expected_claim_owner"], "executor-1:1234")
        self.assertEqual(evidence["expected_attempt_count"], 1)
        self.assertTrue(evidence["previous_owner_pid_confirmed_inactive"])

    def test_expired_claim_recovery_blocks_a_live_previous_owner(self) -> None:
        args = type(
            "Args",
            (),
            {
                "recover_expired_claim_owner": "executor-1:1234",
                "recover_expired_claim_attempt_count": 1,
                "recover_expired_claim_reason": "controlled interrupted-run recovery",
                "approved_operation_keys_sha256": "c" * 64,
            },
        )()

        with self.assertRaisesRegex(RuntimeError, "still running"):
            build_expired_claim_recovery_evidence(
                args,
                hostname="executor-1",
                process_checker=lambda pid: True,
            )

    def test_expired_claim_recovery_rejects_partial_arguments(self) -> None:
        args = type(
            "Args",
            (),
            {
                "recover_expired_claim_owner": "executor-1:1234",
                "recover_expired_claim_attempt_count": None,
                "recover_expired_claim_reason": "",
                "approved_operation_keys_sha256": "c" * 64,
            },
        )()

        with self.assertRaisesRegex(ValueError, "requires owner, attempt count, and reason"):
            build_expired_claim_recovery_evidence(
                args,
                hostname="executor-1",
                process_checker=lambda pid: False,
            )


if __name__ == "__main__":
    unittest.main()
