from __future__ import annotations

import argparse
import ctypes
import errno
import hashlib
import json
import os
from pathlib import Path
import secrets
import socket
import sys
from datetime import datetime, timezone
from typing import Any, Callable, Mapping


PROJECT_ROOT = Path(__file__).resolve().parents[1]
RPA_ROOT = PROJECT_ROOT / "rpa"
if str(RPA_ROOT) not in sys.path:
    sys.path.insert(0, str(RPA_ROOT))

from stop_sale_audit import connect_app_database, resolve_stop_sale_app_config


ACTIVE_REQUEST_STATUSES = ("waiting", "acquiring", "running")
ACTIVE_CRAWLER_STATUSES = (
    "claimed",
    "preflight",
    "running",
    "persisted",
    "validating",
)
SNAPSHOT_VERSION = 4
STOP_SALE_TASK_TYPE = "stop_sale"
RECOVERABLE_RESOURCE_TYPES = frozenset(("account", "browser_slot"))
MAX_BROWSER_SLOT_NUMBER = 3
RECOVERY_MODE_REQUEST_AND_RESOURCES = "request_and_resources"
RECOVERY_MODE_REQUEST_ONLY = "request_only"
RECOVERY_MODE_INVALID_RESOURCE_SET = "invalid_resource_set"
RECOVERY_PROCEDURES = (
    "usp_ali1688_runtime_lease_recover_expired",
    "usp_ali1688_runtime_request_recover_expired",
    "usp_ali1688_runtime_request_complete",
)
REQUIRED_REQUEST_FIELDS = (
    "request_key",
    "account_key",
    "task_type",
    "run_id",
    "owner_token",
    "hostname",
    "pid",
    "status",
    "expires_at",
    "completed_at",
)
REQUIRED_RESOURCE_FIELDS = (
    "resource_type",
    "resource_key",
    "account_key",
    "owner_token",
    "fencing_token",
    "task_type",
    "run_id",
    "hostname",
    "pid",
    "expires_at",
)


class RecoveryGateError(RuntimeError):
    """The exact stale-resource recovery contract is not satisfied."""


def _json_default(value: Any) -> str:
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def _parse_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        result = value
    else:
        text = str(value or "").strip()
        if not text:
            return None
        try:
            result = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return None
    if result.tzinfo is None:
        return result.replace(tzinfo=timezone.utc)
    return result.astimezone(timezone.utc)


