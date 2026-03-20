from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any


EMOJI_PATTERN = re.compile(
    "["
    "\U0001F300-\U0001F5FF"
    "\U0001F600-\U0001F64F"
    "\U0001F680-\U0001F6FF"
    "\U0001F700-\U0001F77F"
    "\U0001F780-\U0001F7FF"
    "\U0001F800-\U0001F8FF"
    "\U0001F900-\U0001F9FF"
    "\U0001FA00-\U0001FA6F"
    "\U0001FA70-\U0001FAFF"
    "\u2600-\u26FF"
    "\u2700-\u27BF"
    "]+",
    flags=re.UNICODE,
)

MANDATORY_FIELDS = {
    "platform",
    "shop_name",
    "old_online_sku_code",
    "new_online_sku_code",
}

DISCONTINUED_SOURCE_REQUIRED_COLUMNS = {
    "统计日期新",
    "商品编码",
    "处理说明",
    "下架备注",
}


@dataclass
class CodeChangeTask:
    task_id: str
    source_type: str
    source_record_id: str
    platform: str
    shop_name: str
    old_online_sku_code: str
    new_online_sku_code: str
    operator_name: str
    remark: str
    raw: dict[str, str]
    sanitization_notes: list[str] = field(default_factory=list)

    @classmethod
    def from_row(cls, row: dict[str, Any], *, source_type: str, row_index: int) -> "CodeChangeTask":
        normalized = normalize_row(row)
        sanitized, notes = sanitize_row(normalized)
        task_id = sanitized.get("task_id", "").strip() or f"{source_type.upper()}-{row_index:05d}"
        return cls(
            task_id=task_id,
            source_type=sanitized.get("source_type", "").strip() or source_type,
            source_record_id=sanitized.get("source_record_id", "").strip() or f"{source_type}-{row_index}",
            platform=sanitized.get("platform", "").strip(),
            shop_name=sanitized.get("shop_name", "").strip(),
            old_online_sku_code=sanitized.get("old_online_sku_code", "").strip(),
            new_online_sku_code=sanitized.get("new_online_sku_code", "").strip(),
            operator_name=sanitized.get("operator_name", "").strip(),
            remark=sanitized.get("remark", "").strip(),
            raw=sanitized,
            sanitization_notes=notes,
        )


@dataclass
class ValidationReport:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


def load_tasks(
    *,
    source_type: str,
    file_path: str = "",
    api_url: str = "",
    api_method: str = "GET",
    api_headers: str = "",
    api_body: str = "",
    api_records_path: str = "",
    manual_payload: str = "",
    default_platform: str = "",
    default_shop_name: str = "",
    default_operator_name: str = "",
    discontinued_target_code: str = "txcj",
    expected_stat_date: str = "",
    discontinued_action: str = "下架",
    discontinued_remark: str = "京喜需下架",
) -> list[CodeChangeTask]:
    source_key = source_type.strip().lower()
    if source_key == "excel":
        return load_tasks_from_file(
            file_path,
            default_platform=default_platform,
            default_shop_name=default_shop_name,
            default_operator_name=default_operator_name,
            discontinued_target_code=discontinued_target_code,
            expected_stat_date=expected_stat_date,
            discontinued_action=discontinued_action,
            discontinued_remark=discontinued_remark,
        )
    if source_key == "api":
        return load_tasks_from_api(
            api_url=api_url,
            method=api_method,
            headers_text=api_headers,
            body_text=api_body,
            records_path=api_records_path,
        )
    if source_key == "manual":
        return load_tasks_from_manual_input(manual_payload)
    raise ValueError(f"Unsupported source_type: {source_type}")


