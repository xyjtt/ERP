from __future__ import annotations

from pathlib import Path
import sys
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_ROOT = PROJECT_ROOT / "scripts"
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

from probe_1688_sku_rows import _build_task, build_argument_parser  # noqa: E402


class Probe1688SkuRowsTests(unittest.TestCase):
    def test_builds_read_only_probe_task(self) -> None:
        args = build_argument_parser().parse_args(
            [
                "--store",
                "STORE-A",
                "--product-id",
                "PRODUCT-1",
                "--sku",
                "SKU-1",
                "--shared-runtime-root",
                "D:/runtime",
            ]
        )

        task = _build_task(args)

        self.assertEqual(task.store_name, "STORE-A")
        self.assertEqual(task.product_id, "PRODUCT-1")
        self.assertEqual(task.online_sku, "SKU-1")
        self.assertEqual(task.handling, "probe_only")
        self.assertEqual(args.lock_wait_seconds, 0)


if __name__ == "__main__":
    unittest.main()
