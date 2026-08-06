from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_ROOT = PROJECT_ROOT / "scripts"
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

from build_interrupted_stop_sale_recovery_manifest import (
    _format_cli_result,
    _canonical_scope_payload,
    _canonical_scope_sha256,
    _source_task_identity,
    build_manifest,
)


class InterruptedStopSaleRecoveryManifestTests(unittest.TestCase):
    def test_cli_result_is_safe_for_windows_gbk_stdout(self) -> None:
        rendered = _format_cli_result({"marker": "范围©"})

        rendered.encode("gbk")
        self.assertIn(r"\u00a9", rendered)

    fieldnames = [
        "店铺名称",
        "平台",
        "商品ID",
        "平台店铺商品编码",
        "线上商品编码",
        "处理说明",
        "可替换商品编码（新）",
        "是否换图",
        "指标日期",
    ]

    def row(self, number: int) -> dict[str, str]:
        return {
            "店铺名称": "STORE-A",
            "平台": "Alibaba",
            "商品ID": f"P{number}",
            "平台店铺商品编码": f"CODE-{number}",
            "线上商品编码": f"SKU-{number}",
            "处理说明": "全渠道下架",
            "可替换商品编码（新）": "",
            "是否换图": "",
            "指标日期": "2026-08-04",
        }

    def write_csv(self, path: Path, rows: list[dict[str, str]]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=self.fieldnames)
            writer.writeheader()
            writer.writerows(rows)

    def prepare_run(self, root: Path) -> tuple[str, Path, Path, Path, list[dict[str, str]]]:
        manager_id = "daily_20260804_130814_970163"
        manager_dir = root / manager_id
        preview_dir = manager_dir / "db_previews"
        source = preview_dir / "preview.csv"
        rows = [self.row(index) for index in range(1, 60)]
        self.write_csv(source, rows)
        (preview_dir / "stop_sale_db_preview_report_20260804_1.json").write_text(
            json.dumps({"preview_csv": str(source), "selected_count": len(rows)}),
            encoding="utf-8",
        )

        pipeline_dir = root / "pipelines"
        offline_dir = root / "run_reports"
        pipeline_dir.mkdir()
        offline_dir.mkdir()
        child = f"{manager_id}_s01_b001"
        child_summary = pipeline_dir / f"{child}.summary.json"
        child_summary.write_text(
            json.dumps({"run_id": child, "audit_status": "failed"}),
            encoding="utf-8",
        )
        technical_records = [
            {
                "store_name": "STORE-A",
                "product_id": f"P{index}",
                "online_sku": f"SKU-{index}",
                "status": "failed",
                "error_category": "automation_error",
            }
            for index in range(53, 60)
        ]
        offline_report = offline_dir / f"{child}.jsonl"
        offline_report.write_text(
            "\n".join(json.dumps(row) for row in technical_records) + "\n",
            encoding="utf-8",
        )
        (manager_dir / "summary.json").write_text(
            json.dumps(
                {
                    "manager_run_id": manager_id,
                    "error_type": "InterruptedDailyManagerProcess",
                    "recovery_status": "finalized",
                    "lock_removed": True,
                    "child_run_count": 1,
                    "child_runs": [
                        {
                            "run_id": child,
                            "status": "failed",
                            "summary_path": str(child_summary),
                            "offline_report_path": str(offline_report),
                            "jushuitan_report_path": "",
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        return manager_id, manager_dir, pipeline_dir, offline_dir, rows

    def write_approval(
        self,
        root: Path,
        manager_id: str,
        missing_rows: list[dict[str, str]],
        technical_rows: list[dict[str, str]],
    ) -> tuple[Path, str]:
        missing = {_source_task_identity(row) for row in missing_rows}
        technical = {_source_task_identity(row) for row in technical_rows}
        canonical = _canonical_scope_payload(manager_id, missing, technical)
        payload = {
            "version": 1,
            **canonical,
            "identity_set_sha256": _canonical_scope_sha256(canonical),
        }
        path = root / "approved_scope.json"
        path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        return path, hashlib.sha256(path.read_bytes()).hexdigest()

    def args(
        self,
        manager_id: str,
        manager_dir: Path,
        pipeline_dir: Path,
        offline_dir: Path,
        output: Path,
        approval: Path,
        approval_sha256: str,
    ) -> SimpleNamespace:
        return SimpleNamespace(
            manager_run_id=manager_id,
            manager_dir=str(manager_dir),
            preview_report="",
            pipeline_dir=str(pipeline_dir),
            offline_report_dir=str(offline_dir),
            output_dir=str(output),
            approved_scope_file=str(approval),
            approved_scope_sha256=approval_sha256,
        )

    def test_exact_52_missing_and_7_technical_scope_writes_executable_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            manager_id, manager_dir, pipeline_dir, offline_dir, rows = self.prepare_run(root)
            approval, approval_hash = self.write_approval(
                root,
                manager_id,
                rows[:52],
                rows[52:],
            )
            output = root / "recovery"

            manifest = build_manifest(
                self.args(
                    manager_id,
                    manager_dir,
                    pipeline_dir,
                    offline_dir,
                    output,
                    approval,
                    approval_hash,
                )
            )

            self.assertEqual(manifest["status"], "approved")
            self.assertEqual(manifest["classification_counts"], {"missing": 52, "technical": 7})
            self.assertEqual(manifest["recovery_count"], 59)
            self.assertTrue(manifest["executable_artifacts_written"])
            for name in ("missing.csv", "technical.csv", "recovery.csv", "manifest.json", "diagnostic.json"):
                self.assertTrue((output / name).is_file())

    def test_identity_drift_writes_diagnostic_only(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            manager_id, manager_dir, pipeline_dir, offline_dir, rows = self.prepare_run(root)
            approval, approval_hash = self.write_approval(
                root,
                manager_id,
                [*rows[:51], rows[52]],
                [rows[51], *rows[53:]],
            )
            output = root / "recovery"

            diagnostic = build_manifest(
                self.args(
                    manager_id,
                    manager_dir,
                    pipeline_dir,
                    offline_dir,
                    output,
                    approval,
                    approval_hash,
                )
            )

            self.assertEqual(diagnostic["status"], "blocked_scope_mismatch")
            self.assertFalse(diagnostic["executable_artifacts_written"])
            self.assertTrue((output / "diagnostic.json").is_file())
            for name in ("missing.csv", "technical.csv", "recovery.csv", "manifest.json"):
                self.assertFalse((output / name).exists())
            self.assertEqual(len(diagnostic["scope_mismatch"]["missing_only_actual"]), 1)
            self.assertEqual(len(diagnostic["scope_mismatch"]["technical_only_actual"]), 1)

    def test_invalid_approval_hash_writes_diagnostic_only(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            manager_id, manager_dir, pipeline_dir, offline_dir, rows = self.prepare_run(root)
            approval, _approval_hash = self.write_approval(root, manager_id, rows[:52], rows[52:])
            output = root / "recovery"

            diagnostic = build_manifest(
                self.args(
                    manager_id,
                    manager_dir,
                    pipeline_dir,
                    offline_dir,
                    output,
                    approval,
                    "0" * 64,
                )
            )

            self.assertEqual(diagnostic["status"], "blocked_approval_invalid")
            self.assertIn("file SHA-256", diagnostic["approval_error"])
            self.assertEqual(list(output.glob("*.csv")), [])

    def test_manifest_rejects_preview_count_mismatch_before_approval(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            manager_id = "daily_1"
            manager_dir = root / manager_id
            preview_dir = manager_dir / "db_previews"
            source = preview_dir / "preview.csv"
            self.write_csv(source, [self.row(1)])
            (preview_dir / "stop_sale_db_preview_report_1.json").write_text(
                json.dumps({"preview_csv": str(source), "selected_count": 2}),
                encoding="utf-8",
            )
            (manager_dir / "summary.json").write_text(
                json.dumps(
                    {
                        "manager_run_id": manager_id,
                        "error_type": "InterruptedDailyManagerProcess",
                        "recovery_status": "finalized",
                        "lock_removed": True,
                        "child_run_count": 0,
                        "child_runs": [],
                    }
                ),
                encoding="utf-8",
            )
            args = SimpleNamespace(
                manager_run_id=manager_id,
                manager_dir=str(manager_dir),
                preview_report="",
                pipeline_dir=str(root / "pipelines"),
                offline_report_dir=str(root / "reports"),
                output_dir=str(root / "output"),
                approved_scope_file=str(root / "missing.json"),
                approved_scope_sha256="0" * 64,
            )
            with self.assertRaisesRegex(RuntimeError, "selected_count mismatch"):
                build_manifest(args)


if __name__ == "__main__":
    unittest.main()
