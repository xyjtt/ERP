from __future__ import annotations

import argparse
import ctypes
from ctypes import wintypes
import hashlib
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
RPA_ROOT = PROJECT_ROOT / "rpa"
if str(RPA_ROOT) not in sys.path:
    sys.path.insert(0, str(RPA_ROOT))

from manage_1688_stop_sale_daily import send_manager_notification
from stop_sale_audit import connect_app_database, resolve_stop_sale_app_config


TERMINAL_CHILD_STATUSES = {"success", "partial", "failed"}
MANAGER_ID_PATTERN = re.compile(r"[A-Za-z0-9_.-]{1,64}")


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Finalize an interrupted daily stop-sale manager after every child run is terminal."
    )
    parser.add_argument("--manager-run-id", required=True)
    parser.add_argument("--manager-dir", required=True)
    parser.add_argument("--shared-runtime-root", required=True)
    parser.add_argument("--shared-lock-path", default="")
    parser.add_argument("--reason", required=True)
    parser.add_argument("--no-notify", action="store_true")
    parser.add_argument("--yes", action="store_true")
    return parser


def _is_process_running(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        process_query_limited_information = 0x1000
        handle = ctypes.windll.kernel32.OpenProcess(  # type: ignore[attr-defined]
            process_query_limited_information, False, int(pid)
        )
        if not handle:
            return False
        ctypes.windll.kernel32.CloseHandle(handle)  # type: ignore[attr-defined]
        return True
    try:
        os.kill(pid, 0)
    except (OSError, ProcessLookupError):
        return False
    return True


def _load_manager_lock(lock_path: Path, manager_run_id: str) -> dict[str, Any]:
    if not lock_path.is_file():
        raise FileNotFoundError(f"Daily manager lock does not exist: {lock_path}")
    payload = json.loads(lock_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise RuntimeError("Daily manager lock payload is not an object.")
    metadata = payload.get("metadata")
    if not isinstance(metadata, dict):
        raise RuntimeError("Daily manager lock metadata is missing.")
    if str(metadata.get("cycle") or "") != "1688_stop_sale_daily_manager":
        raise RuntimeError("Lock does not belong to the daily stop-sale manager.")
    if str(metadata.get("manager_run_id") or "") != manager_run_id:
        raise RuntimeError("Daily manager lock manager_run_id does not match.")
    token = str(payload.get("token") or "").strip()
    if not token:
        raise RuntimeError("Daily manager lock token is missing.")
    owner_pid = int(payload.get("pid") or 0)
    if _is_process_running(owner_pid):
        raise RuntimeError(f"Daily manager lock owner is still active: pid={owner_pid}")
    return payload


def _lock_fingerprint(payload: dict[str, Any]) -> str:
    serialized = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _remove_windows_lock_if_unchanged(
    lock_path: Path,
    manager_run_id: str,
    expected_fingerprint: str,
) -> None:
    if os.name != "nt":
        raise RuntimeError("Atomic manager lock release is supported only on Windows.")

    generic_read = 0x80000000
    delete_access = 0x00010000
    file_share_read = 0x00000001
    open_existing = 3
    file_attribute_normal = 0x00000080
    file_disposition_info = 4
    invalid_handle_value = ctypes.c_void_p(-1).value
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateFileW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    ]
    kernel32.CreateFileW.restype = wintypes.HANDLE
    kernel32.GetFileSizeEx.argtypes = [wintypes.HANDLE, ctypes.POINTER(ctypes.c_longlong)]
    kernel32.GetFileSizeEx.restype = wintypes.BOOL
    kernel32.ReadFile.argtypes = [
        wintypes.HANDLE,
        wintypes.LPVOID,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
        wintypes.LPVOID,
    ]
    kernel32.ReadFile.restype = wintypes.BOOL
    kernel32.SetFileInformationByHandle.argtypes = [
        wintypes.HANDLE,
        ctypes.c_int,
        wintypes.LPVOID,
        wintypes.DWORD,
    ]
    kernel32.SetFileInformationByHandle.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL

    class FileDispositionInfo(ctypes.Structure):
        _fields_ = [("delete_file", wintypes.BOOLEAN)]

    handle = kernel32.CreateFileW(
        str(lock_path),
        generic_read | delete_access,
        file_share_read,
        None,
        open_existing,
        file_attribute_normal,
        None,
    )
    if handle == invalid_handle_value:
        raise OSError(ctypes.get_last_error(), "Unable to exclusively open manager lock")
    try:
        size = ctypes.c_longlong()
        if not kernel32.GetFileSizeEx(handle, ctypes.byref(size)):
            raise OSError(ctypes.get_last_error(), "Unable to read manager lock size")
        if size.value <= 0 or size.value > 1024 * 1024:
            raise RuntimeError(f"Daily manager lock has invalid size: {size.value}")
        buffer = ctypes.create_string_buffer(size.value)
        bytes_read = wintypes.DWORD()
        if not kernel32.ReadFile(
            handle,
            buffer,
            size.value,
            ctypes.byref(bytes_read),
            None,
        ):
            raise OSError(ctypes.get_last_error(), "Unable to read manager lock")
        payload = json.loads(buffer.raw[: bytes_read.value].decode("utf-8"))
        if not isinstance(payload, dict):
            raise RuntimeError("Daily manager lock payload is not an object.")
        metadata = payload.get("metadata")
        if not isinstance(metadata, dict):
            raise RuntimeError("Daily manager lock metadata is missing.")
        if str(metadata.get("cycle") or "") != "1688_stop_sale_daily_manager":
            raise RuntimeError("Lock does not belong to the daily stop-sale manager.")
        if str(metadata.get("manager_run_id") or "") != manager_run_id:
            raise RuntimeError("Daily manager lock manager_run_id does not match.")
        if _lock_fingerprint(payload) != expected_fingerprint:
            raise RuntimeError("Daily manager lock snapshot changed during recovery.")

        disposition = FileDispositionInfo(True)
        if not kernel32.SetFileInformationByHandle(
            handle,
            file_disposition_info,
            ctypes.byref(disposition),
            ctypes.sizeof(disposition),
        ):
            raise OSError(ctypes.get_last_error(), "Unable to atomically release manager lock")
    finally:
        kernel32.CloseHandle(handle)


def _child_pattern(manager_run_id: str) -> re.Pattern[str]:
    return re.compile(
        rf"^{re.escape(manager_run_id)}_s\d{{2}}_b\d{{3}}(?:_a\d{{2}})?$"
    )


def _discover_child_ids(manager_run_id: str, manager_dir: Path) -> set[str]:
    pattern = _child_pattern(manager_run_id)
    child_ids: set[str] = set()
    for path in manager_dir.rglob("*"):
        if not path.is_file():
            continue
        for candidate in (path.stem, path.name):
            for match in re.finditer(
                rf"{re.escape(manager_run_id)}_s\d{{2}}_b\d{{3}}(?:_a\d{{2}})?",
                candidate,
            ):
                child_id = match.group(0)
                if pattern.fullmatch(child_id):
                    child_ids.add(child_id)
    pipeline_dir = PROJECT_ROOT / "logs" / "sku_offline" / "pipelines"
    for path in pipeline_dir.glob(f"{manager_run_id}_s*.summary.json"):
        child_id = path.name.removesuffix(".summary.json")
        if pattern.fullmatch(child_id):
            child_ids.add(child_id)
    return child_ids


def _query_child_runs(shared_runtime_root: Path, manager_run_id: str) -> list[dict[str, Any]]:
    config = resolve_stop_sale_app_config(shared_runtime_root)
    run_table = f"[{config.schema}].[ali1688_stop_sale_run]"
    with connect_app_database(config) as connection:
        rows = connection.cursor().execute(
            f"""
            SELECT run_id, status, finished_at, summary_path,
                   offline_report_path, jushuitan_report_path
            FROM {run_table}
            WHERE run_id LIKE ?
            ORDER BY run_id
            """,
            (manager_run_id + "[_]s%",),
        ).fetchall()
    pattern = _child_pattern(manager_run_id)
    return [
        {
            "run_id": str(row[0] or ""),
            "status": str(row[1] or ""),
            "finished_at": row[2],
            "summary_path": str(row[3] or ""),
            "offline_report_path": str(row[4] or ""),
            "jushuitan_report_path": str(row[5] or ""),
        }
        for row in rows
        if pattern.fullmatch(str(row[0] or ""))
    ]


def _load_child_summary(run_id: str, configured_path: str) -> tuple[Path, dict[str, Any]]:
    candidates = []
    if configured_path:
        candidates.append(Path(configured_path))
    candidates.append(
        PROJECT_ROOT / "logs" / "sku_offline" / "pipelines" / f"{run_id}.summary.json"
    )
    for path in candidates:
        if not path.is_absolute():
            path = PROJECT_ROOT / path
        if not path.is_file():
            continue
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise RuntimeError(f"Child summary is not an object: {path}")
        return path.resolve(), payload
    raise FileNotFoundError(f"Child summary does not exist: {run_id}")


def _timestamp_utc(value: Any, *, naive_is_local: bool) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    else:
        text = str(value or "").strip()
        if not text:
            raise RuntimeError("Child finished_at value is empty.")
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError as exc:
            raise RuntimeError(f"Unsupported child finished_at value: {text!r}") from exc
    if parsed.tzinfo is None:
        parsed = parsed.astimezone() if naive_is_local else parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _validate_children(
    manager_run_id: str,
    observed_child_ids: set[str],
    child_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    by_id = {str(row["run_id"]): row for row in child_rows}
    database_ids = set(by_id)
    if observed_child_ids != database_ids:
        raise RuntimeError(
            "Daily manager child scope mismatch: "
            f"artifact_only={sorted(observed_child_ids - database_ids)}, "
            f"database_only={sorted(database_ids - observed_child_ids)}"
        )
    validated: list[dict[str, Any]] = []
    for run_id in sorted(database_ids):
        row = by_id[run_id]
        status = str(row.get("status") or "")
        if status not in TERMINAL_CHILD_STATUSES or row.get("finished_at") is None:
            raise RuntimeError(
                f"Child run is not terminal: run_id={run_id}, status={status!r}, "
                f"finished_at={row.get('finished_at')!r}"
            )
        summary_path, summary = _load_child_summary(run_id, str(row.get("summary_path") or ""))
        if str(summary.get("run_id") or "") != run_id:
            raise RuntimeError(f"Child summary run_id mismatch: {summary_path}")
        if str(summary.get("audit_status") or "") != status:
            raise RuntimeError(
                f"Child summary status mismatch: run_id={run_id}, "
                f"database={status!r}, summary={summary.get('audit_status')!r}"
            )
        if not str(summary.get("finished_at") or "").strip():
            raise RuntimeError(f"Child summary has no finished_at: {summary_path}")
        summary_finished_at = _timestamp_utc(
            summary.get("finished_at"), naive_is_local=True
        )
        database_finished_at = _timestamp_utc(
            row.get("finished_at"), naive_is_local=False
        )
        if abs((summary_finished_at - database_finished_at).total_seconds()) > 300:
            raise RuntimeError(
                f"Child summary finished_at mismatch: run_id={run_id}, "
                f"database_utc={database_finished_at.isoformat()!r}, "
                f"summary_utc={summary_finished_at.isoformat()!r}"
            )
        validated.append(
            {
                "run_id": run_id,
                "status": status,
                "finished_at": database_finished_at.isoformat(timespec="seconds"),
                "summary_finished_at": summary_finished_at.isoformat(timespec="seconds"),
                "summary_path": str(summary_path),
                "offline_report_path": str(row.get("offline_report_path") or ""),
                "jushuitan_report_path": str(row.get("jushuitan_report_path") or ""),
            }
        )
    return validated


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + f".{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def _load_existing_recovery_summary(
    summary_path: Path,
    *,
    manager_run_id: str,
    manager_dir: Path,
    lock_path: Path,
    reason: str,
    lock_payload: dict[str, Any],
    child_runs: list[dict[str, Any]],
) -> dict[str, Any]:
    payload = json.loads(summary_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise RuntimeError("Existing manager recovery summary is not an object.")
    expected = {
        "manager_run_id": manager_run_id,
        "error_type": "InterruptedDailyManagerProcess",
        "error_message": reason,
        "manager_dir": str(manager_dir),
        "shared_lock_path": str(lock_path),
        "lock_snapshot_sha256": _lock_fingerprint(lock_payload),
        "child_runs": child_runs,
    }
    changed = [name for name, value in expected.items() if payload.get(name) != value]
    if changed:
        raise RuntimeError(
            "Existing manager recovery summary preconditions changed: " + ",".join(changed)
        )
    if bool(payload.get("lock_removed")):
        raise RuntimeError("Daily manager recovery was already finalized.")
    payload["recovery_resumed"] = True
    payload["recovery_status"] = "summary_written_pending_lock_release"
    payload.pop("lock_release_error", None)
    return payload


def recover(args: argparse.Namespace) -> dict[str, Any]:
    if not args.yes:
        raise ValueError("Daily manager recovery requires --yes")
    manager_run_id = str(args.manager_run_id or "").strip()
    reason = str(args.reason or "").strip()
    if not MANAGER_ID_PATTERN.fullmatch(manager_run_id):
        raise ValueError("--manager-run-id contains unsupported characters")
    if not reason:
        raise ValueError("--reason must not be empty")

    manager_dir = Path(args.manager_dir).resolve()
    if not manager_dir.is_dir() or manager_dir.name != manager_run_id:
        raise RuntimeError("Manager directory must exist and its name must match manager_run_id.")
    summary_path = manager_dir / "summary.json"

    runtime_root = Path(args.shared_runtime_root).resolve()
    lock_path = (
        Path(args.shared_lock_path).resolve()
        if str(args.shared_lock_path or "").strip()
        else runtime_root / "artifacts" / "locks" / "ali1688_stop_sale_daily.lock"
    )
    lock_payload = _load_manager_lock(lock_path, manager_run_id)
    observed_child_ids = _discover_child_ids(manager_run_id, manager_dir)
    child_rows = _query_child_runs(runtime_root, manager_run_id)
    child_runs = _validate_children(manager_run_id, observed_child_ids, child_rows)

    token = str(lock_payload["token"])
    lock_fingerprint = _lock_fingerprint(lock_payload)
    if summary_path.exists():
        summary = _load_existing_recovery_summary(
            summary_path,
            manager_run_id=manager_run_id,
            manager_dir=manager_dir,
            lock_path=lock_path,
            reason=reason,
            lock_payload=lock_payload,
            child_runs=child_runs,
        )
    else:
        summary = {
            "manager_run_id": manager_run_id,
            "status": "failed",
            "error_type": "InterruptedDailyManagerProcess",
            "error_message": reason,
            "recovered_at": datetime.now().isoformat(timespec="seconds"),
            "manager_dir": str(manager_dir),
            "shared_lock_path": str(lock_path),
            "lock_owner_pid": int(lock_payload.get("pid") or 0),
            "lock_token_sha256": hashlib.sha256(token.encode("utf-8")).hexdigest(),
            "lock_snapshot_sha256": lock_fingerprint,
            "child_run_count": len(child_runs),
            "child_status_counts": {
                status: sum(1 for row in child_runs if row["status"] == status)
                for status in sorted({row["status"] for row in child_runs})
            },
            "child_runs": child_runs,
            "notification_sent": False,
            "lock_removed": False,
            "recovery_resumed": False,
            "recovery_status": "summary_written_pending_lock_release",
        }
        _write_json_atomic(summary_path, summary)
        summary["notification_sent"] = send_manager_notification(
            summary,
            runtime_root,
            bool(args.no_notify),
        )
    _write_json_atomic(summary_path, summary)

    try:
        _remove_windows_lock_if_unchanged(
            lock_path,
            manager_run_id,
            lock_fingerprint,
        )
    except Exception as exc:
        summary["recovery_status"] = "blocked_lock_revalidation"
        summary["lock_release_error"] = f"{type(exc).__name__}: {exc}"
        _write_json_atomic(summary_path, summary)
        raise

    summary["lock_removed"] = True
    summary["recovery_status"] = "finalized"
    summary.pop("lock_release_error", None)
    _write_json_atomic(summary_path, summary)
    _write_json_atomic(manager_dir.parent / "latest.summary.json", summary)
    return summary


def main() -> int:
    result = recover(build_argument_parser().parse_args())
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
