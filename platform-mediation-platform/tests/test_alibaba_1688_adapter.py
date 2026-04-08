from __future__ import annotations

import json
import subprocess
from pathlib import Path

import platform_mediation.adapters.alibaba_1688_adapter as adapter_module
from platform_mediation.adapters.alibaba_1688_adapter import Alibaba1688Adapter
from platform_mediation.config import Settings
from platform_mediation.domain.enums import ExecutorType, TaskType
from platform_mediation.domain.models import AdapterExecutionRequest


def build_settings(tmp_path: Path, *, mode: str, enable_real_execution: bool = False) -> Settings:
    furniture_root = tmp_path / "furniture-uploader"
    (furniture_root / "rpa").mkdir(parents=True, exist_ok=True)
    (furniture_root / "rpa" / "main.py").write_text("print('stub')\n", encoding="utf-8")
    return Settings(
        app_name="platform-mediation-platform",
        environment="test",
        api_prefix="/api/v1",
        adapter_mode=mode,
        repository_backend="memory",
        database_url=f"sqlite:///{tmp_path / 'platform_mediation.db'}",
        auto_create_schema=True,
        enable_real_1688_execution=enable_real_execution,
        furniture_uploader_root=furniture_root,
    )


def build_request(payload: dict | None = None) -> AdapterExecutionRequest:
    return AdapterExecutionRequest(
        task_id="TASK-TEST-001",
        task_item_id="101",
        platform="1688",
        action=TaskType.LISTING,
        executor_type=ExecutorType.RPA,
        shop_id=20001,
        mapping_version="1688.v1",
        source_snapshot_id="SS-TEST-001",
        payload=payload
        or {
            "sku_id": "SKU-001",
            "title": "测试商品",
            "price": 199,
            "platform_category": "corner_table",
            "shop_name": "demo_shop",
        },
    )


def test_preview_mode_builds_real_bridge_command(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(adapter_module, "PROJECT_ROOT", tmp_path)
    adapter = Alibaba1688Adapter(
        build_settings(tmp_path, mode="preview"),
        python_executable="python-test",
    )

    result = adapter.execute(build_request())

    assert result.status == "success"
    assert result.result_payload["execution_mode"] == "preview"
    command_preview = result.result_payload["command_preview"]
    assert command_preview[:2] == ["python-test", str(tmp_path / "furniture-uploader" / "rpa" / "main.py")]
    assert "--system" in command_preview
    assert "1688_direct" in command_preview
    assert "--input-mode" in command_preview
    assert "variant" in command_preview
    assert "--skip-login" in command_preview
    assert any(item.artifact_type == "input_template" for item in result.artifacts)


def test_validate_mode_runs_bridge_and_captures_logs(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(adapter_module, "PROJECT_ROOT", tmp_path)
    captured_command: list[str] = []

    def fake_runner(command: list[str], cwd: Path, timeout_seconds: int) -> subprocess.CompletedProcess[str]:
        captured_command[:] = command
        return subprocess.CompletedProcess(
            args=command,
            returncode=0,
            stdout=(
                "[INFO] Validation completed.\n"
                "[INFO] Run report path: D:/tmp/validate-report.jsonl\n"
                "[INFO] Run summary path: D:/tmp/validate-summary.json\n"
            ),
            stderr="",
        )

    adapter = Alibaba1688Adapter(
        build_settings(tmp_path, mode="validate"),
        runner=fake_runner,
        python_executable="python-test",
    )

    result = adapter.execute(build_request())

    assert result.status == "success"
    assert result.result_payload["execution_mode"] == "validate"
    assert "--validate-only" in captured_command
    assert any(item.artifact_type == "log" for item in result.artifacts)


def test_execute_mode_parses_run_report_and_summary(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(adapter_module, "PROJECT_ROOT", tmp_path)
    report_path = tmp_path / "run_report.jsonl"
    summary_path = tmp_path / "run_summary.json"
    report_path.write_text(
        json.dumps(
            {
                "status": "success",
                "platform_link_id": "PLATFORM-ITEM-001",
                "platform_link_url": "https://example.com/item/1",
                "draft_submit_backend_message": "ok",
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    summary_path.write_text(
        json.dumps({"success_items": 1, "failed_items": 0}, ensure_ascii=False),
        encoding="utf-8",
    )

    def fake_runner(command: list[str], cwd: Path, timeout_seconds: int) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            args=command,
            returncode=0,
            stdout=(
                f"[INFO] Run report: {report_path}\n"
                f"[INFO] Run summary: {summary_path}\n"
            ),
            stderr="",
        )

    adapter = Alibaba1688Adapter(
        build_settings(tmp_path, mode="execute", enable_real_execution=True),
        runner=fake_runner,
        python_executable="python-test",
    )

    result = adapter.execute(build_request())

    assert result.status == "success"
    assert result.platform_item_id == "PLATFORM-ITEM-001"
    assert result.result_payload["summary"]["success_items"] == 1
    assert any(item.artifact_path == str(report_path) for item in result.artifacts)
    assert any(item.artifact_path == str(summary_path) for item in result.artifacts)
