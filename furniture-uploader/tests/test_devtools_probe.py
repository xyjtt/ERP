from __future__ import annotations

import sys
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
RPA_ROOT = PROJECT_ROOT / "rpa"
if str(RPA_ROOT) not in sys.path:
    sys.path.insert(0, str(RPA_ROOT))

from devtools_probe import choose_target_tab


class DevtoolsProbeTests(unittest.TestCase):
    def test_choose_target_tab_prefers_matching_page(self) -> None:
        tabs = [
            {"type": "page", "url": "https://example.com/a", "webSocketDebuggerUrl": "ws://a"},
            {"type": "page", "url": "https://offer-new.1688.com/popular/publish.htm", "webSocketDebuggerUrl": "ws://b"},
        ]

        result = choose_target_tab(tabs, url_contains="offer-new.1688.com/popular/publish.htm")

        self.assertEqual(result["webSocketDebuggerUrl"], "ws://b")

    def test_choose_target_tab_ignores_non_page_tabs(self) -> None:
        tabs = [
            {"type": "background_page", "url": "https://offer-new.1688.com/popular/publish.htm", "webSocketDebuggerUrl": "ws://a"},
            {"type": "page", "url": "https://example.com/real", "webSocketDebuggerUrl": "ws://b"},
        ]

        result = choose_target_tab(tabs, url_contains="")

        self.assertEqual(result["webSocketDebuggerUrl"], "ws://b")


if __name__ == "__main__":
    unittest.main()
