# -*- coding: utf-8 -*-
"""Read-only audit for legacy null and zero keyword score inputs."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Dict, Iterable, List, Tuple


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from database.db_manager import DatabaseManager


def _json_value(value: Any) -> Any:
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    return value


def build_audit_report(rows: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    normalized: List[Dict[str, Any]] = [
        {key: _json_value(value) for key, value in row.items()}
        for row in rows
    ]
    totals = {
        "source_count": len(normalized),
        "total_rows": sum(int(row.get("total_rows") or 0) for row in normalized),
        "ambiguous_zero_rows": sum(
            int(row.get("ambiguous_zero_rows") or 0)
            for row in normalized
        ),
    }
    warnings = []
    if totals["ambiguous_zero_rows"]:
        warnings.append(
            "Zero values are ambiguous legacy evidence and require source-level review; this tool does not mutate them."
        )
    if not normalized:
        warnings.append(
            "No audit rows returned. Verify that app.ali1688_title_keyword exists and the runtime account has SELECT permission."
        )
    return {
        "read_only": True,
        "table": "app.ali1688_title_keyword",
        "totals": totals,
        "sources": normalized,
        "warnings": warnings,
    }


def build_error_report(error: Exception) -> Dict[str, Any]:
    """Return a structured, redacted report for a failed read-only query."""
    error_text = str(error).lower()
    table_unavailable = any(
        marker in error_text
        for marker in ("42s02", "invalid object name", "does not exist")
    )
    code = "SOURCE_TABLE_UNAVAILABLE" if table_unavailable else "AUDIT_QUERY_FAILED"
    message = (
        "The title keyword table is unavailable or the runtime account lacks SELECT permission."
        if table_unavailable
        else "The read-only keyword score audit query failed."
    )
    return {
        "read_only": True,
        "table": "app.ali1688_title_keyword",
        "totals": {
            "source_count": 0,
            "total_rows": 0,
            "ambiguous_zero_rows": 0,
        },
        "sources": [],
        "warnings": [],
        "error": {
            "code": code,
            "message": message,
            "exception_type": type(error).__name__,
        },
    }


def run_audit(
    manager: DatabaseManager,
    fail_on_ambiguous_zero: bool = False,
) -> Tuple[Dict[str, Any], int]:
    try:
        report = build_audit_report(manager.audit_keyword_score_inputs())
    except Exception as error:
        return build_error_report(error), 4

    if not report["sources"]:
        return report, 3
    if fail_on_ambiguous_zero and report["totals"]["ambiguous_zero_rows"]:
        return report, 2
    return report, 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Audit null and zero title-keyword score inputs without changing data."
    )
    parser.add_argument(
        "--fail-on-ambiguous-zero",
        action="store_true",
        help="Return exit code 2 when ambiguous zero rows are present.",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    report, exit_code = run_audit(
        DatabaseManager(),
        fail_on_ambiguous_zero=args.fail_on_ambiguous_zero,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
