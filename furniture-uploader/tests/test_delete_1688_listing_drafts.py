from __future__ import annotations

from pathlib import Path
import sys
import unittest


SCRIPT_ROOT = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPT_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPT_ROOT))

from delete_1688_listing_drafts import (  # noqa: E402
    choose_confirm_control_index,
    choose_confirm_label,
    is_delete_confirmation,
    normalize_draft_ids,
)


class Delete1688ListingDraftTests(unittest.TestCase):
    def test_normalize_draft_ids_rejects_empty_or_invalid_values(self) -> None:
        with self.assertRaises(ValueError):
            normalize_draft_ids([])
        with self.assertRaises(ValueError):
            normalize_draft_ids(["bad id!"])

    def test_normalize_draft_ids_preserves_unique_order(self) -> None:
        self.assertEqual(normalize_draft_ids(["draft-old", "draft-new", "draft-old"]), ["draft-old", "draft-new"])

    def test_delete_confirmation_requires_delete_subject(self) -> None:
        self.assertTrue(is_delete_confirmation("确定删除该草稿吗？"))
        self.assertTrue(is_delete_confirmation("删除商品后无法恢复"))
        self.assertFalse(is_delete_confirmation("确定发布商品吗？"))

    def test_choose_confirm_label_does_not_select_cancel(self) -> None:
        self.assertEqual(choose_confirm_label(["取消", "确定"]), "确定")
        self.assertEqual(choose_confirm_label(["取消"]), "")

    def test_choose_confirm_control_prefers_unique_explicit_label(self) -> None:
        controls = [
            {"text": "取消", "class": "next-btn"},
            {"text": "确定", "class": "next-btn next-btn-primary"},
        ]
        self.assertEqual(choose_confirm_control_index(controls), 1)

    def test_choose_confirm_control_accepts_unique_primary_when_text_is_corrupt(self) -> None:
        controls = [
            {"text": "corrupt-cancel", "class": "next-btn next-btn-normal"},
            {"text": "corrupt-confirm", "class": "next-btn next-btn-primary"},
        ]
        self.assertEqual(choose_confirm_control_index(controls), 1)

    def test_choose_confirm_control_accepts_confirm_data_attribute(self) -> None:
        controls = [
            {"text": "corrupt-a", "data_action": "cancel"},
            {"text": "corrupt-b", "data_action": "delete"},
        ]
        self.assertEqual(choose_confirm_control_index(controls), 1)

    def test_choose_confirm_control_rejects_ambiguous_or_unclassified_controls(self) -> None:
        self.assertIsNone(
            choose_confirm_control_index(
                [
                    {"text": "corrupt-a", "class": "next-btn"},
                    {"text": "corrupt-b", "class": "next-btn"},
                ]
            )
        )
        self.assertIsNone(
            choose_confirm_control_index(
                [
                    {"text": "a", "class": "next-btn-primary"},
                    {"text": "b", "class": "next-btn-primary"},
                ]
            )
        )


if __name__ == "__main__":
    unittest.main()
