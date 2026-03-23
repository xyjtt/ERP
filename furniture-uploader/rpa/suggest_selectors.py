from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from config_loader import deep_merge


FIELD_RULES = {
    "title": {
        "keywords": ["标题", "商品标题", "主标题", "title"],
        "preferred_tags": {"input", "textarea"},
    },
    "price": {
        "keywords": ["价格", "单价", "售价", "供货价", "price"],
        "preferred_tags": {"input"},
    },
    "quantity": {
        "keywords": ["库存", "可售数量", "数量", "起订量", "quantity", "amount"],
        "preferred_tags": {"input"},
    },
    "category": {
        "keywords": ["类目", "商品类目", "category"],
        "preferred_tags": {"input", "select", "div", "button"},
    },
    "brand": {
        "keywords": ["品牌", "brand"],
        "preferred_tags": {"input", "select", "div"},
    },
    "material": {
        "keywords": ["材质", "material"],
        "preferred_tags": {"input", "select", "div"},
    },
    "main_image": {
        "keywords": ["主图", "主图图片", "商品主图", "image", "upload"],
        "preferred_tags": {"input", "button", "div"},
        "preferred_type": "file",
    },
    "detail_images": {
        "keywords": ["详情图", "详情图片", "描述图", "detail", "upload"],
        "preferred_tags": {"input", "button", "div"},
        "preferred_type": "file",
    },
    "description": {
        "keywords": ["描述", "详情描述", "商品描述", "详情", "description"],
        "preferred_tags": {"textarea", "div", "iframe"},
    },
    "ship_from_template": {
        "keywords": ["发货地址", "发货地", "发货", "ship"],
        "preferred_tags": {"select", "input", "div"},
    },
    "freight_template": {
        "keywords": ["运费模板", "运费", "物流模板", "freight"],
        "preferred_tags": {"select", "input", "div"},
    },
    "ship_time_template": {
        "keywords": ["发货时效", "发货时间", "时效", "ship time"],
        "preferred_tags": {"select", "input", "div"},
    },
    "submit_selector": {
        "keywords": ["发布", "提交", "立即发布", "确认发布", "submit"],
        "preferred_tags": {"button", "a", "div"},
    },
}


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Suggest selector candidates from selector probe JSON.")
    parser.add_argument("--probe-json", required=True, help="Probe JSON path exported by selector_probe.py")
    parser.add_argument(
        "--output",
        default="",
        help="Optional output JSON path. Defaults next to the probe JSON.",
    )
    parser.add_argument(
        "--platform-local-output",
        default="",
        help="Optional path for a generated 1688.local.json-style override snippet.",
    )
    parser.add_argument(
        "--replace-platform-local",
        action="store_true",
        help=(
            "When the platform local output file already exists, replace existing selector values "
            "with the new suggestions instead of only filling blank entries."
        ),
    )
    parser.add_argument(
        "--top",
        type=int,
        default=5,
        help="How many candidates to keep for each field.",
    )
    return parser


