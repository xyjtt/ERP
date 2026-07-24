from __future__ import annotations

from pathlib import Path
import sys
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_ROOT = PROJECT_ROOT / "scripts"
RPA_ROOT = PROJECT_ROOT / "rpa"
for import_root in (SCRIPTS_ROOT, RPA_ROOT):
    if str(import_root) not in sys.path:
        sys.path.insert(0, str(import_root))

from probe_1688_duplicate_candidates import (  # noqa: E402
    _find_target_duplicate_sku_codes,
    _select_candidate_groups,
    build_argument_parser,
)
from sku_offline_tasks import OfflineTask  # noqa: E402


def build_task(store: str, product_id: str, sku: str, row: int) -> OfflineTask:
    return OfflineTask(
        source_file="input.csv",
        source_sheet="CSV",
        source_row_number=row,
        store_name=store,
        platform="Alibaba",
        product_id=product_id,
        online_sku=sku,
        handling="offline",
        replacement_sku="",
        change_image="",
        platform_store_item_code="",
        raw={},
    )


class Probe1688DuplicateCandidatesTests(unittest.TestCase):
    def test_prioritizes_products_with_more_source_skus(self) -> None:
        tasks = [
            build_task("STORE-A", "P2", "S2", 2),
            build_task("STORE-A", "P1", "S1", 3),
            build_task("STORE-A", "P2", "S3", 4),
            build_task("STORE-B", "P3", "S4", 5),
        ]

        groups = _select_candidate_groups(tasks, "STORE-A", 2)

        self.assertEqual([[task.product_id for task in group] for group in groups], [["P2", "P2"], ["P1"]])

    def test_finds_only_duplicate_codes_present_in_source(self) -> None:
        matched = _find_target_duplicate_sku_codes(
            ["SKU-1", "SKU-2"], {"SKU-2": 2, "SKU-3": 3, "SKU-1": 1}
        )

        self.assertEqual(matched, {"SKU-2": 2})

    def test_parser_defaults_to_ten_products(self) -> None:
        args = build_argument_parser().parse_args(
            [
                "--file",
                "input.csv",
                "--store",
                "STORE-A",
                "--shared-runtime-root",
                "D:/runtime",
            ]
        )

        self.assertEqual(args.max_products, 10)
        self.assertEqual(args.skip_products, 0)
        self.assertEqual(args.lock_wait_seconds, 0)


if __name__ == "__main__":
    unittest.main()
