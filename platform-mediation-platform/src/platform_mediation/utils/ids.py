from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4


def build_public_id(prefix: str) -> str:
    timestamp = datetime.now(UTC).strftime("%Y%m%d%H%M%S")
    suffix = uuid4().hex[:6].upper()
    return f"{prefix}-{timestamp}-{suffix}"


def build_task_id() -> str:
    return build_public_id("TASK")


def build_snapshot_id() -> str:
    return build_public_id("SS")


def build_attempt_id() -> str:
    return build_public_id("ATT")


def build_artifact_id() -> str:
    return build_public_id("ART")


def build_event_id() -> str:
    return build_public_id("EVT")
