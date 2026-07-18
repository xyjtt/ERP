from __future__ import annotations

from argparse import Namespace
from pathlib import Path
import json
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
    assert_crawler_worker_paused,
    build_argument_parser,
    build_1688_command,
    build_jushuitan_command,
    build_jushuitan_environment,
    derive_audit_status,
    load_selected_audit_tasks,
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

    def test_manual_login_requires_an_explicit_override(self) -> None:
        args = build_argument_parser().parse_args(
            ["--file", "tasks.csv", "--require-manual-login"]
        )

        self.assertFalse(args.skip_login)

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
                "run_1688_stop_sale_pipeline.subprocess.run",
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

    def test_preview_completes_without_an_audit_repository(self) -> None:
        args = self.build_args(mode="preview")
        with tempfile.TemporaryDirectory() as temp_dir:
            pipeline_dir = Path(temp_dir)
            with patch(
                "run_1688_stop_sale_pipeline.subprocess.run",
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


if __name__ == "__main__":
    unittest.main()
