from __future__ import annotations

import json
import re
from typing import Any


def parse_properties_value(raw_value: str) -> dict[str, str]:
    text = str(raw_value or "").strip()
    if not text:
        return {}

    parts = [item.strip() for item in text.split(";") if item.strip()]
    payload: dict[str, str] = {}
    if parts:
        payload["color"] = parts[0]
        payload["颜色"] = parts[0]
    if len(parts) > 1:
        payload["size"] = parts[1]
        payload["尺寸"] = parts[1]
    return payload


def infer_platform_category_key(category: str, name: str = "") -> str:
    haystack = f"{category} {name}".strip()
    if any(keyword in haystack for keyword in ("床头柜",)):
        return "bedside_table"
    if any(keyword in haystack for keyword in ("鞋柜",)):
        return "shoe_cabinet"
    if any(keyword in haystack for keyword in ("书柜",)):
        return "bookshelf"
    if any(keyword in haystack for keyword in ("电脑桌", "办公桌", "升降桌")):
        return "computer_desk"
    if any(keyword in haystack for keyword in ("床", "床头柜", "卧室")):
        return "bedroom"
    if any(keyword in haystack for keyword in ("边几", "角几", "置物架", "收纳置物架")):
        return "corner_table"
    return "living_room"


def normalize_material(board_type: str, brand: str = "") -> str:
    board_text = str(board_type or "").strip()
    if board_text:
        if "板材" in board_text:
            return "人造板"
        return board_text
    brand_text = str(brand or "").strip()
    return brand_text


def build_platform_attributes(row: dict[str, Any]) -> dict[str, Any]:
    properties = parse_properties_value(str(row.get("properties_value", "")))
    attributes: dict[str, Any] = {
        "brand": str(row.get("brand") or "").strip(),
        "material": normalize_material(str(row.get("boardType") or ""), str(row.get("brand") or "")),
        "材质": normalize_material(str(row.get("boardType") or ""), str(row.get("brand") or "")),
        "category": str(row.get("category") or "").strip(),
        "商品类目": str(row.get("category") or "").strip(),
        "board_type": str(row.get("boardType") or "").strip(),
    }
    attributes.update(properties)

    if row.get("l") and row.get("w") and row.get("h"):
        full_size = f"{row.get('l')}/{row.get('w')}/{row.get('h')}"
        attributes.setdefault("size", full_size)
        attributes.setdefault("尺寸", full_size)
    return {key: value for key, value in attributes.items() if str(value).strip()}


def build_variant_title(row: dict[str, Any]) -> str:
    name = str(row.get("name") or "").strip()
    category = str(row.get("category") or "").strip()
    color = parse_properties_value(str(row.get("properties_value", ""))).get("color", "")
    tokens = [token for token in (name, color, category.split("-")[-1].strip() if "-" in category else category) if token]
    # Keep the generated title under the 1688 common title limit.
    return truncate_text(" ".join(dict.fromkeys(tokens)), 60)


def build_style_name(row: dict[str, Any]) -> str:
    name = str(row.get("name") or "").strip()
    properties = parse_properties_value(str(row.get("properties_value", "")))
    color = properties.get("color", "")
    size = properties.get("size", "")
    tokens = [token for token in (name, color, size) if token]
    return truncate_text(" ".join(dict.fromkeys(tokens)), 40)


def compute_price_value(row: dict[str, Any]) -> str:
    for field_name in ("cost_price", "purchase_price", "sale_price"):
        raw_value = row.get(field_name)
        if raw_value in (None, ""):
            continue
        try:
            cost_value = float(raw_value)
        except (TypeError, ValueError):
            continue
        if cost_value <= 0:
            continue
        final_price = round(cost_value / 0.75, 2)
        return f"{final_price:.2f}".rstrip("0").rstrip(".")
    return "0"


def build_description(row: dict[str, Any]) -> str:
    category = str(row.get("category") or "").strip()
    properties = parse_properties_value(str(row.get("properties_value", "")))
    color = properties.get("color", "")
    size = properties.get("size", "")
    text = f"{row.get('name', '')} {category} {color} {size}".strip()
    text = re.sub(r"\s+", " ", text)
    return truncate_text(text, 200)


def build_attribute_snapshot(row: dict[str, Any]) -> str:
    return json.dumps(build_platform_attributes(row), ensure_ascii=False)


def truncate_text(value: str, max_length: int) -> str:
    return str(value or "").strip()[:max_length]
