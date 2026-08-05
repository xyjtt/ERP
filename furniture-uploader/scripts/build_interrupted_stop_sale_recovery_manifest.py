from __future__ import annotations

import argparse
import csv
import hashlib
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
    parser.add_argument("--approved-scope-file", required=True)
    parser.add_argument("--approved-scope-sha256", required=True)
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


def _identity_payload(identity: tuple[str, str, str, str]) -> dict[str, str]:
    return dict(
        zip(
            ("store_name", "product_id", "online_sku", "platform_store_item_code"),
            identity,
        )
    )


def _identity_from_approval(payload: Any) -> tuple[str, str, str, str]:
    if not isinstance(payload, dict):
        raise RuntimeError("Approved recovery identity must be an object.")
    identity = _task_identity(
        payload.get("store_name"),
        payload.get("product_id"),
        payload.get("online_sku"),
        payload.get("platform_store_item_code"),
    )
    if not all(identity):
        raise RuntimeError("Approved recovery identity contains an empty key field.")
    return identity


def _canonical_scope_payload(
    manager_run_id: str,
    missing: set[tuple[str, str, str, str]],
    technical: set[tuple[str, str, str, str]],
) -> dict[str, Any]:
    return {
        "manager_run_id": manager_run_id,
        "expected_counts": {"missing": len(missing), "technical": len(technical)},
        "missing_identities": [
            _identity_payload(identity) for identity in sorted(missing)
        ],
        "technical_identities": [
            _identity_payload(identity) for identity in sorted(technical)
        ],
    }