def _is_process_running(pid: int) -> bool:
    """Check only the stale owner PID; never terminate or signal it."""
    if int(pid or 0) <= 0:
        return False
    if os.name == "nt":
        handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, int(pid))  # type: ignore[attr-defined]
        if not handle:
            # Access denied means the process is not safely proven dead.  A
            # recovery tool must fail closed in that case.
            try:
                last_error = int(ctypes.windll.kernel32.GetLastError())  # type: ignore[attr-defined]
            except Exception:
                last_error = 0
            return last_error == 5  # ERROR_ACCESS_DENIED
        ctypes.windll.kernel32.CloseHandle(handle)  # type: ignore[attr-defined]
        return True
    try:
        os.kill(int(pid), 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError as exc:
        return exc.errno != errno.ESRCH
    return True


def _fetch_rows(cursor: Any, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    cursor.execute(sql, params)
    columns = [column[0] for column in cursor.description]
    return [dict(zip(columns, row)) for row in cursor.fetchall()]


def _fetch_one(cursor: Any, sql: str, params: tuple[Any, ...] = ()) -> Any:
    cursor.execute(sql, params)
    return cursor.fetchone()


def _procedure_names(cursor: Any) -> list[str]:
    placeholders = ", ".join("?" for _ in RECOVERY_PROCEDURES)
    rows = _fetch_rows(
        cursor,
        f"""
        SELECT name
        FROM sys.procedures
        WHERE schema_id = SCHEMA_ID('app')
          AND name IN ({placeholders})
        ORDER BY name
        """,
        RECOVERY_PROCEDURES,
    )
    return [str(row["name"]) for row in rows]


def _same_text(left: Any, right: Any) -> bool:
    return str(left or "").strip() == str(right or "").strip()


def _resource_key_matches_scope(
    row: Mapping[str, Any], *, account_key: str, hostname: str
) -> bool:
    resource_type = str(row.get("resource_type") or "").strip()
    resource_key = str(row.get("resource_key") or "").strip()
    if resource_type == "account":
        return resource_key == account_key
    if resource_type != "browser_slot":
        return False
    prefix = f"{hostname}:"
    if not resource_key.startswith(prefix):
        return False
    slot_number = resource_key[len(prefix) :]
    return slot_number.isdigit() and 1 <= int(slot_number) <= MAX_BROWSER_SLOT_NUMBER


def _recovery_mode(resources: list[Mapping[str, Any]]) -> str:
    if not resources:
        return RECOVERY_MODE_REQUEST_ONLY
    resource_types = [str(row.get("resource_type") or "").strip() for row in resources]
    if (
        len(resource_types) == 2
        and resource_types.count("account") == 1
        and resource_types.count("browser_slot") == 1
    ):
        return RECOVERY_MODE_REQUEST_AND_RESOURCES
    return RECOVERY_MODE_INVALID_RESOURCE_SET


def _snapshot_fingerprint(snapshot: Mapping[str, Any]) -> str:
    request = dict(snapshot.get("request") or {})
    resources = [
        {
            key: row.get(key)
            for key in REQUIRED_RESOURCE_FIELDS
        }
        for row in snapshot.get("resources", [])
    ]
    resources.sort(key=lambda row: (str(row["resource_type"]), str(row["resource_key"])))
    payload = {
        "snapshot_version": snapshot.get("snapshot_version"),
        "artifact_type": snapshot.get("artifact_type"),
        "hostname": snapshot.get("hostname"),
        "account_key": snapshot.get("account_key"),
        "run_id": snapshot.get("run_id"),
        "request_key": snapshot.get("request_key"),
        "task_type": snapshot.get("task_type"),
        "recovery_mode": snapshot.get("recovery_mode"),
        "request": {
            key: request.get(key)
            for key in REQUIRED_REQUEST_FIELDS
        },
        "resources": resources,
        "foreign_account_requests": [
            {
                key: row.get(key)
                for key in ("request_key", "owner_token", "run_id", "status", "pid")
            }
            for row in snapshot.get("foreign_account_requests", [])
        ],
        "foreign_account_leases": [
            {
                key: row.get(key)
                for key in ("resource_type", "resource_key", "owner_token", "run_id", "pid")
            }
            for row in snapshot.get("foreign_account_leases", [])
        ],
        "active_crawler_tasks": snapshot.get("active_crawler_tasks", []),
        "active_crawler_attempts": snapshot.get("active_crawler_attempts", []),
        "active_stop_sale_runs": snapshot.get("active_stop_sale_runs", []),
        "foreign_runtime_leases": snapshot.get("foreign_runtime_leases", []),
        "procedures": sorted(str(name) for name in snapshot.get("procedures", [])),
        "pid_live": snapshot.get("pid_live", {}),
        "eligibility": snapshot.get("eligibility", {}),
    }
    encoded = json.dumps(payload, ensure_ascii=True, sort_keys=True, default=_json_default).encode(
        "utf-8"
    )
    return hashlib.sha256(encoded).hexdigest()


def _eligibility(
    snapshot: Mapping[str, Any],
    *,
    expected_hostname: str,
    process_checker: Callable[[int], bool],
) -> tuple[bool, list[str]]:
    reasons: list[str] = []
    request = dict(snapshot.get("request") or {})
    resources = [dict(row) for row in snapshot.get("resources", [])]
    declared_recovery_mode = str(snapshot.get("recovery_mode") or "").strip()
    derived_recovery_mode = _recovery_mode(resources)
    server_now = _parse_datetime(snapshot.get("server_now"))
    if server_now is None:
        reasons.append("server_now_missing")
    if len(request) == 0:
        reasons.append("target_request_missing")
    if request and not _same_text(request.get("request_key"), snapshot.get("request_key")):
        reasons.append("request_key_mismatch")
    if request and not _same_text(request.get("account_key"), snapshot.get("account_key")):
        reasons.append("request_account_mismatch")
    if request and not _same_text(request.get("run_id"), snapshot.get("run_id")):
        reasons.append("request_run_id_mismatch")
    if request and str(request.get("task_type") or "").strip() != STOP_SALE_TASK_TYPE:
        reasons.append("request_task_type_mismatch")
    if request and str(request.get("status") or "") not in ACTIVE_REQUEST_STATUSES:
        reasons.append("request_not_active")
    if request and request.get("completed_at") is not None:
        reasons.append("request_already_completed")
    if request and not str(request.get("owner_token") or "").strip():
        reasons.append("request_owner_token_missing")
    if request and not _same_text(request.get("hostname"), expected_hostname):
        reasons.append("request_hostname_mismatch")
    if request:
        expires_at = _parse_datetime(request.get("expires_at"))
        if server_now is not None and (expires_at is None or expires_at > server_now):
            reasons.append("request_not_expired")
        pid = int(request.get("pid") or 0)
        if pid <= 0:
            reasons.append("request_owner_pid_invalid")
        elif process_checker(pid):
            reasons.append("request_owner_pid_is_alive")

    resource_types = {str(row.get("resource_type") or "") for row in resources}
    if declared_recovery_mode != derived_recovery_mode:
        reasons.append("recovery_mode_mismatch")
    if derived_recovery_mode == RECOVERY_MODE_INVALID_RESOURCE_SET:
        reasons.append("target_resource_set_incomplete")
    if resource_types - RECOVERABLE_RESOURCE_TYPES:
        reasons.append("unknown_resource_type")
    if resources and sum(row.get("resource_type") == "account" for row in resources) != 1:
        reasons.append("account_lease_count_invalid")
    if resources and sum(row.get("resource_type") == "browser_slot" for row in resources) != 1:
        reasons.append("browser_slot_lease_count_invalid")
    request_owner = str(request.get("owner_token") or "")
    request_run = str(request.get("run_id") or "")
    request_account = str(request.get("account_key") or "")
    for row in resources:
        if not str(row.get("owner_token") or "") or str(row.get("owner_token")) != request_owner:
            reasons.append("resource_owner_mismatch")
        if not _same_text(row.get("run_id"), request_run):
            reasons.append("resource_run_id_mismatch")
        if not _same_text(row.get("account_key"), request_account):
            reasons.append("resource_account_mismatch")
        if not _resource_key_matches_scope(
            row, account_key=request_account, hostname=expected_hostname
        ):
            reasons.append("resource_key_scope_mismatch")
        if not _same_text(row.get("hostname"), expected_hostname):
            reasons.append("resource_hostname_mismatch")
        if int(row.get("pid") or 0) != int(request.get("pid") or 0):
            reasons.append("resource_pid_mismatch")
        expires_at = _parse_datetime(row.get("expires_at"))
        if server_now is not None and (expires_at is None or expires_at > server_now):
            reasons.append("resource_not_expired")
        if int(row.get("fencing_token") or 0) <= 0:
            reasons.append("resource_fencing_token_invalid")
        if str(row.get("task_type") or "").strip() != STOP_SALE_TASK_TYPE:
            reasons.append("resource_task_type_mismatch")

    if snapshot.get("foreign_account_requests"):
        reasons.append("foreign_account_request_present")
    if snapshot.get("foreign_account_leases"):
        reasons.append("foreign_account_lease_present")
    if snapshot.get("foreign_runtime_leases"):
        reasons.append("foreign_runtime_lease_present")
    if snapshot.get("active_crawler_tasks"):
        reasons.append("active_crawler_task_present")
    if snapshot.get("active_crawler_attempts"):
        reasons.append("active_crawler_attempt_present")
    if snapshot.get("active_stop_sale_runs"):
        reasons.append("active_stop_sale_run_present")
    if set(snapshot.get("procedures") or []) != set(RECOVERY_PROCEDURES):
        reasons.append("recovery_procedure_missing")
    return not reasons, sorted(set(reasons))


class RuntimeRecoveryRepository:
    """Read the shared state and mutate it only through official CAS procedures."""

    def __init__(self, config: Any, *, connect: Callable[[Any], Any] = connect_app_database) -> None:
        self.config = config
        self._connect = connect

    def query_snapshot(
        self,
        *,
        account_key: str,
        run_id: str,
        request_key: str,
        hostname: str,
        process_checker: Callable[[int], bool] = _is_process_running,
    ) -> dict[str, Any]:
        with self._connect(self.config) as connection:
            cursor = connection.cursor()
            server_now = _fetch_one(cursor, "SELECT SYSUTCDATETIME()")[0]
            requests = _fetch_rows(
                cursor,
                """
                SELECT request_sequence, request_key, account_key, task_type, priority,
                       status, run_id, owner_token, hostname, pid, heartbeat_at,
                       expires_at, completed_at, updated_at
                FROM app.ali1688_runtime_request
                WHERE request_key = ?
                """,
                (request_key,),
            )
            account_requests = _fetch_rows(
                cursor,
                """
                SELECT request_sequence, request_key, account_key, task_type, priority,
                       status, run_id, owner_token, hostname, pid, heartbeat_at,
                       expires_at, completed_at, updated_at
                FROM app.ali1688_runtime_request
                WHERE account_key = ? AND status IN (?, ?, ?) AND completed_at IS NULL
                """,
                (account_key, *ACTIVE_REQUEST_STATUSES),
            )
            leases = _fetch_rows(
                cursor,
                """
                SELECT resource_type, resource_key, account_key, owner_token,
                       fencing_token, task_type, run_id, hostname, pid, profile_ref,
                       heartbeat_at, expires_at, updated_at
                FROM app.ali1688_runtime_lease
                WHERE owner_token IS NOT NULL
                  AND
                  (
                      account_key = ? OR run_id = ? OR owner_token =
                      (
                          SELECT owner_token
                          FROM app.ali1688_runtime_request
                          WHERE request_key = ?
                      )
                  )
                """,
                (account_key, run_id, request_key),
            )
            account_leases = _fetch_rows(
                cursor,
                """
                SELECT resource_type, resource_key, account_key, owner_token,
                       fencing_token, task_type, run_id, hostname, pid, profile_ref,
                       heartbeat_at, expires_at, updated_at
                FROM app.ali1688_runtime_lease
                WHERE resource_type = 'account' AND resource_key = ?
                  AND owner_token IS NOT NULL
                """,
                (account_key,),
            )
            active_tasks = _fetch_rows(
                cursor,
                """
                SELECT task_id, account_key, status
                FROM app.crawler_task
                WHERE account_key = ? AND status IN (?, ?, ?, ?, ?)
                """,
                (account_key, *ACTIVE_CRAWLER_STATUSES),
            )
            active_attempts = _fetch_rows(
                cursor,
                """
                SELECT attempt.attempt_id, attempt.task_id, attempt.status,
                       attempt.worker_id, attempt.lease_expires_at
                FROM app.crawler_task_attempt AS attempt
                INNER JOIN app.crawler_task AS task ON task.id = attempt.task_db_id
                WHERE task.account_key = ? AND attempt.status IN (?, ?, ?, ?, ?)
                """,
                (account_key, *ACTIVE_CRAWLER_STATUSES),
            )
            active_runs = _fetch_rows(
                cursor,
                """
                SELECT run_id, status, started_at, finished_at, updated_at
                FROM app.ali1688_stop_sale_run
                WHERE run_id = ? AND status = 'running' AND finished_at IS NULL
                """,
                (run_id,),
            )
            procedures = _procedure_names(cursor)

        request = requests[0] if len(requests) == 1 else {}
        owner = str(request.get("owner_token") or "")
        target_resources = [
            row
            for row in leases
            if owner and str(row.get("owner_token") or "") == owner
            and _same_text(row.get("run_id"), run_id)
        ]
        foreign_requests = [
            row
            for row in account_requests
            if not _same_text(row.get("request_key"), request_key)
        ]
        foreign_leases = [
            row
            for row in account_leases
            if not owner or str(row.get("owner_token") or "") != owner
        ]
        pids = {
            int(row.get("pid") or 0)
            for row in [request, *target_resources]
            if int(row.get("pid") or 0) > 0
        }
        snapshot: dict[str, Any] = {
            "snapshot_version": SNAPSHOT_VERSION,
            "artifact_type": "stop_sale_runtime_recovery_preview",
            "server_now": server_now,
            "hostname": hostname,
            "account_key": account_key,
            "run_id": run_id,
            "request_key": request_key,
            "task_type": STOP_SALE_TASK_TYPE,
            "recovery_mode": _recovery_mode(target_resources),
            "request": request,
            "resources": target_resources,
            "foreign_account_requests": foreign_requests,
            "foreign_account_leases": foreign_leases,
            "foreign_runtime_leases": [
                row
                for row in leases
                if row not in target_resources
            ],
            "active_crawler_tasks": active_tasks,
            "active_crawler_attempts": active_attempts,
            "active_stop_sale_runs": active_runs,
            "procedures": procedures,
            "pid_live": {str(pid): bool(process_checker(pid)) for pid in sorted(pids)},
        }
        eligible, reasons = _eligibility(
            snapshot,
            expected_hostname=hostname,
            process_checker=process_checker,
        )
        snapshot["eligibility"] = {"eligible": eligible, "reasons": reasons}
        snapshot["fingerprint"] = _snapshot_fingerprint(snapshot)
        return snapshot

    def recover_expired_runtime(
        self,
        *,
        resources: list[Mapping[str, Any]],
        account_key: str,
        run_id: str,
        request_key: str,
        expected_owner_token: str,
        expected_owner_pid: int,
        new_owner_token: str,
        hostname: str,
        pid: int,
        actor: str,
    ) -> list[dict[str, Any]]:
        """Recover optional resources and the request in one caller transaction."""
        mode = _recovery_mode(resources)
        if mode == RECOVERY_MODE_INVALID_RESOURCE_SET:
            raise RecoveryGateError("runtime recovery resource set is incomplete")
        with self._connect(self.config) as connection:
            cursor = connection.cursor()
            actions: list[dict[str, Any]] = []
            try:
                cursor.execute(
                    """
                    SELECT COUNT_BIG(*)
                    FROM app.ali1688_runtime_request WITH (UPDLOCK, HOLDLOCK)
                    WHERE request_key = ? AND account_key = ? AND task_type = N'stop_sale'
                      AND run_id = ? AND owner_token = ? AND hostname = ? AND pid = ?
                      AND status IN (N'waiting', N'acquiring', N'running')
                      AND completed_at IS NULL AND expires_at <= SYSUTCDATETIME()
                    """,
                    (
                        request_key,
                        account_key,
                        run_id,
                        expected_owner_token,
                        hostname,
                        int(expected_owner_pid),
                    ),
                )
                request_identity_row = cursor.fetchone()
                if request_identity_row is None:
                    raise RecoveryGateError("request identity recovery gate returned no row")
                request_identity_count = int(request_identity_row[0] or 0)
                actions.append(
                    {
                        "action": "verify_request_identity",
                        "request_key": request_key,
                        "status": "passed" if request_identity_count == 1 else "blocked",
                        "matching_row_count": request_identity_count,
                    }
                )
                if request_identity_count != 1:
                    raise RecoveryGateError(
                        f"request identity recovery gate failed: count={request_identity_count}"
                    )

                cursor.execute(
                    """
                    SELECT
                        (
                            SELECT COUNT_BIG(*)
                            FROM app.ali1688_runtime_request WITH (UPDLOCK, HOLDLOCK)
                            WHERE account_key = ? AND request_key <> ?
                              AND status IN (N'waiting', N'acquiring', N'running')
                              AND completed_at IS NULL
                        ) AS foreign_request_count,
                        (
                            SELECT COUNT_BIG(*)
                            FROM app.crawler_task WITH (UPDLOCK, HOLDLOCK)
                            WHERE account_key = ? AND status IN (?, ?, ?, ?, ?)
                        ) AS active_crawler_task_count,
                        (
                            SELECT COUNT_BIG(*)
                            FROM app.crawler_task_attempt AS attempt WITH (UPDLOCK, HOLDLOCK)
                            INNER JOIN app.crawler_task AS task WITH (HOLDLOCK)
                                ON task.id = attempt.task_db_id
                            WHERE task.account_key = ?
                              AND attempt.status IN (?, ?, ?, ?, ?)
                        ) AS active_crawler_attempt_count,
                        (
                            SELECT COUNT_BIG(*)
                            FROM app.ali1688_stop_sale_run WITH (UPDLOCK, HOLDLOCK)
                            WHERE run_id = ? AND status = N'running' AND finished_at IS NULL
                        ) AS active_stop_sale_run_count
                    """,
                    (
                        account_key,
                        request_key,
                        account_key,
                        *ACTIVE_CRAWLER_STATUSES,
                        account_key,
                        *ACTIVE_CRAWLER_STATUSES,
                        run_id,
                    ),
                )
                active_work_row = cursor.fetchone()
                if active_work_row is None:
                    raise RecoveryGateError("active work recovery gate returned no row")
                active_work_counts = {
                    "foreign_request_count": int(active_work_row[0] or 0),
                    "active_crawler_task_count": int(active_work_row[1] or 0),
                    "active_crawler_attempt_count": int(active_work_row[2] or 0),
                    "active_stop_sale_run_count": int(active_work_row[3] or 0),
                }
                active_work_total = sum(active_work_counts.values())
                actions.append(
                    {
                        "action": "verify_active_work_absent",
                        **active_work_counts,
                        "status": "passed" if active_work_total == 0 else "blocked",
                    }
                )
                if active_work_total != 0:
                    raise RecoveryGateError(
                        "active work recovery gate failed: "
                        + ",".join(
                            f"{name}={value}"
                            for name, value in active_work_counts.items()
                            if value
                        )
                    )

                for row in resources:
                    cursor.execute(
                        "EXEC app.usp_ali1688_runtime_lease_recover_expired ?, ?, ?, ?, ?, ?, ?;",
                        (
                            str(row["resource_type"]),
                            str(row["resource_key"]),
                            str(row["owner_token"]),
                            int(row["fencing_token"]),
                            hostname,
                            actor,
                            "stop_sale_recovery_pid_dead_no_active_work",
                        ),
                    )
                    result = cursor.fetchone()
                    if result is None:
                        raise RecoveryGateError("lease recovery procedure returned no row")
                    status = str(result[0] or "")
                    actions.append(
                        {
                            "action": "recover_lease",
                            "resource_type": row["resource_type"],
                            "resource_key": row["resource_key"],
                            "fencing_token": row["fencing_token"],
                            "status": status,
                        }
                    )
                    if status != "recovered":
                        raise RecoveryGateError(
                            f"lease recovery failed for {row['resource_type']}/{row['resource_key']}: {status}"
                        )

                cursor.execute(
                    """
                    SELECT COUNT_BIG(*)
                    FROM app.ali1688_runtime_lease WITH (UPDLOCK, HOLDLOCK)
                    WHERE owner_token IS NOT NULL
                      AND
                      (
                          account_key = ? OR run_id = ? OR owner_token = ?
                          OR (resource_type = N'account' AND resource_key = ?)
                      )
                    """,
                    (account_key, run_id, expected_owner_token, account_key),
                )
                related_lease_count_row = cursor.fetchone()
                if related_lease_count_row is None:
                    raise RecoveryGateError("related lease recovery gate returned no row")
                related_lease_count = int(related_lease_count_row[0] or 0)
                actions.append(
                    {
                        "action": "verify_related_leases_absent",
                        "recovery_mode": mode,
                        "related_lease_count": related_lease_count,
                        "status": "passed" if related_lease_count == 0 else "blocked",
                    }
                )
                if related_lease_count != 0:
                    raise RecoveryGateError(
                        f"related lease recovery gate failed: count={related_lease_count}"
                    )

                cursor.execute(
                    "EXEC app.usp_ali1688_runtime_request_recover_expired ?, ?, ?, ?, ?, ?, ?, ?;",
                    (
                        request_key,
                        expected_owner_token,
                        new_owner_token,
                        hostname,
                        int(pid),
                        actor,
                        f"stop_sale_{mode}_recovery_pid_dead_no_active_work",
                        120,
                    ),
                )
                result = cursor.fetchone()
                if result is None:
                    raise RecoveryGateError("request recovery procedure returned no row")
                request_status = str(result[0] or "")
                actions.append(
                    {"action": "recover_request", "request_key": request_key, "status": request_status}
                )
                if request_status != "recovered":
                    raise RecoveryGateError(f"request recovery failed: {request_status}")

                cursor.execute(
                    "EXEC app.usp_ali1688_runtime_request_complete ?, ?, ?;",
                    (request_key, new_owner_token, "cancelled"),
                )
                result = cursor.fetchone()
                if result is None:
                    raise RecoveryGateError("request completion procedure returned no row")
                completion_status = str(result[0] or "")
                actions.append(
                    {
                        "action": "complete_recovered_request",
                        "request_key": request_key,
                        "status": completion_status,
                        "terminal_status": "cancelled",
                    }
                )
                if completion_status != "completed":
                    raise RecoveryGateError(
                        f"recovered request completion failed: {completion_status}"
                    )

                cursor.execute(
                    """
                    SELECT
                        (
                            SELECT COUNT_BIG(*)
                            FROM app.ali1688_runtime_request WITH (UPDLOCK, HOLDLOCK)
                            WHERE request_key = ? AND account_key = ? AND task_type = N'stop_sale'
                              AND run_id = ? AND owner_token = ? AND status = N'cancelled'
                              AND completed_at IS NOT NULL
                        ) AS terminal_request_count,
                        (
                            SELECT COUNT_BIG(*)
                            FROM app.ali1688_runtime_lease WITH (UPDLOCK, HOLDLOCK)
                            WHERE owner_token IS NOT NULL
                              AND
                              (
                                  account_key = ? OR run_id = ? OR owner_token = ?
                                  OR (resource_type = N'account' AND resource_key = ?)
                              )
                        ) AS related_lease_count
                    """,
                    (
                        request_key,
                        account_key,
                        run_id,
                        new_owner_token,
                        account_key,
                        run_id,
                        expected_owner_token,
                        account_key,
                    ),
                )
                post_state_row = cursor.fetchone()
                if post_state_row is None:
                    raise RecoveryGateError("transactional post-state gate returned no row")
                terminal_request_count = int(post_state_row[0] or 0)
                post_related_lease_count = int(post_state_row[1] or 0)
                post_state_ok = terminal_request_count == 1 and post_related_lease_count == 0
                actions.append(
                    {
                        "action": "verify_transactional_post_state",
                        "terminal_request_count": terminal_request_count,
                        "related_lease_count": post_related_lease_count,
                        "status": "passed" if post_state_ok else "blocked",
                    }
                )
                if not post_state_ok:
                    raise RecoveryGateError(
                        "transactional post-state gate failed: "
                        f"terminal_request_count={terminal_request_count},"
                        f"related_lease_count={post_related_lease_count}"
                    )
                connection.commit()
            except BaseException:
                try:
                    connection.rollback()
                except Exception:
                    pass
                raise
        return actions

    def query_post_state(
        self,
        *,
        account_key: str,
        run_id: str,
        request_key: str,
        previous_owner_token: str,
    ) -> dict[str, Any]:
        with self._connect(self.config) as connection:
            cursor = connection.cursor()
            request = _fetch_rows(
                cursor,
                """
                SELECT request_key, account_key, task_type, status, owner_token,
                       run_id, completed_at
                FROM app.ali1688_runtime_request
                WHERE request_key = ?
                """,
                (request_key,),
            )
            leases = _fetch_rows(
                cursor,
                """
                SELECT resource_type, resource_key, account_key, owner_token, run_id
                FROM app.ali1688_runtime_lease
                WHERE owner_token IS NOT NULL
                  AND
                  (
                      account_key = ? OR run_id = ?
                      OR (resource_type = N'account' AND resource_key = ?)
                      OR owner_token =
                      (
                          SELECT owner_token
                          FROM app.ali1688_runtime_request
                          WHERE request_key = ?
                      )
                      OR owner_token = ?
                  )
                """,
                (account_key, run_id, account_key, request_key, previous_owner_token),
            )
        return {"request": request, "leases": leases}


def validate_snapshot(snapshot: Mapping[str, Any]) -> None:
    if int(snapshot.get("snapshot_version") or 0) != SNAPSHOT_VERSION:
        raise RecoveryGateError("unsupported snapshot_version")
    if snapshot.get("artifact_type") != "stop_sale_runtime_recovery_preview":
        raise RecoveryGateError("unsupported snapshot artifact_type")
    for field in ("hostname", "account_key", "run_id", "request_key"):
        if not str(snapshot.get(field) or "").strip():
            raise RecoveryGateError(f"snapshot {field} is missing")
    if snapshot.get("task_type") != STOP_SALE_TASK_TYPE:
        raise RecoveryGateError("snapshot task_type is not stop_sale")
    resources = list(snapshot.get("resources") or [])
    recovery_mode = str(snapshot.get("recovery_mode") or "").strip()
    if recovery_mode != _recovery_mode(resources):
        raise RecoveryGateError("snapshot recovery_mode does not match resources")
    if recovery_mode == RECOVERY_MODE_INVALID_RESOURCE_SET:
        raise RecoveryGateError("snapshot resource set is incomplete")
    if not snapshot.get("eligibility", {}).get("eligible"):
        raise RecoveryGateError("snapshot was not eligible for recovery")
    request = dict(snapshot.get("request") or {})
    missing_request = [field for field in REQUIRED_REQUEST_FIELDS if field not in request]
    if missing_request:
        raise RecoveryGateError(f"snapshot request fields missing: {','.join(missing_request)}")
    if request.get("request_key") != snapshot.get("request_key"):
        raise RecoveryGateError("snapshot request_key mismatch")
    if request.get("account_key") != snapshot.get("account_key"):
        raise RecoveryGateError("snapshot account_key mismatch")
    if request.get("run_id") != snapshot.get("run_id"):
        raise RecoveryGateError("snapshot run_id mismatch")
    if request.get("task_type") != STOP_SALE_TASK_TYPE:
        raise RecoveryGateError("snapshot request task_type is not stop_sale")
    if request.get("owner_token") in (None, ""):
        raise RecoveryGateError("snapshot request owner_token is missing")
    for index, row in enumerate(resources):
        missing = [field for field in REQUIRED_RESOURCE_FIELDS if field not in row]
        if missing:
            raise RecoveryGateError(
                f"snapshot resource[{index}] fields missing: {','.join(missing)}"
            )
        if row.get("task_type") != STOP_SALE_TASK_TYPE:
            raise RecoveryGateError(f"snapshot resource[{index}] task_type is not stop_sale")
        if row.get("account_key") != snapshot.get("account_key"):
            raise RecoveryGateError(f"snapshot resource[{index}] account_key mismatch")
        if row.get("run_id") != snapshot.get("run_id"):
            raise RecoveryGateError(f"snapshot resource[{index}] run_id mismatch")
        if not _resource_key_matches_scope(
            row,
            account_key=str(snapshot["account_key"]),
            hostname=str(snapshot["hostname"]),
        ):
            raise RecoveryGateError(f"snapshot resource[{index}] resource_key scope mismatch")
        if row.get("owner_token") != request.get("owner_token"):
            raise RecoveryGateError(f"snapshot resource[{index}] owner_token mismatch")
        if row.get("hostname") != snapshot.get("hostname"):
            raise RecoveryGateError(f"snapshot resource[{index}] hostname mismatch")
        if int(row.get("pid") or 0) != int(request.get("pid") or 0):
            raise RecoveryGateError(f"snapshot resource[{index}] pid mismatch")
    resource_keys = [
        (str(row.get("resource_type")), str(row.get("resource_key"))) for row in resources
    ]
    if len(resource_keys) != len(set(resource_keys)):
        raise RecoveryGateError("snapshot resources contain duplicates")
    if not str(snapshot.get("fingerprint") or ""):
        raise RecoveryGateError("snapshot fingerprint is missing")
    if snapshot["fingerprint"] != _snapshot_fingerprint(snapshot):
        raise RecoveryGateError("snapshot fingerprint mismatch")


def apply_snapshot(
    snapshot: Mapping[str, Any],
    repository: RuntimeRecoveryRepository,
    *,
    hostname: str,
    process_checker: Callable[[int], bool] = _is_process_running,
    yes: bool = False,
) -> dict[str, Any]:
    validate_snapshot(snapshot)
    if not yes:
        raise RecoveryGateError("apply requires --yes")
    if str(snapshot.get("hostname") or "") != hostname:
        raise RecoveryGateError("snapshot hostname mismatch")
    fresh = repository.query_snapshot(
        account_key=str(snapshot["account_key"]),
        run_id=str(snapshot["run_id"]),
        request_key=str(snapshot["request_key"]),
        hostname=hostname,
        process_checker=process_checker,
    )
    if fresh.get("fingerprint") != snapshot.get("fingerprint"):
        raise RecoveryGateError("fresh runtime snapshot drifted")
    if not fresh.get("eligibility", {}).get("eligible"):
        reasons = fresh.get("eligibility", {}).get("reasons", [])
        raise RecoveryGateError("fresh recovery gate failed: " + ",".join(reasons))

    old_owner = str(snapshot["request"]["owner_token"])
    new_owner = secrets.token_hex(16)
    actor = "erp-stop-sale-runtime-recovery"
    actions: list[dict[str, Any]] = []
    resources = sorted(
        (dict(row) for row in snapshot["resources"]),
        key=lambda row: (
            0 if row["resource_type"] == "browser_slot" else 1,
            str(row["resource_key"]),
        ),
    )
    if not hasattr(repository, "recover_expired_runtime"):
        raise RecoveryGateError("repository lacks atomic runtime recovery contract")
    actions = repository.recover_expired_runtime(
        resources=resources,
        account_key=str(snapshot["account_key"]),
        run_id=str(snapshot["run_id"]),
        request_key=str(snapshot["request_key"]),
        expected_owner_token=old_owner,
        expected_owner_pid=int(snapshot["request"]["pid"]),
        new_owner_token=new_owner,
        hostname=hostname,
        pid=os.getpid(),
        actor=actor,
    )
    post_state = repository.query_post_state(
        account_key=str(snapshot["account_key"]),
        run_id=str(snapshot["run_id"]),
        request_key=str(snapshot["request_key"]),
        previous_owner_token=old_owner,
    )
    request_rows = list(post_state.get("request") or [])
    if len(request_rows) != 1:
        raise RecoveryGateError("post recovery request row count is not exactly one")
    request_row = request_rows[0]
    if request_row.get("request_key") != snapshot["request_key"]:
        raise RecoveryGateError("post recovery request_key mismatch")
    if request_row.get("run_id") != snapshot["run_id"]:
        raise RecoveryGateError("post recovery run_id mismatch")
    if request_row.get("account_key") != snapshot["account_key"]:
        raise RecoveryGateError("post recovery account_key mismatch")
    if request_row.get("task_type") != STOP_SALE_TASK_TYPE:
        raise RecoveryGateError("post recovery request task_type mismatch")
    if request_row.get("owner_token") != new_owner:
        raise RecoveryGateError("post recovery owner_token mismatch")
    if request_row.get("status") != "cancelled":
        raise RecoveryGateError("post recovery request is not cancelled")
    if request_row.get("completed_at") is None:
        raise RecoveryGateError("post recovery request is missing completed_at")
    if list(post_state.get("leases") or []):
        raise RecoveryGateError("post recovery still has a related runtime lease")
    return {
        "artifact_type": "stop_sale_runtime_recovery_apply",
        "snapshot_fingerprint": snapshot["fingerprint"],
        "account_key": snapshot["account_key"],
        "run_id": snapshot["run_id"],
        "request_key": snapshot["request_key"],
        "recovery_mode": snapshot["recovery_mode"],
        "old_owner_token": old_owner,
        "actions": actions,
        "post_state": post_state,
    }


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Recover one expired Stop-Sale runtime owner via CAS procedures.")
    parser.add_argument("--mode", choices=("preview", "apply"), default="preview")
    parser.add_argument("--account-key", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--request-key", required=True)
    parser.add_argument("--shared-runtime-root", required=True)
    parser.add_argument("--snapshot-file", default="")
    parser.add_argument("--output", default="")
    parser.add_argument("--hostname", default=socket.gethostname())
    parser.add_argument("--yes", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_argument_parser().parse_args(argv)
    output_path = Path(args.output).resolve() if args.output else None
    try:
        local_hostname = socket.gethostname().strip()
        if args.hostname.strip().casefold() != local_hostname.casefold():
            raise RecoveryGateError("CLI hostname does not match the local machine")
        config = resolve_stop_sale_app_config(Path(args.shared_runtime_root).resolve())
        repository = RuntimeRecoveryRepository(config)
        if args.mode == "preview":
            snapshot = repository.query_snapshot(
                account_key=args.account_key,
                run_id=args.run_id,
                request_key=args.request_key,
                hostname=args.hostname,
            )
            result: dict[str, Any] = snapshot
            exit_code = 0 if snapshot["eligibility"]["eligible"] else 2
        else:
            if not args.snapshot_file:
                raise RecoveryGateError("apply requires --snapshot-file")
            snapshot = json.loads(Path(args.snapshot_file).read_text(encoding="utf-8-sig"))
            if not isinstance(snapshot, Mapping):
                raise RecoveryGateError("snapshot must be a JSON object")
            for field, expected in (
                ("account_key", args.account_key),
                ("run_id", args.run_id),
                ("request_key", args.request_key),
            ):
                if str(snapshot.get(field) or "") != expected:
                    raise RecoveryGateError(f"snapshot {field} does not match CLI scope")
            result = apply_snapshot(snapshot, repository, hostname=args.hostname, yes=args.yes)
            exit_code = 0
    except Exception as exc:
        result = {
            "artifact_type": "stop_sale_runtime_recovery_error",
            "status": "blocked",
            "error_type": type(exc).__name__,
            "error": str(exc),
        }
        exit_code = 2
    serialized = json.dumps(result, ensure_ascii=False, indent=2, default=_json_default) + "\n"
    if output_path:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(serialized, encoding="utf-8")
    print(serialized, end="")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
