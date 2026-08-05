from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
RPA_ROOT = PROJECT_ROOT / "rpa"
if str(RPA_ROOT) not in sys.path:
    sys.path.insert(0, str(RPA_ROOT))

from manage_1688_stop_sale_daily import (
    NON_RETRYABLE_OFFLINE_CATEGORIES,
    SAFETY_ERROR_CATEGORIES,
    TERMINAL_JUSHUITAN_STATUSES,
    TERMINAL_OFFLINE_STATUSES,
)
from stop_sale_audit import load_jsonl_records, normalized_identity_part


MANAGER_ID_PATTERN = re.compile(r"[A-Za-z0-9_.-]{1,64}")


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Build exact recovery CSV files from one interrupted stop-sale manager run."
    )
    parser.add_argument("--manager-run-id", required=True)
    parser.add_argument("--manager-dir", required=True)
    parser.add_argument("--preview-report", default="")
    parser.add_argument("--pipeline-dir", default=str(PROJECT_ROOT / "logs" / "sku_offline" / "pipelines"))
    parser.add_argument("--offline-report-dir", default=str(PROJECT_ROOT / "logs" / "sku_offline" / "run_reports"))
    parser.add_argument("--output-dir", required=True)
    return parser


def _row_value(row: dict[str, Any], *names: str) -> str:
    for name in names:
        if name in row:
            return str(row.get(name) or "").strip()
    return ""


def _offline_identity(store_name: Any, product_id: Any, online_sku: Any) -> tuple[str, str, str]:
    return tuple(
        normalized_identity_part(value)
        for value in (store_name, product_id, online_sku)
    )  # type: ignore[return-value]


def _task_identity(
    store_name: Any,
    product_id: Any,
    online_sku: Any,
    platform_store_item_code: Any,
) -> tuple[str, str, str, str]:
    return tuple(
        normalized_identity_part(value)
        for value in (store_name, product_id, online_sku, platform_store_item_code)
    )  # type: ignore[return-value]


def _record_offline_identity(record: dict[str, Any]) -> tuple[str, str, str]:
    return _offline_identity(
        record.get("store_name"), record.get("product_id"), record.get("online_sku")
    )


def _record_task_identity(record: dict[str, Any]) -> tuple[str, str, str, str]:
    return _task_identity(
        record.get("store_name"),
        record.get("product_id"),
        record.get("online_sku"),
        record.get("platform_store_item_code"),
    )


def _source_task_identity(row: dict[str, Any]) -> tuple[str, str, str, str]:
    return _task_identity(
        _row_value(row, "店铺名称", "store_name"),
        _row_value(row, "商品ID", "product_id"),
        _row_value(row, "线上商品编码", "online_sku"),
        _row_value(row, "平台店铺商品编码", "platform_store_item_code"),
    )


def _source_offline_identity(row: dict[str, Any]) -> tuple[str, str, str]:
    return _offline_identity(
        _row_value(row, "店铺名称", "store_name"),
        _row_value(row, "商品ID", "product_id"),
        _row_value(row, "线上商品编码", "online_sku"),
    )


