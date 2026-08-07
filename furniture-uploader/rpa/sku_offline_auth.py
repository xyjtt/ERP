from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlsplit, urlunsplit

from exceptions import (
    OfflineLoginRequiredError,
    OfflineRiskControlError,
    OfflineStoreMismatchError,
)


CommandRunner = Callable[..., subprocess.CompletedProcess[Any]]


_SENSITIVE_VALUE_PATTERN = re.compile(
    r"(?i)\b(password|passwd|pwd|secret|token|access_token|cookie|authorization)\b"
    r"(\s*[\"']?\s*[:=]\s*[\"']?)([^\s,;\"']+)"
)
_URL_PATTERN = re.compile(r"https?://[^\s\"']+")


def _redact_login_diagnostic(value: Any) -> str:
    text = str(value or "").replace("\r", " ").replace("\x00", " ").strip()
    text = _SENSITIVE_VALUE_PATTERN.sub(r"\1\2<redacted>", text)

    def strip_query(match: re.Match[str]) -> str:
        raw_url = match.group(0)
        try:
            parts = urlsplit(raw_url)
        except ValueError:
            return "<redacted-url>"
        if not parts.query and not parts.fragment:
            return raw_url
        return urlunsplit((parts.scheme, parts.netloc, parts.path, "<redacted>", ""))

    return _URL_PATTERN.sub(strip_query, text)


def extract_login_failure_diagnostic(
    stdout: Any,
    stderr: Any,
    *,
    max_chars: int = 1200,
) -> str:
    """Return useful CLI failure details while excluding credential material."""
    fragments: list[str] = []
    for stream in (stdout, stderr):
        for raw_line in str(stream or "").splitlines():
            line = raw_line.strip()
            if not line:
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                fragments.append(_redact_login_diagnostic(line))
                continue
            if not isinstance(payload, dict):
                fragments.append(_redact_login_diagnostic(line))
                continue
            event = _redact_login_diagnostic(payload.get("event", ""))
            message = _redact_login_diagnostic(payload.get("message", ""))
            detail = _redact_login_diagnostic(
                payload.get("exception")
                or payload.get("error")
                or payload.get("error_message")
                or ""
            )
            if "\n" in detail:
                detail = next(
                    (item.strip() for item in reversed(detail.splitlines()) if item.strip()),
                    "",
                )
            parts = [item for item in (event, message, detail) if item]
            if parts:
                fragments.append(" | ".join(parts))

    compact = " ; ".join(item for item in fragments[-6:] if item)
    return compact[-max(1, int(max_chars)) :]


def _read_capture(stream: Any) -> str:
    stream.flush()
    stream.seek(0)
    return str(stream.read() or "")


def _completed_output(value: Any, captured: str) -> str:
    if value is None:
        return captured
    if isinstance(value, bytes):
        rendered = value.decode("utf-8", errors="replace")
    else:
        rendered = str(value)
    return rendered or captured


