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
    with Path(path).open("r", encoding="utf-8") as file:
        return json.load(file)


def deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    result = dict(base)
    for key, value in override.items():
        current = result.get(key)
        if isinstance(current, dict) and isinstance(value, dict):
            result[key] = deep_merge(current, value)
        elif isinstance(current, list) and isinstance(value, list):
            result[key] = list(value)
        else:
            result[key] = value
    return result
