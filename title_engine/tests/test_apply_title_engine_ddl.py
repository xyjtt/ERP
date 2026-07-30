from __future__ import annotations

import unittest

from scripts.apply_title_engine_ddl import split_batches


class ApplyTitleEngineDdlTests(unittest.TestCase):
    def test_split_batches_only_treats_standalone_go_as_separator(self) -> None:
        sql = "SELECT 'GO';\nGO\nSELECT 2;\n  go  \nSELECT 3;"

        self.assertEqual(split_batches(sql), ["SELECT 'GO';", "SELECT 2;", "SELECT 3;"])


if __name__ == "__main__":
    unittest.main()
