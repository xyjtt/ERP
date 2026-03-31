from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
RPA_ROOT = PROJECT_ROOT / "rpa"
if str(RPA_ROOT) not in sys.path:
    sys.path.insert(0, str(RPA_ROOT))

from parser import load_products


class ParserCsvEncodingTests(unittest.TestCase):
    def test_load_products_supports_gb18030_csv(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            csv_path = Path(temp_dir) / "products_gbk.csv"
            csv_content = (
                "title,price,quantity,platform_category,main_image\n"
                "北欧床头柜,299,20,bedside_table,D:\\images\\main.jpg\n"
            )
            csv_path.write_text(csv_content, encoding="gb18030")

            products = load_products(csv_path)

        self.assertEqual(len(products), 1)
        self.assertEqual(products[0].title, "北欧床头柜")
        self.assertEqual(products[0].price, "299")
        self.assertEqual(products[0].platform_category, "bedside_table")


if __name__ == "__main__":
    unittest.main()
