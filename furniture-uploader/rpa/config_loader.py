from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def load_json_with_local_override(path: str | Path) -> dict[str, Any]:
    base_path = Path(path)
    payload = load_json_file(base_path)

    local_path = base_path.with_suffix(f".local{base_path.suffix}")
    if local_path.exists():
        payload = deep_merge(payload, load_json_file(local_path))

    return payload


def load_json_file(path: str | Path) -> dict[str, Any]:
    config_path = Path(path)
    with config_path.open("r", encoding="utf-8") as file:
        return json.load(file)


def deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    result = dict(base)
    for key, value in override.items():
        current = result.get(key)
        if isinstance(current, dict) and isinstance(value, dict):
            result[key] = deep_merge(current, value)
        elif isinstance(current, list) and isinstance(value, list):
            result[key] = merge_lists(current, value)
        else:
            result[key] = value
    return result


def merge_lists(base: list[Any], override: list[Any]) -> list[Any]:
    if _is_named_dict_list(base) and _is_named_dict_list(override):
        return _merge_named_dict_lists(base, override)
    return list(override)


def _is_named_dict_list(values: list[Any]) -> bool:
    return all(
        isinstance(item, dict) and str(item.get("name", "")).strip()
        for item in values
    )


def _merge_named_dict_lists(base: list[Any], override: list[Any]) -> list[Any]:
    override_by_name = {
        str(item["name"]).strip(): item
        for item in override
    }
    merged: list[Any] = []
    seen_names: set[str] = set()

    for item in base:
        name = str(item["name"]).strip()
        seen_names.add(name)
        override_item = override_by_name.get(name)
        if isinstance(override_item, dict):
            merged.append(deep_merge(item, override_item))
        else:
            merged.append(item)

    for item in override:
        name = str(item["name"]).strip()
        if name not in seen_names:
            merged.append(item)

    return merged
