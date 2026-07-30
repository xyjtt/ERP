"""ERP client for the shared 1688 cross-project runtime lease protocol."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import hmac
import json
import os
from pathlib import Path
import socket
import subprocess
import threading
import time
from typing import Any, Callable, Mapping
import uuid

from stop_sale_audit import StopSaleAppConfig, connect_app_database


PROTOCOL_NAME = "ali1688-cross-project-lease"
PROTOCOL_VERSION = 1
WRITE_PRIORITY = 100
LEASE_TTL_SECONDS = 120
HEARTBEAT_INTERVAL_SECONDS = 20
HEARTBEAT_FAILURE_THRESHOLD = 2
LEASE_STOP_AFTER_LAST_SUCCESS_SECONDS = 40
OWNED_BROWSER_SHUTDOWN_DEADLINE_SECONDS = 20
MAX_BROWSER_ACCOUNTS = 3


class RuntimeProtocolError(RuntimeError):
    pass


class RuntimeResourceBusyError(RuntimeError):
    pass


class RuntimeLeaseLostError(RuntimeError):
    pass


@dataclass(frozen=True)
class RuntimeProtocolState:
    server_now: datetime
    minimum_version: int
    enforcement_enabled: bool
    max_parallel_browser_accounts: int


@dataclass(frozen=True)
class ExecutorBinding:
    account_key: str
    profile_ref: str
    cdp_port: int
    config_revision: str = ""
    config_hash: str = ""
    target_hostname: str = ""


@dataclass(frozen=True)
class ResourceLease:
    resource_type: str
    resource_key: str
    owner_token: str
    fencing_token: int
    server_now: datetime
    expires_at: datetime


@dataclass(frozen=True)
class LeaseAcquireResult:
    status: str
    reason_code: str
    lease: ResourceLease | None = None
    current_owner_token: str = ""
    current_fencing_token: int = 0
    current_hostname: str = ""
    current_pid: int = 0

    @property
    def acquired(self) -> bool:
        return self.status == "acquired" and self.lease is not None


def canonical_accounts_config_hash(payload: Mapping[str, Any]) -> str:
    canonical = dict(payload)
    canonical.pop("config_hash", None)
    serialized = json.dumps(
        canonical,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(serialized).hexdigest()


def resolve_external_config_root() -> Path:
    configured = str(os.getenv("YYDD_1688_CONFIG_ROOT", "")).strip()
    if configured:
        return Path(configured).resolve()
    return Path(os.getenv("PROGRAMDATA", r"C:\ProgramData")) / "YYDD" / "1688-crawler" / "config"


def resolve_executor_binding(
    account_key: str,
    *,
    fallback_profile_ref: str = "",
    fallback_cdp_port: int = 0,
    config_root: str | Path | None = None,
) -> ExecutorBinding:
    normalized_key = str(account_key or "").strip()
    if not normalized_key:
        raise RuntimeProtocolError("runtime_account_key_missing")
    root = Path(config_root).resolve() if config_root else resolve_external_config_root()
    path = root / "accounts.json"
    if not path.exists():
        raise RuntimeProtocolError(f"runtime_accounts_config_missing:{path}")
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(payload, dict) or not isinstance(payload.get("accounts"), list):
        raise RuntimeProtocolError("runtime_config_accounts_invalid")

    revision = str(payload.get("config_revision") or "").strip()
    declared_hash = str(payload.get("config_hash") or "").strip().lower()
    target_hostname = str(payload.get("target_hostname") or payload.get("hostname") or "").strip()
    metadata_present = bool(revision or declared_hash or target_hostname)
    if metadata_present:
        if not revision or not declared_hash or not target_hostname:
            raise RuntimeProtocolError("runtime_config_metadata_incomplete")
        if len(declared_hash) != 64 or any(char not in "0123456789abcdef" for char in declared_hash):
            raise RuntimeProtocolError("runtime_config_hash_invalid")
        computed_hash = canonical_accounts_config_hash(payload)
        if not hmac.compare_digest(declared_hash, computed_hash):
            raise RuntimeProtocolError("runtime_config_hash_mismatch")

    account = next(
        (
            item
            for item in payload["accounts"]
            if isinstance(item, Mapping) and str(item.get("account_key") or "").strip() == normalized_key
        ),
        None,
    )
    if account is None:
        raise RuntimeProtocolError(f"runtime_account_binding_missing:{normalized_key}")
    if account.get("enabled") is False:
        raise RuntimeProtocolError(f"runtime_account_disabled:{normalized_key}")
    profile_ref = str(
        account.get("profile_ref")
        or account.get("profile_key")
        or account.get("browser_profile_dir")
        or fallback_profile_ref
        or ""
    ).strip()
    cdp_port = int(account.get("cdp_port") or fallback_cdp_port or 0)
    if not profile_ref:
        raise RuntimeProtocolError(f"runtime_profile_ref_missing:{normalized_key}")
    if cdp_port <= 0:
        raise RuntimeProtocolError(f"runtime_cdp_port_missing:{normalized_key}")
    return ExecutorBinding(
        account_key=normalized_key,
        profile_ref=profile_ref,
        cdp_port=cdp_port,
        config_revision=revision,
        config_hash=declared_hash,
        target_hostname=target_hostname,
    )


def resolve_build_sha(repository_root: str | Path) -> str:
    configured = str(os.getenv("ERP_BUILD_SHA", "")).strip().lower()
    if configured:
        if len(configured) != 40 or any(char not in "0123456789abcdef" for char in configured):
            raise RuntimeProtocolError("ERP_BUILD_SHA must be a full 40-character commit SHA")
        return configured
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=Path(repository_root),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        timeout=15,
    )
    sha = str(result.stdout or "").strip().lower()
    if result.returncode != 0 or len(sha) != 40:
        raise RuntimeProtocolError("runtime_build_sha_unavailable")
    return sha


class RuntimeLeaseRepository:
    """Stored-procedure-only access to Crawler-owned runtime lease objects."""

    def __init__(
        self,
        config: StopSaleAppConfig,
        *,
        connect: Callable[[StopSaleAppConfig], Any] = connect_app_database,
    ) -> None:
        self.config = config
        self._connect = connect

    def assert_protocol(self, *, component: str, build_sha: str) -> RuntimeProtocolState:
        row = self._execute_row(
            "EXEC app.usp_ali1688_runtime_protocol_assert ?, ?, ?, ?;",
            (PROTOCOL_NAME, component, PROTOCOL_VERSION, build_sha),
        )
        if str(row[0] or "") != "ready":
            raise RuntimeProtocolError(str(row[1] or "runtime_protocol_rejected"))
        return RuntimeProtocolState(
            server_now=_as_utc_datetime(row[2]),
            minimum_version=int(row[3]),
            enforcement_enabled=bool(row[4]),
            max_parallel_browser_accounts=int(row[5]),
        )

    def assert_binding(
        self,
        binding: ExecutorBinding,
        *,
        hostname: str,
        require_match: bool,
    ) -> None:
        row = self._execute_row(
            "EXEC app.usp_ali1688_executor_binding_assert ?, ?, ?, ?, ?, ?, ?;",
            (
                hostname,
                binding.account_key,
                binding.profile_ref,
                binding.cdp_port,
                binding.config_revision,
                binding.config_hash,
                int(require_match),
            ),
        )
        status = str(row[0] or "")
        if status == "ready":
            return
        if status == "compatibility_unverified" and not require_match:
            return
        raise RuntimeProtocolError(str(row[1] or "runtime_config_revision_mismatch"))

    def register_request(
        self,
        *,
        request_key: str,
        account_key: str,
        task_type: str,
        priority: int,
        run_id: str,
        owner_token: str,
        hostname: str,
        pid: int,
        build_sha: str,
        ttl_seconds: int = LEASE_TTL_SECONDS,
    ) -> None:
        row = self._execute_row(
            "EXEC app.usp_ali1688_runtime_request_register ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?;",
            (
                PROTOCOL_NAME,
                PROTOCOL_VERSION,
                build_sha,
                request_key,
                account_key,
                task_type,
                priority,
                run_id,
                owner_token,
                hostname,
                pid,
                ttl_seconds,
            ),
        )
        if str(row[0] or "") != "registered":
            raise RuntimeResourceBusyError(str(row[1] or "runtime_request_not_registered"))

    def heartbeat_request(self, request_key: str, owner_token: str) -> None:
        row = self._execute_row(
            "EXEC app.usp_ali1688_runtime_request_heartbeat ?, ?, ?;",
            (request_key, owner_token, LEASE_TTL_SECONDS),
        )
        if str(row[0] or "") != "renewed":
            raise RuntimeLeaseLostError(str(row[1] or "runtime_request_lost"))

    def complete_request(self, request_key: str, owner_token: str, status: str) -> bool:
        row = self._execute_row(
            "EXEC app.usp_ali1688_runtime_request_complete ?, ?, ?;",
            (request_key, owner_token, status),
        )
        return str(row[0] or "") == "completed"

    def acquire(
        self,
        *,
        resource_type: str,
        resource_key: str,
        account_key: str,
        owner_token: str,
        task_type: str,
        run_id: str,
        hostname: str,
        pid: int,
        profile_ref: str,
        priority: int,
        build_sha: str,
        request_key: str = "",
    ) -> LeaseAcquireResult:
        row = self._execute_row(
            "EXEC app.usp_ali1688_runtime_lease_acquire ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?;",
            (
                PROTOCOL_NAME,
                PROTOCOL_VERSION,
                build_sha,
                resource_type,
                resource_key,
                account_key,
                owner_token,
                task_type,
                run_id,
                hostname,
                pid,
                profile_ref or None,
                priority,
                LEASE_TTL_SECONDS,
                request_key or None,
            ),
        )
        status = str(row[0] or "")
        lease = None
        if status == "acquired":
            lease = ResourceLease(
                resource_type=resource_type,
                resource_key=resource_key,
                owner_token=owner_token,
                fencing_token=int(row[4]),
                server_now=_as_utc_datetime(row[2]),
                expires_at=_as_utc_datetime(row[3]),
            )
        return LeaseAcquireResult(
            status=status,
            reason_code=str(row[1] or ""),
            lease=lease,
            current_owner_token=str(row[5] or ""),
            current_fencing_token=int(row[6] or 0),
            current_hostname=str(row[7] or ""),
            current_pid=int(row[8] or 0),
        )

    def heartbeat(self, lease: ResourceLease) -> ResourceLease:
        row = self._execute_row(
            "EXEC app.usp_ali1688_runtime_lease_heartbeat ?, ?, ?, ?, ?;",
            (
                lease.resource_type,
                lease.resource_key,
                lease.owner_token,
                lease.fencing_token,
                LEASE_TTL_SECONDS,
            ),
        )
        if str(row[0] or "") != "renewed":
            raise RuntimeLeaseLostError(str(row[1] or "runtime_lease_lost"))
        return ResourceLease(
            resource_type=lease.resource_type,
            resource_key=lease.resource_key,
            owner_token=lease.owner_token,
            fencing_token=lease.fencing_token,
            server_now=_as_utc_datetime(row[2]),
            expires_at=_as_utc_datetime(row[3]),
        )

    def release(self, lease: ResourceLease) -> bool:
        row = self._execute_row(
            "EXEC app.usp_ali1688_runtime_lease_release ?, ?, ?, ?;",
            (lease.resource_type, lease.resource_key, lease.owner_token, lease.fencing_token),
        )
        return str(row[0] or "") == "released"

    def _execute_row(self, sql: str, params: tuple[Any, ...]) -> Any:
        with self._connect(self.config) as connection:
            cursor = connection.cursor()
            cursor.execute(sql, params)
            row = _fetch_first_result_row(cursor)
            connection.commit()
        if row is None:
            raise RuntimeProtocolError("Runtime lease procedure returned no result row")
        return row


class RuntimeLeaseGuard:
    """High-priority ERP request plus account and browser-slot lease ownership."""

    def __init__(
        self,
        repository: RuntimeLeaseRepository,
        *,
        binding: ExecutorBinding,
        task_type: str,
        run_id: str,
        request_key: str,
        build_sha: str,
        component: str,
        priority: int = WRITE_PRIORITY,
        wait_timeout_seconds: float = 4800,
        poll_interval_seconds: float = 5,
        hostname: str | None = None,
        pid: int | None = None,
    ) -> None:
        self.repository = repository
        self.binding = binding
        self.task_type = task_type
        self.run_id = run_id
        self.request_key = request_key
        self.build_sha = build_sha
        self.component = component
        self.priority = int(priority)
        self.wait_timeout_seconds = max(0.0, float(wait_timeout_seconds))
        self.poll_interval_seconds = max(0.1, float(poll_interval_seconds))
        self.hostname = hostname or socket.gethostname()
        self.pid = int(pid or os.getpid())
        self.owner_token = uuid.uuid4().hex
        self.protocol_state: RuntimeProtocolState | None = None
        self.account_lease: ResourceLease | None = None
        self.browser_slot_lease: ResourceLease | None = None
        self._request_registered = False
        self._last_success_monotonic = time.monotonic()
        self._heartbeat_failures = 0
        self._lost_error: BaseException | None = None
        self._state_lock = threading.Lock()
        self._stop_event = threading.Event()
        self._heartbeat_thread: threading.Thread | None = None
        self._owned_browser_closers: list[Callable[[], None]] = []

    @property
    def account_fencing_token(self) -> int:
        return int(self.account_lease.fencing_token if self.account_lease else 0)

    @property
    def browser_slot_fencing_token(self) -> int:
        return int(self.browser_slot_lease.fencing_token if self.browser_slot_lease else 0)

    @property
    def browser_slot_key(self) -> str:
        return str(self.browser_slot_lease.resource_key if self.browser_slot_lease else "")

    def __enter__(self) -> RuntimeLeaseGuard:
        self.acquire()
        return self

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        self.release("completed" if exc_type is None else "failed")

    def acquire(self) -> None:
        self.protocol_state = self.repository.assert_protocol(
            component=self.component,
            build_sha=self.build_sha,
        )
        if (
            self.binding.target_hostname
            and self.binding.target_hostname.casefold() != self.hostname.casefold()
        ):
            raise RuntimeProtocolError("runtime_config_target_hostname_mismatch")
        self.repository.assert_binding(
            self.binding,
            hostname=self.hostname,
            require_match=self.protocol_state.enforcement_enabled,
        )
        self.repository.register_request(
            request_key=self.request_key,
            account_key=self.binding.account_key,
            task_type=self.task_type,
            priority=self.priority,
            run_id=self.run_id,
            owner_token=self.owner_token,
            hostname=self.hostname,
            pid=self.pid,
            build_sha=self.build_sha,
        )
        self._request_registered = True
        deadline = time.monotonic() + self.wait_timeout_seconds
        try:
            self.account_lease = self._wait_for_resource(
                resource_type="account",
                resource_key=self.binding.account_key,
                deadline=deadline,
            )
            capacity = min(
                MAX_BROWSER_ACCOUNTS,
                max(1, self.protocol_state.max_parallel_browser_accounts),
            )
            while self.browser_slot_lease is None:
                for slot_no in range(1, capacity + 1):
                    result = self._acquire_resource(
                        resource_type="browser_slot",
                        resource_key=f"{self.hostname}:{slot_no}",
                    )
                    if result.acquired:
                        self.browser_slot_lease = result.lease
                        break
                    self._assert_waitable(result)
                if self.browser_slot_lease is not None:
                    break
                self._wait_or_timeout(deadline, "browser_slot_busy", heartbeat_account=True)
        except BaseException:
            self.release("failed", suppress_errors=True)
            raise
        self._last_success_monotonic = time.monotonic()
        self._heartbeat_thread = threading.Thread(
            target=self._heartbeat_loop,
            name=f"ali1688-lease-{self.task_type}-{self.binding.account_key}",
            daemon=True,
        )
        self._heartbeat_thread.start()

    def register_owned_browser_closer(self, closer: Callable[[], None]) -> None:
        self._owned_browser_closers.append(closer)

    def assert_active(self) -> None:
        with self._state_lock:
            error = self._lost_error
        if error is not None:
            raise RuntimeLeaseLostError(f"Cross-project runtime lease is lost: {error}")

    def environment(self) -> dict[str, str]:
        self.assert_active()
        return {
            "ALI1688_RUNTIME_PROTOCOL": PROTOCOL_NAME,
            "ALI1688_RUNTIME_PROTOCOL_VERSION": str(PROTOCOL_VERSION),
            "ALI1688_RUNTIME_BUILD_SHA": self.build_sha,
            "ALI1688_RUNTIME_REQUEST_KEY": self.request_key,
            "ALI1688_RUNTIME_OWNER_TOKEN": self.owner_token,
            "ALI1688_RUNTIME_ACCOUNT_KEY": self.binding.account_key,
            "ALI1688_RUNTIME_ACCOUNT_FENCING_TOKEN": str(self.account_fencing_token),
            "ALI1688_RUNTIME_BROWSER_SLOT_KEY": self.browser_slot_key,
            "ALI1688_RUNTIME_BROWSER_SLOT_FENCING_TOKEN": str(self.browser_slot_fencing_token),
        }

    def release(self, request_status: str, *, suppress_errors: bool = False) -> None:
        self._stop_event.set()
        thread = self._heartbeat_thread
        if thread and thread is not threading.current_thread():
            thread.join(timeout=OWNED_BROWSER_SHUTDOWN_DEADLINE_SECONDS)
        errors: list[BaseException] = []
        for lease in (self.browser_slot_lease, self.account_lease):
            if lease is None:
                continue
            try:
                if not self.repository.release(lease):
                    raise RuntimeLeaseLostError(
                        f"runtime_lease_release_compare_and_set_failed:{lease.resource_type}/{lease.resource_key}"
                    )
            except BaseException as exc:
                errors.append(exc)
        self.browser_slot_lease = None
        self.account_lease = None
        if self._request_registered:
            try:
                if not self.repository.complete_request(
                    self.request_key,
                    self.owner_token,
                    request_status,
                ):
                    raise RuntimeLeaseLostError("runtime_request_complete_compare_and_set_failed")
            except BaseException as exc:
                errors.append(exc)
            self._request_registered = False
        if errors and not suppress_errors:
            raise RuntimeLeaseLostError(str(errors[0]))

    def _wait_for_resource(self, *, resource_type: str, resource_key: str, deadline: float) -> ResourceLease:
        while True:
            result = self._acquire_resource(resource_type=resource_type, resource_key=resource_key)
            if result.acquired and result.lease is not None:
                return result.lease
            self._assert_waitable(result)
            self._wait_or_timeout(deadline, result.reason_code or "resource_lease_busy")

    def _acquire_resource(self, *, resource_type: str, resource_key: str) -> LeaseAcquireResult:
        return self.repository.acquire(
            resource_type=resource_type,
            resource_key=resource_key,
            account_key=self.binding.account_key,
            owner_token=self.owner_token,
            task_type=self.task_type,
            run_id=self.run_id,
            hostname=self.hostname,
            pid=self.pid,
            profile_ref=self.binding.profile_ref,
            priority=self.priority,
            build_sha=self.build_sha,
            request_key=self.request_key,
        )

    @staticmethod
    def _assert_waitable(result: LeaseAcquireResult) -> None:
        if result.status == "busy" and result.reason_code in {
            "resource_lease_busy",
            "higher_priority_account_write",
        }:
            return
        if result.status == "expired_owner_requires_recovery":
            raise RuntimeResourceBusyError("expired_owner_requires_recovery")
        raise RuntimeProtocolError(result.reason_code or f"runtime_lease_{result.status}")

    def _wait_or_timeout(self, deadline: float, reason: str, *, heartbeat_account: bool = False) -> None:
        if time.monotonic() >= deadline:
            raise RuntimeResourceBusyError(f"{reason}:wait_timeout")
        self.repository.heartbeat_request(self.request_key, self.owner_token)
        if heartbeat_account and self.account_lease is not None:
            self.account_lease = self.repository.heartbeat(self.account_lease)
        remaining = max(0.0, deadline - time.monotonic())
        time.sleep(min(self.poll_interval_seconds, remaining))

    def _heartbeat_loop(self) -> None:
        while not self._stop_event.wait(HEARTBEAT_INTERVAL_SECONDS):
            try:
                self.repository.heartbeat_request(self.request_key, self.owner_token)
                if self.account_lease is None or self.browser_slot_lease is None:
                    raise RuntimeLeaseLostError("runtime_lease_context_incomplete")
                account = self.repository.heartbeat(self.account_lease)
                slot = self.repository.heartbeat(self.browser_slot_lease)
            except BaseException as exc:
                with self._state_lock:
                    self._heartbeat_failures += 1
                    elapsed = time.monotonic() - self._last_success_monotonic
                    lost = (
                        self._heartbeat_failures >= HEARTBEAT_FAILURE_THRESHOLD
                        or elapsed >= LEASE_STOP_AFTER_LAST_SUCCESS_SECONDS
                    )
                    if lost and self._lost_error is None:
                        self._lost_error = exc
                if lost:
                    self._close_owned_browsers()
                    return
            else:
                with self._state_lock:
                    self.account_lease = account
                    self.browser_slot_lease = slot
                    self._heartbeat_failures = 0
                    self._last_success_monotonic = time.monotonic()

    def _close_owned_browsers(self) -> None:
        deadline = time.monotonic() + OWNED_BROWSER_SHUTDOWN_DEADLINE_SECONDS
        for closer in list(self._owned_browser_closers):
            if time.monotonic() >= deadline:
                break
            try:
                closer()
            except Exception:
                pass


class JushuitanRuntimeGuard:
    """Global single-lane Jushuitan lease for the ERP outbox worker."""

    def __init__(
        self,
        repository: RuntimeLeaseRepository,
        *,
        run_id: str,
        build_sha: str,
        wait_timeout_seconds: float = 3600,
        poll_interval_seconds: float = 5,
        hostname: str | None = None,
        pid: int | None = None,
    ) -> None:
        self.repository = repository
        self.run_id = run_id
        self.build_sha = build_sha
        self.wait_timeout_seconds = max(0.0, float(wait_timeout_seconds))
        self.poll_interval_seconds = max(0.1, float(poll_interval_seconds))
        self.hostname = hostname or socket.gethostname()
        self.pid = int(pid or os.getpid())
        self.owner_token = uuid.uuid4().hex
        self.lease: ResourceLease | None = None
        self._stop = threading.Event()
        self._lost_error: BaseException | None = None
        self._thread: threading.Thread | None = None

    def __enter__(self) -> JushuitanRuntimeGuard:
        protocol = self.repository.assert_protocol(
            component="erp-jushuitan-outbox",
            build_sha=self.build_sha,
        )
        deadline = time.monotonic() + self.wait_timeout_seconds
        while True:
            result = self.repository.acquire(
                resource_type="jushuitan",
                resource_key="global",
                account_key="",
                owner_token=self.owner_token,
                task_type="jushuitan",
                run_id=self.run_id,
                hostname=self.hostname,
                pid=self.pid,
                profile_ref="",
                priority=WRITE_PRIORITY,
                build_sha=self.build_sha,
            )
            if result.acquired:
                self.lease = result.lease
                break
            if result.status != "busy" or result.reason_code != "resource_lease_busy":
                raise RuntimeProtocolError(result.reason_code or "jushuitan_lease_rejected")
            if time.monotonic() >= deadline:
                raise RuntimeResourceBusyError("jushuitan_global_busy:wait_timeout")
            time.sleep(min(self.poll_interval_seconds, max(0.0, deadline - time.monotonic())))
        if protocol.max_parallel_browser_accounts < 1:
            raise RuntimeProtocolError("runtime_browser_capacity_invalid")
        self._thread = threading.Thread(target=self._heartbeat_loop, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=OWNED_BROWSER_SHUTDOWN_DEADLINE_SECONDS)
        if self.lease is not None and not self.repository.release(self.lease):
            raise RuntimeLeaseLostError("jushuitan_runtime_lease_release_failed")

    def assert_active(self) -> None:
        if self._lost_error is not None:
            raise RuntimeLeaseLostError(f"Jushuitan runtime lease is lost: {self._lost_error}")

    def _heartbeat_loop(self) -> None:
        failures = 0
        last_success = time.monotonic()
        while not self._stop.wait(HEARTBEAT_INTERVAL_SECONDS):
            try:
                if self.lease is None:
                    raise RuntimeLeaseLostError("jushuitan_runtime_lease_missing")
                self.lease = self.repository.heartbeat(self.lease)
            except BaseException as exc:
                failures += 1
                if failures >= HEARTBEAT_FAILURE_THRESHOLD or (
                    time.monotonic() - last_success >= LEASE_STOP_AFTER_LAST_SUCCESS_SECONDS
                ):
                    self._lost_error = exc
                    return
            else:
                failures = 0
                last_success = time.monotonic()


def _fetch_first_result_row(cursor: Any) -> Any | None:
    while cursor.description is None:
        if not cursor.nextset():
            return None
    return cursor.fetchone()


def _as_utc_datetime(value: Any) -> datetime:
    if not isinstance(value, datetime):
        raise RuntimeProtocolError(f"Runtime lease procedure returned invalid datetime: {value!r}")
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)