def main() -> None:
    args = build_argument_parser().parse_args()
    probe_path = Path(args.probe_json)
    payload = load_json_path(probe_path)
    suggestions = build_suggestions(payload, top=args.top)
    output_path = Path(args.output) if args.output else probe_path.with_suffix(".suggestions.json")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(suggestions, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Saved selector suggestions: {output_path}")

    if args.platform_local_output:
        platform_payload = build_platform_local_override(suggestions)
        platform_output_path = Path(args.platform_local_output)
        platform_output_path.parent.mkdir(parents=True, exist_ok=True)
        if platform_output_path.exists():
            existing_payload = load_json_path(platform_output_path)
            if args.replace_platform_local:
                platform_payload = deep_merge(existing_payload, platform_payload)
            else:
                platform_payload = merge_missing_values(existing_payload, platform_payload)
        platform_output_path.write_text(
            json.dumps(platform_payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        print(f"Saved platform local override snippet: {platform_output_path}")


def build_suggestions(payload: dict[str, Any], *, top: int) -> dict[str, Any]:
    elements = payload.get("elements", [])
    if not isinstance(elements, list):
        raise ValueError("Probe JSON does not contain a valid elements list.")

    output: dict[str, Any] = {
        "captured_at": payload.get("captured_at", ""),
        "current_url": payload.get("current_url", ""),
        "page_title": payload.get("page_title", ""),
        "fields": {},
    }

    for field_name, rule in FIELD_RULES.items():
        candidates = []
        for element in elements:
            if not isinstance(element, dict):
                continue
            score = score_element(element, rule)
            if score <= 0:
                continue
            candidates.append(
                {
                    "score": score,
                    "selector": {
                        "by": "css",
                        "value": str(element.get("selector_hint", "")).strip(),
                    },
                    "tag": str(element.get("tag", "")).strip(),
                    "type": str(element.get("type", "")).strip(),
                    "text": str(element.get("text", "")).strip(),
                    "label_text": str(element.get("label_text", "")).strip(),
                    "parent_text": str(element.get("parent_text", "")).strip(),
                    "placeholder": str(element.get("placeholder", "")).strip(),
                    "id": str(element.get("id", "")).strip(),
                    "name": str(element.get("name", "")).strip(),
                    "dom_path_hint": str(element.get("dom_path_hint", "")).strip(),
                    "classes": element.get("classes", []),
                }
            )

        candidates.sort(key=lambda item: item["score"], reverse=True)
        output["fields"][field_name] = candidates[:top]

    return output


def load_json_path(path: str | Path) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def build_platform_local_override(suggestions: dict[str, Any]) -> dict[str, Any]:
    fields = suggestions.get("fields", {})
    step_names = [
        "title",
        "price",
        "quantity",
        "category",
        "brand",
        "material",
        "main_image",
        "detail_images",
        "description",
        "ship_from_template",
        "freight_template",
        "ship_time_template",
    ]
    steps: list[dict[str, Any]] = []
    for step_name in step_names:
        selector = first_selector(fields.get(step_name, []))
        if selector:
            steps.append(
                {
                    "name": step_name,
                    "selector": selector,
                }
            )

    payload: dict[str, Any] = {
        "publish": {
            "steps": steps,
        }
    }
    submit_selector = first_selector(fields.get("submit_selector", []))
    if submit_selector:
        payload["publish"]["submit_selector"] = submit_selector
    return payload


def first_selector(candidates: Any) -> dict[str, str] | None:
    if not isinstance(candidates, list) or not candidates:
        return None
    selector = candidates[0].get("selector", {})
    if not isinstance(selector, dict):
        return None
    value = str(selector.get("value", "")).strip()
    if not value:
        return None
    return {
        "by": str(selector.get("by", "css")).strip() or "css",
        "value": value,
    }


def merge_missing_values(existing: Any, generated: Any) -> Any:
    if isinstance(existing, dict) and isinstance(generated, dict):
        merged = dict(existing)
        for key, generated_value in generated.items():
            if key in merged:
                merged[key] = merge_missing_values(merged[key], generated_value)
            else:
                merged[key] = generated_value
        return merged

    if isinstance(existing, list) and isinstance(generated, list):
        if is_named_dict_list(existing) and is_named_dict_list(generated):
            generated_by_name = {
                str(item["name"]).strip(): item
                for item in generated
            }
            merged_items: list[Any] = []
            seen_names: set[str] = set()

            for item in existing:
                name = str(item["name"]).strip()
                seen_names.add(name)
                if name in generated_by_name:
                    merged_items.append(merge_missing_values(item, generated_by_name[name]))
                else:
                    merged_items.append(item)

            for item in generated:
                name = str(item["name"]).strip()
                if name not in seen_names:
                    merged_items.append(item)
            return merged_items

        return existing if existing else generated

    if has_value(existing):
        return existing
    return generated


def is_named_dict_list(values: list[Any]) -> bool:
    return all(
        isinstance(item, dict) and str(item.get("name", "")).strip()
        for item in values
    )


def has_value(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    return True


def score_element(element: dict[str, Any], rule: dict[str, Any]) -> int:
    score = 0
    matched_keyword = False
    haystacks = [
        str(element.get("text", "")),
        str(element.get("label_text", "")),
        str(element.get("parent_text", "")),
        str(element.get("placeholder", "")),
        str(element.get("name", "")),
        str(element.get("id", "")),
        str(element.get("selector_hint", "")),
        str(element.get("dom_path_hint", "")),
        str(element.get("value", "")),
        str((element.get("attributes", {}) or {}).get("title", "")),
        str((element.get("attributes", {}) or {}).get("aria-label", "")),
        str((element.get("attributes", {}) or {}).get("data-name", "")),
        str((element.get("attributes", {}) or {}).get("data-testid", "")),
    ]
    normalized = " | ".join(item.lower() for item in haystacks if item).strip()
    if not normalized:
        return 0

    for keyword in rule.get("keywords", []):
        token = str(keyword).lower().strip()
        if token and token in normalized:
            score += 10
            matched_keyword = True

    tag = str(element.get("tag", "")).strip().lower()
    if tag in rule.get("preferred_tags", set()):
        score += 5

    expected_type = str(rule.get("preferred_type", "")).strip().lower()
    actual_type = str(element.get("type", "")).strip().lower()
    type_matched = False
    if expected_type and actual_type == expected_type:
        score += 12
        type_matched = True

    if not matched_keyword and not type_matched:
        return 0

    selector_hint = str(element.get("selector_hint", "")).strip().lower()
    if selector_hint.startswith("#"):
        score += 3
    if "[name=" in selector_hint:
        score += 2
    if actual_type in {"hidden", "checkbox", "radio"}:
        score -= 8

    return score


if __name__ == "__main__":
    main()
