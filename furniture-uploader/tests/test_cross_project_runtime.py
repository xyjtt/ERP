from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch


PROJECT_ROOT = Path(__file__).resolve().parents[1]
RPA_ROOT = PROJECT_ROOT / "rpa"
import sys

if str(RPA_ROOT) not in sys.path:
    sys.path.insert(0, str(RPA_ROOT))

from cross_project_runtime import (
    ExecutorBinding,
    LeaseAcquireResult,
    PROTOCOL_NAME,
    PROTOCOL_VERSION,
    ResourceLease,
    RuntimeLeaseGuard,
    RuntimeProtocolError,
    RuntimeProtocolState,
    canonical_accounts_config_hash,
    resolve_executor_binding,
)


NOW = datetime.now(timezone.utc)


class FakeRuntimeRepository:
    def __init__(self) -> None:
        self.requests = 0
        self.request_heartbeats = 0
        self.acquires: list[tuple[str, str]] = []
        self.releases: list[tuple[str, str, int]] = []
        self.completed: list[str] = []

    def assert_protocol(self, **_kwargs) -> RuntimeProtocolState:
        return RuntimeProtocolState(NOW, 1, True, 2)

    def assert_binding(self, binding, **_kwargs) -> None:
        self.binding = binding

    def register_request(self, **_kwargs) -> None:
        self.requests += 1

    def heartbeat_request(self, _request_key: str, _owner_token: str) -> None:
        self.request_heartbeats += 1

    def complete_request(self, _request_key: str, _owner_token: str, status: str) -> bool:
        self.completed.append(status)
        return True

    def acquire(self, **kwargs) -> LeaseAcquireResult:
        resource_type = kwargs["resource_type"]
        resource_key = kwargs["resource_key"]
        self.acquires.append((resource_type, resource_key))
        if resource_type == "browser_slot" and resource_key.endswith(":1"):
            return LeaseAcquireResult("busy", "resource_lease_busy")
        token = 7 if resource_type == "account" else 11
        return LeaseAcquireResult(
            "acquired",
            "",
            ResourceLease(
                resource_type,
                resource_key,
                kwargs["owner_token"],
                token,
                NOW,
                NOW + timedelta(seconds=120),
            ),
        )

    def heartbeat(self, lease: ResourceLease) -> ResourceLease:
        return lease

    def release(self, lease: ResourceLease) -> bool:
        self.releases.append((lease.resource_type, lease.resource_key, lease.fencing_token))
        return True


class CrossProjectRuntimeTests(unittest.TestCase):
    def test_binding_uses_canonical_versioned_accounts_config(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            payload = {
                "config_revision": "20260730-1",
                "target_hostname": "executor-a",
                "accounts": [
                    {
                        "account_key": "gonglai",
                        "enabled": True,
                        "browser_profile_dir": "D:/profiles/gonglai",
                        "cdp_port": 9301,
                    }
                ],
            }
            payload["config_hash"] = canonical_accounts_config_hash(payload)
            (root / "accounts.json").write_text(json.dumps(payload), encoding="utf-8")

            binding = resolve_executor_binding("gonglai", config_root=root)

        self.assertEqual(binding.profile_ref, "D:/profiles/gonglai")
        self.assertEqual(binding.cdp_port, 9301)
        self.assertEqual(binding.config_revision, "20260730-1")

    def test_binding_prefers_profile_key_over_profile_dir(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            payload = {
                "config_revision": "20260730-1",
                "target_hostname": "executor-a",
                "accounts": [
                    {
                        "account_key": "gonglai",
                        "enabled": True,
                        "profile_key": "gonglai",
                        "browser_profile_dir": "D:/profiles/gonglai",
                        "cdp_port": 9301,
                    }
                ],
            }
            payload["config_hash"] = canonical_accounts_config_hash(payload)
            (root / "accounts.json").write_text(json.dumps(payload), encoding="utf-8")

            binding = resolve_executor_binding("gonglai", config_root=root)

        self.assertEqual(binding.profile_ref, "gonglai")

    def test_binding_rejects_changed_config_without_hash_refresh(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            payload = {
                "config_revision": "20260730-1",
                "target_hostname": "executor-a",
                "accounts": [
                    {
                        "account_key": "gonglai",
                        "enabled": True,
                        "browser_profile_dir": "D:/profiles/gonglai",
                        "cdp_port": 9301,
                    }
                ],
            }
            payload["config_hash"] = canonical_accounts_config_hash(payload)
            payload["accounts"][0]["cdp_port"] = 9302
            (root / "accounts.json").write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaisesRegex(RuntimeProtocolError, "runtime_config_hash_mismatch"):
                resolve_executor_binding("gonglai", config_root=root)

    def test_write_guard_registers_once_and_acquires_account_before_slot(self) -> None:
        repository = FakeRuntimeRepository()
        binding = ExecutorBinding(
            "gonglai",
            "D:/profiles/gonglai",
            9301,
            "20260730-1",
            "a" * 64,
            "executor-a",
        )
        guard = RuntimeLeaseGuard(
            repository,  # type: ignore[arg-type]
            binding=binding,
            task_type="stop_sale",
            run_id="run-1",
            request_key="request-1",
            build_sha="b" * 40,
            component="erp-stop-sale",
            hostname="executor-a",
            wait_timeout_seconds=2,
            poll_interval_seconds=0.001,
        )
        with patch("cross_project_runtime.HEARTBEAT_INTERVAL_SECONDS", 3600):
            with guard:
                self.assertEqual(guard.account_fencing_token, 7)
                self.assertEqual(guard.browser_slot_key, "executor-a:2")
                environment = guard.environment()
                self.assertEqual(environment["ALI1688_RUNTIME_PROTOCOL"], PROTOCOL_NAME)
                self.assertEqual(environment["ALI1688_RUNTIME_PROTOCOL_VERSION"], str(PROTOCOL_VERSION))

        self.assertEqual(repository.requests, 1)
        self.assertEqual(repository.acquires[:3], [
            ("account", "gonglai"),
            ("browser_slot", "executor-a:1"),
            ("browser_slot", "executor-a:2"),
        ])
        self.assertEqual(repository.completed, ["completed"])
        self.assertEqual([item[0] for item in repository.releases], ["browser_slot", "account"])

    def test_source_contains_only_stored_procedure_contract_for_shared_objects(self) -> None:
        source = (RPA_ROOT / "cross_project_runtime.py").read_text(encoding="utf-8")
        for procedure in (
            "usp_ali1688_runtime_protocol_assert",
            "usp_ali1688_executor_binding_assert",
            "usp_ali1688_runtime_request_register",
            "usp_ali1688_runtime_request_heartbeat",
            "usp_ali1688_runtime_request_complete",
            "usp_ali1688_runtime_lease_acquire",
            "usp_ali1688_runtime_lease_heartbeat",
            "usp_ali1688_runtime_lease_release",
        ):
            self.assertIn(procedure, source)
        self.assertNotIn("CREATE TABLE app.ali1688_runtime_", source)


if __name__ == "__main__":
    unittest.main()
