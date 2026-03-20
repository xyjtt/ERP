from pathlib import Path
import sys
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "rpa"))

from config_loader import deep_merge


class ConfigLoaderTest(unittest.TestCase):
    def test_deep_merge_overrides_nested_values(self) -> None:
        merged = deep_merge(
            {"browser": {"headless": False, "wait": 20}, "items": [1]},
            {"browser": {"wait": 10}, "items": [2]},
        )
        self.assertEqual(merged["browser"]["headless"], False)
        self.assertEqual(merged["browser"]["wait"], 10)
        self.assertEqual(merged["items"], [2])


if __name__ == "__main__":
    unittest.main()
