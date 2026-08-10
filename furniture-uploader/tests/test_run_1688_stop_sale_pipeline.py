from __future__ import annotations

from argparse import Namespace
from pathlib import Path
import hashlib
import json
import subprocess
import sys
import tempfile
import types
import unittest
from unittest.mock import patch
from types import SimpleNamespace


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_ROOT = PROJECT_ROOT / "scripts"
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

from run_1688_stop_sale_pipeline import (
    assert_interrupted_recovery_input_unchanged,
    assert_no_recent_stop_sale_runs,
    assert_crawler_worker_paused,
    build_argument_parser,
    build_1688_command,
    build_jushuitan_command,
    build_jushuitan_environment,
    build_jushuitan_lock,
    derive_audit_status,
    load_selected_audit_tasks,
    load_interrupted_recovery_approval,
    notify_execute_startup_failure,
    PipelineStageTimeoutError,
    AuditHeartbeatProcessError,
    run_audit_heartbeat_process,
    run_stage_command,
    run_pipeline,
    prepare_saga_operations,
    SagaOperation,
    resolve_pipeline_account,
    resolve_shared_lock_path,
    validate_interrupted_recovery_arguments,
    wait_for_active_crawler_tasks,
    write_startup_failure_summary,
)


class Run1688StopSalePipelineTests(unittest.TestCase):
    def recovery_operation(self, key: str = "a" * 64) -> SagaOperation:
        return SagaOperation(
            operation_key=key,
            run_id="recovery-run",
            task_type="stop_sale",
            account_key="gonglai",
            business_key="store|product|sku|item",
            payload={"operation_key": key},
        )

    def write_recovery_approval(
        self,
        root: Path,
        input_path: Path,
        operation: SagaOperation,
        **overrides,
    ) -> tuple[Path, str]:
        payload = {
            "version": 1,
            "task_type": "stop_sale",
            "account_key": "gonglai",
            "source_run_id": "source-run",
            "recovery_run_id": operation.run_id,
            "expected_saga_state": "failed_terminal",
            "expected_ali1688_status": "failed",
            "expected_saga_error_code": "interrupted_executor_process",
            "expected_item_status": "failed",
            "expected_item_error_category": "automation_error",
            "expected_item_error_message_prefix": "interrupted_executor_process:",
            "expected_outbox_count": 0,
            "input_sha256": hashlib.sha256(input_path.read_bytes()).hexdigest(),
            "operation_count": 1,
            "operation_keys": [operation.operation_key],
            **overrides,
        }
        path = root / "approval.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        return path, hashlib.sha256(path.read_bytes()).hexdigest()

    def test_interrupted_recovery_approval_binds_input_and_exact_keys(self) -> None:
        operation = self.recovery_operation()
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            input_path = root / "tasks.csv"
            input_path.write_text("exact input", encoding="utf-8")
            approval_path, approval_sha = self.write_recovery_approval(
                root, input_path, operation
            )

            result = load_interrupted_recovery_approval(
                approval_path,
                approval_sha,
                input_path=input_path,
                operations=[operation],
                account_key="gonglai",
            )

        self.assertEqual(result["source_run_id"], "source-run")
        self.assertEqual(result["recovery_run_id"], operation.run_id)
        self.assertEqual(result["operation_keys"], [operation.operation_key])

    def test_interrupted_recovery_approval_rejects_business_terminal_contract(self) -> None:
        operation = self.recovery_operation()
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            input_path = root / "tasks.csv"
            input_path.write_text("exact input", encoding="utf-8")
            approval_path, approval_sha = self.write_recovery_approval(
                root,
                input_path,
                operation,
                expected_saga_error_code="sole_sku_requires_product_offline",
            )

            with self.assertRaisesRegex(RuntimeError, "contract changed"):
                load_interrupted_recovery_approval(
                    approval_path,
                    approval_sha,
                    input_path=input_path,
                    operations=[operation],
                    account_key="gonglai",
                )

    def test_interrupted_recovery_approval_rejects_input_drift(self) -> None:
        operation = self.recovery_operation()
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            input_path = root / "tasks.csv"
            input_path.write_text("exact input", encoding="utf-8")
            approval_path, approval_sha = self.write_recovery_approval(
                root, input_path, operation
            )
            input_path.write_text("changed input", encoding="utf-8")

            with self.assertRaisesRegex(RuntimeError, "input SHA-256"):
                load_interrupted_recovery_approval(
                    approval_path,
                    approval_sha,
                    input_path=input_path,
                    operations=[operation],
                    account_key="gonglai",
                )

    def test_interrupted_recovery_approval_rejects_key_scope_drift(self) -> None:
        operation = self.recovery_operation()
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            input_path = root / "tasks.csv"
            input_path.write_text("exact input", encoding="utf-8")
            approval_path, approval_sha = self.write_recovery_approval(
                root,
                input_path,
                operation,
                operation_keys=["b" * 64],
            )

            with self.assertRaisesRegex(RuntimeError, "selected input"):
                load_interrupted_recovery_approval(
                    approval_path,
                    approval_sha,
                    input_path=input_path,
                    operations=[operation],
                    account_key="gonglai",
                )

    def test_interrupted_recovery_approval_rejects_target_run_drift(self) -> None:
        operation = self.recovery_operation()
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            input_path = root / "tasks.csv"
            input_path.write_text("exact input", encoding="utf-8")
            approval_path, approval_sha = self.write_recovery_approval(
                root,
                input_path,
                operation,
                recovery_run_id="different-recovery-run",
            )

            with self.assertRaisesRegex(RuntimeError, "new run_id"):
                load_interrupted_recovery_approval(
                    approval_path,
                    approval_sha,
                    input_path=input_path,
                    operations=[operation],
                    account_key="gonglai",
                )

    def test_interrupted_recovery_rechecks_input_before_execution(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            input_path = Path(temp_dir) / "tasks.csv"
            input_path.write_text("exact input", encoding="utf-8")
            approval = {
                "input_path": str(input_path),
                "input_sha256": hashlib.sha256(input_path.read_bytes()).hexdigest(),
            }

            assert_interrupted_recovery_input_unchanged(approval)
            input_path.write_text("changed input", encoding="utf-8")

            with self.assertRaisesRegex(RuntimeError, "changed before execution"):
                assert_interrupted_recovery_input_unchanged(approval)

    def test_interrupted_recovery_cli_requires_fail_fast_standard_runtime(self) -> None:
        parser = build_argument_parser()
        args = parser.parse_args(
            [
                "--file",
                "tasks.csv",
                "--mode",
                "execute",
                "--yes",
                "--approved-recovery-file",
                "approval.json",
                "--approved-recovery-sha256",
                "a" * 64,
                "--runtime-lease-wait-seconds",
                "0",
                "--crawler-task-wait-seconds",
                "0",
                "--jushuitan-lock-wait-seconds",
                "0",
            ]
        )

        validate_interrupted_recovery_arguments(args, recovery_requested=True)

        args.runtime_lease_wait_seconds = 1
        with self.assertRaisesRegex(ValueError, "fail-fast"):
            validate_interrupted_recovery_arguments(args, recovery_requested=True)

        args.runtime_lease_wait_seconds = 0
        args.no_shared_lock = True
        with self.assertRaisesRegex(ValueError, "standard account-scoped"):
            validate_interrupted_recovery_arguments(args, recovery_requested=True)

    def test_pipeline_uses_exact_recovery_prepare_when_approval_is_present(self) -> None:
        operation = self.recovery_operation()
        calls: list[tuple[str, dict]] = []

        class Repository:
            def prepare_interrupted_stop_sale_recovery_many(self, operations, **kwargs):
                calls.append(("recovery", {"operations": operations, **kwargs}))
                return {operation.operation_key: "prepared"}

            def prepare_many(self, operations, **kwargs):
                calls.append(("generic", {"operations": operations, **kwargs}))
                return {}

        guard = SimpleNamespace(
            owner_token="owner",
            account_fencing_token=11,
            browser_slot_key="host:1",
            browser_slot_fencing_token=22,
        )
        approval = {
            "source_run_id": "source-run",
            "operation_keys": [operation.operation_key],
            "approval_sha256": "c" * 64,
            "input_sha256": "d" * 64,
        }

        prepare_saga_operations(Repository(), [operation], guard, approval)  # type: ignore[arg-type]

        self.assertEqual([name for name, _ in calls], ["recovery"])
        self.assertEqual(calls[0][1]["source_run_id"], "source-run")
        self.assertEqual(calls[0][1]["approval_sha256"], "c" * 64)

    def test_audit_heartbeat_runs_in_an_isolated_bounded_process(self) -> None:
        completed = subprocess.CompletedProcess(["python"], 0, stdout="", stderr="")
        with patch("run_1688_stop_sale_pipeline.subprocess.run", return_value=completed) as run:
            run_audit_heartbeat_process(
                kind="stop_sale",
                run_id="run-1",
                shared_runtime_root="D:/runtime",
                timeout_seconds=7,
            )

        command = run.call_args.args[0]
        self.assertIn("run_1688_audit_heartbeat.py", command[1])
        self.assertEqual(command[command.index("--kind") + 1], "stop_sale")
        self.assertEqual(command[command.index("--run-id") + 1], "run-1")
        self.assertEqual(run.call_args.kwargs["timeout"], 7)

    def test_audit_heartbeat_process_failure_is_explicit(self) -> None:
        completed = subprocess.CompletedProcess(
            ["python"],
            5,
            stdout="",
            stderr="database timeout",
        )
        with (
            patch("run_1688_stop_sale_pipeline.subprocess.run", return_value=completed),
            self.assertRaisesRegex(AuditHeartbeatProcessError, "code 5"),
        ):
            run_audit_heartbeat_process(
                kind="stop_sale",
                run_id="run-1",
                shared_runtime_root="D:/runtime",
                max_attempts=1,
            )

    def test_audit_heartbeat_process_retries_a_transient_exit(self) -> None:
        failed = subprocess.CompletedProcess(["python"], 1, stdout="", stderr="temporary")
        succeeded = subprocess.CompletedProcess(["python"], 0, stdout="", stderr="")
        with (
            patch(
                "run_1688_stop_sale_pipeline.subprocess.run",
                side_effect=[failed, succeeded],
            ) as run,
            patch("run_1688_stop_sale_pipeline.time.sleep") as sleep,
        ):
            run_audit_heartbeat_process(
                kind="stop_sale",
                run_id="run-1",
                shared_runtime_root="D:/runtime",
                retry_seconds=0.25,
            )

        self.assertEqual(run.call_count, 2)
        sleep.assert_called_once_with(0.25)

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
        with tempfile.TemporaryDirectory() as temp_dir:
            handoff = Path(temp_dir) / "handoff.jsonl"
            handoff.write_text(json.dumps({"operation_key": "a" * 64}), encoding="utf-8")
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
        repository = SimpleNamespace(count_recent_active_stop_sale_runs=lambda _minutes, _stores: 1)

        with self.assertRaisesRegex(RuntimeError, "cross-machine execute is blocked"):
            assert_no_recent_stop_sale_runs(repository, 240)

    def test_cross_machine_guard_accepts_no_recent_running_batch(self) -> None:
        repository = SimpleNamespace(count_recent_active_stop_sale_runs=lambda _minutes, _stores: 0)

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

    def test_expired_runtime_owner_writes_structured_startup_failure(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            args = SimpleNamespace(mode="execute", file=str(root / "input.csv"))
            summary = write_startup_failure_summary(
                args=args,
                run_id="run-expired",
                pipeline_dir=root,
                account_key="lechang",
                store_name="阿里巴巴-常州乐畅家居有限公司",
                exc=RuntimeError("expired_owner_requires_recovery"),
            )
            persisted = json.loads(
                (root / "run-expired.summary.json").read_text(encoding="utf-8")
            )

        self.assertEqual(summary["failure_scope"], "infrastructure")
        self.assertEqual(summary["error_code"], "expired_owner_requires_recovery")
        self.assertTrue(summary["requires_runtime_recovery"])
        self.assertFalse(summary["retryable"])
        self.assertEqual(persisted["account_key"], "lechang")
        self.assertEqual(persisted["store_name"], "阿里巴巴-常州乐畅家居有限公司")

    def test_all_expired_browser_slots_use_runtime_recovery_error_code(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            args = SimpleNamespace(mode="execute", file=str(root / "input.csv"))
            summary = write_startup_failure_summary(
                args=args,
                run_id="run-all-slots-expired",
                pipeline_dir=root,
                account_key="gonglai",
                store_name="阿里巴巴-常州工莱家具",
                exc=RuntimeError(
                    "expired_owner_requires_recovery:all_browser_slots"
                ),
            )

        self.assertEqual(summary["error_code"], "expired_owner_requires_recovery")
        self.assertTrue(summary["requires_runtime_recovery"])

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


    def test_runtime_lease_releases_before_jushuitan_stage(self) -> None:
        events: list[str] = []

        class FakeLease:
            resource_type = "account"
            resource_key = "gonglai"
            owner_token = "owner"
            fencing_token = 7

        class FakeGuard:
            def __init__(self) -> None:
                self.account_lease = FakeLease()
                self.owner_token = "owner"

            @property
            def account_fencing_token(self) -> int:
                return 7

            @property
            def browser_slot_key(self) -> str:
                return "HOST:1"

            @property
            def browser_slot_fencing_token(self) -> int:
                return 3

            def acquire(self) -> None:
                events.append("acquire")

            def environment(self) -> dict:
                return {}

            def assert_active(self) -> None:
                return None

            def release(self, status, suppress_errors=False) -> None:
                events.append(f"release:{status}")

        class FakeSagaRepository:
            def prepare_many(self, operations, **kwargs) -> dict:
                events.append("prepare")
                return {}

        class FakeAuditConfig:
            def safe_dict(self):
                return {"database": "JSReportReplica", "schema": "app"}

        class FakeAuditRepository:
            config = FakeAuditConfig()

            def start_run(self, **kwargs) -> None:
                return None

            def finish_run(self, **kwargs) -> None:
                return None

            def record_1688_results(self, *args, **kwargs) -> None:
                return None

            def record_jushuitan_results(self, *args, **kwargs) -> None:
                return None

        def fake_stage(command, **kwargs):
            stage = kwargs.get("stage", "")
            events.append(f"stage:{stage}")
            return SimpleNamespace(returncode=0, stdout=b"", stderr=b"")

        args = self.build_args()
        with tempfile.TemporaryDirectory() as temp_dir:
            pipeline_dir = Path(temp_dir)
            handoff = pipeline_dir / "run_20260718_001.jushuitan.jsonl"
            handoff.write_text(
                '{"task_id": "t1"}' + chr(10),
                encoding="utf-8",
            )
            with patch(
                "run_1688_stop_sale_pipeline.run_stage_command",
                side_effect=fake_stage,
            ), patch(
                "run_1688_stop_sale_pipeline.load_jsonl_records",
                return_value=[{"status": "already_offline"}],
            ), patch(
                "run_1688_stop_sale_pipeline.persist_ali1688_results",
                return_value=None,
            ), patch(
                "run_1688_stop_sale_pipeline.run_audit_heartbeat_process",
                return_value=None,
            ):
                run_pipeline(
                    args,
                    run_id="run_20260718_001",
                    pipeline_dir=pipeline_dir,
                    jushuitan_root=pipeline_dir,
                    shared_lock_path="D:/lock",
                    audit_repository=FakeAuditRepository(),  # type: ignore[arg-type]
                    audit_tasks=[],
                    runtime_guard=FakeGuard(),  # type: ignore[arg-type]
                    saga_repository=FakeSagaRepository(),  # type: ignore[arg-type]
                    saga_operations=[],
                )

        self.assertIn("release:completed", events)
        jushuitan_marks = [index for index, event in enumerate(events) if event.startswith("stage:jushuitan")]
        self.assertTrue(jushuitan_marks, f"jushuitan stage did not run: {events}")
        release_index = events.index("release:completed")
        self.assertLess(release_index, jushuitan_marks[0], f"release must precede jushuitan stage: {events}")

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
            ) as run_stage:
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
        self.assertEqual(run_stage.call_count, 1)
        self.assertEqual(run_stage.call_args.kwargs["stage"], "1688")
        self.assertIsNone(summary["jushuitan_return_code"])
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

    def test_active_crawler_task_waits_without_consuming_pipeline_attempt(self) -> None:
        repository = SimpleNamespace(
            count_active_crawler_tasks=unittest.mock.Mock(side_effect=[1, 1, 0])
        )

        with patch("run_1688_stop_sale_pipeline.time.sleep") as sleep:
            result = wait_for_active_crawler_tasks(
                repository,
                account_key="gonglai",
                timeout_seconds=30,
                poll_seconds=2,
            )

        self.assertEqual(result, 0)
        self.assertEqual(repository.count_active_crawler_tasks.call_count, 3)
        repository.count_active_crawler_tasks.assert_called_with("gonglai")
        self.assertEqual(sleep.call_count, 2)

    def test_active_crawler_task_wait_timeout_remains_fail_closed(self) -> None:
        repository = SimpleNamespace(count_active_crawler_tasks=lambda _account_key: 1)

        with (
            patch("run_1688_stop_sale_pipeline.time.monotonic", side_effect=[0.0, 0.0]),
            self.assertRaisesRegex(RuntimeError, "after waiting 0 seconds"),
        ):
            wait_for_active_crawler_tasks(
                repository,
                account_key="gonglai",
                timeout_seconds=0,
                poll_seconds=2,
            )

    def test_pipeline_account_is_resolved_from_exactly_one_store(self) -> None:
        args = SimpleNamespace(account_key="")
        tasks = [SimpleNamespace(store_name="STORE-A")]
        with (
            patch("run_1688_stop_sale_pipeline.load_json_with_local_override", return_value={}),
            patch(
                "run_1688_stop_sale_pipeline.resolve_store_account_binding",
                return_value={"account_key": "lechang"},
            ),
        ):
            account_key, store_name = resolve_pipeline_account(args, tasks)

        self.assertEqual((account_key, store_name), ("lechang", "STORE-A"))

    def test_pipeline_rejects_multiple_stores_before_opening_a_browser(self) -> None:
        args = SimpleNamespace(account_key="")
        tasks = [SimpleNamespace(store_name="STORE-A"), SimpleNamespace(store_name="STORE-B")]

        with self.assertRaisesRegex(RuntimeError, "exactly one 1688 store"):
            resolve_pipeline_account(args, tasks)

    def test_account_and_jushuitan_lock_paths_are_isolated_by_scope(self) -> None:
        fake_module = types.ModuleType("src.runtime.global_lock")

        class FakeLock:
            def __init__(self, path, **kwargs):
                self.path = Path(path)
                self.kwargs = kwargs

        fake_module.GlobalFileLock = FakeLock
        fake_module.account_lock_path = (
            lambda root, account_key: Path(root).resolve()
            / "artifacts"
            / "locks"
            / f"ali1688_account_{account_key}.lock"
        )
        args = SimpleNamespace(
            shared_lock_path="",
            shared_runtime_root="D:/runtime/1688",
            mode="execute",
            no_shared_lock=False,
            lock_stale_seconds=21600,
            lock_poll_seconds=10.0,
            jushuitan_lock_wait_seconds=3600,
        )
        with patch.dict(sys.modules, {"src.runtime.global_lock": fake_module}):
            lechang_path = resolve_shared_lock_path(args, "lechang")
            gonglai_path = resolve_shared_lock_path(args, "gonglai")
            first_lock, first_path = build_jushuitan_lock(args, "run-1")
            second_lock, second_path = build_jushuitan_lock(args, "run-2")

        self.assertNotEqual(lechang_path, gonglai_path)
        self.assertEqual(first_path, second_path)
        self.assertEqual(first_lock.path, second_lock.path)

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
