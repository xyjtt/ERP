from __future__ import annotations

import sys
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


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
    run,
    resolve_pipeline_account,
    resolve_shared_lock_path,
)
from sku_offline_tasks import OfflineTask  # noqa: E402


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
            shared_lock_path="",
            no_shared_lock=False,
            lock_stale_seconds=21600,
            lock_wait_seconds=0,
            lock_poll_seconds=10,
            account_key="",
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

    def test_shared_lock_is_scoped_to_account(self) -> None:
        args = self.build_args()

        path = resolve_shared_lock_path(args, "gonglai")

        self.assertEqual(path.name, "ali1688_account_gonglai.lock")

    def test_pipeline_account_requires_one_store(self) -> None:
        args = self.build_args()
        tasks = [
            SimpleNamespace(store_name="STORE-A"),
            SimpleNamespace(store_name="STORE-B"),
        ]

        with self.assertRaisesRegex(RuntimeError, "exactly one"):
            resolve_pipeline_account(args, tasks)

    def test_jushuitan_command_uses_sync_action(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            handoff = Path(temp_dir) / "handoff.jsonl"
            handoff.write_text(json.dumps({"operation_key": "a" * 64}), encoding="utf-8")
            command = build_jushuitan_command(
                self.build_args(),
                handoff,
                Path(temp_dir) / "results",
            )

            self.assertTrue(command[1].endswith("run_1688_jushuitan_outbox_worker.py"))
            self.assertEqual(command[command.index("--action") + 1], "sync")
            self.assertIn("--approved-operation-keys-file", command)
            self.assertIn("--approved-operation-keys-sha256", command)
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

    def test_combination_sku_exits_before_lease_saga_browser_and_jushuitan(self) -> None:
        task = OfflineTask(
            source_file="replace.csv", source_sheet="CSV", source_row_number=2,
            store_name="STORE-A", platform="Alibaba", product_id="1001",
            online_sku="OLD", handling="全渠道替换", replacement_sku="运营自行组合替换",
            change_image="", platform_store_item_code="CODE", raw={},
        )
        args = build_argument_parser().parse_args([
            "--file", "replace.csv",
            "--mode", "execute",
            "--yes",
            "--no-notify",
            "--run-id", "combination-sku-test",
        ])

        with (
            tempfile.TemporaryDirectory() as temp_dir,
            patch("run_1688_sku_replace_pipeline.PROJECT_ROOT", Path(temp_dir)),
            patch(
                "run_1688_sku_replace_pipeline.load_partitioned_replace_tasks",
                return_value=([], [task]),
            ),
            patch("run_1688_sku_replace_pipeline.resolve_stop_sale_app_config") as app_config,
            patch("run_1688_sku_replace_pipeline.RuntimeLeaseGuard") as lease_guard,
            patch("run_1688_sku_replace_pipeline.OperationSagaRepository") as saga_repository,
            patch("run_1688_sku_replace_pipeline.build_shared_lock") as shared_lock,
            patch("run_1688_sku_replace_pipeline.build_jushuitan_lock") as jushuitan_lock,
            patch("run_1688_sku_replace_pipeline.run_stage_command") as run_stage,
        ):
            return_code = run(args)
            summary = Path(temp_dir, "logs", "sku_replace", "pipelines", "combination-sku-test.summary.json")
            summary_text = summary.read_text(encoding="utf-8")

        self.assertEqual(return_code, 0)
        self.assertIn('"status": "business_skipped"', summary_text)
        self.assertIn('"exception_reason": "组合货号"', summary_text)
        app_config.assert_not_called()
        lease_guard.assert_not_called()
        saga_repository.assert_not_called()
        shared_lock.assert_not_called()
        jushuitan_lock.assert_not_called()
        run_stage.assert_not_called()


if __name__ == "__main__":
    unittest.main()
