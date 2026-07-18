from __future__ import annotations

import argparse
import sys
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_ROOT = PROJECT_ROOT / "scripts"
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

from run_1688_stop_sale_pipeline import (
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
        )

    def test_preview_commands_do_not_include_live_confirmation(self) -> None:
        args = self.build_args("preview")
        handoff = PROJECT_ROOT / "logs" / "handoff.jsonl"
        command_1688 = build_1688_command(args, handoff)
        command_jushuitan = build_jushuitan_command(args, handoff, PROJECT_ROOT.parent)

        self.assertNotIn("--yes", command_1688)
        self.assertNotIn("--yes", command_jushuitan)
        self.assertIn("--jushuitan-handoff-out", command_1688)

    def test_execute_commands_require_explicit_live_flags(self) -> None:
        args = self.build_args("execute")
        handoff = PROJECT_ROOT / "logs" / "handoff.jsonl"
        command_1688 = build_1688_command(args, handoff)
        command_jushuitan = build_jushuitan_command(args, handoff, PROJECT_ROOT.parent)

        self.assertIn("--yes", command_1688)
        self.assertIn("--yes", command_jushuitan)
        self.assertIn("--no-notify", command_jushuitan)

    def test_shared_lock_defaults_to_existing_1688_runtime_lock(self) -> None:
        args = self.build_args("execute")
        self.assertEqual(
            resolve_shared_lock_path(args),
            Path("D:/script_1688/artifacts/locks/ali1688_full_cycle.lock").resolve(),
        )

    def test_pipeline_notification_can_be_disabled_without_credentials(self) -> None:
        self.assertFalse(send_pipeline_notification("demo", disabled=True))


if __name__ == "__main__":
    unittest.main()
