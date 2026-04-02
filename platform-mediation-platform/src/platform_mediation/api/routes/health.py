from __future__ import annotations

from fastapi import APIRouter, Depends

from platform_mediation.api.dependencies import get_settings
from platform_mediation.config import Settings


router = APIRouter(tags=["health"])


@router.get("/healthz")
def healthz(settings: Settings = Depends(get_settings)) -> dict[str, str]:
    return {
        "status": "ok",
        "app": settings.app_name,
        "environment": settings.environment,
        "adapter_mode": settings.adapter_mode,
    }

