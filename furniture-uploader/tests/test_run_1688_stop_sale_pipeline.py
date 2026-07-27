from __future__ import annotations

from argparse import Namespace
from pathlib import Path
import json
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from types import SimpleNamespace


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_ROOT = PROJECT_ROOT / "scripts"
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

from run_1688_stop_sale_pipeline import (
    assert_no_recent_stop_sale_runs,
    assert_crawler_worker_paused,
    build_argument_parser,
    build_1688_command,
    build_jushuitan_command,
    build_jushuitan_environment,
    derive_audit_status,
    load_selected_audit_tasks,
    notify_execute_startup_failure,
    PipelineStageTimeoutError,
    run_stage_command,
    run_pipeline,
)


class Run1688StopSalePipelineTests(unittest.TestCase):
    def build_args(self, *, mode: str = "execute") -> Namespace:
        return Namespace(
            mode=mode,
            file="D:/input/tasks.csv",
            limit=1,
            skip_login=True,
            no_notify=True,
            run_id="run_20260718_001",
            source_database="JSDataMiddlePlatform",
            source_table="dbo.op_stop_sale",
            timeout_1688_seconds=2700,
            timeout_jushuitan_seconds=1200,
            shared_runtime_root="D:/runtime/1688",
        )

    def test_commands_share_deterministic_run_id(self) -> None:
        args = self.build_args()
        handoff = Path("D:/audit/handoff.jsonl")
        command_1688 = build_1688_command(args, handoff)
        command_jst = build_jushuitan_command(
            args,
            handoff,
            Path("D:/jst"),
            Path("D:/audit/jst-results"),
        )

        self.assertEqual(command_1688[command_1688.index("--run-id") + 1], args.run_id)
        self.assertEqual(command_jst[command_jst.index("--run-id") + 1], args.run_id)
        self.assertIn("--yes", command_1688)
        self.assertIn("--yes", command_jst)
        self.assertEqual(
            command_1688[command_1688.index("--shared-runtime-root") + 1],
            str(Path(args.shared_runtime_root).resolve()),
        )

    def test_jushuitan_cleanup_environment_supplies_shared_config_requirements(self) -> None:
        handoff = Path("D:/audit/handoff.jsonl")
        with patch.dict("os.environ", {}, clear=True):
            environment = build_jushuitan_environment(handoff)

        self.assertEqual(environment["JST_LOGIN_URL"], "https://www.erp321.com/login.aspx")
        self.assertEqual(environment["JST_PRODUCT_URL"], "https://www.erp321.com/epaas")
        self.assertTrue(environment["EXCEL_PATH"].endswith("handoff.jsonl"))

    def test_production_pipeline_reuses_logged_in_profile_by_default(self) -> None:
        args = build_argument_parser().parse_args(["--file", "tasks.csv"])

        self.assertTrue(args.skip_login)

    def test_scheduler_can_supply_a_deterministic_run_id(self) -> None:
        args = build_argument_parser().parse_args(
            ["--file", "tasks.csv", "--run-id", "daily_20260721_s01"]
        )

        self.assertEqual(args.run_id, "daily_20260721_s01")

    def test_manual_login_requires_an_explicit_override(self) -> None:
        args = build_argument_parser().parse_args(
            ["--file", "tasks.csv", "--require-manual-login"]
        )

        self.assertFalse(args.skip_login)

    def test_stage_timeout_defaults_are_bounded(self) -> None:
        args = build_argument_parser().parse_args(["--file", "tasks.csv"])

        self.assertEqual(args.timeout_1688_seconds, 2700)
        self.assertEqual(args.timeout_jushuitan_seconds, 1200)
        self.assertEqual(args.active_stop_sale_max_age_minutes, 240)
        self.assertEqual(args.lock_wait_seconds, 0)

    def test_cross_machine_guard_rejects_recent_running_batch(self) -> None:
        repository = SimpleNamespace(count_recent_active_stop_sale_runs=lambda _minutes: 1)

        with self.assertRaisesRegex(RuntimeError, "cross-machine execute is blocked"):
            assert_no_recent_stop_sale_runs(repository, 240)

    def test_cross_machine_guard_accepts_no_recent_running_batch(self) -> None:
        repository = SimpleNamespace(count_recent_active_stop_sale_runs=lambda _minutes: 0)

        assert_no_recent_stop_sale_runs(repository, 240)

    def test_startup_failure_notification_is_sanitized(self) -> None:
        with patch("run_1688_stop_sale_pipeline.send_pipeline_notification") as notify:
            notify_execute_startup_failure(
                run_id="run-1",
                exc=RuntimeError("blocked\nsecond line"),
                disabled=False,
            )

        content = notify.call_args.args[0]
        self.assertIn("RuntimeError: blocked second line", content)
        self.assertEqual(notify.call_args.kwargs["disabled"], False)

    def test_startup_failure_notification_respects_disabled_flag(self) -> None:
        with patch("run_1688_stop_sale_pipeline.send_pipeline_notification") as notify:
            notify_execute_startup_failure(
                run_id="run-1",
                exc=RuntimeError("blocked"),
                disabled=True,
            )

        notify.assert_not_called()

    def test_audit_status_is_partial_when_some_items_succeeded(self) -> None:
        status = derive_audit_status(
            offline_return_code=2,
            jushuitan_return_code=None,
            offline_records=[
                {"status": "success"},
                {"status": "failed"},
            ],
            expected_count=2,
        )

        self.assertEqual(status, "partial")

    def test_audit_status_is_failed_when_all_items_failed_despite_zero_process_code(self) -> None:
        status = derive_audit_status(
            offline_return_code=0,
            jushuitan_return_code=None,
            offline_records=[{"status": "failed"}],
            expected_count=1,
        )

        self.assertEqual(status, "failed")

    def test_pipeline_exception_cannot_be_recorded_as_audit_success(self) -> None:
        status = derive_audit_status(
            offline_return_code=0,
            jushuitan_return_code=None,
            offline_records=[{"status": "already_offline"}],
            expected_count=1,
            pipeline_error=True,
        )

        self.assertEqual(status, "partial")

    def test_execute_records_terminal_audit_when_subprocess_start_fails(self) -> None:
        class FakeConfig:
            def safe_dict(self):
                return {"database": "JSReportReplica", "schema": "app"}

        class FakeAuditRepository:
            config = FakeConfig()

            def __init__(self) -> None:
                self.started = False
                self.finished: dict | None = None

            def start_run(self, **kwargs) -> None:
                self.started = True

            def finish_run(self, **kwargs) -> None:
                self.finished = kwargs

        repository = FakeAuditRepository()
        with tempfile.TemporaryDirectory() as temp_dir:
            with patch(
                "run_1688_stop_sale_pipeline.run_stage_command",
                side_effect=OSError("cannot start"),
            ):
                with self.assertRaises(OSError):
                    run_pipeline(
                        self.build_args(),
                        run_id="run_20260718_001",
                        pipeline_dir=Path(temp_dir),
                        jushuitan_root=Path(temp_dir),
                        shared_lock_path="D:/lock",
                        audit_repository=repository,  # type: ignore[arg-type]
                        audit_tasks=[],
                    )

        self.assertTrue(repository.started)
        self.assertIsNotNone(repository.finished)
        self.assertEqual(repository.finished["status"], "failed")
        self.assertFalse(repository.finished["notification_sent"])

    def test_execute_guard_failure_is_audited_summarized_and_notified(self) -> None:
        class FakeConfig:
            def safe_dict(self):
                return {"database": "JSReportReplica", "schema": "app"}

        class FakeAuditRepository:
            config = FakeConfig()

            def __init__(self) -> None:
                self.started = False
                self.recorded: list[dict] | None = None
                self.finished: dict | None = None

            def start_run(self, **_kwargs) -> None:
                self.started = True

            def record_1688_results(self, _run_id: str, records: list[dict]) -> None:
                self.recorded = records

            def finish_run(self, **kwargs) -> None:
                self.finished = kwargs

        args = self.build_args()
        args.no_notify = False
        repository = FakeAuditRepository()
        guard_error = RuntimeError("Crawler Worker restarted during the daily run")
        with tempfile.TemporaryDirectory() as temp_dir:
            pipeline_dir = Path(temp_dir)
            with (
                patch("run_1688_stop_sale_pipeline.run_stage_command") as run_stage,
                patch(
                    "run_1688_stop_sale_pipeline.send_pipeline_notification",
                    return_value=True,
                ) as notify,
            ):
                with self.assertRaisesRegex(RuntimeError, "Crawler Worker restarted"):
                    run_pipeline(
                        args,
                        run_id=args.run_id,
                        pipeline_dir=pipeline_dir,
                        jushuitan_root=pipeline_dir,
                        shared_lock_path="D:/runtime/lock",
                        audit_repository=repository,  # type: ignore[arg-type]
                        audit_tasks=[SimpleNamespace(dedupe_key=("store", "product", "sku"))],
                        execute_guard=lambda: (_ for _ in ()).throw(guard_error),
                    )
            summary = json.loads(
                (pipeline_dir / f"{args.run_id}.summary.json").read_text(encoding="utf-8")
            )

        self.assertTrue(repository.started)
        self.assertEqual(repository.recorded, [])
        self.assertIsNotNone(repository.finished)
        self.assertEqual(repository.finished["status"], "failed")
        self.assertTrue(repository.finished["notification_sent"])
        self.assertEqual(summary["audit_status"], "failed")
        self.assertEqual(summary["error_type"], "RuntimeError")
        self.assertTrue(summary["notification_sent"])
        run_stage.assert_not_called()
        notify.assert_called_once()

    def test_execute_notifies_and_records_terminal_login_failure(self) -> None:
        class FakeConfig:
            def safe_dict(self):
                return {"database": "JSReportReplica", "schema": "app"}

        class FakeAuditRepository:
            config = FakeConfig()

            def __init__(self) -> None:
                self.finished: dict | None = None

            def start_run(self, **_kwargs) -> None:
                return None

            def heartbeat_run(self, _run_id: str) -> None:
                return None

            def record_1688_results(self, _run_id: str, _records: list[dict]) -> None:
                return None

            def record_jushuitan_results(self, _run_id: str, _records: list[dict]) -> None:
                return None

            def finish_run(self, **kwargs) -> None:
                self.finished = kwargs

        args = self.build_args()
        args.no_notify = False
        repository = FakeAuditRepository()
        failed_record = {
            "status": "failed",
            "error_category": "login_required",
            "error_message": "automatic login worker failed",
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            pipeline_dir = Path(temp_dir)
            with (
                patch(
                    "run_1688_stop_sale_pipeline.run_stage_command",
                    return_value=SimpleNamespace(returncode=0),
                ),
                patch(
                    "run_1688_stop_sale_pipeline.load_jsonl_records",
                    return_value=[failed_record],
                ),
                patch(
                    "run_1688_stop_sale_pipeline.send_pipeline_notification",
                    return_value=True,
                ) as notify,
            ):
                return_code = run_pipeline(
                    args,
                    run_id=args.run_id,
                    pipeline_dir=pipeline_dir,
                    jushuitan_root=pipeline_dir,
                    shared_lock_path="D:/runtime/lock",
                    audit_repository=repository,  # type: ignore[arg-type]
                    audit_tasks=[SimpleNamespace(dedupe_key=("store", "product", "sku"))],
                )
            summary = json.loads(
                (pipeline_dir / f"{args.run_id}.summary.json").read_text(encoding="utf-8")
            )

        self.assertEqual(return_code, 2)
        self.assertEqual(summary["audit_status"], "failed")
        self.assertEqual(summary["offline_counts"], {"failed": 1})
        self.assertEqual(summary["item_error_categories"], {"login_required": 1})
        self.assertTrue(summary["notification_sent"])
        self.assertIn("login_required", notify.call_args.args[0])
        self.assertIsNotNone(repository.finished)
        self.assertTrue(repository.finished["notification_sent"])

    def test_preview_completes_without_an_audit_repository(self) -> None:
        args = self.build_args(mode="preview")
        with tempfile.TemporaryDirectory() as temp_dir:
            pipeline_dir = Path(temp_dir)
            with patch(
                "run_1688_stop_sale_pipeline.run_stage_command",
                return_value=SimpleNamespace(returncode=0),
            ):
                return_code = run_pipeline(
                    args,
                    run_id=args.run_id,
                    pipeline_dir=pipeline_dir,
                    jushuitan_root=pipeline_dir,
                    shared_lock_path="",
                )
            summary = json.loads(
                (pipeline_dir / f"{args.run_id}.summary.json").read_text(encoding="utf-8")
            )

        self.assertEqual(return_code, 0)
        self.assertIsNone(summary["audit_database"])
        self.assertIsNone(summary["audit_status"])

    def test_execute_guard_rejects_running_crawler_worker(self) -> None:
        with patch(
            "run_1688_stop_sale_pipeline.query_crawler_worker_state",
            return_value={
                "scheduled_task_exists": True,
                "scheduled_task_state": "Running",
                "worker_process_count": 1,
                "profile_edge_process_count": 0,
            },
        ):
            with self.assertRaisesRegex(RuntimeError, "Crawler Worker or an account Profile Edge"):
                assert_crawler_worker_paused("YYDD-1688-Crawler-Worker")

    def test_execute_guard_accepts_stopped_worker_without_processes(self) -> None:
        with patch(
            "run_1688_stop_sale_pipeline.query_crawler_worker_state",
            return_value={
                "scheduled_task_exists": True,
                "scheduled_task_state": "Ready",
                "worker_process_count": 0,
                "profile_edge_process_count": 0,
            },
        ):
            state = assert_crawler_worker_paused("YYDD-1688-Crawler-Worker")

        self.assertEqual(state["worker_process_count"], 0)

    def test_audit_tasks_keep_all_platform_codes_for_one_offline_sku(self) -> None:
        args = self.build_args()
        with tempfile.TemporaryDirectory() as temp_dir:
            source = Path(temp_dir) / "tasks.csv"
            source.write_text(
                "店铺名称,平台,商品ID,平台店铺商品编码,线上商品编码,处理说明,可替换商品编码,是否换图\n"
                "阿里巴巴-常州工莱家具,Alibaba,1001,CODE-A,SKU-A,全渠道下架,,否\n"
                "阿里巴巴-常州工莱家具,Alibaba,1001,CODE-B,SKU-A,全渠道下架,,否\n",
                encoding="utf-8-sig",
            )
            args.file = str(source)
            args.limit = 1

            tasks = load_selected_audit_tasks(args)

        self.assertEqual(len(tasks), 2)
        self.assertEqual({task.platform_store_item_code for task in tasks}, {"CODE-A", "CODE-B"})
        self.assertEqual(len({task.dedupe_key for task in tasks}), 1)

    def test_stage_timeout_stops_only_the_owned_process_tree(self) -> None:
        class FakeProcess:
            pid = 4321

            def wait(self, timeout=None):
                raise subprocess.TimeoutExpired(cmd="python", timeout=timeout)

            def poll(self):
                return None

        with (
            patch("run_1688_stop_sale_pipeline.subprocess.Popen", return_value=FakeProcess()),
            patch("run_1688_stop_sale_pipeline.terminate_stage_process_tree") as terminate,
        ):
            with self.assertRaises(PipelineStageTimeoutError):
                run_stage_command(
                    ["python", "worker.py"],
                    cwd=PROJECT_ROOT,
                    timeout_seconds=3,
                    stage="1688",
                )

        terminate.assert_called_once()

    def test_running_stage_refreshes_the_audit_heartbeat(self) -> None:
        class FakeProcess:
            pid = 4321

            def __init__(self) -> None:
                self.wait_count = 0

            def wait(self, timeout=None):
                self.wait_count += 1
                if self.wait_count == 1:
                    raise subprocess.TimeoutExpired(cmd="python", timeout=timeout)
                return 0

            def poll(self):
                return None

        heartbeat_calls: list[bool] = []
        with patch("run_1688_stop_sale_pipeline.subprocess.Popen", return_value=FakeProcess()):
            result = run_stage_command(
                ["python", "worker.py"],
                cwd=PROJECT_ROOT,
                timeout_seconds=30,
                stage="1688",
                heartbeat=lambda: heartbeat_calls.append(True),
                heartbeat_interval_seconds=1,
            )

        self.assertEqual(result.returncode, 0)
        self.assertEqual(heartbeat_calls, [True])

    def test_audit_heartbeat_failure_stops_the_owned_process_tree(self) -> None:
        class FakeProcess:
            pid = 4321

            def wait(self, timeout=None):
                raise subprocess.TimeoutExpired(cmd="python", timeout=timeout)

            def poll(self):
                return None

        def fail_heartbeat() -> None:
            raise RuntimeError("audit unavailable")

        with (
            patch("run_1688_stop_sale_pipeline.subprocess.Popen", return_value=FakeProcess()),
            patch("run_1688_stop_sale_pipeline.terminate_stage_process_tree") as terminate,
        ):
            with self.assertRaisesRegex(RuntimeError, "audit unavailable"):
                run_stage_command(
                    ["python", "worker.py"],
                    cwd=PROJECT_ROOT,
                    timeout_seconds=30,
                    stage="1688",
                    heartbeat=fail_heartbeat,
                    heartbeat_interval_seconds=1,
                )

        terminate.assert_called_once()


if __name__ == "__main__":
    unittest.main()
