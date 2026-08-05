from __future__ import annotations

import argparse
import json
import sys
import tempfile
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_ROOT = PROJECT_ROOT / "scripts"
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

from unittest import mock

from run_1688_stop_sale_pipeline import (
    assert_crawler_worker_paused,
    build_1688_command,
    build_jushuitan_command,
    resolve_shared_lock_path,
    send_pipeline_notification,
)


class StopSalePipelineTests(unittest.TestCase):
    def build_args(self, mode: str) -> argparse.Namespace:
        return argparse.Namespace(
            file="demo.csv",
            mode=mode,
            limit=1,
            yes=mode == "execute",
            skip_login=True,
            no_notify=True,
            shared_lock_path="",
            shared_runtime_root="D:/script_1688",
            timeout_jushuitan_seconds=1200,
            run_id="run-1",
        )

    def test_preview_commands_do_not_include_live_confirmation(self) -> None:
        args = self.build_args("preview")
        handoff = PROJECT_ROOT / "logs" / "handoff.jsonl"
        command_1688 = build_1688_command(args, handoff)

        self.assertNotIn("--yes", command_1688)
        self.assertIn("--jushuitan-handoff-out", command_1688)
        with self.assertRaisesRegex(ValueError, "execute-only"):
            build_jushuitan_command(args, handoff, PROJECT_ROOT.parent)

    def test_execute_commands_require_explicit_live_flags(self) -> None:
        args = self.build_args("execute")
        with tempfile.TemporaryDirectory() as temp_dir:
            handoff = Path(temp_dir) / "handoff.jsonl"
            handoff.write_text(json.dumps({"operation_key": "a" * 64}), encoding="utf-8")
            command_1688 = build_1688_command(args, handoff)
            command_jushuitan = build_jushuitan_command(args, handoff, PROJECT_ROOT.parent)

            self.assertIn("--yes", command_1688)
            self.assertIn("--yes", command_jushuitan)
            self.assertIn("--approved-operation-keys-file", command_jushuitan)
            self.assertIn("--approved-operation-keys-sha256", command_jushuitan)
            self.assertTrue(command_jushuitan[1].endswith("run_1688_jushuitan_outbox_worker.py"))
            self.assertEqual(command_jushuitan[command_jushuitan.index("--action") + 1], "cleanup")

    def test_shared_lock_is_scoped_to_1688_account(self) -> None:
        args = self.build_args("execute")
        self.assertEqual(
            resolve_shared_lock_path(args, "gonglai"),
            Path("D:/script_1688/artifacts/locks/ali1688_account_gonglai.lock").resolve(),
        )
        self.assertNotEqual(
            resolve_shared_lock_path(args, "gonglai"),
            resolve_shared_lock_path(args, "lechang"),
        )

    def test_shared_lock_rejects_unsafe_account_key(self) -> None:
        args = self.build_args("execute")

        with self.assertRaisesRegex(ValueError, "account_key"):
            resolve_shared_lock_path(args, "../shared")

    def test_pipeline_notification_can_be_disabled_without_credentials(self) -> None:
        self.assertFalse(send_pipeline_notification("demo", disabled=True))

    def test_worker_gate_blocks_active_worker_by_default(self) -> None:
        state = {
            "scheduled_task_exists": True,
            "scheduled_task_state": "Running",
            "worker_process_count": 1,
            "profile_edge_process_count": 0,
        }
        with mock.patch(
            "run_1688_stop_sale_pipeline.query_crawler_worker_state",
            return_value=dict(state),
        ):
            with self.assertRaisesRegex(RuntimeError, "still active"):
                assert_crawler_worker_paused("YYDD-1688-Crawler-Worker", "gonglai")

    def test_worker_gate_protocol_bypass_keeps_profile_edge_check(self) -> None:
        state = {
            "scheduled_task_exists": True,
            "scheduled_task_state": "Running",
            "worker_process_count": 1,
            "profile_edge_process_count": 0,
        }
        with mock.patch(
            "run_1688_stop_sale_pipeline.query_crawler_worker_state",
            return_value=dict(state),
        ):
            result = assert_crawler_worker_paused(
                "YYDD-1688-Crawler-Worker",
                "gonglai",
                allow_active_worker=True,
            )
        self.assertTrue(result["worker_activity_bypassed_by_protocol"])

        state_with_edge = dict(state, profile_edge_process_count=1)
        with mock.patch(
            "run_1688_stop_sale_pipeline.query_crawler_worker_state",
            return_value=state_with_edge,
        ):
            with self.assertRaisesRegex(RuntimeError, "Profile Edge"):
                assert_crawler_worker_paused(
                    "YYDD-1688-Crawler-Worker",
                    "gonglai",
                    allow_active_worker=True,
                )


if __name__ == "__main__":
    unittest.main()
