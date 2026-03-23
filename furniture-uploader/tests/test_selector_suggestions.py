from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
RPA_ROOT = PROJECT_ROOT / "rpa"
if str(RPA_ROOT) not in sys.path:
    sys.path.insert(0, str(RPA_ROOT))

from suggest_selectors import (
    build_platform_local_override,
    build_suggestions,
    load_json_path,
    merge_missing_values,
    score_element,
)


class SelectorSuggestionTests(unittest.TestCase):
    def test_score_element_uses_label_and_parent_text(self) -> None:
        element = {
            "tag": "input",
            "type": "text",
            "text": "",
            "label_text": "Product title",
            "parent_text": "Fill in the product title before publishing.",
            "placeholder": "",
            "name": "",
            "id": "",
            "selector_hint": "input.title-input",
            "dom_path_hint": "div.form-item > input.title-input",
            "value": "",
            "attributes": {
                "title": "",
                "aria-label": "",
                "data-name": "",
                "data-testid": "",
            },
        }

        score = score_element(
            element,
            {
                "keywords": ["product title", "title"],
                "preferred_tags": {"input"},
            },
        )

        self.assertGreater(score, 0)

    def test_build_suggestions_keeps_best_title_candidate(self) -> None:
        payload = {
            "captured_at": "2026-03-23T10:00:00",
            "current_url": "https://example.com",
            "page_title": "Test page",
            "elements": [
                {
                    "tag": "input",
                    "type": "text",
                    "text": "",
                    "label_text": "Product title",
                    "parent_text": "Product title",
                    "placeholder": "Please input product title",
                    "name": "subject",
                    "id": "title-input",
                    "selector_hint": "#title-input",
                    "dom_path_hint": "div.form-item > input#title-input",
                    "value": "",
                    "classes": ["title-input"],
                    "attributes": {
                        "title": "",
                        "aria-label": "",
                        "data-name": "",
                        "data-testid": "",
                    },
                },
                {
                    "tag": "button",
                    "type": "",
                    "text": "Save draft",
                    "label_text": "",
                    "parent_text": "Footer actions",
                    "placeholder": "",
                    "name": "",
                    "id": "",
                    "selector_hint": "button.btn",
                    "dom_path_hint": "div.footer > button.btn",
                    "value": "",
                    "classes": ["btn"],
                    "attributes": {
                        "title": "",
                        "aria-label": "",
                        "data-name": "",
                        "data-testid": "",
                    },
                },
            ],
        }

        suggestions = build_suggestions(payload, top=3)
        title_candidates = suggestions["fields"]["title"]

        self.assertEqual(len(title_candidates), 1)
        self.assertEqual(title_candidates[0]["selector"]["value"], "#title-input")
        self.assertEqual(title_candidates[0]["label_text"], "Product title")
        self.assertEqual(suggestions["fields"]["price"], [])

    def test_build_platform_local_override_uses_top_ranked_candidates(self) -> None:
        suggestions = {
            "fields": {
                "title": [
                    {"selector": {"by": "css", "value": "#title"}},
                    {"selector": {"by": "css", "value": ".title-input"}},
                ],
                "price": [
                    {"selector": {"by": "css", "value": "#price"}},
                ],
                "category": [
                    {"selector": {"by": "css", "value": "#category"}},
                ],
                "brand": [
                    {"selector": {"by": "css", "value": "#brand"}},
                ],
                "material": [
                    {"selector": {"by": "css", "value": "#material"}},
                ],
                "submit_selector": [
                    {"selector": {"by": "css", "value": "button.submit"}},
                ],
            }
        }

        payload = build_platform_local_override(suggestions)

        self.assertEqual(
            payload["publish"]["steps"],
            [
                {"name": "title", "selector": {"by": "css", "value": "#title"}},
                {"name": "price", "selector": {"by": "css", "value": "#price"}},
                {"name": "category", "selector": {"by": "css", "value": "#category"}},
                {"name": "brand", "selector": {"by": "css", "value": "#brand"}},
                {"name": "material", "selector": {"by": "css", "value": "#material"}},
            ],
        )
        self.assertEqual(
            payload["publish"]["submit_selector"],
            {"by": "css", "value": "button.submit"},
        )

    def test_merge_missing_values_preserves_existing_selectors(self) -> None:
        existing = {
            "publish": {
                "submit_selector": {"by": "css", "value": "button.real-submit"},
                "steps": [
                    {"name": "title", "selector": {"by": "css", "value": "#real-title"}},
                    {"name": "quantity", "selector": {"by": "css", "value": ""}},
                ],
            }
        }
        generated = {
            "publish": {
                "submit_selector": {"by": "css", "value": "button.suggested-submit"},
                "steps": [
                    {"name": "title", "selector": {"by": "css", "value": "#suggested-title"}},
                    {"name": "quantity", "selector": {"by": "css", "value": "#quantity"}},
                    {"name": "price", "selector": {"by": "css", "value": "#price"}},
                ],
            }
        }

        merged = merge_missing_values(existing, generated)

        self.assertEqual(
            merged["publish"]["submit_selector"]["value"],
            "button.real-submit",
        )
        self.assertEqual(
            merged["publish"]["steps"][0]["selector"]["value"],
            "#real-title",
        )
        self.assertEqual(
            merged["publish"]["steps"][1]["selector"]["value"],
            "#quantity",
        )
        self.assertEqual(
            merged["publish"]["steps"][2]["selector"]["value"],
            "#price",
        )

    def test_load_json_path_supports_utf8_bom(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            target = Path(temp_dir) / "probe.json"
            target.write_text('{"fields": {"title": []}}', encoding="utf-8-sig")

            payload = load_json_path(target)

        self.assertEqual(payload, {"fields": {"title": []}})


if __name__ == "__main__":
    unittest.main()
