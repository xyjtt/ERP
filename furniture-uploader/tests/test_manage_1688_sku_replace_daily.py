from __future__ import annotations

import json
from contextlib import contextmanager, nullcontext
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_ROOT = PROJECT_ROOT / "scripts"
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

from manage_1688_sku_replace_daily import (  # noqa: E402
    build_argument_parser,
    build_batch_command,
    build_preview_command,
    load_authoritative_store_names,
    run_daily,
    summarize_batch_state,
)


class ReplaceDailyManagerTests(unittest.TestCase):
    def test_authoritative_roster_excludes_disabled_without_rewriting_shop_name(self) -> None:
        with self.subTest("roster"):
            with self.temp_dir() as root:
                roster_path = root / "scripts" / "account_runtime_migration_roster.json"
                roster_path.parent.mkdir(parents=True)
                roster_path.write_text(
                    json.dumps(
                        {
                            "accounts": [
                                {
                                    "account_key": "pingcan",
                                    "migration_order": 3,
                                    "migration_group": "normal",
                                    "expected_shop_name": "阿里巴巴-常州平灿家居有限公司",
                                },
                                {
                                    "account_key": "pingcan_rpa",
                                    "migration_order": 4,
                                    "migration_group": "disabled",
                                    "expected_shop_name": "阿里巴巴-常州平灿家居有限公司",
                                },
                            ]
                        },
                        ensure_ascii=False,
                    ),
                    encoding="utf-8",
                )
                self.assertEqual(
                    load_authoritative_store_names(root),
                    ["阿里巴巴-常州平灿家居有限公司"],
                )

    def test_parser_defaults_to_formal_replace_schedule_contract(self) -> None:
        args = build_argument_parser().parse_args(["run"])

        self.assertEqual(args.worker_task_name, "YYDD-1688-Crawler-Worker")
        self.assertEqual(args.source_database, "JSReportReplica")
        self.assertEqual(args.source_table, "app.op_stop_sale")
        self.assertEqual(args.batch_size, 10)
        self.assertEqual(args.mode, "preview")

    def test_preview_command_uses_replacement_handling_and_app_source(self) -> None:
        with self.temp_dir() as root:
            roster_path = root / "scripts" / "account_runtime_migration_roster.json"
            roster_path.parent.mkdir(parents=True)
            roster_path.write_text(
                json.dumps(
                    {
                        "accounts": [
                            {
                                "account_key": "pingcan",
                                "migration_order": 1,
                                "migration_group": "normal",
                                "expected_shop_name": "阿里巴巴-常州平灿家居有限公司",
                            }
                        ]
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            args = build_argument_parser().parse_args(
                ["run", "--shared-runtime-root", str(root)]
            )
            command = build_preview_command(args, root / "preview")

        self.assertEqual(command[command.index("--handling") + 1], "全渠道替换")
        self.assertEqual(command[command.index("--database") + 1], "JSReportReplica")
        self.assertEqual(command[command.index("--table") + 1], "app.op_stop_sale")
        self.assertIn("阿里巴巴-常州平灿家居有限公司", command)

    def test_batch_command_forwards_worker_task_name(self) -> None:
        args = build_argument_parser().parse_args(
            [
                "run",
                "--worker-task-name",
                "YYDD-1688-Crawler-Worker-Test",
                "--shared-runtime-root",
                "D:/runtime",
                "--jushuitan-root",
                "D:/jst",
            ]
        )
        command = build_batch_command(
            args,
            Path("D:/input"),
            "pingcan",
            "queue-1",
            Path("D:/state.json"),
        )

        self.assertEqual(
            command[command.index("--crawler-worker-task-name") + 1],
            "YYDD-1688-Crawler-Worker-Test",
        )

    def test_summary_uses_only_latest_attempt_and_preserves_system_prompt(self) -> None:
        with self.temp_dir() as root:
            report = root / "replace.jsonl"
            report.write_text(
                json.dumps(
                    {
                        "status": "failed",
                        "error_category": "system_prompt",
                        "system_prompt": "毛重必须为数字",
                        "task_key": "task-1",
                        "store_name": "阿里巴巴-常州平灿家居有限公司",
                        "product_id": "1001",
                        "online_sku": "SKU-1",
                    },
                    ensure_ascii=False,
                )
                + "\n",
                encoding="utf-8",
            )
            old_summary = root / "old.summary.json"
            old_summary.write_text(
                json.dumps(
                    {
                        "run_id": "old",
                        "replace_report_path": str(report),
                        "replace_counts": {"system_prompt": 1},
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            latest_summary = root / "latest.summary.json"
            latest_summary.write_text(
                json.dumps(
                    {
                        "run_id": "latest",
                        "replace_report_path": str(report),
                        "replace_counts": {"system_prompt": 1},
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            state = {
                "status": "completed",
                "batches": {
                    "batch_001.csv": {
                        "classification": "business_terminal",
                        "attempts": [
                            {"classification": "business_terminal", "summary_path": str(old_summary)},
                            {"classification": "business_terminal", "summary_path": str(latest_summary)},
                        ],
                    }
                },
            }
            result = summarize_batch_state(state)

        self.assertEqual(result["classifications"], ["business_terminal"])
        self.assertEqual(result["system_prompt_count"], 1)
        self.assertEqual(result["system_prompt_records"][0]["raw_message"], "毛重必须为数字")
        self.assertEqual(result["non_success_classifications"], ["business_terminal"])

    def test_execute_all_combination_rows_is_business_skipped(self) -> None:
        with self.temp_dir() as root:
            args = build_argument_parser().parse_args(
                [
                    "run",
                    "--date",
                    "2026-08-08",
                    "--mode",
                    "execute",
                    "--yes",
                    "--store",
                    "阿里巴巴-常州平灿家居有限公司",
                    "--output-root",
                    str(root / "output"),
                    "--shared-runtime-root",
                    str(root / "runtime"),
                    "--jushuitan-root",
                    str(root / "jst"),
                ]
            )
            preview_output = json.dumps(
                {
                    "status": "ok",
                    "selected_count": 0,
                    "business_skipped_count": 2,
                    "business_skipped_reason_counts": {"combination_sku": 2},
                    "per_store_preview_csv": [],
                },
                ensure_ascii=False,
            )
            with (
                patch("manage_1688_sku_replace_daily.build_manager_lock", return_value=nullcontext()),
                patch(
                    "manage_1688_sku_replace_daily._run_capture",
                    side_effect=[(0, '{"status":"ok"}'), (0, preview_output)],
                ),
                patch("manage_1688_sku_replace_daily._run_batch") as run_batch,
            ):
                return_code, summary = run_daily(args)

        self.assertEqual(return_code, 0)
        self.assertEqual(summary["status"], "business_skipped")
        self.assertEqual(summary["business_skipped_count"], 2)
        run_batch.assert_not_called()

    def test_existing_terminal_business_date_does_not_run_again(self) -> None:
        with self.temp_dir() as root:
            output_root = root / "output"
            summary_path = output_root / "replace_daily_20260808" / "summary.json"
            summary_path.parent.mkdir(parents=True)
            summary_path.write_text(
                json.dumps(
                    {
                        "manager_run_id": "replace_daily_20260808",
                        "business_date": "2026-08-08",
                        "mode": "execute",
                        "status": "completed_with_exceptions",
                        "system_prompt_count": 1,
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            args = build_argument_parser().parse_args(
                [
                    "run",
                    "--date",
                    "2026-08-08",
                    "--mode",
                    "execute",
                    "--yes",
                    "--output-root",
                    str(output_root),
                    "--shared-runtime-root",
                    str(root / "runtime"),
                    "--jushuitan-root",
                    str(root / "jst"),
                ]
            )
            with (
                patch("manage_1688_sku_replace_daily.build_manager_lock") as manager_lock,
                patch("manage_1688_sku_replace_daily._run_capture") as run_capture,
                patch("manage_1688_sku_replace_daily._run_batch") as run_batch,
            ):
                return_code, summary = run_daily(args)

        self.assertEqual(return_code, 0)
        self.assertEqual(summary["status"], "completed_with_exceptions")
        self.assertEqual(summary["idempotency"], "existing_terminal_summary")
        manager_lock.assert_not_called()
        run_capture.assert_not_called()
        run_batch.assert_not_called()

    def test_system_prompt_makes_daily_summary_completed_with_exceptions(self) -> None:
        with self.temp_dir() as root:
            source_csv = root / "store.csv"
            source_csv.write_text("store_name,product_id,online_sku\n店铺,1001,SKU-1\n", encoding="utf-8")
            report = root / "replace.jsonl"
            report.write_text(
                json.dumps(
                    {
                        "status": "failed",
                        "error_category": "system_prompt",
                        "system_prompt": "毛重必须为数字",
                        "task_key": "task-1",
                    },
                    ensure_ascii=False,
                )
                + "\n",
                encoding="utf-8",
            )
            pipeline_summary = root / "pipeline.summary.json"
            pipeline_summary.write_text(
                json.dumps(
                    {
                        "run_id": "replace_daily_20260808_pingcan_b001_a01",
                        "status": "partial",
                        "replace_return_code": 0,
                        "replace_counts": {"failed": 1},
                        "replace_report_path": str(report),
                        "notification_sent": True,
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            state = {
                "status": "completed",
                "batches": {
                    "batch_001.csv": {
                        "classification": "business_terminal",
                        "attempts": [{"classification": "business_terminal", "summary_path": str(pipeline_summary)}],
                    }
                },
            }
            args = build_argument_parser().parse_args(
                [
                    "run",
                    "--date",
                    "2026-08-08",
                    "--mode",
                    "execute",
                    "--yes",
                    "--store",
                    "店铺",
                    "--output-root",
                    str(root / "output"),
                    "--shared-runtime-root",
                    str(root / "runtime"),
                    "--jushuitan-root",
                    str(root / "jst"),
                ]
            )
            preview_output = json.dumps(
                {
                    "status": "ok",
                    "selected_count": 1,
                    "business_skipped_count": 0,
                    "per_store_preview_csv": [{"store_name": "店铺", "count": 1, "path": str(source_csv)}],
                },
                ensure_ascii=False,
            )
            with (
                patch("manage_1688_sku_replace_daily.build_manager_lock", return_value=nullcontext()),
                patch(
                    "manage_1688_sku_replace_daily._run_capture",
                    side_effect=[(0, '{"status":"ok"}'), (0, preview_output)],
                ),
                patch(
                    "manage_1688_sku_replace_daily._store_binding",
                    return_value={"account_key": "pingcan"},
                ),
                patch("manage_1688_sku_replace_daily._load_system_config", return_value={}),
                patch("manage_1688_sku_replace_daily._run_batch", return_value=(0, state)),
            ):
                return_code, summary = run_daily(args)

        self.assertEqual(return_code, 0)
        self.assertEqual(summary["status"], "completed_with_exceptions")
        self.assertEqual(summary["system_prompt_count"], 1)
        self.assertEqual(summary["system_prompt_records"][0]["raw_message"], "毛重必须为数字")
        self.assertNotEqual(summary["status"], "success")

    @staticmethod
    @contextmanager
    def temp_dir():
        with tempfile.TemporaryDirectory() as temp_dir:
            yield Path(temp_dir)


class ReplaceDailyTaskScriptContractTests(unittest.TestCase):
    def test_windows_task_script_has_formal_s4u_and_launcher_contract(self) -> None:
        script = (
            PROJECT_ROOT / "scripts" / "manage_1688_sku_replace_daily_task.ps1"
        ).read_text(encoding="utf-8")

        self.assertIn('TaskName = "YYDD-1688-Replace-Daily"', script)
        self.assertIn('LauncherTaskName = "YYDD-1688-Replace-Daily-Launcher"', script)
        self.assertIn('[string]$DailyAt = "14:00"', script)
        self.assertIn("New-ScheduledTaskTrigger -Daily -At $DailyAt", script)
        self.assertIn("Register-ScheduledTask -TaskName $TaskName", script)
        self.assertIn("Register-ScheduledTask `\n            -TaskName $LauncherTaskName", script)
        self.assertIn("-MultipleInstances IgnoreNew", script)
        self.assertIn("-LogonType S4U", script)
        self.assertIn('if ($logonType -eq "S4U")', script)
        self.assertIn("Start-ScheduledTask -TaskName $TaskName", script)
        self.assertIn('launch_mode = "s4u"', script)
        self.assertIn('launch_mode = "interactive_runex"', script)
        self.assertIn('New-Object -ComObject "Schedule.Service"', script)
        self.assertIn('$registeredTask.RunEx($null, 4, $session, $null)', script)
        self.assertIn('-UserId "SYSTEM"', script)
        self.assertIn("-LogonType ServiceAccount", script)
        self.assertIn("--crawler-task-wait-seconds", script)
        self.assertIn("--jushuitan-lock-wait-seconds", script)
        self.assertNotIn("--mode submit", script)


if __name__ == "__main__":
    unittest.main()
