from __future__ import annotations

import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


SCRIPT_ROOT = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPT_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPT_ROOT))
RPA_ROOT = Path(__file__).resolve().parents[1] / "rpa"
if str(RPA_ROOT) not in sys.path:
    sys.path.insert(0, str(RPA_ROOT))

from auto_listing import STATE_DRAFT_PENDING_REVIEW  # noqa: E402
from manage_1688_listing_daily import (  # noqa: E402
    DAILY_TASK_NAME,
    build_argument_parser,
    build_task_command,
    prepare_scheduled_payload,
    run_daily,
)


class FakeSourceReader:
    def __init__(self, rows_by_sku: dict[str, list[dict]]) -> None:
        self.rows_by_sku = rows_by_sku
        self.read_skus: list[str] = []

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def read(self, sku: str):
        self.read_skus.append(sku)
        columns = {
            "enabled": "enabled",
            "stock_disabled": "stock_disabled",
            "other_5": "other_5",
            "item_type": "item_type",
        }
        return self.rows_by_sku.get(sku, []), columns


class FakeAuditRepository:
    def __init__(self, existing_task_ids: set[str] | None = None) -> None:
        self.existing_task_ids = existing_task_ids or set()

    def check_contract(self):
        return {"ready": True}

    def find_existing_task(self, *, task_id: str, idempotency_key: str):
        del idempotency_key
        if task_id in self.existing_task_ids:
            return {
                "task_id": task_id,
                "workflow_state": "submitted",
            }
        return None

    def claim_scheduled_task(self, payload, *, claim_owner: str, claim_seconds: int):
        del claim_seconds
        task_id = str(payload.get("task_id") or "")
        if task_id in self.existing_task_ids:
            return {
                "status": "duplicate_existing",
                "task_id": task_id,
                "workflow_state": "submitted",
            }
        self.existing_task_ids.add(task_id)
        return {
            "status": "claimed",
            "task_id": task_id,
            "claimed_by": claim_owner,
        }


