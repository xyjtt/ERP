from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from platform_mediation.adapters.base import PlatformAdapter
from platform_mediation.config import Settings
from platform_mediation.domain.models import (
    AdapterArtifact,
    AdapterExecutionRequest,
    AdapterExecutionResult,
)


class Alibaba1688Adapter(PlatformAdapter):
    adapter_code = "1688_adapter"
    platform = "1688"
    executor_type = "rpa"
    version = "v1"

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def capability(self) -> dict[str, object]:
        return {
            "adapter_code": self.adapter_code,
            "platform": self.platform,
            "executor_type": self.executor_type,
            "version": self.version,
            "mode": self._settings.adapter_mode,
            "furniture_uploader_root": str(self._settings.furniture_uploader_root),
            "supports": ["listing", "draft", "artifact_tracking"],
        }

    def build_execution_command(self, request: AdapterExecutionRequest) -> list[str]:
        main_script = self._settings.furniture_uploader_root / "rpa" / "main.py"
        return [
            "python",
            str(main_script),
            "--mode",
            "product",
            "--task-id",
            request.task_id,
            "--shop-id",
            str(request.shop_id),
            "--mapping-version",
            request.mapping_version,
        ]

    def execute(self, request: AdapterExecutionRequest) -> AdapterExecutionResult:
        if self._settings.adapter_mode != "mock":
            return self._preview_result(request)
        return self._mock_result(request)

    def _mock_result(self, request: AdapterExecutionRequest) -> AdapterExecutionResult:
        run_id = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
        artifact_path = f"mock://1688/{request.task_id}/{request.task_item_id}/{run_id}.summary.json"
        payload: dict[str, Any] = {
            "draft_id": f"draft-{request.task_item_id}",
            "execution_mode": "mock",
            "source_snapshot_id": request.source_snapshot_id,
        }
        return AdapterExecutionResult(
            status="success",
            error_type=None,
            error_message=None,
            platform_item_id=f"ITEM-{request.task_item_id}",
            platform_sku_id=f"{request.payload.get('sku_id', request.task_item_id)}-1688",
            result_payload=payload,
            artifacts=[
                AdapterArtifact(
                    artifact_type="run_report",
                    artifact_path=artifact_path,
                    metadata={"adapter_mode": "mock"},
                )
            ],
            raw_result={"command_preview": self.build_execution_command(request)},
        )

    def _preview_result(self, request: AdapterExecutionRequest) -> AdapterExecutionResult:
        command_preview = self.build_execution_command(request)
        artifact_path = Path("logs") / "command_previews" / f"{request.task_id}-{request.task_item_id}.json"
        return AdapterExecutionResult(
            status="success",
            error_type=None,
            error_message=None,
            platform_item_id=None,
            platform_sku_id=None,
            result_payload={
                "execution_mode": self._settings.adapter_mode,
                "command_preview": command_preview,
            },
            artifacts=[
                AdapterArtifact(
                    artifact_type="log",
                    artifact_path=str(artifact_path),
                    metadata={"mode": self._settings.adapter_mode},
                )
            ],
            raw_result={"command_preview": command_preview},
        )
