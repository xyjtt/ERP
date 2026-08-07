from __future__ import annotations

import subprocess
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch


PROJECT_ROOT = Path(__file__).resolve().parents[1]
RPA_ROOT = PROJECT_ROOT / "rpa"
if str(RPA_ROOT) not in sys.path:
    sys.path.insert(0, str(RPA_ROOT))

from exceptions import (  # noqa: E402
    OfflineLoginRequiredError,
    OfflineRiskControlError,
    OfflineStoreMismatchError,
)
from sku_offline_auth import (  # noqa: E402
    ensure_1688_authenticated_session,
    extract_login_failure_diagnostic,
    stop_owned_1688_account_runtime,
)


class SkuOfflineAuthTests(unittest.TestCase):
    def create_runtime(self, root: Path, *, with_venv_python: bool = False) -> Path:
        cli_path = root / "src" / "cli.py"
        cli_path.parent.mkdir(parents=True)
        cli_path.write_text("# test runtime\n", encoding="utf-8")
        if with_venv_python:
            python_path = root / ".venv" / "Scripts" / "python.exe"
            python_path.parent.mkdir(parents=True)
            python_path.write_bytes(b"")
        return root

    def test_success_uses_account_scoped_login_without_secrets(self) -> None:
        captured: dict = {}

        def runner(command, **kwargs):
            captured["command"] = list(command)
            captured["kwargs"] = dict(kwargs)
            kwargs["stdout"].write("sensitive runtime output")
            kwargs["stdout"].flush()
            return subprocess.CompletedProcess(command, 0)

        with tempfile.TemporaryDirectory() as temp_dir:
            runtime_root = self.create_runtime(Path(temp_dir), with_venv_python=True)
            result = ensure_1688_authenticated_session(
                runtime_root,
                "gonglai",
                "阿里巴巴-常州工莱家具",
                command_runner=runner,
            )

        command = captured["command"]
        self.assertTrue(command[0].endswith(".venv\\Scripts\\python.exe"))
        self.assertEqual(command[1:4], ["-m", "src.cli", "login"])
        self.assertEqual(command[command.index("--account-key") + 1], "gonglai")
        self.assertEqual(command[command.index("--shop-name") + 1], "阿里巴巴-常州工莱家具")
        self.assertIn("--auto-solve-slider", command)
        self.assertEqual(command[command.index("--slider-max-attempts") + 1], "4")
        self.assertIn("--verify-account-identity", command)
        self.assertNotIn("--keep-browser-open", command)
        self.assertNotIn("username", " ".join(command).lower())
        self.assertNotIn("password", " ".join(command).lower())
        self.assertEqual(result["status"], "success")
        self.assertNotIn("stdout", result)
        self.assertEqual(captured["kwargs"]["timeout"], 300)
        self.assertNotIn("capture_output", captured["kwargs"])
        self.assertIsNot(captured["kwargs"]["stdout"], subprocess.PIPE)
        self.assertIsNot(captured["kwargs"]["stderr"], subprocess.PIPE)
        self.assertEqual(captured["kwargs"]["stdin"], subprocess.DEVNULL)
        self.assertEqual(captured["kwargs"]["env"]["PYTHONUNBUFFERED"], "1")

    def test_listing_handoff_keeps_browser_and_accepts_unconfirmed_identity(self) -> None:
        captured: dict = {}

        def runner(command, **_kwargs):
            captured["command"] = list(command)
            return subprocess.CompletedProcess(
                command,
                4,
                stdout='LOGIN_IDENTITY_RESULT:{"status":"identity_unconfirmed"}',
            )

        with tempfile.TemporaryDirectory() as temp_dir:
            runtime_root = self.create_runtime(Path(temp_dir))
            result = ensure_1688_authenticated_session(
                runtime_root,
                "gonglai",
                "常州工莱家具",
                command_runner=runner,
                keep_browser_open=True,
                allow_unconfirmed_identity=True,
            )

        self.assertEqual(result["status"], "identity_unconfirmed")
        self.assertTrue(result["browser_runtime_preserved"])
        self.assertIn("--keep-browser-open", captured["command"])

    def test_falls_back_to_current_python_when_runtime_venv_is_missing(self) -> None:
        captured: dict = {}

        def runner(command, **_kwargs):
            captured["command"] = list(command)
            return subprocess.CompletedProcess(command, 0)

        with tempfile.TemporaryDirectory() as temp_dir:
            runtime_root = self.create_runtime(Path(temp_dir))
            ensure_1688_authenticated_session(
                runtime_root,
                "gonglai",
                "常州工莱家具",
                command_runner=runner,
            )

        self.assertEqual(captured["command"][0], sys.executable)

    def test_default_runner_supports_file_backed_subprocess_output(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            runtime_root = self.create_runtime(Path(temp_dir) / "Runtime Root With Spaces")
            result = ensure_1688_authenticated_session(
                runtime_root,
                "gonglai",
                "常州工莱家具",
                timeout_seconds=10,
            )

        self.assertEqual(result["status"], "success")

    def test_captcha_exit_code_is_risk_control(self) -> None:
        def runner(command, **_kwargs):
            return subprocess.CompletedProcess(command, 2, stdout="captcha details")

        with tempfile.TemporaryDirectory() as temp_dir:
            runtime_root = self.create_runtime(Path(temp_dir))
            with self.assertRaises(OfflineRiskControlError):
                ensure_1688_authenticated_session(
                    runtime_root,
                    "gonglai",
                    "常州工莱家具",
                    command_runner=runner,
                )

    def test_identity_mismatch_exit_code_stops_only_the_mismatched_store(self) -> None:
        def runner(command, **_kwargs):
            return subprocess.CompletedProcess(
                command,
                3,
                stdout='LOGIN_IDENTITY_RESULT:{"status":"member_id_mismatch"}',
            )

        with tempfile.TemporaryDirectory() as temp_dir:
            runtime_root = self.create_runtime(Path(temp_dir))
            with self.assertRaises(OfflineStoreMismatchError):
                ensure_1688_authenticated_session(
                    runtime_root,
                    "gonglai",
                    "常州工莱家具",
                    command_runner=runner,
                )

    def test_unproven_identity_is_retryable_login_failure_not_store_mismatch(self) -> None:
        attempts = 0

        def runner(command, **_kwargs):
            nonlocal attempts
            attempts += 1
            return subprocess.CompletedProcess(
                command,
                4,
                stdout='LOGIN_IDENTITY_RESULT:{"status":"member_id_missing"}',
            )

        with tempfile.TemporaryDirectory() as temp_dir:
            runtime_root = self.create_runtime(Path(temp_dir))
            with self.assertRaises(OfflineLoginRequiredError):
                ensure_1688_authenticated_session(
                    runtime_root,
                    "gonglai",
                    "常州工莱家具",
                    command_runner=runner,
                )

        self.assertEqual(attempts, 2)

    def test_unproven_identity_is_verified_once_more_before_success(self) -> None:
        return_codes = iter((4, 0))

        def runner(command, **_kwargs):
            return subprocess.CompletedProcess(command, next(return_codes), stdout="")

        with tempfile.TemporaryDirectory() as temp_dir:
            runtime_root = self.create_runtime(Path(temp_dir))
            result = ensure_1688_authenticated_session(
                runtime_root,
                "gonglai",
                "常州工莱家具",
                command_runner=runner,
            )

        self.assertEqual(result["status"], "success")

    def test_nonzero_exit_includes_sanitized_subprocess_diagnostic(self) -> None:
        def runner(command, **_kwargs):
            return subprocess.CompletedProcess(
                command,
                1,
                stderr=(
                    '{"event":"auth.login.worker_failed",'
                    '"message":"Failed to start account Edge",'
                    '"exception":"EdgeWorkerError: port unavailable",'
                    '"password":"do-not-leak"}\n'
                    "password=do-not-leak"
                ),
            )

        with tempfile.TemporaryDirectory() as temp_dir:
            runtime_root = self.create_runtime(Path(temp_dir))
            with self.assertRaises(OfflineLoginRequiredError) as caught:
                ensure_1688_authenticated_session(
                    runtime_root,
                    "gonglai",
                    "常州工莱家具",
                    command_runner=runner,
                )

        self.assertNotIn("do-not-leak", str(caught.exception))
        self.assertIn("auth.login.worker_failed", str(caught.exception))
        self.assertIn("EdgeWorkerError: port unavailable", str(caught.exception))
        self.assertIn("password=<redacted>", str(caught.exception))

    def test_diagnostic_removes_url_queries_and_secret_values(self) -> None:
        diagnostic = extract_login_failure_diagnostic(
            "https://example.test/login?access_token=abc123\n",
            "secret=hidden-value",
        )

        self.assertIn("https://example.test/login?<redacted>", diagnostic)
        self.assertIn("secret=<redacted>", diagnostic)
        self.assertNotIn("abc123", diagnostic)
        self.assertNotIn("hidden-value", diagnostic)

    def test_timeout_is_login_required(self) -> None:
        def runner(command, **kwargs):
            kwargs["stderr"].write("login worker waiting password=do-not-leak")
            kwargs["stderr"].flush()
            raise subprocess.TimeoutExpired(command, kwargs["timeout"])

        with tempfile.TemporaryDirectory() as temp_dir:
            runtime_root = self.create_runtime(Path(temp_dir))
            with self.assertRaisesRegex(OfflineLoginRequiredError, "timed out") as caught:
                ensure_1688_authenticated_session(
                    runtime_root,
                    "gonglai",
                    "常州工莱家具",
                    timeout_seconds=12,
                    command_runner=runner,
                )

        self.assertIn("login worker waiting", str(caught.exception))
        self.assertIn("password=<redacted>", str(caught.exception))
        self.assertNotIn("do-not-leak", str(caught.exception))

    def test_runtime_handoff_cleanup_stops_only_the_bound_account_edge(self) -> None:
        captured: dict = {}

        class FakeSpec:
            def __init__(self, **kwargs):
                captured["spec"] = kwargs

        class FakeRuntime:
            def __init__(self, spec):
                captured["runtime_spec"] = spec

            def stop_owned(self) -> bool:
                captured["stopped"] = True
                return True

        src_package = types.ModuleType("src")
        src_package.__path__ = []
        runtime_package = types.ModuleType("src.runtime")
        runtime_package.__path__ = []
        edge_module = types.ModuleType("src.runtime.edge_worker")
        edge_module.AccountEdgeRuntime = FakeRuntime
        edge_module.AccountEdgeSpec = FakeSpec

        with tempfile.TemporaryDirectory() as temp_dir, patch.dict(
            sys.modules,
            {
                "src": src_package,
                "src.runtime": runtime_package,
                "src.runtime.edge_worker": edge_module,
            },
        ):
            profile_dir = Path(temp_dir) / "profile"
            stop_owned_1688_account_runtime(
                temp_dir,
                "muke_lixiang",
                profile_dir,
                9306,
            )

        self.assertTrue(captured["stopped"])
        self.assertEqual(captured["spec"]["account_key"], "muke_lixiang")
        self.assertEqual(captured["spec"]["user_data_dir"], profile_dir.resolve())
        self.assertEqual(captured["spec"]["cdp_port"], 9306)


if __name__ == "__main__":
    unittest.main()
