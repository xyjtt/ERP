from __future__ import annotations

from fastapi import FastAPI

from platform_mediation.api.routes.health import router as health_router
from platform_mediation.api.routes.tasks import router as tasks_router
from platform_mediation.config import get_settings


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="Platform Mediation Platform",
        version="0.1.0",
        description="Phase-1 control plane skeleton for 1688 mediation workflows.",
    )
    app.include_router(health_router)
    app.include_router(tasks_router, prefix=settings.api_prefix)
    return app


app = create_app()

