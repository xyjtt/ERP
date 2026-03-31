from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any


DEFAULT_SHEET_NAME = "停产下架通知-链接维度"
WATCH_FILE_SUFFIXES = {".csv", ".xlsx", ".xls"}


@dataclass(frozen=True)
class OfflineTask:
    source_file: str
    source_sheet: str
    source_row_number: int
    store_name: str
    platform: str
    product_id: str
    online_sku: str
    handling: str
    replacement_sku: str
    change_image: str
    platform_store_item_code: str
    raw: dict[str, str]

    @property
    def dedupe_key(self) -> tuple[str, str, str]:
        return (
            normalize_cell(self.store_name),
            normalize_cell(self.product_id),
            normalize_cell(self.online_sku),
        )

    def to_context(self) -> dict[str, str]:
        context = dict(self.raw)
        context.update(
            {
                "source_file": self.source_file,
                "source_sheet": self.source_sheet,
                "source_row_number": str(self.source_row_number),
                "store_name": self.store_name,
                "platform": self.platform,
                "product_id": self.product_id,
                "online_sku": self.online_sku,
                "handling": self.handling,
                "replacement_sku": self.replacement_sku,
                "change_image": self.change_image,
                "platform_store_item_code": self.platform_store_item_code,
            }
        )
        return context


@dataclass(frozen=True)
class FileIdentity:
    file_name: str
    modified_at: str

    @classmethod
    def from_path(cls, path: str | Path) -> "FileIdentity":
        file_path = Path(path)
        stat = file_path.stat()
        modified_at = datetime.fromtimestamp(stat.st_mtime).isoformat(timespec="seconds")
        return cls(file_name=file_path.name, modified_at=modified_at)

    def as_dict(self) -> dict[str, str]:
        return {
            "file_name": self.file_name,
            "modified_at": self.modified_at,
        }


class ProcessedFileRegistry:
    def __init__(self, registry_path: str | Path) -> None:
        self.registry_path = Path(registry_path)
        self.registry_path.parent.mkdir(parents=True, exist_ok=True)
        self._processed_keys = self._load_processed_keys()

    def contains(self, path: str | Path) -> bool:
        identity = FileIdentity.from_path(path)
        return self._build_key(identity) in self._processed_keys

    def record(
        self,
        path: str | Path,
        *,
        status: str,
        moved_to: str = "",
        note: str = "",
    ) -> dict[str, str]:
        identity = FileIdentity.from_path(path)
        return self.record_identity(identity, status=status, moved_to=moved_to, note=note)

    def record_identity(
        self,
        identity: FileIdentity,
        *,
        status: str,
        moved_to: str = "",
        note: str = "",
    ) -> dict[str, str]:
        payload = {
            **identity.as_dict(),
            "status": status,
            "moved_to": moved_to,
            "note": note,
            "recorded_at": datetime.now().isoformat(timespec="seconds"),
        }
        with self.registry_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False) + "\n")
        self._processed_keys.add(self._build_key(identity))
        return payload

    def _load_processed_keys(self) -> set[str]:
        if not self.registry_path.exists():
            return set()
        processed_keys: set[str] = set()
        for line in self.registry_path.read_text(encoding="utf-8").splitlines():
            text = line.strip()
            if not text:
                continue
            try:
                payload = json.loads(text)
            except json.JSONDecodeError:
                continue
            identity = FileIdentity(
                file_name=str(payload.get("file_name", "")).strip(),
                modified_at=str(payload.get("modified_at", "")).strip(),
            )
            if identity.file_name and identity.modified_at:
                processed_keys.add(self._build_key(identity))
        return processed_keys

    def _build_key(self, identity: FileIdentity) -> str:
        return f"{identity.file_name}|{identity.modified_at}"


