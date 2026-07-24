from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Any, Callable

from exceptions import OfflineLoginRequiredError, OfflineRiskControlError


CommandRunner = Callable[..., subprocess.CompletedProcess[Any]]


def ensure_1688_authenticated_session(
    shared_runtime_root: str | Path,
    account_key: str,
    store_name: str,
    *,
    timeout_seconds: int = 300,
    command_runner: CommandRunner = subprocess.run,
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
    ]

    try:
        result = command_runner(
            command,
            cwd=str(runtime_root),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise OfflineLoginRequiredError(
            f"1688 automatic login timed out after {timeout} seconds."
        ) from exc
    except OSError as exc:
        raise OfflineLoginRequiredError("1688 automatic login process could not be started.") from exc

    return_code = int(result.returncode)
    if return_code == 0:
        return {
            "status": "success",
            "account_key": normalized_account_key,
            "store_name": normalized_store_name,
        }
    if return_code == 2:
        raise OfflineRiskControlError(
            "1688 automatic login reached captcha, slider, or platform risk verification."
        )
    raise OfflineLoginRequiredError(
        f"1688 automatic login failed with exit code {return_code}; the store was stopped."
    )
