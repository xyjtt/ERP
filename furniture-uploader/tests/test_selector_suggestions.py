from __future__ import annotations

import sys
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
RPA_ROOT = PROJECT_ROOT / "rpa"
if str(RPA_ROOT) not in sys.path:
    sys.path.insert(0, str(RPA_ROOT))

from suggest_selectors import build_suggestions, score_element


class SelectorSuggestionTests(unittest.TestCase):
    def test_score_element_uses_label_and_parent_text(self) -> None:
        element = {
            "tag": "input",
            "type": "text",
            "text": "",
            "label_text": "商品标题",
            "parent_text": "请填写商品标题，建议 20 到 30 字",
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
                "keywords": ["商品标题", "标题"],
                "preferred_tags": {"input"},
            },
        )

        self.assertGreater(score, 0)

    def test_build_suggestions_keeps_best_title_candidate(self) -> None:
        payload = {
            "captured_at": "2026-03-23T10:00:00",
            "current_url": "https://example.com",
            "page_title": "测试页",
            "elements": [
                {
                    "tag": "input",
                    "type": "text",
                    "text": "",
                    "label_text": "商品标题",
                    "parent_text": "商品标题",
                    "placeholder": "请输入商品标题",
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
                    "text": "保存草稿",
                    "label_text": "",
                    "parent_text": "页面底部按钮",
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
        self.assertEqual(title_candidates[0]["label_text"], "商品标题")


if __name__ == "__main__":
    unittest.main()
