from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta


class LockConflictError(RuntimeError):
    """Raised when an active lock already exists for the same resource."""


@dataclass
class LockLease:
    resource_type: str
    resource_key: str
    owner: str
    lease_until: datetime


class InMemoryLockService:
    def __init__(self, default_lease_seconds: int = 120) -> None:
        self._default_lease_seconds = default_lease_seconds
        self._locks: dict[tuple[str, str], LockLease] = {}

    def acquire(self, resource_type: str, resource_key: str, owner: str) -> LockLease:
        key = (resource_type, resource_key)
        current = self._locks.get(key)
        now = datetime.now(UTC)
        if current is not None and current.owner != owner and current.lease_until > now:
            raise LockConflictError(f"resource locked: {resource_type}/{resource_key}")

        lease = LockLease(
            resource_type=resource_type,
            resource_key=resource_key,
            owner=owner,
            lease_until=now + timedelta(seconds=self._default_lease_seconds),
        )
        self._locks[key] = lease
        return lease

    def release(self, resource_type: str, resource_key: str, owner: str) -> None:
        key = (resource_type, resource_key)
        current = self._locks.get(key)
        if current is not None and current.owner == owner:
            self._locks.pop(key, None)
