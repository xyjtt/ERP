from __future__ import annotations

from platform_mediation.bootstrap import get_container


def get_task_service():
    return get_container().task_service


def get_dispatch_service():
    return get_container().dispatch_service


def get_settings():
    return get_container().settings

