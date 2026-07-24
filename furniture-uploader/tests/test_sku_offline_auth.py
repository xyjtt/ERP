from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
RPA_ROOT = PROJECT_ROOT / "rpa"
if str(RPA_ROOT) not in sys.path:
    sys.path.insert(0, str(RPA_ROOT))

from exceptions import OfflineLoginRequiredError, OfflineRiskControlError  # noqa: E402
from sku_offline_auth import (  # noqa: E402
    ensure_1688_authenticated_session,
    extract_login_failure_diagnostic,
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
            return subprocess.CompletedProcess(command, 0, stdout="sensitive runtime output", stderr="")

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
        self.assertNotIn("username", " ".join(command).lower())
        self.assertNotIn("password", " ".join(command).lower())
        self.assertEqual(result["status"], "success")
        self.assertNotIn("stdout", result)
        self.assertEqual(captured["kwargs"]["timeout"], 300)

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
            raise subprocess.TimeoutExpired(command, kwargs["timeout"])

        with tempfile.TemporaryDirectory() as temp_dir:
            runtime_root = self.create_runtime(Path(temp_dir))
            with self.assertRaisesRegex(OfflineLoginRequiredError, "timed out"):
                ensure_1688_authenticated_session(
                    runtime_root,
                    "gonglai",
                    "常州工莱家具",
                    timeout_seconds=12,
                    command_runner=runner,
                )


if __name__ == "__main__":
    unittest.main()
