from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path


SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "inspect_1688_saved_draft.py"
SPEC = importlib.util.spec_from_file_location("inspect_1688_saved_draft", SCRIPT_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(MODULE)


class InspectSavedDraftTests(unittest.TestCase):
    def test_spec_equal_requires_exact_chinese_value(self) -> None:
        self.assertTrue(MODULE._spec_equal("胡桃色", "胡桃色"))
        self.assertFalse(MODULE._spec_equal("红色100", "胡桃色"))

    def test_spec_equal_requires_non_empty_expected_value(self) -> None:
        self.assertFalse(MODULE._spec_equal("胡桃色", ""))
        self.assertFalse(MODULE._spec_equal("", "48/40/50"))


if __name__ == "__main__":
    unittest.main()
