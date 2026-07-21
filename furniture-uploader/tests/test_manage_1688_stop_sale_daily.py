from __future__ import annotations

import json
import sys
import tempfile
import unittest
from contextlib import contextmanager, nullcontext
from pathlib import Path
from unittest.mock import patch


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_ROOT = PROJECT_ROOT / "scripts"
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

from manage_1688_stop_sale_daily import (
    _json_from_output,
    _load_preview_report,
    _load_pipeline_result,
    build_argument_parser,
    build_pipeline_command,
    paused_worker,
    run_daily,
    split_store_input,
)


class Manage1688StopSaleDailyTests(unittest.TestCase):
    @staticmethod
    def write_store_csv(path: Path, rows: list[tuple[str, str, str]]) -> None:
        content = "store_name,product_id,online_sku\n" + "".join(
            f"{store},{product},{sku}\n" for store, product, sku in rows
        )
        path.write_text(content, encoding="utf-8")

    def build_args(self, output_root: Path, *, mode: str = "execute"):
        values = [
            "run",
            "--date",
            "2026-07-20",
            "--mode",
            mode,
            "--output-root",
            str(output_root),
            "--shared-runtime-root",
            "E:/1688/1688-script-new",
            "--jushuitan-root",
            "D:/deploy/erp-stop-sale/jushuitan-sku-offline-batch",
            "--worker-task-name",
            "YYDD-1688-Crawler-Worker",
            "--no-notify",
        ]
        if mode == "execute":
            values.append("--yes")
        return build_argument_parser().parse_args(values)

    def test_json_parser_uses_the_last_json_object(self) -> None:
        output = 'noise\n{"status": "old"}\nmore\n{"status": "ok", "count": 4}\n'

        self.assertEqual(_json_from_output(output), {"status": "ok", "count": 4})

    def test_pipeline_command_is_full_execute_with_correlated_run_id(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            args = self.build_args(Path(temp_dir))
            command = build_pipeline_command(args, Path(temp_dir) / "store.csv", "daily_run_s01")

        self.assertIn("--yes", command)
        self.assertIn("--skip-login", command)
        self.assertEqual(command[command.index("--run-id") + 1], "daily_run_s01")
        self.assertEqual(command[command.index("--1688-timeout-seconds") + 1], "10800")
        self.assertEqual(command[command.index("--jushuitan-timeout-seconds") + 1], "7200")

    def test_store_input_is_split_into_recoverable_batches(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "store.csv"
            self.write_store_csv(
                source,
                [("STORE-A", str(index), f"SKU-{index}") for index in range(1, 6)],
            )

            paths = split_store_input(source, root / "batches", 2)
            row_counts = [len(path.read_text(encoding="utf-8-sig").splitlines()) - 1 for path in paths]

        self.assertEqual([path.name for path in paths], ["batch_001.csv", "batch_002.csv", "batch_003.csv"])
        self.assertEqual(row_counts, [2, 2, 1])

    def test_full_preview_report_supplies_per_store_csv_paths(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            report_path = Path(temp_dir) / "report.json"
            report_path.write_text(
                json.dumps(
                    {
                        "selected_count": 1,
                        "per_store_preview_csv": [
                            {"store_name": "STORE-A", "count": 1, "path": "store-a.csv"}
                        ],
                    }
                ),
                encoding="utf-8",
            )

            preview = _load_preview_report({"report_path": str(report_path), "selected_count": 1})

        self.assertEqual(preview["per_store_preview_csv"][0]["store_name"], "STORE-A")

    def test_worker_is_disabled_stopped_then_restored_to_original_running_state(self) -> None:
        running = {
            "scheduled_task_exists": True,
            "scheduled_task_state": "Running",
            "worker_process_count": 1,
            "profile_edge_process_count": 0,
        }
        paused = {
            "scheduled_task_exists": True,
            "scheduled_task_state": "Disabled",
            "worker_process_count": 0,
            "profile_edge_process_count": 0,
        }
        actions: list[str] = []
        with (
            patch(
                "manage_1688_stop_sale_daily.query_crawler_worker_state",
                side_effect=[running, running],
            ),
            patch("manage_1688_stop_sale_daily._wait_for_worker_quiet", return_value=paused),
            patch(
                "manage_1688_stop_sale_daily._powershell_task_action",
                side_effect=lambda action, task_name: actions.append(action),
            ),
        ):
            with paused_worker("YYDD-1688-Crawler-Worker") as lifecycle:
                self.assertEqual(lifecycle["paused"]["worker_process_count"], 0)

        self.assertEqual(actions, ["Disable", "Stop", "Enable", "Start"])
        self.assertEqual(lifecycle["restored"]["scheduled_task_state"], "Running")

    def test_no_tasks_is_a_successful_noop(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            output_root = Path(temp_dir) / "scheduler"
            args = self.build_args(output_root)
            responses = [
                (0, json.dumps({"status": "ok"})),
                (
                    0,
                    json.dumps(
                        {
                            "selected_count": 0,
                            "required_null_counts": {},
                            "per_store_preview_csv": [],
                        }
                    ),
                ),
            ]
            with (
                patch("manage_1688_stop_sale_daily._run_capture", side_effect=responses),
                patch("manage_1688_stop_sale_daily.build_manager_lock", return_value=nullcontext()),
                patch("manage_1688_stop_sale_daily.send_manager_notification", return_value=True),
            ):
                return_code, summary = run_daily(args)

            latest = json.loads((output_root / "latest.summary.json").read_text(encoding="utf-8"))

        self.assertEqual(return_code, 0)
        self.assertEqual(summary["status"], "no_tasks")
        self.assertEqual(latest["status"], "no_tasks")

    def test_business_exception_continues_other_stores_and_returns_success(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            first = root / "store-a.csv"
            second = root / "store-b.csv"
            self.write_store_csv(first, [("STORE-A", "1", "SKU-1")])
            self.write_store_csv(second, [("STORE-B", "2", "SKU-2")])
            args = self.build_args(root / "scheduler")
            preview = {
                "selected_count": 2,
                "required_null_counts": {},
                "per_store_preview_csv": [
                    {"store_name": "STORE-A", "count": 1, "path": str(first)},
                    {"store_name": "STORE-B", "count": 1, "path": str(second)},
                ],
            }

            @contextmanager
            def fake_paused_worker(_task_name):
                yield {
                    "before": {"scheduled_task_state": "Running"},
                    "paused": {"scheduled_task_state": "Disabled"},
                    "restored": {"scheduled_task_state": "Running"},
                }

            pipeline_results = [
                {"state": "completed_with_exceptions", "audit_status": "partial"},
                {"state": "success", "audit_status": "success"},
            ]
            with (
                patch(
                    "manage_1688_stop_sale_daily._run_capture",
                    side_effect=[(0, json.dumps({"status": "ok"})), (0, json.dumps(preview))],
                ),
                patch("manage_1688_stop_sale_daily.build_manager_lock", return_value=nullcontext()),
                patch("manage_1688_stop_sale_daily.paused_worker", fake_paused_worker),
                patch("manage_1688_stop_sale_daily._run_logged", side_effect=[2, 0]) as run_logged,
                patch(
                    "manage_1688_stop_sale_daily._load_pipeline_result",
                    side_effect=pipeline_results,
                ),
                patch("manage_1688_stop_sale_daily.send_manager_notification", return_value=True),
            ):
                return_code, summary = run_daily(args)

        self.assertEqual(return_code, 0)
        self.assertEqual(summary["status"], "completed_with_exceptions")
        self.assertEqual(summary["exception_store_count"], 1)
        self.assertEqual(run_logged.call_count, 2)

    def test_safety_exception_stops_only_the_affected_store(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            first = root / "store-a.csv"
            second = root / "store-b.csv"
            self.write_store_csv(first, [("STORE-A", "1", "SKU-1"), ("STORE-A", "2", "SKU-2")])
            self.write_store_csv(second, [("STORE-B", "3", "SKU-3")])
            args = self.build_args(root / "scheduler")
            args.batch_size = 1
            preview = {
                "selected_count": 3,
                "required_null_counts": {},
                "per_store_preview_csv": [
                    {"store_name": "STORE-A", "count": 2, "path": str(first)},
                    {"store_name": "STORE-B", "count": 1, "path": str(second)},
                ],
            }

            @contextmanager
            def fake_paused_worker(_task_name):
                yield {
                    "before": {"scheduled_task_state": "Running"},
                    "paused": {"scheduled_task_state": "Disabled"},
                    "restored": {"scheduled_task_state": "Running"},
                }

            with (
                patch(
                    "manage_1688_stop_sale_daily._run_capture",
                    side_effect=[(0, json.dumps({"status": "ok"})), (0, json.dumps(preview))],
                ),
                patch("manage_1688_stop_sale_daily.build_manager_lock", return_value=nullcontext()),
                patch("manage_1688_stop_sale_daily.paused_worker", fake_paused_worker),
                patch("manage_1688_stop_sale_daily._run_logged", side_effect=[2, 0]) as run_logged,
                patch(
                    "manage_1688_stop_sale_daily._load_pipeline_result",
                    side_effect=[
                        {"state": "completed_with_exceptions", "safety_stop": True},
                        {"state": "success", "safety_stop": False},
                    ],
                ),
                patch("manage_1688_stop_sale_daily.send_manager_notification", return_value=True),
            ):
                return_code, summary = run_daily(args)

        self.assertEqual(return_code, 0)
        self.assertEqual(summary["status"], "completed_with_exceptions")
        self.assertTrue(summary["stores"][0]["safety_stopped"])
        self.assertEqual(summary["stores"][0]["completed_batch_count"], 1)
        self.assertEqual(summary["stores"][1]["state"], "success")
        self.assertEqual(run_logged.call_count, 2)

    def test_missing_pipeline_summary_is_infrastructure_failure(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            result = _load_pipeline_result(Path(temp_dir), "missing", 0)

        self.assertEqual(result["state"], "failed")


if __name__ == "__main__":
    unittest.main()
