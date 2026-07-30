from __future__ import annotations

import sys
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
RPA_ROOT = PROJECT_ROOT / "rpa"
if str(RPA_ROOT) not in sys.path:
    sys.path.insert(0, str(RPA_ROOT))

from auto_listing import advance_listing_state
from listing_audit import build_listing_task_record
from test_auto_listing import sample_payload


class ListingAuditTests(unittest.TestCase):
    def test_task_record_uses_stable_business_identity(self) -> None:
        first = build_listing_task_record(sample_payload())
        changed = sample_payload()
        changed["product"]["selected_title"] = "new selected title"
        second = build_listing_task_record(changed)

        self.assertEqual(first["idempotency_key"], second["idempotency_key"])
        self.assertEqual(first["workflow_state"], "draft_pending")
        self.assertEqual(first["approval_status"], "pending")

    def test_task_record_extracts_approval(self) -> None:
        draft = advance_listing_state(
            sample_payload(),
            "draft_saved",
            evidence={"draft_id": "draft-1", "draft_url": "https://draft.invalid/1"},
        )
        approved = advance_listing_state(
            draft,
            "review_approved",
            evidence={"approved_by": "reviewer"},
        )

        record = build_listing_task_record(approved)

        self.assertEqual(record["workflow_state"], "submit_pending")
        self.assertEqual(record["approval_status"], "approved")
        self.assertEqual(record["approved_by"], "reviewer")


if __name__ == "__main__":
    unittest.main()
