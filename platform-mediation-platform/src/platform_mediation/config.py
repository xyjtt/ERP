from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
import os


PROJECT_ROOT = Path(__file__).resolve().parents[2]
ERP_ROOT = PROJECT_ROOT.parent


def _as_bool(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    app_name: str
    environment: str
    api_prefix: str
    adapter_mode: str
    enable_real_1688_execution: bool
    furniture_uploader_root: Path


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings(
        app_name="platform-mediation-platform",
        environment=os.getenv("PLATFORM_MEDIATION_ENV", "local"),
        api_prefix="/api/v1",
        adapter_mode=os.getenv("PLATFORM_MEDIATION_ADAPTER_MODE", "mock"),
        enable_real_1688_execution=_as_bool(
            os.getenv("PLATFORM_MEDIATION_ENABLE_REAL_1688_EXECUTION"),
        ),
        furniture_uploader_root=Path(
            os.getenv(
                "PLATFORM_MEDIATION_FURNITURE_UPLOADER_ROOT",
                str(ERP_ROOT / "furniture-uploader"),
            )
        ),
    )
