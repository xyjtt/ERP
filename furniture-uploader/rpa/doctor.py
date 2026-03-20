from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from config_loader import load_json_with_local_override

SELECTOR_ACTIONS = {"click", "input", "textarea", "file", "select", "extract"}
SPECIAL_SELECTOR_KEYS = {
    "match_candidates": [
        "row_selector",
        "title_selector",
        "store_name_selector",
        "source_id_selector",
        "use_button_selector",
        "fallback_selector",
    ],
    "assert_no_errors": [],
    "sanitize_inputs": ["selector"],
}
ERROR_DETECTION_KEYS = [
    "message_selector",
    "close_selector",
]


@dataclass
class DoctorIssue:
    severity: str
    area: str
    step_name: str
    message: str


@dataclass
class DoctorReport:
    system: str
    platform: str
    issues: list[DoctorIssue] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not any(issue.severity == "error" for issue in self.issues)

    def to_dict(self) -> dict[str, Any]:
        return {
            "system": self.system,
            "platform": self.platform,
            "ok": self.ok,
            "issues": [
                {
                    "severity": issue.severity,
                    "area": issue.area,
                    "step_name": issue.step_name,
                    "message": issue.message,
                }
                for issue in self.issues
            ],
        }

    def to_text(self) -> str:
        lines = [
            f"Doctor report for system={self.system}, platform={self.platform}",
            f"status: {'ok' if self.ok else 'needs_config'}",
        ]
        if not self.issues:
            lines.append("No issues found.")
            return "\n".join(lines)

        for issue in self.issues:
            lines.append(
                f"[{issue.severity.upper()}] {issue.area}::{issue.step_name} - {issue.message}"
            )
        return "\n".join(lines)


def build_doctor_report(
    config_dir: str | Path,
    *,
    system: str,
    platform: str,
) -> DoctorReport:
    config_root = Path(config_dir)
    system_config = load_json_with_local_override(config_root / "systems" / f"{system}.json")
    platform_config = load_json_with_local_override(config_root / "platforms" / f"{platform}.json")
    report = DoctorReport(system=system, platform=platform)

    inspect_system_config(system_config, report)
    inspect_platform_config(platform_config, report)
    return report


def inspect_system_config(system_config: dict[str, Any], report: DoctorReport) -> None:
    workflow_steps = system_config.get("workflow", {}).get("steps", [])
    if not workflow_steps:
        report.issues.append(
            DoctorIssue("error", "system", "workflow", "No workflow steps configured.")
        )
        return

    for step in workflow_steps:
        inspect_step(step, report, area="system")
        inspect_error_detection(
            step.get("invalid_error_detection", {}),
            report,
            area="system",
            step_name=step.get("name", "unnamed"),
            required=False,
        )


def inspect_platform_config(platform_config: dict[str, Any], report: DoctorReport) -> None:
    publish_config = platform_config.get("publish", {})
    steps = publish_config.get("steps", [])
    if not steps:
        report.issues.append(
            DoctorIssue("error", "platform", "publish", "No publish steps configured.")
        )
    for step in steps:
        inspect_step(step, report, area="platform")
        if step.get("action") == "assert_no_errors":
            inspect_error_detection(
                step.get("error_detection", {}),
                report,
                area="platform",
                step_name=step.get("name", "unnamed"),
                required=False,
            )

    submit_selector = publish_config.get("submit_selector", {})
    if publish_config.get("auto_submit") and not selector_is_configured(submit_selector):
        report.issues.append(
            DoctorIssue(
                "error",
                "platform",
                "submit_selector",
                "auto_submit is enabled but submit_selector is empty.",
            )
        )

    inspect_error_detection(
        publish_config.get("pre_submit_error_detection", {}),
        report,
        area="platform",
        step_name="pre_submit_error_detection",
        required=False,
    )
    inspect_error_detection(
        publish_config.get("submit_error_detection", {}),
        report,
        area="platform",
        step_name="submit_error_detection",
        required=False,
    )

    for extractor in publish_config.get("success_extractors", []):
        inspect_step(extractor, report, area="platform")


def inspect_step(step: dict[str, Any], report: DoctorReport, *, area: str) -> None:
    action = str(step.get("action", "")).strip()
    step_name = step.get("name", "unnamed")
    required = bool(step.get("required", False))

    if action in SELECTOR_ACTIONS:
        selector = step.get("selector", {})
        if not selector_is_configured(selector):
            severity = "error" if required else "warning"
            report.issues.append(
                DoctorIssue(
                    severity,
                    area,
                    step_name,
                    f"selector is empty for action '{action}'",
                )
            )

    for selector_key in SPECIAL_SELECTOR_KEYS.get(action, []):
        selector = step.get(selector_key, {})
        if not selector_is_configured(selector):
            severity = "error" if required and selector_key != "fallback_selector" else "warning"
            report.issues.append(
                DoctorIssue(
                    severity,
                    area,
                    step_name,
                    f"{selector_key} is empty for action '{action}'",
                )
            )


def inspect_error_detection(
    detection_config: dict[str, Any],
    report: DoctorReport,
    *,
    area: str,
    step_name: str,
    required: bool,
) -> None:
    if not detection_config:
        return

    keywords = detection_config.get("keywords", [])
    if detection_config.get("enabled", True) and not keywords:
        report.issues.append(
            DoctorIssue(
                "warning",
                area,
                step_name,
                "error detection is enabled but keywords are empty.",
            )
        )

    for selector_key in ERROR_DETECTION_KEYS:
        selector = detection_config.get(selector_key, {})
        if not selector_is_configured(selector):
            severity = "error" if required else "warning"
            report.issues.append(
                DoctorIssue(
                    severity,
                    area,
                    step_name,
                    f"{selector_key} is empty in error detection config",
                )
            )


