from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import tempfile
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = PROJECT_ROOT / "scripts" / "reconcile_1688_listing_saga.py"
SPEC = importlib.util.spec_from_file_location("reconcile_1688_listing_saga", SCRIPT_PATH)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class ListingSagaReconcileEvidenceTests(unittest.TestCase):
    def write_context(self, payload: dict) -> Path:
        temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(temporary_directory.cleanup)
        directory = Path(temporary_directory.name)
        path = directory / "failure-context.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        return path

    def valid_payload(self, *, cdp_port: int = 9306) -> dict:
        return {
            "task_id": "1688-listing-CTG028601N1416V01",
            "error_type": "SessionNotCreatedException",
            "error": f"cannot connect to microsoft edge at 127.0.0.1:{cdp_port}",
            "result_context": {},
        }

    def test_accepts_empty_pre_attach_failure_context(self) -> None:
        path = self.write_context(self.valid_payload())
        evidence = MODULE._validate_failure_context(
            path,
            task_id="1688-listing-CTG028601N1416V01",
        )
        self.assertEqual(evidence["error_type"], "SessionNotCreatedException")
        self.assertTrue(evidence["result_context_empty"])
        self.assertEqual(evidence["cdp_port"], 9306)
        self.assertEqual(evidence["pre_attach_signature"], "edge_127.0.0.1_9306_unreachable")
        self.assertEqual(len(evidence["sha256"]), 64)

    def test_accepts_legacy_cdp_port_without_hardcoding_it(self) -> None:
        path = self.write_context(self.valid_payload(cdp_port=9222))
        evidence = MODULE._validate_failure_context(
            path,
            task_id="1688-listing-CTG028601N1416V01",
        )
        self.assertEqual(evidence["cdp_port"], 9222)

    def test_accepts_chromedriver_edge_version_mismatch_before_attach(self) -> None:
        payload = self.valid_payload()
        payload["error"] = (
            "session not created: cannot connect to chrome at 127.0.0.1:9306\n"
            "from unknown error: unrecognized Chrome version: Edg/151.0.4129.59"
        )
        path = self.write_context(payload)

        evidence = MODULE._validate_failure_context(
            path,
            task_id="1688-listing-CTG028601N1416V01",
        )

        self.assertEqual(evidence["cdp_port"], 9306)
        self.assertEqual(
            evidence["pre_attach_signature"],
            "chromedriver_edge_version_mismatch_127.0.0.1_9306_unreachable",
        )

    def test_rejects_generic_chrome_attach_failure_without_edge_mismatch(self) -> None:
        payload = self.valid_payload()
        payload["error"] = "cannot connect to chrome at 127.0.0.1:9306"
        path = self.write_context(payload)

        with self.assertRaisesRegex(RuntimeError, "missing_pre_attach_signature"):
            MODULE._validate_failure_context(
                path,
                task_id="1688-listing-CTG028601N1416V01",
            )

    def test_rejects_non_loopback_pre_attach_endpoint(self) -> None:
        payload = self.valid_payload()
        payload["error"] = "cannot connect to microsoft edge at 192.168.1.5:9306"
        path = self.write_context(payload)
        with self.assertRaisesRegex(RuntimeError, "missing_pre_attach_signature"):
            MODULE._validate_failure_context(
                path,
                task_id="1688-listing-CTG028601N1416V01",
            )

    def test_rejects_any_browser_action_context(self) -> None:
        payload = self.valid_payload()
        payload["result_context"] = {"draft_response_status": 200}
        path = self.write_context(payload)
        with self.assertRaisesRegex(RuntimeError, "contains_browser_action_evidence"):
            MODULE._validate_failure_context(
                path,
                task_id="1688-listing-CTG028601N1416V01",
            )

    def test_rejects_wrong_task(self) -> None:
        path = self.write_context(self.valid_payload())
        with self.assertRaisesRegex(RuntimeError, "task_id_mismatch"):
            MODULE._validate_failure_context(path, task_id="different-task")

    def test_reconcile_uses_phase_and_draft_specific_operation_key(self) -> None:
        draft_key = MODULE.build_listing_operation_key(
            account_key="muke_lixiang",
            task_id="task-1",
            draft_id="draft-1",
            mode="draft",
        )
        submit_key = MODULE.build_listing_operation_key(
            account_key="muke_lixiang",
            task_id="task-1",
            draft_id="draft-1",
            mode="submit",
        )
        self.assertNotEqual(draft_key, submit_key)


if __name__ == "__main__":
    unittest.main()