def load_tasks_from_file(
    path: str | Path,
    *,
    default_platform: str = "",
    default_shop_name: str = "",
    default_operator_name: str = "",
    discontinued_target_code: str = "txcj",
    expected_stat_date: str = "",
    discontinued_action: str = "下架",
    discontinued_remark: str = "京喜需下架",
) -> list[CodeChangeTask]:
    try:
        import pandas as pd
    except ModuleNotFoundError as exc:
        raise ModuleNotFoundError("Missing dependency 'pandas'.") from exc

    file_path = Path(path)
    if not file_path.exists():
        raise FileNotFoundError(f"Input file not found: {file_path}")

    suffix = file_path.suffix.lower()
    if suffix == ".csv":
        dataframe = pd.read_csv(file_path, dtype=str).fillna("")
    elif suffix in {".xlsx", ".xls"}:
        dataframe = pd.read_excel(file_path, dtype=str).fillna("")
    else:
        raise ValueError(f"Unsupported input format: {suffix}")

    records = dataframe.to_dict(orient="records")
    if is_discontinued_notice_source(records):
        transformed_rows = transform_discontinued_notice_rows(
            records,
            default_platform=default_platform,
            default_shop_name=default_shop_name,
            default_operator_name=default_operator_name,
            discontinued_target_code=discontinued_target_code,
            expected_stat_date=expected_stat_date,
            discontinued_action=discontinued_action,
            discontinued_remark=discontinued_remark,
        )
        return build_tasks(transformed_rows, source_type="excel")

    return build_tasks(records, source_type="excel")


def load_tasks_from_api(
    *,
    api_url: str,
    method: str = "GET",
    headers_text: str = "",
    body_text: str = "",
    records_path: str = "",
) -> list[CodeChangeTask]:
    if not api_url.strip():
        raise ValueError("api_url is required for API source")

    headers = json.loads(headers_text) if headers_text.strip() else {}
    body_bytes = body_text.encode("utf-8") if body_text else None
    request = urllib.request.Request(
        api_url.strip(),
        data=body_bytes,
        headers=headers,
        method=method.strip().upper() or "GET",
    )

    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Failed to load API data: {exc}") from exc

    records = extract_records(payload, records_path)
    if not isinstance(records, list):
        raise ValueError("API payload must resolve to a list of task records")
    return build_tasks(records, source_type="api")


def load_tasks_from_manual_input(payload_text: str) -> list[CodeChangeTask]:
    if not payload_text.strip():
        raise ValueError("manual_payload is required for manual source")
    payload = json.loads(payload_text)
    if isinstance(payload, dict):
        payload = [payload]
    if not isinstance(payload, list):
        raise ValueError("manual_payload must be a JSON object or JSON array")
    return build_tasks(payload, source_type="manual")


def build_tasks(rows: list[dict[str, Any]], *, source_type: str) -> list[CodeChangeTask]:
    tasks: list[CodeChangeTask] = []
    for index, row in enumerate(rows, start=1):
        if not isinstance(row, dict):
            raise ValueError(f"Row {index} is not a JSON object")
        if not any(str(value).strip() for value in row.values()):
            continue
        tasks.append(CodeChangeTask.from_row(row, source_type=source_type, row_index=index))
    return tasks


def is_discontinued_notice_source(rows: list[dict[str, Any]]) -> bool:
    if not rows:
        return False
    header_names = {str(key).strip() for key in rows[0].keys()}
    return DISCONTINUED_SOURCE_REQUIRED_COLUMNS.issubset(header_names)


