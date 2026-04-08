from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable

from platform_mediation.adapters.base import PlatformAdapter
from platform_mediation.config import PROJECT_ROOT, Settings
from platform_mediation.domain.enums import TaskType
from platform_mediation.domain.models import (
    AdapterArtifact,
    AdapterExecutionRequest,
    AdapterExecutionResult,
)


Runner = Callable[[list[str], Path, int], subprocess.CompletedProcess[str]]

SUPPORTED_MODES = {
    "mock": "mock",
    "preview": "preview",
    "validate": "validate",
    "bridge_validate": "validate",
    "execute": "execute",
    "bridge_execute": "execute",
    "real": "execute",
}
REPORT_PATH_PREFIXES = ("[INFO] Run report:", "[INFO] Run report path:")
SUMMARY_PATH_PREFIXES = ("[INFO] Run summary:", "[INFO] Run summary path:")


@dataclass(frozen=True)
class PreparedBridgeRun:
    run_id: str
    run_dir: Path
    template_path: Path
    stdout_path: Path
    stderr_path: Path
    variant_payload: dict[str, Any]


def _default_runner(command: list[str], cwd: Path, timeout_seconds: int) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        cwd=str(cwd),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        timeout=timeout_seconds,
    )


class Alibaba1688Adapter(PlatformAdapter):
    adapter_code = "1688_adapter"
    platform = "1688"
    executor_type = "rpa"
    version = "v1"

    def __init__(
        self,
        settings: Settings,
        *,
        runner: Runner | None = None,
        python_executable: str | None = None,
    ) -> None:
        self._settings = settings
        self._runner = runner or _default_runner
        self._python_executable = python_executable or sys.executable

    def capability(self) -> dict[str, object]:
        return {
            "adapter_code": self.adapter_code,
            "platform": self.platform,
            "executor_type": self.executor_type,
            "version": self.version,
            "mode": self._resolve_mode(),
            "furniture_uploader_root": str(self._settings.furniture_uploader_root),
            "supports": [
                "listing",
                "draft",
                "artifact_tracking",
                "variant_template_bridge",
                "subprocess_bridge",
            ],
            "real_execution_enabled": self._settings.enable_real_1688_execution,
        }

    def build_execution_command(
        self,
        request: AdapterExecutionRequest,
        *,
        template_path: Path,
        validate_only: bool,
    ) -> list[str]:
        main_script = self._settings.furniture_uploader_root / "rpa" / "main.py"
        payload = request.payload or {}
        command = [
            self._python_executable,
            str(main_script),
            "--system",
            str(payload.get("system", "1688_direct")).strip() or "1688_direct",
            "--platform",
            self.platform,
            "--file",
            str(template_path),
            "--input-mode",
            "variant",
            "--no-notify",
        ]
        if validate_only:
            command.append("--validate-only")
        if self._as_bool(payload.get("skip_login"), default=not validate_only):
            command.append("--skip-login")
        config_dir = str(payload.get("config_dir", "")).strip()
        if config_dir:
            command.extend(["--config-dir", config_dir])
        db_config = str(payload.get("db_config", "")).strip()
        if db_config:
            command.extend(["--db-config", db_config])
        return command

    def execute(self, request: AdapterExecutionRequest) -> AdapterExecutionResult:
        if request.action != TaskType.LISTING:
            return self._failure_result(
                request,
                error_type="validation_error",
                error_message=f"1688 adapter v1 only supports listing for now, got {request.action.value}.",
            )

        mode = self._resolve_mode()
        if mode == "mock":
            return self._mock_result(request)

        prepared = self._prepare_bridge_run(request)
        if mode == "preview":
            return self._preview_result(request, prepared)
        if mode == "validate":
            return self._run_bridge(request, prepared, validate_only=True)
        if mode == "execute":
            if not self._settings.enable_real_1688_execution:
                return self._failure_result(
                    request,
                    error_type="validation_error",
                    error_message="Real 1688 execution is disabled by PLATFORM_MEDIATION_ENABLE_REAL_1688_EXECUTION.",
                    artifacts=self._base_artifacts(prepared),
                    raw_result={
                        "adapter_mode": mode,
                        "real_execution_enabled": self._settings.enable_real_1688_execution,
                    },
                )
            return self._run_bridge(request, prepared, validate_only=False)
        return self._failure_result(
            request,
            error_type="validation_error",
            error_message=f"Unsupported adapter mode: {self._settings.adapter_mode}",
        )

    def _resolve_mode(self) -> str:
        normalized = str(self._settings.adapter_mode or "mock").strip().lower()
        return SUPPORTED_MODES.get(normalized, normalized)

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
            raw_result={"adapter_mode": "mock"},
        )

    def _preview_result(
        self,
        request: AdapterExecutionRequest,
        prepared: PreparedBridgeRun,
    ) -> AdapterExecutionResult:
        command_preview = self.build_execution_command(
            request,
            template_path=prepared.template_path,
            validate_only=False,
        )
        return AdapterExecutionResult(
            status="success",
            error_type=None,
            error_message=None,
            platform_item_id=None,
            platform_sku_id=None,
            result_payload={
                "execution_mode": "preview",
                "command_preview": command_preview,
                "template_path": str(prepared.template_path),
            },
            artifacts=self._base_artifacts(prepared),
            raw_result={
                "adapter_mode": "preview",
                "command_preview": command_preview,
                "variant_payload": prepared.variant_payload,
            },
        )

    def _run_bridge(
        self,
        request: AdapterExecutionRequest,
        prepared: PreparedBridgeRun,
        *,
        validate_only: bool,
    ) -> AdapterExecutionResult:
        command = self.build_execution_command(
            request,
            template_path=prepared.template_path,
            validate_only=validate_only,
        )
        timeout_seconds = self._resolve_timeout_seconds(request.payload)
        try:
            completed = self._runner(
                command,
                self._settings.furniture_uploader_root / "rpa",
                timeout_seconds,
            )
        except subprocess.TimeoutExpired as exc:
            stdout_text = exc.stdout or ""
            stderr_text = exc.stderr or ""
            self._write_text(prepared.stdout_path, stdout_text)
            self._write_text(prepared.stderr_path, stderr_text)
            return self._failure_result(
                request,
                error_type="timeout",
                error_message=f"1688 bridge timed out after {timeout_seconds} seconds.",
                artifacts=self._base_artifacts(prepared, include_logs=True),
                raw_result={
                    "adapter_mode": "validate" if validate_only else "execute",
                    "command": command,
                    "timeout_seconds": timeout_seconds,
                },
            )

        stdout_text = completed.stdout or ""
        stderr_text = completed.stderr or ""
        self._write_text(prepared.stdout_path, stdout_text)
        self._write_text(prepared.stderr_path, stderr_text)

        report_path = self._extract_info_path(stdout_text, REPORT_PATH_PREFIXES)
        summary_path = self._extract_info_path(stdout_text, SUMMARY_PATH_PREFIXES)
        report_payload = self._read_first_json_line(report_path)
        summary_payload = self._read_json_file(summary_path)
        artifacts = self._bridge_artifacts(prepared, report_path=report_path, summary_path=summary_path)

        raw_result = {
            "adapter_mode": "validate" if validate_only else "execute",
            "command": command,
            "returncode": completed.returncode,
            "stdout_path": str(prepared.stdout_path),
            "stderr_path": str(prepared.stderr_path),
            "report_path": str(report_path) if report_path else "",
            "summary_path": str(summary_path) if summary_path else "",
            "variant_payload": prepared.variant_payload,
        }

        if completed.returncode != 0:
            return self._failure_result(
                request,
                error_type=self._classify_error_type(stdout_text, stderr_text),
                error_message=self._extract_error_message(stdout_text, stderr_text),
                artifacts=artifacts,
                raw_result=raw_result,
            )

        if validate_only:
            return AdapterExecutionResult(
                status="success",
                error_type=None,
                error_message=None,
                platform_item_id=None,
                platform_sku_id=None,
                result_payload={
                    "execution_mode": "validate",
                    "template_path": str(prepared.template_path),
                    "validation_completed": True,
                },
                artifacts=artifacts,
                raw_result=raw_result,
            )

        if report_payload and str(report_payload.get("status", "")).strip().lower() == "success":
            return AdapterExecutionResult(
                status="success",
                error_type=None,
                error_message=None,
                platform_item_id=str(report_payload.get("platform_link_id", "")).strip() or None,
                platform_sku_id=str(request.payload.get("sku_id", "")).strip() or None,
                result_payload={
                    "execution_mode": "execute",
                    "draft_submit_backend_message": report_payload.get("draft_submit_backend_message"),
                    "platform_link_url": report_payload.get("platform_link_url"),
                    "summary": summary_payload,
                },
                artifacts=artifacts,
                raw_result={**raw_result, "report_payload": report_payload, "summary_payload": summary_payload},
            )

        summary_failed = int((summary_payload or {}).get("failed_items", 0) or 0) > 0
        if report_payload or summary_failed:
            return self._failure_result(
                request,
                error_type=str((report_payload or {}).get("error_type", "")).strip() or "platform_runtime_error",
                error_message=str((report_payload or {}).get("error_message", "")).strip()
                or self._extract_error_message(stdout_text, stderr_text)
                or "1688 execution finished without a success result.",
                artifacts=artifacts,
                raw_result={**raw_result, "report_payload": report_payload, "summary_payload": summary_payload},
            )

        return AdapterExecutionResult(
            status="success",
            error_type=None,
            error_message=None,
            platform_item_id=None,
            platform_sku_id=str(request.payload.get("sku_id", "")).strip() or None,
            result_payload={
                "execution_mode": "execute",
                "summary": summary_payload,
            },
            artifacts=artifacts,
            raw_result={**raw_result, "report_payload": report_payload, "summary_payload": summary_payload},
        )

    def _prepare_bridge_run(self, request: AdapterExecutionRequest) -> PreparedBridgeRun:
        run_id = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
        run_dir = (
            PROJECT_ROOT
            / "logs"
            / "adapter_bridge"
            / self.platform
            / request.task_id
            / str(request.task_item_id)
            / run_id
        )
        run_dir.mkdir(parents=True, exist_ok=True)
        variant_payload = self._build_variant_payload(request)
        template_path = run_dir / "variant_input.json"
        template_path.write_text(
            json.dumps([variant_payload], ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        return PreparedBridgeRun(
            run_id=run_id,
            run_dir=run_dir,
            template_path=template_path,
            stdout_path=run_dir / "stdout.log",
            stderr_path=run_dir / "stderr.log",
            variant_payload=variant_payload,
        )

    def _build_variant_payload(self, request: AdapterExecutionRequest) -> dict[str, Any]:
        raw = dict(request.payload or {})
        attributes = raw.get("attributes")
        if not isinstance(attributes, dict):
            attributes = {}

        known_attribute_keys = {
            "brand": raw.get("brand"),
            "material": raw.get("material"),
            "color": raw.get("color"),
            "size": raw.get("size"),
        }
        for key, value in known_attribute_keys.items():
            if value not in (None, "") and key not in attributes:
                attributes[key] = value

        variant_payload = dict(raw)
        variant_payload["variant_id"] = str(
            raw.get("variant_id")
            or raw.get("task_item_id")
            or f"{request.task_id}-{request.task_item_id}"
        ).strip()
        variant_payload["source_product_id"] = str(
            raw.get("source_product_id")
            or raw.get("sku_id")
            or raw.get("outer_sku")
            or request.task_item_id
        ).strip()
        variant_payload["platform"] = self.platform
        variant_payload["channel"] = str(raw.get("channel") or self.platform).strip() or self.platform
        variant_payload["shop_id"] = str(raw.get("shop_id") or request.shop_id).strip()
        variant_payload["shop_name"] = str(
            raw.get("shop_name")
            or raw.get("store_name")
            or raw.get("store_label")
            or f"shop_{request.shop_id}"
        ).strip()
        operator_name = str(raw.get("operator_name") or raw.get("created_by") or "platform_mediation").strip()
        variant_payload["operator_name"] = operator_name
        variant_payload["operator_id"] = str(raw.get("operator_id") or operator_name).strip()
        variant_payload["title"] = str(
            raw.get("title")
            or raw.get("name")
            or raw.get("sku_id")
            or f"task-item-{request.task_item_id}"
        ).strip()
        variant_payload["style_name"] = str(raw.get("style_name") or "platform_mediation").strip()
        variant_payload["sell_points"] = str(raw.get("sell_points") or raw.get("subtitle") or "").strip()
        variant_payload["attributes"] = attributes
        variant_payload["platform_category"] = str(
            raw.get("platform_category")
            or raw.get("category_path")
            or raw.get("category")
            or raw.get("platform_category_name")
            or ""
        ).strip()
        variant_payload["price_value"] = str(
            raw.get("price_value")
            or raw.get("price")
            or raw.get("sale_price")
            or ""
        ).strip()
        variant_payload["quantity"] = str(raw.get("quantity") or "1").strip() or "1"
        variant_payload["publish_mode"] = str(raw.get("publish_mode") or "draft").strip() or "draft"
        variant_payload["status"] = str(raw.get("status") or "draft").strip() or "draft"
        variant_payload["description"] = str(raw.get("description") or raw.get("sell_points") or "").strip()
        variant_payload["main_images"] = self._normalize_string_list(
            raw.get("main_images") or raw.get("main_image") or raw.get("main_image_remote")
        )
        variant_payload["detail_images"] = self._normalize_string_list(
            raw.get("detail_images") or raw.get("detail_images_remote")
        )
        variant_payload["sku_images"] = self._normalize_string_list(raw.get("sku_images"))
        return variant_payload

    def _base_artifacts(
        self,
        prepared: PreparedBridgeRun,
        *,
        include_logs: bool = False,
    ) -> list[AdapterArtifact]:
        artifacts = [
            AdapterArtifact(
                artifact_type="input_template",
                artifact_path=str(prepared.template_path),
                metadata={"adapter_mode": self._resolve_mode(), "run_id": prepared.run_id},
            )
        ]
        if include_logs:
            artifacts.extend(
                [
                    AdapterArtifact(
                        artifact_type="log",
                        artifact_path=str(prepared.stdout_path),
                        metadata={"stream": "stdout"},
                    ),
                    AdapterArtifact(
                        artifact_type="log",
                        artifact_path=str(prepared.stderr_path),
                        metadata={"stream": "stderr"},
                    ),
                ]
            )
        return artifacts

    def _bridge_artifacts(
        self,
        prepared: PreparedBridgeRun,
        *,
        report_path: Path | None,
        summary_path: Path | None,
    ) -> list[AdapterArtifact]:
        artifacts = self._base_artifacts(prepared, include_logs=True)
        if report_path and report_path.exists():
            artifacts.append(
                AdapterArtifact(
                    artifact_type="run_report",
                    artifact_path=str(report_path),
                    metadata={"source": "furniture_uploader"},
                )
            )
        if summary_path and summary_path.exists():
            artifacts.append(
                AdapterArtifact(
                    artifact_type="run_report",
                    artifact_path=str(summary_path),
                    metadata={"source": "furniture_uploader", "kind": "summary"},
                )
            )
        return artifacts

    def _failure_result(
        self,
        request: AdapterExecutionRequest,
        *,
        error_type: str,
        error_message: str,
        artifacts: list[AdapterArtifact] | None = None,
        raw_result: dict[str, Any] | None = None,
    ) -> AdapterExecutionResult:
        return AdapterExecutionResult(
            status="failed",
            error_type=error_type,
            error_message=error_message,
            platform_item_id=None,
            platform_sku_id=str(request.payload.get("sku_id", "")).strip() or None,
            result_payload={},
            artifacts=artifacts or [],
            raw_result=raw_result or {},
        )

    @staticmethod
    def _normalize_string_list(raw_value: Any) -> list[str]:
        if raw_value is None:
            return []
        if isinstance(raw_value, list):
            return [str(item).strip() for item in raw_value if str(item).strip()]
        text = str(raw_value).strip()
        if not text:
            return []
        if text.startswith("[") and text.endswith("]"):
            try:
                parsed = json.loads(text)
            except json.JSONDecodeError:
                parsed = None
            if isinstance(parsed, list):
                return [str(item).strip() for item in parsed if str(item).strip()]
        return [item.strip() for item in text.split("|") if item.strip()]

    @staticmethod
    def _extract_info_path(output_text: str, prefixes: tuple[str, ...]) -> Path | None:
        for raw_line in output_text.splitlines():
            line = raw_line.strip()
            for prefix in prefixes:
                if line.startswith(prefix):
                    candidate = line.removeprefix(prefix).strip()
                    if candidate:
                        return Path(candidate)
        return None

    @staticmethod
    def _read_json_file(path: Path | None) -> dict[str, Any]:
        if not path or not path.exists():
            return {}
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}

    @staticmethod
    def _read_first_json_line(path: Path | None) -> dict[str, Any]:
        if not path or not path.exists():
            return {}
        try:
            with path.open("r", encoding="utf-8") as handle:
                first_line = handle.readline().strip()
        except OSError:
            return {}
        if not first_line:
            return {}
        try:
            return json.loads(first_line)
        except json.JSONDecodeError:
            return {}

    @staticmethod
    def _write_text(path: Path, content: str) -> None:
        path.write_text(content or "", encoding="utf-8")

    @staticmethod
    def _classify_error_type(stdout_text: str, stderr_text: str) -> str:
        combined = f"{stdout_text}\n{stderr_text}".lower()
        if "validation failed" in combined or "[error]" in combined:
            return "validation_error"
        if "timeout" in combined:
            return "timeout"
        return "platform_runtime_error"

    @staticmethod
    def _extract_error_message(stdout_text: str, stderr_text: str) -> str:
        for raw_line in [*stderr_text.splitlines(), *stdout_text.splitlines()]:
            line = raw_line.strip()
            if line.startswith("[ERROR] "):
                return line.removeprefix("[ERROR] ").strip()
        fallback = stderr_text.strip() or stdout_text.strip()
        if not fallback:
            return "1688 bridge execution failed without an explicit error message."
        return fallback.splitlines()[-1].strip()

    @staticmethod
    def _as_bool(value: Any, *, default: bool = False) -> bool:
        if value is None:
            return default
        return str(value).strip().lower() in {"1", "true", "yes", "on"}

    @staticmethod
    def _resolve_timeout_seconds(payload: dict[str, Any]) -> int:
        raw = payload.get("_adapter_timeout_seconds", 900)
        try:
            resolved = int(raw)
        except (TypeError, ValueError):
            resolved = 900
        return max(resolved, 30)