def _read_csv(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        fieldnames = list(reader.fieldnames or [])
        rows = list(reader)
    if not fieldnames:
        raise RuntimeError(f"Recovery source CSV has no header: {path}")
    return fieldnames, rows


def _resolve_preview_report(manager_dir: Path, configured: str) -> tuple[Path, dict[str, Any]]:
    if configured:
        candidates = [Path(configured).resolve()]
    else:
        candidates = sorted((manager_dir / "db_previews").glob("*_db_preview_report_*.json"))
    if len(candidates) != 1 or not candidates[0].is_file():
        raise RuntimeError(
            "Exactly one preview report is required; "
            f"found={[str(path) for path in candidates]}"
        )
    payload = json.loads(candidates[0].read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise RuntimeError(f"Preview report is not an object: {candidates[0]}")
    return candidates[0], payload


def _resolve_source_csv(preview_path: Path, preview: dict[str, Any]) -> Path:
    source = Path(str(preview.get("preview_csv") or ""))
    if not source.is_absolute():
        source = preview_path.parent / source
    source = source.resolve()
    if not source.is_file():
        raise FileNotFoundError(f"Aggregate preview CSV does not exist: {source}")
    return source


def _child_sequence(run_id: str, manager_run_id: str) -> tuple[int, int, int]:
    match = re.fullmatch(
        rf"{re.escape(manager_run_id)}_s(\d{{2}})_b(\d{{3}})(?:_a(\d{{2}}))?",
        run_id,
    )
    if not match:
        raise RuntimeError(f"Unexpected child run_id: {run_id}")
    return int(match.group(1)), int(match.group(2)), int(match.group(3) or 1)


def _resolve_evidence_path(configured: Any, fallback: Path) -> Path:
    configured_text = str(configured or "").strip()
    path = Path(configured_text) if configured_text else fallback
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    path = path.resolve()
    if configured_text and not path.is_file():
        raise FileNotFoundError(f"Configured child evidence does not exist: {path}")
    return path


def _load_manager_children(
    manager_run_id: str,
    manager_dir: Path,
) -> list[dict[str, Any]]:
    summary_path = manager_dir / "summary.json"
    if not summary_path.is_file():
        raise FileNotFoundError(f"Recovered manager summary does not exist: {summary_path}")
    payload = json.loads(summary_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise RuntimeError(f"Recovered manager summary is not an object: {summary_path}")
    if str(payload.get("manager_run_id") or "") != manager_run_id:
        raise RuntimeError("Recovered manager summary manager_run_id does not match.")
    if str(payload.get("error_type") or "") != "InterruptedDailyManagerProcess":
        raise RuntimeError("Manager summary was not produced by interrupted-run recovery.")
    if payload.get("recovery_status") != "finalized" or payload.get("lock_removed") is not True:
        raise RuntimeError("Interrupted manager recovery is not finalized.")
    raw_children = payload.get("child_runs")
    if not isinstance(raw_children, list):
        raise RuntimeError("Recovered manager summary child_runs is missing.")
    children: list[dict[str, Any]] = []
    observed: set[str] = set()
    for raw_child in raw_children:
        if not isinstance(raw_child, dict):
            raise RuntimeError("Recovered manager summary contains an invalid child run.")
        run_id = str(raw_child.get("run_id") or "")
        _child_sequence(run_id, manager_run_id)
        if run_id in observed:
            raise RuntimeError(f"Recovered manager summary contains duplicate child run: {run_id}")
        observed.add(run_id)
        children.append(dict(raw_child))
    declared_count = int(payload.get("child_run_count") or 0)
    if declared_count != len(children):
        raise RuntimeError(
            f"Recovered manager child count mismatch: summary={declared_count}, rows={len(children)}"
        )
    return sorted(children, key=lambda item: _child_sequence(str(item["run_id"]), manager_run_id))


def _latest_records(
    manager_run_id: str,
    child_rows: list[dict[str, Any]],
    pipeline_dir: Path,
    offline_report_dir: Path,
) -> tuple[dict[tuple[str, str, str], dict[str, Any]], dict[tuple[str, str, str, str], dict[str, Any]], list[dict[str, Any]]]:
    offline: dict[tuple[str, str, str], dict[str, Any]] = {}
    jushuitan: dict[tuple[str, str, str, str], dict[str, Any]] = {}
    children: list[dict[str, Any]] = []
    for child_row in child_rows:
        run_id = str(child_row["run_id"])
        summary_path = _resolve_evidence_path(
            child_row.get("summary_path"),
            pipeline_dir / f"{run_id}.summary.json",
        )
        offline_path = _resolve_evidence_path(
            child_row.get("offline_report_path"),
            offline_report_dir / f"{run_id}.jsonl",
        )
        jushuitan_path = _resolve_evidence_path(
            child_row.get("jushuitan_report_path"),
            pipeline_dir / f"{run_id}.jushuitan-results" / f"{run_id}.jsonl",
        )
        if not summary_path.is_file():
            raise FileNotFoundError(f"Pipeline summary does not exist: {summary_path}")
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
        if not isinstance(summary, dict) or str(summary.get("run_id") or "") != run_id:
            raise RuntimeError(f"Pipeline summary run_id mismatch: {summary_path}")
        audit_status = str(summary.get("audit_status") or "")
        if audit_status != str(child_row.get("status") or ""):
            raise RuntimeError(f"Pipeline summary status changed after manager recovery: {run_id}")
        offline_records = load_jsonl_records(offline_path)
        jushuitan_records = load_jsonl_records(jushuitan_path)
        for record in offline_records:
            offline[_record_offline_identity(record)] = {**record, "evidence_run_id": run_id}
        for record in jushuitan_records:
            jushuitan[_record_task_identity(record)] = {**record, "evidence_run_id": run_id}
        children.append(
            {
                "run_id": run_id,
                "sequence": _child_sequence(run_id, manager_run_id),
                "summary_path": str(summary_path),
                "audit_status": audit_status,
                "offline_report_path": str(offline_path) if offline_path.is_file() else "",
                "offline_record_count": len(offline_records),
                "jushuitan_report_path": str(jushuitan_path) if jushuitan_path.is_file() else "",
                "jushuitan_record_count": len(jushuitan_records),
            }
        )
    return offline, jushuitan, children


def classify_recovery_row(
    row: dict[str, Any],
    offline_by_identity: dict[tuple[str, str, str], dict[str, Any]],
    jushuitan_by_identity: dict[tuple[str, str, str, str], dict[str, Any]],
) -> tuple[str, str, str, dict[str, Any]]:
    offline = offline_by_identity.get(_source_offline_identity(row))
    jushuitan = jushuitan_by_identity.get(_source_task_identity(row))
    if offline is None or str(offline.get("status") or "").strip() in {"", "pending", "not_attempted"}:
        return "missing", "1688", "missing_1688_result", {"offline": offline, "jushuitan": jushuitan}

    offline_status = str(offline.get("status") or "").strip()
    offline_category = str(
        offline.get("error_category") or offline.get("page_error_category") or ""
    ).strip()
    if offline_status == "failed":
        if offline_category in NON_RETRYABLE_OFFLINE_CATEGORIES:
            return "excluded", "1688", f"business_terminal:{offline_category}", {"offline": offline, "jushuitan": jushuitan}
        if offline_category in SAFETY_ERROR_CATEGORIES:
            return "technical", "1688", f"safety:{offline_category}", {"offline": offline, "jushuitan": jushuitan}
        return "technical", "1688", offline_category or "failed_without_category", {"offline": offline, "jushuitan": jushuitan}

    if offline_status not in TERMINAL_OFFLINE_STATUSES:
        return "technical", "1688", f"unexpected_1688_status:{offline_status}", {"offline": offline, "jushuitan": jushuitan}
    if jushuitan is None:
        return "technical", "jushuitan", "missing_jushuitan_result", {"offline": offline, "jushuitan": None}
    jushuitan_status = str(jushuitan.get("status") or "").strip()
    if jushuitan_status in TERMINAL_JUSHUITAN_STATUSES:
        return "excluded", "completed", f"completed:{offline_status}+{jushuitan_status}", {"offline": offline, "jushuitan": jushuitan}
    category = str(jushuitan.get("category") or jushuitan.get("error_category") or "").strip()
    return "technical", "jushuitan", category or f"jushuitan_status:{jushuitan_status}", {"offline": offline, "jushuitan": jushuitan}


def _write_csv(path: Path, fieldnames: list[str], rows: list[dict[str, Any]]) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows([{key: row.get(key, "") for key in fieldnames} for row in rows])
    return str(path.resolve())


def build_manifest(args: argparse.Namespace) -> dict[str, Any]:
    manager_run_id = str(args.manager_run_id or "").strip()
    if not MANAGER_ID_PATTERN.fullmatch(manager_run_id):
        raise ValueError("--manager-run-id contains unsupported characters")
    manager_dir = Path(args.manager_dir).resolve()
    if not manager_dir.is_dir() or manager_dir.name != manager_run_id:
        raise RuntimeError("Manager directory must exist and match manager_run_id.")
    preview_path, preview = _resolve_preview_report(manager_dir, str(args.preview_report or ""))
    source_path = _resolve_source_csv(preview_path, preview)
    fieldnames, source_rows = _read_csv(source_path)
    selected_count = int(preview.get("selected_count") or 0)
    if selected_count != len(source_rows):
        raise RuntimeError(
            f"Preview selected_count mismatch: report={selected_count}, csv={len(source_rows)}"
        )
    identities = [_source_task_identity(row) for row in source_rows]
    if len(set(identities)) != len(identities):
        raise RuntimeError("Aggregate preview CSV contains duplicate task identities.")

    pipeline_dir = Path(args.pipeline_dir).resolve()
    offline_report_dir = Path(args.offline_report_dir).resolve()
    child_rows = _load_manager_children(manager_run_id, manager_dir)
    offline, jushuitan, children = _latest_records(
        manager_run_id, child_rows, pipeline_dir, offline_report_dir
    )

    classified: list[dict[str, Any]] = []
    recovery_rows: dict[str, list[dict[str, Any]]] = {"missing": [], "technical": []}
    for row in source_rows:
        classification, stage, reason, evidence = classify_recovery_row(row, offline, jushuitan)
        classified.append(
            {
                "classification": classification,
                "stage": stage,
                "reason": reason,
                "identity": _source_task_identity(row),
                "source": row,
                "evidence": evidence,
            }
        )
        if classification in recovery_rows:
            recovery_rows[classification].append(row)

    output_dir = Path(args.output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    missing_path = _write_csv(output_dir / "missing.csv", fieldnames, recovery_rows["missing"])
    technical_path = _write_csv(output_dir / "technical.csv", fieldnames, recovery_rows["technical"])
    combined_rows = [
        item["source"] for item in classified if item["classification"] in recovery_rows
    ]
    combined_path = _write_csv(output_dir / "recovery.csv", fieldnames, combined_rows)
    counts = Counter(item["classification"] for item in classified)
    reason_counts = Counter(item["reason"] for item in classified)
    manifest = {
        "manager_run_id": manager_run_id,
        "manager_dir": str(manager_dir),
        "preview_report_path": str(preview_path),
        "source_csv": str(source_path),
        "selected_count": selected_count,
        "child_run_count": len(child_rows),
        "children": children,
        "classification_counts": dict(sorted(counts.items())),
        "reason_counts": dict(sorted(reason_counts.items())),
        "missing_csv": missing_path,
        "technical_csv": technical_path,
        "recovery_csv": combined_path,
        "recovery_count": len(combined_rows),
        "records": classified,
    }
    manifest_path = output_dir / "manifest.json"
    manifest["manifest_path"] = str(manifest_path)
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    return manifest


def main() -> int:
    result = build_manifest(build_argument_parser().parse_args())
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