def selector_is_configured(selector: dict[str, Any] | None) -> bool:
    if not selector:
        return False
    return bool(str(selector.get("value", "")).strip())


def report_to_json(report: DoctorReport) -> str:
    return json.dumps(report.to_dict(), ensure_ascii=False, indent=2)


def write_local_selector_templates(
    config_dir: str | Path,
    *,
    system: str,
    platform: str,
) -> dict[str, str]:
    config_root = Path(config_dir)
    system_base_path = config_root / "systems" / f"{system}.json"
    platform_base_path = config_root / "platforms" / f"{platform}.json"

    system_template = build_selector_template(
        load_json_with_local_override(system_base_path),
        area="system",
    )
    platform_template = build_selector_template(
        load_json_with_local_override(platform_base_path),
        area="platform",
    )

    system_target = system_base_path.with_suffix(f".local{system_base_path.suffix}")
    platform_target = platform_base_path.with_suffix(f".local{platform_base_path.suffix}")

    write_json_if_missing(system_target, system_template)
    write_json_if_missing(platform_target, platform_template)

    return {
        "system_local": str(system_target),
        "platform_local": str(platform_target),
    }


def build_selector_template(config: dict[str, Any], *, area: str) -> dict[str, Any]:
    if area == "system":
        return _extract_system_template(config)
    return _extract_platform_template(config)


def _extract_system_template(config: dict[str, Any]) -> dict[str, Any]:
    result = {
        "workflow": {
            "steps": [],
        },
    }
    for step in config.get("workflow", {}).get("steps", []):
        step_template = _extract_step_template(step)
        if step.get("action") == "match_candidates":
            invalid_detection = step.get("invalid_error_detection", {})
            if invalid_detection:
                error_detection_template = _extract_error_detection_template(invalid_detection)
                if error_detection_template:
                    step_template["invalid_error_detection"] = error_detection_template
        if step_template:
            result["workflow"]["steps"].append(step_template)
    return _trim_empty(result)


def _extract_platform_template(config: dict[str, Any]) -> dict[str, Any]:
    publish = config.get("publish", {})
    result = {
        "publish": {
            "submit_selector": _extract_selector_template(publish.get("submit_selector", {})),
            "pre_submit_error_detection": _extract_error_detection_template(
                publish.get("pre_submit_error_detection", {})
            ),
            "submit_error_detection": _extract_error_detection_template(
                publish.get("submit_error_detection", {})
            ),
            "success_extractors": [],
            "steps": [],
        }
    }

    for extractor in publish.get("success_extractors", []):
        extracted = _extract_step_template(extractor)
        if extracted:
            result["publish"]["success_extractors"].append(extracted)

    for step in publish.get("steps", []):
        step_template = _extract_step_template(step)
        if step.get("action") == "assert_no_errors":
            error_detection = step.get("error_detection", {})
            if error_detection:
                if not step_template:
                    step_template = {"name": step.get("name", "")}
                error_detection_template = _extract_error_detection_template(error_detection)
                if error_detection_template:
                    step_template["error_detection"] = error_detection_template
        if step_template:
            result["publish"]["steps"].append(step_template)

    return _trim_empty(result)


def _extract_step_template(step: dict[str, Any]) -> dict[str, Any]:
    action = str(step.get("action", "")).strip()
    template: dict[str, Any] = {"name": step.get("name", "")}
    added = False

    if action in SELECTOR_ACTIONS:
        selector = _extract_selector_template(step.get("selector", {}))
        if selector:
            template["selector"] = selector
            added = True

    for selector_key in SPECIAL_SELECTOR_KEYS.get(action, []):
        selector = _extract_selector_template(step.get(selector_key, {}))
        if selector:
            template[selector_key] = selector
            added = True

    return template if added else {}


def _extract_error_detection_template(config: dict[str, Any]) -> dict[str, Any]:
    template: dict[str, Any] = {}
    for selector_key in ERROR_DETECTION_KEYS:
        selector = _extract_selector_template(config.get(selector_key, {}))
        if selector:
            template[selector_key] = selector
    return template


def _extract_selector_template(selector: dict[str, Any]) -> dict[str, Any]:
    if not selector_is_configured(selector):
        return {
            "by": str(selector.get("by", "css")).strip() or "css",
            "value": "",
        }
    return {}


def _trim_empty(value: Any) -> Any:
    if isinstance(value, dict):
        cleaned: dict[str, Any] = {}
        for key, child in value.items():
            trimmed = _trim_empty(child)
            if key == "value":
                cleaned[key] = trimmed
                continue
            if trimmed not in ({}, [], None, ""):
                cleaned[key] = trimmed
        return cleaned
    if isinstance(value, list):
        cleaned_list = []
        for item in value:
            trimmed = _trim_empty(item)
            if trimmed not in ({}, [], None, ""):
                cleaned_list.append(trimmed)
        return cleaned_list
    return value


def write_json_if_missing(path: str | Path, payload: dict[str, Any]) -> None:
    target = Path(path)
    if target.exists():
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("w", encoding="utf-8") as file:
        json.dump(payload, file, ensure_ascii=False, indent=2)
        file.write("\n")