def ensure_1688_authenticated_session(
    shared_runtime_root: str | Path,
    account_key: str,
    store_name: str,
    *,
    timeout_seconds: int = 300,
    command_runner: CommandRunner = subprocess.run,
    keep_browser_open: bool = False,
    allow_unconfirmed_identity: bool = False,
) -> dict[str, Any]:
    """Reuse the 1688 runtime's account-scoped login without exposing credentials."""
    runtime_root = Path(shared_runtime_root).resolve()
    cli_path = runtime_root / "src" / "cli.py"
    if not runtime_root.is_dir() or not cli_path.is_file():
        raise OfflineLoginRequiredError(
            "1688 automatic login runtime is unavailable; verify --shared-runtime-root."
        )

    normalized_account_key = str(account_key or "").strip()
    normalized_store_name = str(store_name or "").strip()
    if not normalized_account_key or not normalized_store_name:
        raise OfflineLoginRequiredError(
            "1688 automatic login requires a mapped account key and store name."
        )

    timeout = int(timeout_seconds)
    if timeout <= 0:
        raise OfflineLoginRequiredError("1688 automatic login timeout must be greater than zero.")

    runtime_python = runtime_root / ".venv" / "Scripts" / "python.exe"
    python_executable = runtime_python if runtime_python.is_file() else Path(sys.executable)
    command = [
        str(python_executable),
        "-m",
        "src.cli",
        "login",
        "--account-key",
        normalized_account_key,
        "--shop-name",
        normalized_store_name,
        "--auto-solve-slider",
        "--slider-max-attempts",
        "4",
        "--verify-account-identity",
    ]
    if keep_browser_open:
        command.append("--keep-browser-open")

    result: subprocess.CompletedProcess[Any] | None = None
    result_stdout = ""
    result_stderr = ""
    environment = os.environ.copy()
    environment["PYTHONUTF8"] = "1"
    environment["PYTHONUNBUFFERED"] = "1"
    for identity_attempt in range(2):
        with (
            tempfile.TemporaryFile(mode="w+t", encoding="utf-8", errors="replace") as stdout,
            tempfile.TemporaryFile(mode="w+t", encoding="utf-8", errors="replace") as stderr,
        ):
            try:
                result = command_runner(
                    command,
                    cwd=str(runtime_root),
                    stdin=subprocess.DEVNULL,
                    stdout=stdout,
                    stderr=stderr,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=timeout,
                    check=False,
                    env=environment,
                )
            except subprocess.TimeoutExpired as exc:
                diagnostic = extract_login_failure_diagnostic(
                    _read_capture(stdout),
                    _read_capture(stderr),
                )
                raise OfflineLoginRequiredError(
                    f"1688 automatic login timed out after {timeout} seconds."
                    + (f" Diagnostic: {diagnostic}" if diagnostic else "")
                ) from exc
            except OSError as exc:
                raise OfflineLoginRequiredError(
                    "1688 automatic login process could not be started."
                ) from exc
            result_stdout = _completed_output(result.stdout, _read_capture(stdout))
            result_stderr = _completed_output(result.stderr, _read_capture(stderr))
        if int(result.returncode) != 4 or identity_attempt > 0:
            break

    if result is None:
        raise OfflineLoginRequiredError("1688 automatic login returned no result.")

    return_code = int(result.returncode)
    if return_code == 0:
        return {
            "status": "success",
            "account_key": normalized_account_key,
            "store_name": normalized_store_name,
            "browser_runtime_preserved": bool(keep_browser_open),
        }
    if return_code == 2:
        diagnostic = extract_login_failure_diagnostic(result_stdout, result_stderr)
        raise OfflineRiskControlError(
            "1688 automatic login reached captcha, slider, or platform risk verification."
            + (f" Diagnostic: {diagnostic}" if diagnostic else "")
        )
    if return_code == 3:
        diagnostic = extract_login_failure_diagnostic(result_stdout, result_stderr)
        raise OfflineStoreMismatchError(
            "1688 automatic login succeeded but the member_id or store identity did not match."
            + (f" Diagnostic: {diagnostic}" if diagnostic else "")
        )
    if return_code == 4:
        if allow_unconfirmed_identity:
            return {
                "status": "identity_unconfirmed",
                "account_key": normalized_account_key,
                "store_name": normalized_store_name,
                "browser_runtime_preserved": bool(keep_browser_open),
            }
        diagnostic = extract_login_failure_diagnostic(result_stdout, result_stderr)
        raise OfflineLoginRequiredError(
            "1688 automatic login could not prove the configured member_id and store identity."
            + (f" Diagnostic: {diagnostic}" if diagnostic else "")
        )
    diagnostic = extract_login_failure_diagnostic(result_stdout, result_stderr)
    raise OfflineLoginRequiredError(
        f"1688 automatic login failed with exit code {return_code}; the store was stopped."
        + (f" Diagnostic: {diagnostic}" if diagnostic else "")
    )


def stop_owned_1688_account_runtime(
    shared_runtime_root: str | Path,
    account_key: str,
    browser_profile_dir: str | Path,
    cdp_port: int,
) -> None:
    """Stop only the account Edge runtime preserved for the current leased ERP task."""
    runtime_root = Path(shared_runtime_root).resolve()
    normalized_account_key = str(account_key or "").strip()
    profile_dir = Path(browser_profile_dir).resolve()
    port = int(cdp_port)
    if not normalized_account_key or not profile_dir.is_absolute() or port <= 0:
        raise OfflineLoginRequiredError("Invalid account Edge runtime handoff metadata.")
    if str(runtime_root) not in sys.path:
        sys.path.insert(0, str(runtime_root))

    from src.runtime.edge_worker import AccountEdgeRuntime, AccountEdgeSpec

    runtime = AccountEdgeRuntime(
        AccountEdgeSpec(
            account_key=normalized_account_key,
            user_data_dir=profile_dir,
            cdp_port=port,
            profile_directory="Default",
        )
    )
    if not runtime.stop_owned():
        raise OfflineLoginRequiredError(
            f"Account Edge runtime could not be released safely: account_key={normalized_account_key}."
        )
