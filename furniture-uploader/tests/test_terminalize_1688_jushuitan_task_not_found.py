from __future__ import annotations

from argparse import Namespace
from pathlib import Path
import sys
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_ROOT = PROJECT_ROOT / "scripts"
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

from terminalize_1688_jushuitan_task_not_found import build_parser, run


class _FakeRepository:
    def __init__(self) -> None:
        self.terminalize_calls: list[dict[str, object]] = []

    def get_outbox_state(self, operation_key: str) -> dict[str, object]:
        return {
            "operation_key": operation_key,
            "status": "failed_retryable",
            "last_error_code": "task_not_found",
            "attempt_count": 3,
            "run_id": "daily_20260804_s01_b001",
            "saga_state": "jushuitan_pending",
        }

    def terminalize_outbox(self, operation_key: str, **kwargs):
        self.terminalize_calls.append({"operation_key": operation_key, **kwargs})
        return {
            "operation_key": operation_key,
            "status": "failed_terminal",
            "error_code": "task_not_found",
        }


def _args(*, yes: bool) -> Namespace:
    return Namespace(
        operation_key="a" * 64,
        expected_status="failed_retryable",
        expected_error_code="task_not_found",
        expected_run_id="daily_20260804_s01_b001",
        expected_attempt_count=3,
        expected_saga_state="jushuitan_pending",
        reason="历史精确查询确认无匹配任务",
        shared_runtime_root="local-test-runtime",
        yes=yes,
    )


class TerminalizeTaskNotFoundCliTests(unittest.TestCase):
    def test_parser_defaults_to_preview(self) -> None:
        args = build_parser().parse_args(
            [
                "--operation-key",
                "a" * 64,
                "--expected-status",
                "failed_retryable",
                "--expected-error-code",
                "task_not_found",
                "--expected-run-id",
                "daily_20260804_s01_b001",
                "--expected-attempt-count",
                "3",
                "--expected-saga-state",
                "jushuitan_pending",
                "--reason",
                "approved historical terminalization",
            ]
        )

        self.assertFalse(args.yes)

    def test_default_mode_is_preview_and_does_not_apply(self) -> None:
        repository = _FakeRepository()
        result = run(_args(yes=False), repository_factory=lambda _root: repository)

        self.assertEqual(result["status"], "preview")
        self.assertEqual(repository.terminalize_calls, [])

    def test_yes_applies_one_exact_operation_key(self) -> None:
        repository = _FakeRepository()
        result = run(_args(yes=True), repository_factory=lambda _root: repository)

        self.assertEqual(result["status"], "applied")
        self.assertEqual(len(repository.terminalize_calls), 1)
        self.assertEqual(repository.terminalize_calls[0]["operation_key"], "a" * 64)
        self.assertEqual(repository.terminalize_calls[0]["expected_error_code"], "task_not_found")

    def test_preview_fails_closed_on_any_exact_scope_drift(self) -> None:
        repository = _FakeRepository()
        repository.get_outbox_state = lambda _key: {
            "operation_key": "a" * 64,
            "status": "failed_retryable",
            "last_error_code": "task_not_found",
            "attempt_count": 3,
            "run_id": "daily_20260804_s01_b999",
            "saga_state": "jushuitan_pending",
        }

        with self.assertRaisesRegex(RuntimeError, "run_id"):
            run(_args(yes=False), repository_factory=lambda _root: repository)


if __name__ == "__main__":
    unittest.main()
