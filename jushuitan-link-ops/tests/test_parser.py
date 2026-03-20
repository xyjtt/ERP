from pathlib import Path
import sys
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "rpa"))

from parser import (
    build_tasks,
    normalize_expected_date,
    strip_emoji,
    transform_discontinued_notice_rows,
    validate_tasks,
)


class ParserTest(unittest.TestCase):
    def test_build_tasks_from_manual_rows(self) -> None:
        tasks = build_tasks(
            [
                {
                    "platform": "京喜",
                    "shop_name": "京喜旗舰店",
                    "old_online_sku_code": "OLD001",
                    "new_online_sku_code": "NEW001",
                    "operator_name": "张三",
                }
            ],
            source_type="manual",
        )
        self.assertEqual(len(tasks), 1)
        self.assertEqual(tasks[0].task_id, "MANUAL-00001")

    def test_validate_tasks_rejects_same_old_and_new_code(self) -> None:
        task = build_tasks(
            [
                {
                    "platform": "京喜",
                    "shop_name": "店铺A",
                    "old_online_sku_code": "SAME001",
                    "new_online_sku_code": "SAME001",
                }
            ],
            source_type="manual",
        )[0]
        report = validate_tasks([task])
        self.assertFalse(report.ok)
        self.assertIn("old_online_sku_code equals new_online_sku_code", report.errors[0])

    def test_strip_emoji(self) -> None:
        self.assertEqual(strip_emoji("ABC😀DEF"), "ABCDEF")

    def test_transform_discontinued_notice_rows_filters_and_maps(self) -> None:
        rows = [
            {
                "统计日期新": "2026-03-19",
                "商品编码": "135150",
                "处理说明": "下架",
                "下架备注": "京喜需下架",
            },
            {
                "统计日期新": "2026-03-19",
                "商品编码": "135151",
                "处理说明": "替换编码",
                "下架备注": "京喜需下架",
            },
            {
                "统计日期新": "2026-03-18",
                "商品编码": "135152",
                "处理说明": "下架",
                "下架备注": "京喜需下架",
            },
        ]

        tasks = transform_discontinued_notice_rows(
            rows,
            default_platform="京喜",
            default_shop_name="京喜店铺",
            default_operator_name="系统",
            discontinued_target_code="txcj",
            expected_stat_date="2026-03-19",
            discontinued_action="下架",
            discontinued_remark="京喜需下架",
        )

        self.assertEqual(len(tasks), 1)
        self.assertEqual(tasks[0]["platform"], "京喜")
        self.assertEqual(tasks[0]["shop_name"], "京喜店铺")
        self.assertEqual(tasks[0]["old_online_sku_code"], "135150")
        self.assertEqual(tasks[0]["new_online_sku_code"], "txcj")
        self.assertEqual(tasks[0]["remark"], "京喜需下架")

    def test_normalize_expected_date_accepts_slashes(self) -> None:
        self.assertEqual(normalize_expected_date("2026/03/19"), "2026-03-19")


if __name__ == "__main__":
    unittest.main()