def transform_discontinued_notice_rows(
    rows: list[dict[str, Any]],
    *,
    default_platform: str,
    default_shop_name: str,
    default_operator_name: str,
    discontinued_target_code: str,
    expected_stat_date: str,
    discontinued_action: str,
    discontinued_remark: str,
) -> list[dict[str, str]]:
    expected_date = normalize_expected_date(expected_stat_date)
    target_code = discontinued_target_code.strip() or "txcj"
    action = discontinued_action.strip() or "下架"
    remark = discontinued_remark.strip() or "京喜需下架"
    transformed: list[dict[str, str]] = []

    for index, raw_row in enumerate(rows, start=1):
        row = normalize_row(raw_row)
        if not should_include_discontinued_row(
            row,
            expected_date=expected_date,
            action=action,
            remark=remark,
        ):
            continue

        old_code = row.get("商品编码", "").strip()
        if not old_code:
            continue

        transformed.append(
            {
                "task_id": f"EXCEL-{index:05d}",
                "source_type": "excel",
                "source_record_id": f"excel-{index}",
                "platform": default_platform.strip(),
                "shop_name": default_shop_name.strip(),
                "old_online_sku_code": old_code,
                "new_online_sku_code": target_code,
                "operator_name": default_operator_name.strip(),
                "remark": row.get("下架备注", "").strip() or remark,
                "统计日期新": row.get("统计日期新", "").strip(),
                "处理说明": row.get("处理说明", "").strip(),
                "原始商品编码": old_code,
            }
        )

    return transformed


def should_include_discontinued_row(
    row: dict[str, str],
    *,
    expected_date: str,
    action: str,
    remark: str,
) -> bool:
    return (
        row.get("统计日期新", "").strip() == expected_date
        and row.get("处理说明", "").strip() == action
        and row.get("下架备注", "").strip() == remark
    )


def normalize_expected_date(value: str) -> str:
    raw_value = value.strip()
    if not raw_value:
        return date.today().isoformat()

    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y.%m.%d"):
        try:
            return datetime.strptime(raw_value, fmt).date().isoformat()
        except ValueError:
            continue
    return raw_value


def validate_tasks(tasks: list[CodeChangeTask]) -> ValidationReport:
    report = ValidationReport()

    for index, task in enumerate(tasks, start=1):
        prefix = f"Row {index} ({task.task_id})"
        for field_name in sorted(MANDATORY_FIELDS):
            if not getattr(task, field_name).strip():
                report.errors.append(f"{prefix}: missing required field '{field_name}'")

        if task.old_online_sku_code and task.new_online_sku_code:
            if task.old_online_sku_code == task.new_online_sku_code:
                report.errors.append(f"{prefix}: old_online_sku_code equals new_online_sku_code")

        if not task.operator_name:
            report.warnings.append(f"{prefix}: operator_name is empty")

    return report


def collect_sanitization_warnings(tasks: list[CodeChangeTask]) -> list[str]:
    warnings: list[str] = []
    for index, task in enumerate(tasks, start=1):
        for note in task.sanitization_notes:
            warnings.append(f"Row {index} ({task.task_id}): {note}")
    return warnings


def extract_records(payload: Any, records_path: str) -> Any:
    if not records_path.strip():
        if isinstance(payload, list):
            return payload
        if isinstance(payload, dict):
            for candidate in ("records", "items", "data", "rows", "result"):
                value = payload.get(candidate)
                if isinstance(value, list):
                    return value
        return payload

    current = payload
    for part in records_path.split("."):
        key = part.strip()
        if not key:
            continue
        if not isinstance(current, dict):
            raise ValueError(f"Cannot resolve records_path at '{key}'")
        current = current[key]
    return current


def normalize_row(row: dict[str, Any]) -> dict[str, str]:
    normalized: dict[str, str] = {}
    for key, value in row.items():
        normalized[str(key).strip()] = "" if value is None else str(value).strip()
    return normalized


def sanitize_row(row: dict[str, str]) -> tuple[dict[str, str], list[str]]:
    sanitized = dict(row)
    notes: list[str] = []

    for field_name in ("old_online_sku_code", "new_online_sku_code", "remark"):
        raw_value = sanitized.get(field_name, "")
        cleaned = strip_emoji(raw_value)
        cleaned = " ".join(cleaned.split())
        if cleaned != raw_value:
            sanitized[field_name] = cleaned
            notes.append(f"removed emoji from field '{field_name}'")

    return sanitized, notes


def strip_emoji(value: str) -> str:
    return EMOJI_PATTERN.sub("", value)