def _canonical_scope_sha256(payload: dict[str, Any]) -> str:
    serialized = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _load_approved_scope(
    path: Path,
    expected_file_sha256: str,
    manager_run_id: str,
) -> tuple[set[tuple[str, str, str, str]], set[tuple[str, str, str, str]], dict[str, Any]]:
    if not path.is_file():
        raise FileNotFoundError(f"Approved recovery scope does not exist: {path}")
    raw = path.read_bytes()
    observed_file_sha256 = hashlib.sha256(raw).hexdigest()
    if not re.fullmatch(r"[0-9a-f]{64}", expected_file_sha256):
        raise ValueError("--approved-scope-sha256 must be a lowercase SHA-256 value")
    if observed_file_sha256 != expected_file_sha256:
        raise RuntimeError("Approved recovery scope file SHA-256 does not match.")
    payload = json.loads(raw.decode("utf-8-sig"))
    if not isinstance(payload, dict):
        raise RuntimeError("Approved recovery scope must be an object.")
    if int(payload.get("version") or 0) != 1:
        raise RuntimeError("Approved recovery scope version must be 1.")
    if str(payload.get("manager_run_id") or "") != manager_run_id:
        raise RuntimeError("Approved recovery scope manager_run_id does not match.")
    expected_counts = payload.get("expected_counts")
    if expected_counts != {"missing": 52, "technical": 7}:
        raise RuntimeError("Approved recovery scope must declare exactly 52 missing and 7 technical.")

    missing_raw = payload.get("missing_identities")
    technical_raw = payload.get("technical_identities")
    if not isinstance(missing_raw, list) or not isinstance(technical_raw, list):
        raise RuntimeError("Approved recovery scope identity lists are missing.")
    missing = {_identity_from_approval(item) for item in missing_raw}
    technical = {_identity_from_approval(item) for item in technical_raw}
    if len(missing) != len(missing_raw) or len(technical) != len(technical_raw):
        raise RuntimeError("Approved recovery scope contains duplicate identities.")
    if len(missing) != 52 or len(technical) != 7:
        raise RuntimeError("Approved recovery scope identity counts must be exactly 52 and 7.")
    if missing & technical:
        raise RuntimeError("Approved recovery scope classifications overlap.")

    canonical = _canonical_scope_payload(manager_run_id, missing, technical)
    observed_identity_sha256 = _canonical_scope_sha256(canonical)
    if str(payload.get("identity_set_sha256") or "") != observed_identity_sha256:
        raise RuntimeError("Approved recovery identity-set SHA-256 does not match.")
    evidence = {
        "approved_scope_path": str(path),
        "approved_scope_file_sha256": observed_file_sha256,
        "approved_identity_set_sha256": observed_identity_sha256,
    }
    return missing, technical, evidence


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)
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
    executable_paths = [
        output_dir / name
        for name in ("missing.csv", "technical.csv", "recovery.csv", "manifest.json")
    ]
    existing = [str(path) for path in executable_paths if path.exists()]
    if existing:
        raise RuntimeError(
            "Recovery output directory contains prior executable artifacts: "
            + ", ".join(existing)
        )

    actual_missing = {_source_task_identity(row) for row in recovery_rows["missing"]}
    actual_technical = {_source_task_identity(row) for row in recovery_rows["technical"]}
    counts = Counter(item["classification"] for item in classified)
    reason_counts = Counter(item["reason"] for item in classified)
    approved_scope_path = Path(str(args.approved_scope_file)).resolve()
    try:
        approved_missing, approved_technical, approval_evidence = _load_approved_scope(
            approved_scope_path,
            str(args.approved_scope_sha256 or "").strip(),
            manager_run_id,
        )
    except Exception as exc:
        diagnostic = {
            "status": "blocked_approval_invalid",
            "manager_run_id": manager_run_id,
            "manager_dir": str(manager_dir),
            "preview_report_path": str(preview_path),
            "source_csv": str(source_path),
            "selected_count": selected_count,
            "child_run_count": len(child_rows),
            "classification_counts": dict(sorted(counts.items())),
            "reason_counts": dict(sorted(reason_counts.items())),
            "approved_scope_path": str(approved_scope_path),
            "approval_error": f"{type(exc).__name__}: {exc}",
            "executable_artifacts_written": False,
        }
        diagnostic["diagnostic_path"] = _write_json_atomic(
            output_dir / "diagnostic.json",
            diagnostic,
        )
        return diagnostic
    missing_only_actual = sorted(actual_missing - approved_missing)
    missing_only_approved = sorted(approved_missing - actual_missing)
    technical_only_actual = sorted(actual_technical - approved_technical)
    technical_only_approved = sorted(approved_technical - actual_technical)
    scope_matches = not any(
        (
            missing_only_actual,
            missing_only_approved,
            technical_only_actual,
            technical_only_approved,
        )
    )
    diagnostic = {
        "status": "approved_scope_match" if scope_matches else "blocked_scope_mismatch",
        "manager_run_id": manager_run_id,
        "manager_dir": str(manager_dir),
        "preview_report_path": str(preview_path),
        "source_csv": str(source_path),
        "selected_count": selected_count,
        "child_run_count": len(child_rows),
        "classification_counts": dict(sorted(counts.items())),
        "reason_counts": dict(sorted(reason_counts.items())),
        **approval_evidence,
        "actual_identity_set_sha256": _canonical_scope_sha256(
            _canonical_scope_payload(manager_run_id, actual_missing, actual_technical)
        ),
        "scope_mismatch": {
            "missing_only_actual": [_identity_payload(item) for item in missing_only_actual],
            "missing_only_approved": [_identity_payload(item) for item in missing_only_approved],
            "technical_only_actual": [_identity_payload(item) for item in technical_only_actual],
            "technical_only_approved": [_identity_payload(item) for item in technical_only_approved],
        },
        "executable_artifacts_written": False,
    }
    diagnostic["diagnostic_path"] = _write_json_atomic(
        output_dir / "diagnostic.json",
        diagnostic,
    )
    if not scope_matches:
        return diagnostic

    missing_path = _write_csv(output_dir / "missing.csv", fieldnames, recovery_rows["missing"])
    technical_path = _write_csv(output_dir / "technical.csv", fieldnames, recovery_rows["technical"])
    combined_rows = [
        item["source"] for item in classified if item["classification"] in recovery_rows
    ]
    combined_path = _write_csv(output_dir / "recovery.csv", fieldnames, combined_rows)
    manifest = {
        "status": "approved",
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
        **approval_evidence,
        "actual_identity_set_sha256": diagnostic["actual_identity_set_sha256"],
        "executable_artifacts_written": True,
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
    return 0 if result.get("status") == "approved" else 2


if __name__ == "__main__":
    raise SystemExit(main())