class ListingDailyManagerTests(unittest.TestCase):
    def payload(self, sku: str = "SKU-1") -> dict:
        payload = json.loads(
            (Path(__file__).resolve().parents[1] / "templates" / "1688_listing_task_sample.json").read_text(
                encoding="utf-8"
            )
        )
        payload["task_id"] = f"1688-listing-{sku}"
        payload["shop"] = {
            "account_key": "muke_lixiang",
            "shop_name": "木刻理想",
        }
        payload["source"]["company_sku"] = sku
        payload["source"]["company_spu"] = f"SPU-{sku}"
        payload["product"]["sku_code"] = sku
        payload["product"]["spu_code"] = f"SPU-{sku}"
        payload["source"]["duplicate_check"] = {
            "status": "clear",
            "checked_at": "2026-08-05T00:00:00+08:00",
        }
        payload["logistics"] = {
            "length_cm": "50",
            "width_cm": "40",
            "height_cm": "49",
            "weight_g": "10000",
        }
        payload["workflow"]["pending_draft_id"] = f"draft-{sku}"
        return payload

    def source_row(self, *, eligible: bool = True) -> dict:
        return {
            "enabled": 1,
            "stock_disabled": 0,
            "other_5": "销售" if eligible else "下架",
            "item_type": "成品",
        }

    def args(self, root: Path, *, mode: str = "preview"):
        return build_argument_parser().parse_args(
            [
                "run",
                "--mode",
                mode,
                "--candidate-root",
                str(root / "inbox"),
                "--output-root",
                str(root / "output"),
                "--shared-runtime-root",
                str(root / "runtime"),
                *( ["--yes"] if mode == "execute" else [] ),
            ]
        )

    def write_candidate(self, root: Path, payload: dict, name: str = "candidate.json") -> Path:
        inbox = root / "inbox"
        inbox.mkdir(parents=True, exist_ok=True)
        path = inbox / name
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        return path

    def test_prepare_refreshes_source_without_changing_business_identity(self) -> None:
        payload = self.payload()
        prepared = prepare_scheduled_payload(
            payload,
            source_rows=[self.source_row()],
            source_columns={
                "enabled": "enabled",
                "stock_disabled": "stock_disabled",
                "other_5": "other_5",
                "item_type": "item_type",
            },
            manager_run_id="daily-1",
            business_date="2026-08-05",
            candidate_path=Path("candidate.json"),
        )
        self.assertEqual(prepared["shop"], payload["shop"])
        self.assertEqual(prepared["source"]["company_sku"], "SKU-1")
        self.assertEqual(prepared["schedule"]["task_name"], DAILY_TASK_NAME)
        self.assertEqual(prepared["preflight"]["status"], "passed")

    def test_preview_creates_single_item_task_and_never_runs_browser(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            self.write_candidate(root, self.payload())
            source = FakeSourceReader({"SKU-1": [self.source_row()]})

            def forbidden_runner(*_args, **_kwargs):
                self.fail("preview must not run a child browser task")

            return_code, summary = run_daily(
                self.args(root),
                source_reader_factory=lambda _args: source,
                audit_repository_factory=lambda _args: FakeAuditRepository(),
                command_runner=forbidden_runner,
            )

            self.assertEqual(return_code, 0)
            self.assertEqual(summary["status"], "preview_ready")
            self.assertEqual(summary["counts"], {"preview_ready": 1})
            self.assertFalse(summary["auto_submit"])
            task_payload = json.loads(Path(summary["items"][0]["input_path"]).read_text(encoding="utf-8"))
            self.assertEqual(task_payload["shop"]["account_key"], "muke_lixiang")
            self.assertEqual(task_payload["shop"]["shop_name"], "木刻理想")

    def test_existing_task_is_skipped_before_source_or_browser(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            payload = self.payload()
            self.write_candidate(root, payload)
            source = FakeSourceReader({"SKU-1": [self.source_row()]})
            return_code, summary = run_daily(
                self.args(root),
                source_reader_factory=lambda _args: source,
                audit_repository_factory=lambda _args: FakeAuditRepository({payload["task_id"]}),
            )

            self.assertEqual(return_code, 0)
            self.assertEqual(summary["counts"], {"duplicate_existing": 1})
            self.assertEqual(source.read_skus, [])

    def test_source_ineligible_is_explicit_not_numeric_zero(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            self.write_candidate(root, self.payload())
            source = FakeSourceReader({"SKU-1": [self.source_row(eligible=False)]})
            return_code, summary = run_daily(
                self.args(root),
                source_reader_factory=lambda _args: source,
                audit_repository_factory=lambda _args: FakeAuditRepository(),
            )
            self.assertEqual(return_code, 0)
            self.assertEqual(summary["counts"], {"source_ineligible": 1})
            self.assertEqual(summary["status"], "no_eligible_candidates")

    def test_candidate_without_one_existing_draft_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            payload = self.payload()
            payload["workflow"].pop("pending_draft_id")
            self.write_candidate(root, payload)
            return_code, summary = run_daily(
                self.args(root),
                source_reader_factory=lambda _args: FakeSourceReader({}),
                audit_repository_factory=lambda _args: FakeAuditRepository(),
            )
        self.assertEqual(return_code, 1)
        self.assertEqual(summary["counts"], {"invalid": 1})
        self.assertIn("exactly one existing draft_id", summary["items"][0]["error"])

    def test_execute_claim_is_atomic_before_child_start(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            payload = self.payload()
            repository = FakeAuditRepository({payload["task_id"]})
            self.write_candidate(root, payload)
            return_code, summary = run_daily(
                self.args(root, mode="execute"),
                source_reader_factory=lambda _args: FakeSourceReader(
                    {"SKU-1": [self.source_row()]}
                ),
                audit_repository_factory=lambda _args: repository,
                command_runner=lambda *_args, **_kwargs: self.fail("duplicate claim must not run"),
            )
        self.assertEqual(return_code, 0)
        self.assertEqual(summary["counts"], {"duplicate_existing": 1})

    def test_execute_isolates_failure_and_continues_to_next_item(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            self.write_candidate(root, self.payload("SKU-1"), "01.json")
            self.write_candidate(root, self.payload("SKU-2"), "02.json")
            source = FakeSourceReader(
                {
                    "SKU-1": [self.source_row()],
                    "SKU-2": [self.source_row()],
                }
            )
            calls: list[list[str]] = []

            def runner(command, **_kwargs):
                calls.append(command)
                output_path = Path(command[command.index("--output") + 1])
                if len(calls) == 1:
                    return subprocess.CompletedProcess(command, 3, "", "failed")
                input_path = Path(command[command.index("--payload") + 1])
                result = json.loads(input_path.read_text(encoding="utf-8"))
                result["workflow"] = {
                    **result["workflow"],
                    "state": STATE_DRAFT_PENDING_REVIEW,
                    "draft": {"draft_id": "draft-SKU-2"},
                }
                output_path.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
                return subprocess.CompletedProcess(command, 0, "ok", "")

            return_code, summary = run_daily(
                self.args(root, mode="execute"),
                source_reader_factory=lambda _args: source,
                audit_repository_factory=lambda _args: FakeAuditRepository(),
                command_runner=runner,
            )

            self.assertEqual(return_code, 1)
            self.assertEqual(len(calls), 2)
            self.assertEqual(summary["counts"]["failed"], 1)
            self.assertEqual(summary["counts"]["draft_saved_pending_review"], 1)
            second = next(item for item in summary["items"] if item["company_sku"] == "SKU-2")
            self.assertEqual(second["draft_id"], "draft-SKU-2")

    def test_malformed_child_result_is_runtime_failure_not_invalid_candidate(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            self.write_candidate(root, self.payload())
            source = FakeSourceReader({"SKU-1": [self.source_row()]})

            def runner(command, **_kwargs):
                output_path = Path(command[command.index("--output") + 1])
                output_path.write_text("not json", encoding="utf-8")
                return subprocess.CompletedProcess(command, 0, "ok", "")

            return_code, summary = run_daily(
                self.args(root, mode="execute"),
                source_reader_factory=lambda _args: source,
                audit_repository_factory=lambda _args: FakeAuditRepository(),
                command_runner=runner,
            )

            self.assertEqual(return_code, 1)
            self.assertEqual(summary["counts"], {"failed": 1})
            self.assertEqual(summary["items"][0]["error_type"], "JSONDecodeError")

    def test_child_command_is_draft_only(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            command = build_task_command(
                self.args(root, mode="execute"),
                root / "input.json",
                root / "output.json",
                "draft-SKU-1",
            )
        self.assertEqual(command[command.index("--mode") + 1], "draft")
        self.assertEqual(command[command.index("--draft-id") + 1], "draft-SKU-1")
        self.assertNotIn("submit", command)
        self.assertNotIn("approve", command)

    def test_account_identity_mismatch_is_isolated(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            payload = self.payload()
            payload["shop"]["account_key"] = "other_account"
            self.write_candidate(root, payload)
            return_code, summary = run_daily(
                self.args(root),
                source_reader_factory=lambda _args: FakeSourceReader({}),
                audit_repository_factory=lambda _args: FakeAuditRepository(),
            )
        self.assertEqual(return_code, 1)
        self.assertEqual(summary["counts"], {"identity_mismatch": 1})
        self.assertEqual(summary["status"], "completed_with_failures")
        self.assertIn("account_key", summary["items"][0]["error"])

    def test_infrastructure_failure_is_written_to_both_summaries(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            self.write_candidate(root, self.payload())

            class BrokenRepository(FakeAuditRepository):
                def check_contract(self):
                    raise RuntimeError("audit unavailable")

            return_code, summary = run_daily(
                self.args(root),
                source_reader_factory=lambda _args: self.fail("source must not open"),
                audit_repository_factory=lambda _args: BrokenRepository(),
            )

            self.assertEqual(return_code, 1)
            self.assertEqual(summary["status"], "infrastructure_failed")
            self.assertEqual(summary["error_type"], "RuntimeError")
            latest = json.loads(
                (root / "output" / "latest.summary.json").read_text(encoding="utf-8")
            )
            run_summary = json.loads(
                (Path(summary["manager_dir"]) / "summary.json").read_text(encoding="utf-8")
            )
            self.assertEqual(latest["status"], "infrastructure_failed")
            self.assertEqual(run_summary["error"], "audit unavailable")


class ListingDailyTaskScriptContractTests(unittest.TestCase):
    def test_windows_task_script_is_guarded_and_draft_only(self) -> None:
        script = (
            Path(__file__).resolve().parents[1] / "scripts" / "manage_1688_listing_daily_task.ps1"
        ).read_text(encoding="utf-8")
        self.assertIn('TaskName = "YYDD-1688-Listing-Daily"', script)
        self.assertIn('AccountKey = "muke_lixiang"', script)
        self.assertIn("New-ScheduledTaskTrigger -Daily -At $DailyAt", script)
        self.assertIn("Register-ScheduledTask -TaskName $TaskName", script)
        self.assertIn("-MultipleInstances IgnoreNew", script)
        self.assertIn("Remove-Item Env:ENABLE_1688_LISTING_SUBMIT", script)
        self.assertIn("logon_type = [string]$principal.LogonType", script)
        self.assertIn("run_level = [string]$principal.RunLevel", script)
        self.assertIn("working_directory = [string]$_.WorkingDirectory", script)
        self.assertNotIn('"--mode", "submit"', script)


if __name__ == "__main__":
    unittest.main()