def load_offline_tasks(
    template_path: str | Path,
    input_config: dict[str, Any],
) -> list[OfflineTask]:
    try:
        import pandas as pd
    except ModuleNotFoundError as exc:
        raise ModuleNotFoundError(
            "Missing dependency 'pandas'. Install requirements before loading offline templates."
        ) from exc

    file_path = Path(template_path)
    suffix = file_path.suffix.lower()
    sheet_name = str(input_config.get("sheet_name", DEFAULT_SHEET_NAME)).strip()

    if suffix == ".csv":
        dataframe = pd.read_csv(file_path, dtype=str).fillna("")
        source_sheet = "CSV"
    elif suffix in {".xlsx", ".xls"}:
        dataframe = pd.read_excel(file_path, sheet_name=sheet_name, dtype=str).fillna("")
        source_sheet = sheet_name
    else:
        raise ValueError(f"Unsupported offline template format: {suffix}")

    column_map = dict(input_config.get("columns", {}))
    required_aliases = [
        "store_name",
        "platform",
        "product_id",
        "online_sku",
        "handling",
    ]
    missing_columns = [
        column_map[alias]
        for alias in required_aliases
        if str(column_map.get(alias, "")).strip() not in {str(column).strip() for column in dataframe.columns}
    ]
    if missing_columns:
        raise ValueError(f"Missing mandatory offline columns: {', '.join(sorted(missing_columns))}")

    tasks: list[OfflineTask] = []
    for index, row in enumerate(dataframe.to_dict(orient="records"), start=2):
        normalized = {str(key).strip(): normalize_cell(value) for key, value in row.items()}
        if not any(normalized.values()):
            continue
        tasks.append(
            OfflineTask(
                source_file=str(file_path),
                source_sheet=source_sheet,
                source_row_number=index,
                store_name=normalized.get(column_map["store_name"], ""),
                platform=normalized.get(column_map["platform"], ""),
                product_id=normalized.get(column_map["product_id"], ""),
                online_sku=normalized.get(column_map["online_sku"], ""),
                handling=normalized.get(column_map["handling"], ""),
                replacement_sku=normalized.get(column_map.get("replacement_sku", ""), ""),
                change_image=normalized.get(column_map.get("change_image", ""), ""),
                platform_store_item_code=normalized.get(column_map.get("platform_store_item_code", ""), ""),
                raw=normalized,
            )
        )
    return tasks


def filter_offline_tasks(
    tasks: list[OfflineTask],
    filters: dict[str, Any],
) -> tuple[list[OfflineTask], list[OfflineTask]]:
    target_store_name = normalize_cell(filters.get("store_name", ""))
    store_aliases = parse_store_aliases(filters)
    target_platform = normalize_cell(filters.get("platform", ""))
    target_handling = normalize_cell(filters.get("handling", ""))

    selected: list[OfflineTask] = []
    filtered_out: list[OfflineTask] = []
    for task in tasks:
        if target_store_name and not store_name_matches(
            task.store_name,
            target_store_name,
            aliases=store_aliases,
        ):
            filtered_out.append(task)
            continue
        if target_platform and normalize_cell(task.platform) != target_platform:
            filtered_out.append(task)
            continue
        if target_handling and normalize_cell(task.handling) != target_handling:
            filtered_out.append(task)
            continue
        selected.append(task)
    return selected, filtered_out


def dedupe_offline_tasks(tasks: list[OfflineTask]) -> tuple[list[OfflineTask], list[OfflineTask]]:
    deduped: list[OfflineTask] = []
    duplicates: list[OfflineTask] = []
    seen_keys: set[tuple[str, str, str]] = set()
    for task in tasks:
        if task.dedupe_key in seen_keys:
            duplicates.append(task)
            continue
        seen_keys.add(task.dedupe_key)
        deduped.append(task)
    return deduped, duplicates


def group_tasks_by_store(tasks: list[OfflineTask]) -> dict[str, list[OfflineTask]]:
    groups: dict[str, list[OfflineTask]] = {}
    for task in tasks:
        groups.setdefault(task.store_name or "UNKNOWN_STORE", []).append(task)
    return groups


