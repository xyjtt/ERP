# -*- coding: utf-8 -*-
"""Tests for source preview CLI helpers."""

import os
import sys
import unittest


sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts.preview_keyword_sources import parse_attributes


class TestPreviewKeywordSources(unittest.TestCase):
    def test_parse_repeated_attributes(self):
        self.assertEqual(
            parse_attributes(["材质=实木", "颜色=胡桃色"]),
            {"材质": "实木", "颜色": "胡桃色"},
        )

    def test_invalid_attribute_is_rejected(self):
        with self.assertRaises(ValueError):
            parse_attributes(["没有分隔符"])


if __name__ == "__main__":
    unittest.main()
