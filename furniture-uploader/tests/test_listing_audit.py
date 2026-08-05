from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
RPA_ROOT = PROJECT_ROOT / "rpa"
if str(RPA_ROOT) not in sys.path:
    sys.path.insert(0, str(RPA_ROOT))
TESTS_ROOT = PROJECT_ROOT / "tests"
if str(TESTS_ROOT) not in sys.path:
    sys.path.insert(0, str(TESTS_ROOT))

from auto_listing import advance_listing_state
from listing_audit import ListingAuditRepository, build_listing_task_record
from test_auto_listing import review_approval_evidence, sample_payload


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
            evidence=review_approval_evidence(draft),
        )

        record = build_listing_task_record(approved)

        self.assertEqual(record["workflow_state"], "submit_pending")
        self.assertEqual(record["approval_status"], "approved")
        self.assertEqual(record["approved_by"], "reviewer")

    def test_find_existing_task_uses_parameterized_business_identity(self) -> None:
        class Cursor:
            description = [
                ("task_id",),
                ("idempotency_key",),
                ("account_key",),
                ("shop_name",),
                ("company_sku",),
                ("workflow_state",),
                ("approval_status",),
                ("updated_at",),
            ]

            def __init__(self) -> None:
                self.sql = ""
                self.params = ()

            def execute(self, sql, params):
                self.sql = sql
                self.params = params
                return self

            def fetchone(self):
                return (
                    "task-1",
                    "idem-1",
                    "muke_lixiang",
                    "木刻理想",
                    "SKU-1",
                    "draft_pending_review",
                    "pending",
                    "2026-08-05",
                )

        class Connection:
            def __init__(self, cursor) -> None:
                self.cursor_obj = cursor

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return None

            def cursor(self):
                return self.cursor_obj

        cursor = Cursor()
        config = type("Config", (), {"schema": "app"})()
        repository = ListingAuditRepository(
            config,
            connect=lambda _config: Connection(cursor),
        )

        result = repository.find_existing_task(task_id="task-1", idempotency_key="idem-1")

        self.assertEqual(result["account_key"], "muke_lixiang")
        self.assertIn("WHERE task_id = ? OR idempotency_key = ?", cursor.sql)
        self.assertEqual(cursor.params, ("task-1", "idem-1", "task-1"))

    def test_atomic_claim_inserts_under_update_and_hold_locks(self) -> None:
        class Cursor:
            rowcount = 1

            def __init__(self) -> None:
                self.calls = []

            def execute(self, sql, params):
                self.calls.append((sql, params))
                return self

            def fetchone(self):
                return None

        class Connection:
            def __init__(self, cursor) -> None:
                self.cursor_obj = cursor
                self.committed = False

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return None

            def cursor(self):
                return self.cursor_obj

            def commit(self):
                self.committed = True

        cursor = Cursor()
        connection = Connection(cursor)
        config = type("Config", (), {"schema": "app"})()
        repository = ListingAuditRepository(config, connect=lambda _config: connection)

        result = repository.claim_scheduled_task(
            sample_payload(),
            claim_owner="daily-1:task-1",
            claim_seconds=300,
        )

        self.assertEqual(result["status"], "claimed")
        self.assertIn("WITH (UPDLOCK, HOLDLOCK)", cursor.calls[0][0])
        self.assertIn("INSERT INTO [app].[ali1688_listing_task]", cursor.calls[1][0])
        self.assertTrue(connection.committed)

    def test_current_payload_guard_rejects_stale_payload(self) -> None:
        payload = sample_payload()
        payload["schedule"] = {"claim_owner": "owner-1"}
        stale = json.loads(json.dumps(payload))
        stale["product"]["selected_title"] = "older title"

        class Cursor:
            def execute(self, _sql, _params):
                return self

            def fetchone(self):
                return (
                    payload["task_id"],
                    build_listing_task_record(payload)["idempotency_key"],
                    "draft_pending",
                    "owner-1",
                    "2026-08-05T00:00:00",
                    json.dumps(stale),
                    1,
                )

        class Connection:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return None

            def cursor(self):
                return Cursor()

            def commit(self):
                return None

        config = type("Config", (), {"schema": "app"})()
        repository = ListingAuditRepository(config, connect=lambda _config: Connection())
        with self.assertRaisesRegex(RuntimeError, "payload is stale"):
            repository.assert_execution_payload_current(payload)

    def test_manual_execution_registration_rejects_stale_existing_payload(self) -> None:
        payload = sample_payload()
        stale = json.loads(json.dumps(payload))
        stale["workflow"]["state"] = "submitted"

        class Cursor:
            def execute(self, _sql, _params):
                return self

            def fetchone(self):
                return (
                    payload["task_id"],
                    build_listing_task_record(payload)["idempotency_key"],
                    json.dumps(stale),
                )

        class Connection:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return None

            def cursor(self):
                return Cursor()

            def commit(self):
                return None

        config = type("Config", (), {"schema": "app"})()
        repository = ListingAuditRepository(config, connect=lambda _config: Connection())
        with self.assertRaisesRegex(RuntimeError, "payload is stale"):
            repository.register_execution_payload(payload)


if __name__ == "__main__":
    unittest.main()
