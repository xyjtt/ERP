from __future__ import annotations

import csv
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

from build_interrupted_stop_sale_recovery_manifest import build_manifest


class InterruptedStopSaleRecoveryManifestTests(unittest.TestCase):
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

    def write_csv(self, path: Path, rows: list[dict[str, str]]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=self.fieldnames)
            writer.writeheader()
            writer.writerows(rows)

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

    def test_manifest_separates_missing_technical_and_excluded_rows(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            manager_id = "daily_20260804_130814_970163"
            manager_dir = root / manager_id
            preview_dir = manager_dir / "db_previews"
            source = preview_dir / "preview.csv"
            rows = [self.row(index) for index in range(1, 7)]
            self.write_csv(source, rows)
            preview = preview_dir / "stop_sale_db_preview_report_20260804_1.json"
            preview.write_text(
                json.dumps({"preview_csv": str(source), "selected_count": len(rows)}),
                encoding="utf-8",
            )
            pipeline_dir = root / "pipelines"
            offline_dir = root / "run_reports"
            pipeline_dir.mkdir()
            offline_dir.mkdir()
            child = f"{manager_id}_s01_b001"
            (pipeline_dir / f"{child}.summary.json").write_text(
                json.dumps({"run_id": child, "audit_status": "partial"}), encoding="utf-8"
            )
            offline_records = [
                {"store_name": "STORE-A", "product_id": "P2", "online_sku": "SKU-2", "status": "failed", "error_category": "automation_error"},
                {"store_name": "STORE-A", "product_id": "P3", "online_sku": "SKU-3", "status": "failed", "error_category": "system_prompt"},
                {"store_name": "STORE-A", "product_id": "P4", "online_sku": "SKU-4", "status": "success"},
                {"store_name": "STORE-A", "product_id": "P5", "online_sku": "SKU-5", "status": "already_offline"},
                {"store_name": "STORE-A", "product_id": "P6", "online_sku": "SKU-6", "status": "failed", "error_category": "login_required"},
            ]
            (offline_dir / f"{child}.jsonl").write_text(
                "\n".join(json.dumps(row) for row in offline_records) + "\n", encoding="utf-8"
            )
            jst_dir = pipeline_dir / f"{child}.jushuitan-results"
            jst_dir.mkdir()
            jst_records = [
                {"store_name": "STORE-A", "product_id": "P4", "online_sku": "SKU-4", "platform_store_item_code": "CODE-4", "status": "success"},
                {"store_name": "STORE-A", "product_id": "P5", "online_sku": "SKU-5", "platform_store_item_code": "CODE-5", "status": "failed", "category": "task_not_found"},
            ]
            (jst_dir / f"{child}.jsonl").write_text(
                "\n".join(json.dumps(row) for row in jst_records) + "\n", encoding="utf-8"
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
                                "status": "partial",
                                "summary_path": str(pipeline_dir / f"{child}.summary.json"),
                                "offline_report_path": str(offline_dir / f"{child}.jsonl"),
                                "jushuitan_report_path": str(jst_dir / f"{child}.jsonl"),
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            output = root / "recovery"
            args = SimpleNamespace(
                manager_run_id=manager_id,
                manager_dir=str(manager_dir),
                preview_report="",
                pipeline_dir=str(pipeline_dir),
                offline_report_dir=str(offline_dir),
                output_dir=str(output),
            )

            manifest = build_manifest(args)

            self.assertEqual(
                manifest["classification_counts"],
                {"excluded": 2, "missing": 1, "technical": 3},
            )
            self.assertEqual(manifest["recovery_count"], 4)
            with Path(manifest["missing_csv"]).open(encoding="utf-8-sig", newline="") as handle:
                missing_rows = list(csv.DictReader(handle))
            with Path(manifest["technical_csv"]).open(encoding="utf-8-sig", newline="") as handle:
                technical_rows = list(csv.DictReader(handle))
            self.assertEqual([row["商品ID"] for row in missing_rows], ["P1"])
            self.assertEqual([row["商品ID"] for row in technical_rows], ["P2", "P5", "P6"])
            with Path(manifest["recovery_csv"]).open(encoding="utf-8-sig", newline="") as handle:
                recovery_rows = list(csv.DictReader(handle))
            self.assertEqual(
                [row["商品ID"] for row in recovery_rows], ["P1", "P2", "P5", "P6"]
            )

    def test_manifest_rejects_preview_count_mismatch(self) -> None:
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
            )
            with self.assertRaisesRegex(RuntimeError, "selected_count mismatch"):
                build_manifest(args)

    def test_manifest_with_no_children_marks_every_source_row_missing(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            manager_id = "daily_2"
            manager_dir = root / manager_id
            preview_dir = manager_dir / "db_previews"
            source = preview_dir / "preview.csv"
            self.write_csv(source, [self.row(1), self.row(2)])
            (preview_dir / "stop_sale_db_preview_report_2.json").write_text(
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
            output = root / "output"
            manifest = build_manifest(
                SimpleNamespace(
                    manager_run_id=manager_id,
                    manager_dir=str(manager_dir),
                    preview_report="",
                    pipeline_dir=str(root / "pipelines"),
                    offline_report_dir=str(root / "reports"),
                    output_dir=str(output),
                )
            )

            self.assertEqual(manifest["classification_counts"], {"missing": 2})
            self.assertEqual(manifest["recovery_count"], 2)
            self.assertTrue(Path(manifest["technical_csv"]).is_file())
            with Path(manifest["technical_csv"]).open(
                encoding="utf-8-sig", newline=""
            ) as handle:
                self.assertEqual(list(csv.DictReader(handle)), [])


if __name__ == "__main__":
    unittest.main()
