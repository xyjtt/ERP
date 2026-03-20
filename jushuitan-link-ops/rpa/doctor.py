from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .config_loader import load_json_with_local_override


@dataclass
class DoctorIssue:
    severity: str
    area: str
    name: str
    message: str


@dataclass
class DoctorReport:
    issues: list[DoctorIssue] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not any(issue.severity == "error" for issue in self.issues)

    def to_text(self) -> str:
        lines = [f"Doctor status: {'ok' if self.ok else 'needs_config'}"]
        if not self.issues:
            lines.append("No issues found.")
        for issue in self.issues:
            lines.append(f"[{issue.severity.upper()}] {issue.area}::{issue.name} - {issue.message}")
        return "\n".join(lines)

    def to_json(self) -> str:
        return json.dumps(
            {
                "ok": self.ok,
                "issues": [
                    {
                        "severity": issue.severity,
                        "area": issue.area,
                        "name": issue.name,
                        "message": issue.message,
                    }
                    for issue in self.issues
                ],
            },
            ensure_ascii=False,
            indent=2,
        )


def build_doctor_report(config_dir: str | Path) -> DoctorReport:
    config_root = Path(config_dir)
    system_config = load_json_with_local_override(config_root / "systems" / "jushuitan.json")
    operation_config = load_json_with_local_override(config_root / "operations" / "code_change.json")

    report = DoctorReport()
    _inspect_required_value(system_config, report, "system", "login_url")
    _inspect_required_value(system_config, report, "system", "manage_page_url")
    _inspect_selector_group(operation_config.get("selectors", {}), report, "operation")
    return report


def write_local_selector_templates(config_dir: str | Path) -> dict[str, str]:
    config_root = Path(config_dir)
    base_path = config_root / "operations" / "code_change.json"
    target_path = base_path.with_suffix(".local.json")
    if target_path.exists():
        return {"operation_local": str(target_path)}

    base_config = load_json_with_local_override(base_path)
    selectors = base_config.get("selectors", {})
    template = {"selectors": {}}
    for name, selector in selectors.items():
        if isinstance(selector, dict):
            template["selectors"][name] = {
                "by": str(selector.get("by", "css")).strip() or "css",
                "value": "",
            }
    target_path.write_text(json.dumps(template, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {"operation_local": str(target_path)}


def _inspect_required_value(
    payload: dict[str, Any],
    report: DoctorReport,
    area: str,
    key: str,
) -> None:
    if str(payload.get(key, "")).strip():
        return
    report.issues.append(DoctorIssue("warning", area, key, "value is empty"))


def _inspect_selector_group(selectors: dict[str, Any], report: DoctorReport, area: str) -> None:
    if not selectors:
        report.issues.append(DoctorIssue("error", area, "selectors", "selectors block is missing"))
        return
    for name, selector in selectors.items():
        if not isinstance(selector, dict):
            continue
        if not str(selector.get("value", "")).strip():
            report.issues.append(DoctorIssue("warning", area, name, "selector value is empty"))
