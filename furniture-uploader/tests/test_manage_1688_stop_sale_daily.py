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
    build_batch_retry_input,
    _json_from_output,
    _load_preview_report,
    _load_pipeline_result,
    build_argument_parser,
    build_pipeline_command,
    build_preview_command,
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
        self.assertEqual(command[command.index("--1688-timeout-seconds") + 1], "3600")
        self.assertEqual(command[command.index("--jushuitan-timeout-seconds") + 1], "1800")

    def test_preview_command_supplies_shared_runtime_for_source_credentials(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            args = self.build_args(Path(temp_dir), mode="preview")
            command = build_preview_command(args, Path(temp_dir) / "preview")

        self.assertEqual(
            command[command.index("--shared-runtime-root") + 1],
            str(Path("E:/1688/1688-script-new").resolve()),
        )

    def test_scheduler_defaults_to_current_business_date_and_small_recoverable_batches(self) -> None:
        args = build_argument_parser().parse_args(["run"])

        from datetime import date

        self.assertEqual(args.date, date.today().isoformat())
        self.assertEqual(args.batch_size, 10)
        self.assertEqual(args.batch_max_attempts, 2)
        self.assertEqual(args.batch_retry_backoff_seconds, 60)

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

    def test_store_input_never_splits_skus_for_the_same_product(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "store.csv"
            self.write_store_csv(
                source,
                [
                    ("STORE-A", "PRODUCT-A", "SKU-1"),
                    ("STORE-A", "PRODUCT-B", "SKU-2"),
                    ("STORE-A", "PRODUCT-B", "SKU-3"),
                    ("STORE-A", "PRODUCT-C", "SKU-4"),
                ],
            )

            paths = split_store_input(source, root / "batches", 2)
            batches = [path.read_text(encoding="utf-8-sig").splitlines()[1:] for path in paths]

        self.assertEqual([len(rows) for rows in batches], [1, 2, 1])
        self.assertTrue(all("PRODUCT-B" in row for row in batches[1]))

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

    def test_retry_input_contains_only_unclosed_1688_or_jushuitan_items(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "batch.csv"
            source.write_text(
                "store_name,product_id,online_sku,platform_store_item_code\n"
                "STORE-A,P1,SKU-1,CODE-1\n"
                "STORE-A,P2,SKU-2,CODE-2\n"
                "STORE-A,P3,SKU-3,CODE-3\n"
                "STORE-A,P4,SKU-4,CODE-4\n",
                encoding="utf-8",
            )
            offline_path = root / "offline.jsonl"
            offline_path.write_text(
                "\n".join(
                    json.dumps(record)
                    for record in (
                        {"status": "success", "store_name": "STORE-A", "product_id": "P1", "online_sku": "SKU-1"},
                        {"status": "already_offline", "store_name": "STORE-A", "product_id": "P2", "online_sku": "SKU-2"},
                        {
                            "status": "failed",
                            "store_name": "STORE-A",
                            "product_id": "P3",
                            "online_sku": "SKU-3",
                            "error_category": "product_unavailable",
                        },
                    )
                )
                + "\n",
                encoding="utf-8",
            )
            jushuitan_path = root / "jushuitan.jsonl"
            jushuitan_path.write_text(
                "\n".join(
                    json.dumps(record)
                    for record in (
                        {
                            "status": "success",
                            "store_name": "STORE-A",
                            "product_id": "P1",
                            "online_sku": "SKU-1",
                            "platform_store_item_code": "CODE-1",
                        },
                        {
                            "status": "failed",
                            "store_name": "STORE-A",
                            "product_id": "P2",
                            "online_sku": "SKU-2",
                            "platform_store_item_code": "CODE-2",
                            "category": "row_selection_failed",
                        },
                    )
                )
                + "\n",
                encoding="utf-8",
            )
            retry_path = root / "retry.csv"

            plan = build_batch_retry_input(
                source,
                [
                    {
                        "offline_report_path": str(offline_path),
                        "jushuitan_report_path": str(jushuitan_path),
                    }
                ],
                retry_path,
            )
            retry_rows = retry_path.read_text(encoding="utf-8-sig").splitlines()[1:]

        self.assertEqual(plan["retry_count"], 2)
        self.assertEqual(plan["business_terminal_count"], 1)
        self.assertTrue(any("SKU-2" in row for row in retry_rows))
        self.assertTrue(any("SKU-4" in row for row in retry_rows))
        self.assertFalse(any("SKU-1" in row for row in retry_rows))
        self.assertFalse(any("SKU-3" in row for row in retry_rows))

    def test_retry_input_tolerates_missing_jushuitan_report_after_1688_timeout(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "batch.csv"
            self.write_store_csv(source, [("STORE-A", "1", "SKU-1")])
            offline_path = root / "offline.jsonl"
            offline_path.write_text(
                json.dumps(
                    {
                        "status": "success",
                        "store_name": "STORE-A",
                        "product_id": "1",
                        "online_sku": "SKU-1",
                    }
                )
                + "\n",
                encoding="utf-8",
            )

            plan = build_batch_retry_input(
                source,
                [{"offline_report_path": str(offline_path), "jushuitan_report_path": ""}],
                root / "retry.csv",
            )

        self.assertEqual(plan["retry_count"], 1)
        self.assertEqual(plan["retry_reasons"], {"missing_jushuitan_result": 1})

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
                patch(
                    "manage_1688_stop_sale_daily.build_batch_retry_input",
                    side_effect=[
                        {
                            "retry_count": 0,
                            "business_terminal_count": 1,
                            "offline_counts": {"failed": 1},
                            "jushuitan_counts": {},
                        },
                        {
                            "retry_count": 0,
                            "business_terminal_count": 0,
                            "offline_counts": {"success": 1},
                            "jushuitan_counts": {"success": 1},
                        },
                    ],
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
                patch(
                    "manage_1688_stop_sale_daily.build_batch_retry_input",
                    side_effect=[
                        {
                            "retry_count": 2,
                            "business_terminal_count": 0,
                            "offline_counts": {"failed": 2},
                            "jushuitan_counts": {},
                        },
                        {
                            "retry_count": 0,
                            "business_terminal_count": 0,
                            "offline_counts": {"success": 1},
                            "jushuitan_counts": {"success": 1},
                        },
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

    def test_timeout_batch_retries_only_generated_retry_input(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "store-a.csv"
            self.write_store_csv(source, [("STORE-A", "1", "SKU-1")])
            retry_file = root / "scheduler" / "retry.csv"
            retry_file.parent.mkdir(parents=True)
            self.write_store_csv(retry_file, [("STORE-A", "1", "SKU-1")])
            args = self.build_args(root / "scheduler")
            args.batch_retry_backoff_seconds = 0
            preview = {
                "selected_count": 1,
                "required_null_counts": {},
                "per_store_preview_csv": [
                    {"store_name": "STORE-A", "count": 1, "path": str(source)}
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
                patch("manage_1688_stop_sale_daily._run_logged", side_effect=[124, 0]) as run_logged,
                patch(
                    "manage_1688_stop_sale_daily._load_pipeline_result",
                    side_effect=[
                        {"state": "failed", "safety_stop": False},
                        {"state": "success", "safety_stop": False},
                    ],
                ),
                patch(
                    "manage_1688_stop_sale_daily.build_batch_retry_input",
                    side_effect=[
                        {
                            "retry_count": 1,
                            "business_terminal_count": 0,
                            "retry_input_file": str(retry_file),
                            "offline_counts": {},
                            "jushuitan_counts": {},
                        },
                        {
                            "retry_count": 0,
                            "business_terminal_count": 0,
                            "retry_input_file": "",
                            "offline_counts": {"success": 1},
                            "jushuitan_counts": {"success": 1},
                        },
                    ],
                ),
                patch("manage_1688_stop_sale_daily.send_manager_notification", return_value=True),
            ):
                return_code, summary = run_daily(args)

        second_command = run_logged.call_args_list[1].args[0]
        self.assertEqual(return_code, 0)
        self.assertEqual(summary["status"], "success")
        self.assertEqual(len(summary["batch_attempts"]), 2)
        self.assertEqual(second_command[second_command.index("--file") + 1], str(retry_file.resolve()))

    def test_missing_pipeline_summary_is_infrastructure_failure(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            result = _load_pipeline_result(Path(temp_dir), "missing", 0)

        self.assertEqual(result["state"], "failed")


if __name__ == "__main__":
    unittest.main()
