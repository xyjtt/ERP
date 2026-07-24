from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_ROOT = PROJECT_ROOT / "scripts"
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

from run_1688_sku_replace_pipeline import (  # noqa: E402
    build_argument_parser,
    build_1688_command,
    build_jushuitan_command,
    build_jushuitan_environment,
    build_pipeline_notification,
    count_statuses,
)


class Run1688SkuReplacePipelineTests(unittest.TestCase):
    def build_args(self) -> SimpleNamespace:
        return SimpleNamespace(
            file="replace.csv",
            mode="execute",
            run_id="replace-001",
            limit=1,
            skip_login=True,
            no_notify=False,
            shared_runtime_root="D:/runtime/1688",
        )

    def test_1688_command_uses_replace_system(self) -> None:
        command = build_1688_command(self.build_args(), Path("handoff.jsonl"))

        self.assertIn("1688_sku_replace", command)
        self.assertIn("--jushuitan-handoff-out", command)
        self.assertIn("--yes", command)
        self.assertEqual(
            command[command.index("--shared-runtime-root") + 1],
            str(Path(self.build_args().shared_runtime_root).resolve()),
        )

    def test_duplicate_runs_fail_fast_on_shared_lock_by_default(self) -> None:
        args = build_argument_parser().parse_args(["--file", "replace.csv"])

        self.assertEqual(args.lock_wait_seconds, 0)

    def test_jushuitan_command_uses_sync_action(self) -> None:
        command = build_jushuitan_command(
            self.build_args(),
            Path("handoff.jsonl"),
            Path("results"),
        )

        self.assertIn("sync:1688", command)
        self.assertIn("--yes", command)

    def test_jushuitan_environment_points_legacy_schema_to_handoff(self) -> None:
        environment = build_jushuitan_environment(Path("handoff.jsonl"))

        self.assertTrue(environment["JST_LOGIN_URL"])
        self.assertTrue(environment["JST_PRODUCT_URL"])
        self.assertTrue(environment["EXCEL_PATH"].endswith("handoff.jsonl"))

    def test_count_statuses(self) -> None:
        self.assertEqual(
            count_statuses([{"status": "success"}, {"status": "success"}, {"status": "failed"}]),
            {"success": 2, "failed": 1},
        )

    def test_pipeline_notification_contains_both_stage_counts(self) -> None:
        content = build_pipeline_notification({
            "run_id": "replace-001",
            "status": "partial",
            "replace_counts": {"success": 2, "failed": 1},
            "jushuitan_counts": {"success": 2},
            "error_message": "demo",
            "summary_path": "summary.json",
        })

        self.assertIn("replace-001", content)
        self.assertIn('"failed": 1', content)
        self.assertIn("summary.json", content)


if __name__ == "__main__":
    unittest.main()