def build_preview_payload(
    *,
    source_files: list[str],
    loaded_tasks: list[OfflineTask],
    selected_tasks: list[OfflineTask],
    filtered_out_tasks: list[OfflineTask],
    duplicate_tasks: list[OfflineTask],
) -> dict[str, Any]:
    grouped = group_tasks_by_store(selected_tasks)
    return {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "source_files": source_files,
        "loaded_count": len(loaded_tasks),
        "selected_count": len(selected_tasks),
        "filtered_out_count": len(filtered_out_tasks),
        "duplicate_count": len(duplicate_tasks),
        "stores": {
            store_name: len(store_tasks)
            for store_name, store_tasks in sorted(grouped.items())
        },
        "tasks": [
            {
                "store_name": task.store_name,
                "product_id": task.product_id,
                "online_sku": task.online_sku,
                "handling": task.handling,
                "source_file": task.source_file,
                "source_row_number": task.source_row_number,
            }
            for task in selected_tasks
        ],
        "duplicates": [
            {
                "store_name": task.store_name,
                "product_id": task.product_id,
                "online_sku": task.online_sku,
                "source_file": task.source_file,
                "source_row_number": task.source_row_number,
            }
            for task in duplicate_tasks
        ],
    }


def discover_scan_files(scan_dir: str | Path) -> list[Path]:
    root = Path(scan_dir)
    if not root.exists():
        return []
    files = [
        path
        for path in root.iterdir()
        if path.is_file() and path.suffix.lower() in WATCH_FILE_SUFFIXES
    ]
    return sorted(files, key=lambda path: (path.stat().st_mtime, path.name))


def normalize_cell(value: Any) -> str:
    return str(value or "").strip()


def parse_store_aliases(filters: dict[str, Any]) -> list[str]:
    raw_aliases = filters.get("store_name_aliases", filters.get("store_aliases", []))
    if isinstance(raw_aliases, str):
        return [item.strip() for item in raw_aliases.split("|") if item.strip()]
    if isinstance(raw_aliases, list):
        return [str(item).strip() for item in raw_aliases if str(item).strip()]
    return []


def store_name_matches(task_store_name: str, target_store_name: str, *, aliases: list[str] | None = None) -> bool:
    task_store = normalize_cell(task_store_name)
    target_store = normalize_cell(target_store_name)
    if not target_store:
        return True
    if task_store == target_store:
        return True

    normalized_task_store = normalize_store_name(task_store)
    candidate_names = {normalize_store_name(target_store)}
    for alias in aliases or []:
        normalized_alias = normalize_store_name(alias)
        if normalized_alias:
            candidate_names.add(normalized_alias)

    if normalized_task_store in candidate_names:
        return True

    if not normalized_task_store:
        return False
    for candidate in candidate_names:
        if not candidate:
            continue
        # Accept common full-name vs short-name forms, e.g.
        # "阿里巴巴-常州速班达家居有限公司" <-> "速班达家居".
        if len(normalized_task_store) >= 4 and len(candidate) >= 4:
            if normalized_task_store in candidate or candidate in normalized_task_store:
                return True
    return False


def normalize_store_name(value: str) -> str:
    normalized = normalize_cell(value).lower()
    if not normalized:
        return ""
    for token in ("阿里巴巴-", "阿里巴巴", "1688-", "1688", "alibaba-", "alibaba"):
        if normalized.startswith(token):
            normalized = normalized[len(token):]
            break

    for suffix in (
        "有限责任公司",
        "有限公司",
        "旗舰店",
        "专卖店",
        "专营店",
        "企业店",
        "店铺",
        "官方店",
        "官方",
    ):
        if normalized.endswith(suffix):
            normalized = normalized[: -len(suffix)]
            break

    for char in (" ", "-", "_", ":", "：", "/", "\\", "(", ")", "（", "）", "[", "]", "【", "】"):
        normalized = normalized.replace(char, "")
    return normalized.strip()
