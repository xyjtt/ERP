from __future__ import annotations

import sys
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
RPA_ROOT = PROJECT_ROOT / "rpa"
if str(RPA_ROOT) not in sys.path:
    sys.path.insert(0, str(RPA_ROOT))

from config_loader import deep_merge


class ConfigLoaderTests(unittest.TestCase):
    def test_named_step_lists_merge_by_name(self) -> None:
        base = {
            "publish": {
                "steps": [
                    {"name": "title", "action": "input", "source": "title", "selector": {"by": "css", "value": ""}},
                    {"name": "price", "action": "input", "source": "price", "selector": {"by": "css", "value": ""}},
                ]
            }
        }
        override = {
            "publish": {
                "steps": [
                    {"name": "title", "selector": {"by": "css", "value": "#title"}},
                ]
            }
        }

        merged = deep_merge(base, override)

        self.assertEqual(merged["publish"]["steps"][0]["action"], "input")
        self.assertEqual(merged["publish"]["steps"][0]["source"], "title")
        self.assertEqual(merged["publish"]["steps"][0]["selector"]["value"], "#title")
        self.assertEqual(merged["publish"]["steps"][1]["name"], "price")

    def test_named_step_lists_append_new_step(self) -> None:
        base = {"workflow": {"steps": [{"name": "search_store_name", "action": "input"}]}}
        override = {"workflow": {"steps": [{"name": "click_store_search", "selector": {"by": "css", "value": ".btn"}}]}}

        merged = deep_merge(base, override)

        self.assertEqual(len(merged["workflow"]["steps"]), 2)
        self.assertEqual(merged["workflow"]["steps"][1]["name"], "click_store_search")


if __name__ == "__main__":
    unittest.main()
