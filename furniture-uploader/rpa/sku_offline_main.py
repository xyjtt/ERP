from __future__ import annotations

import argparse
import copy
import hashlib
import json
import re
import shutil
import sys
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any

from config_loader import load_json_with_local_override
from dingtalk import build_signed_webhook, resolve_dingtalk_credentials
from exceptions import OfflineAccountMappingError
from run_report import RunReportWriter
from sku_offline_browser import SkuOfflineBrowser
from sku_offline_tasks import (
    FileIdentity,
    ProcessedFileRegistry,
    build_preview_payload,
    dedupe_offline_tasks,
    discover_scan_files,
    filter_offline_tasks,
    group_tasks_by_store,
    load_offline_tasks,
    store_name_matches,
)


def safe_console_print(message: str) -> None:
    try:
        print(message)
    except UnicodeEncodeError:
        encoded = message.encode(sys.stdout.encoding or "utf-8", errors="replace")
        print(encoded.decode(sys.stdout.encoding or "utf-8", errors="replace"))


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="1688 SKU offline automation entrypoint.")
    parser.add_argument(
        "--system",
        default="1688_sku_offline",
        help="Execution system, defaults to 1688_sku_offline.",
    )
    parser.add_argument(
        "--file",
        default="",
        help="Path to a single CSV/XLSX offline task file.",
    )
    parser.add_argument(
        "--dir",
        default="",
        help="Path to a directory that contains CSV/XLSX offline task files.",
    )
    parser.add_argument(
        "--mode",
        choices=("preview", "execute", "scan"),
        default="preview",
        help="preview: parse and summarize; execute: run one file; scan: auto execute files in watch_dir.",
    )
    parser.add_argument(
        "--config-dir",
        default=str(Path(__file__).resolve().parents[1] / "config"),
        help="Path to the config directory.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="Only process the first N selected tasks after filtering and dedupe.",
    )
    parser.add_argument(
        "--skip-login",
        action="store_true",
        help="Skip the login step when the browser session is already authenticated.",
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="Skip the interactive execution confirmation in execute mode.",
    )
    parser.add_argument(
        "--no-notify",
        action="store_true",
        help="Do not send DingTalk notifications for this run.",
    )
    parser.add_argument(
        "--jushuitan-handoff-out",
        default="",
        help="Optional JSONL path for exact Jushuitan 1688 link-cleanup tasks.",
    )
    parser.add_argument(
        "--run-id",
        default="",
        help="Optional externally assigned run ID used for deterministic audit report paths.",
    )
    return parser


def main() -> None:
    args = build_argument_parser().parse_args()
    if args.run_id and not re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", str(args.run_id)):
        raise ValueError("--run-id must contain only letters, numbers, dot, underscore, or dash")
    project_root = Path(__file__).resolve().parents[1]
    config_dir = Path(args.config_dir)
    system_key = args.system.strip().lower()
    system_config = load_json_with_local_override(config_dir / "systems" / f"{system_key}.json")
    operator_config = load_json_with_local_override(config_dir / "operator_config.json")
    run_report_options = {
        "project_root": project_root,
        "report_dir": system_config.get("reporting", {}).get(
            "run_report_dir", "logs/sku_offline/run_reports"
        ),
    }
    if str(args.run_id or "").strip():
        run_report_options["session_id"] = str(args.run_id).strip()
    run_report = RunReportWriter(**run_report_options)

    try:
        if args.mode == "preview":
            preview = load_preview_for_files(
                system_config,
                resolve_input_files(file_path=args.file, dir_path=args.dir),
                limit=args.limit,
            )
            preview_path = write_preview_report(project_root, preview)
            print_preview(preview, preview_path)
            if args.jushuitan_handoff_out:
                write_jushuitan_handoff(
                    Path(args.jushuitan_handoff_out),
                    build_jushuitan_handoff_records(preview),
                )
            return

        if args.mode == "execute":
            preview = load_preview_for_files(
                system_config,
                resolve_input_files(file_path=args.file, dir_path=args.dir),
                limit=args.limit,
            )
            preview_path = write_preview_report(project_root, preview)
            print_preview(preview, preview_path)
            if not preview["selected_tasks"]:
                safe_console_print("[INFO] No executable offline tasks were found.")
                return
            if not args.yes:
                confirmation = input("输入 YES 继续执行 1688 下架任务：").strip()
                if confirmation != "YES":
                    safe_console_print("[INFO] Cancelled by user.")
                    return
            execute_preview(
                project_root=project_root,
                operator_config=operator_config,
                system_config=system_config,
                preview=preview,
                skip_login=args.skip_login,
                no_notify=args.no_notify,
                run_report=run_report,
                jushuitan_handoff_path=(
                    Path(args.jushuitan_handoff_out)
                    if args.jushuitan_handoff_out
                    else project_root
                    / "logs"
                    / "sku_offline"
                    / "jushuitan_handoffs"
                    / f"{run_report.session_id}.jsonl"
                ),
            )
            return

        process_scan_mode(
            project_root=project_root,
            operator_config=operator_config,
            system_config=system_config,
            watch_dir_override=args.dir,
            skip_login=args.skip_login,
            limit=args.limit,
            no_notify=args.no_notify,
            run_report=run_report,
        )
    finally:
        run_report.write_summary(
            {
                "system": system_key,
                "mode": args.mode,
            }
        )


def load_preview_for_files(
    system_config: dict[str, Any],
    files: list[Path],
    *,
    limit: int = 0,
) -> dict[str, Any]:
    input_config = dict(system_config.get("input", {}))
    filters = dict(input_config.get("filters", {}))
    loaded_tasks = []
    filtered_out_tasks = []
    selected_tasks = []
    duplicate_tasks = []

    for file_path in files:
        tasks = load_offline_tasks(file_path, input_config)
        loaded_tasks.extend(tasks)
        filtered, skipped = filter_offline_tasks(tasks, input_config.get("filters", {}))
        deduped, duplicates = dedupe_offline_tasks(filtered)
        selected_tasks.extend(deduped)
        filtered_out_tasks.extend(skipped)
        duplicate_tasks.extend(duplicates)

    deduped_selected, cross_file_duplicates = dedupe_offline_tasks(selected_tasks)
    duplicate_tasks.extend(cross_file_duplicates)
    if limit > 0:
        deduped_selected = deduped_selected[:limit]

    preview_payload = build_preview_payload(
        source_files=[str(file_path) for file_path in files],
        loaded_tasks=loaded_tasks,
        selected_tasks=deduped_selected,
        filtered_out_tasks=filtered_out_tasks,
        duplicate_tasks=duplicate_tasks,
    )
    loaded_store_names = sorted(
        {
            str(task.store_name).strip()
            for task in loaded_tasks
            if str(task.store_name).strip()
        }
    )
    preview_payload["applied_filters"] = {
        "store_name": str(filters.get("store_name", "")).strip(),
        "platform": str(filters.get("platform", "")).strip(),
        "handling": str(filters.get("handling", "")).strip(),
    }
    preview_payload["loaded_store_names"] = loaded_store_names
    if not deduped_selected and preview_payload["applied_filters"]["store_name"]:
        preview_payload["filter_hint"] = (
            "No tasks matched the configured store filter. "
            f"target={preview_payload['applied_filters']['store_name']}; "
            f"loaded_stores={', '.join(loaded_store_names[:8]) or 'NONE'}"
        )
    preview_payload["selected_tasks"] = deduped_selected
    preview_payload["filtered_out_tasks"] = filtered_out_tasks
    preview_payload["duplicate_tasks"] = duplicate_tasks
    return preview_payload


def resolve_input_files(*, file_path: str, dir_path: str) -> list[Path]:
    normalized_file_path = str(file_path or "").strip()
    normalized_dir_path = str(dir_path or "").strip()

    if normalized_file_path and normalized_dir_path:
        raise ValueError("Use either --file or --dir, not both.")

    if normalized_file_path:
        return [Path(normalized_file_path)]

    if normalized_dir_path:
        candidate_files = discover_scan_files(Path(normalized_dir_path))
        if not candidate_files:
            raise ValueError(f"No CSV/XLSX offline task files were found in directory: {normalized_dir_path}")
        return candidate_files

    raise ValueError("Either --file or --dir is required.")


def execute_preview(
    *,
    project_root: Path,
    operator_config: dict[str, Any],
    system_config: dict[str, Any],
    preview: dict[str, Any],
    skip_login: bool,
    no_notify: bool,
    run_report: RunReportWriter,
    jushuitan_handoff_path: Path | None = None,
) -> dict[str, Any]:
    selected_tasks = list(preview.get("selected_tasks", []))
    store_groups = group_tasks_by_store(selected_tasks)
    execution_config = dict(system_config.get("execution", {}))
    if len(store_groups) > 1 and not bool(execution_config.get("allow_multi_store_batch", False)):
        raise ValueError(
            "Current phase only supports one store per execution batch. "
            "Please split the file by store or enable store mapping in local config."
        )

    summary = {
        "success": 0,
        "failed": 0,
        "already_offline": 0,
        "stopped_stores": 0,
        "stopped_store_names": [],
    }
    max_attempts = max(1, int(execution_config.get("max_retry", 1)) + 1)
    successful_task_statuses: dict[tuple[str, str, str], str] = {}

    for store_name, store_tasks in store_groups.items():
        try:
            account_binding = resolve_store_account_binding(system_config, store_name)
            store_operator_config = build_store_operator_config(
                operator_config,
                account_binding=account_binding,
                execution_config=execution_config,
            )
        except Exception as exc:
            error_category = classify_offline_error(exc, {"page_error_category": "account_mapping"})
            summary["failed"] += len(store_tasks)
            summary["stopped_stores"] += 1
            summary["stopped_store_names"].append(store_name)
            for index, task in enumerate(store_tasks):
                payload = build_run_report_payload(
                    task=task,
                    status="failed",
                    attempts=0,
                    result_context={
                        "page_error_category": error_category,
                        "page_error_stage": "pre_execution_store_binding",
                        "page_error_text": str(exc),
                    },
                    exc=exc,
                )
                run_report.append(payload)
                if index == 0:
                    send_failure_notification(system_config, payload, disabled=no_notify)
            safe_console_print(
                f"[ERROR] Stop store batch for {store_name}: safety category={error_category}; {exc}"
            )
            continue

        browser = SkuOfflineBrowser(store_operator_config.get("browser", {}), project_root)
        store_stopped = False

        browser.open()
        try:
            browser.prepare_session(system_config, skip_login=skip_login)
            for task in store_tasks:
                browser.reset_runtime_artifacts()
                attempts = 0
                while attempts < max_attempts:
                    attempts += 1
                    try:
                        result_context = browser.execute_offline_task(
                            system_config,
                            task,
                            account_binding=account_binding,
                        )
                        status = (
                            "already_offline"
                            if result_context.get("execution_result") == "already_offline"
                            else "success"
                        )
                        if status == "already_offline":
                            summary["already_offline"] += 1
                        else:
                            summary["success"] += 1
                        successful_task_statuses[task.dedupe_key] = status
                        run_report.append(
                            build_run_report_payload(
                                task=task,
                                status=status,
                                attempts=attempts,
                                result_context=result_context,
                            )
                        )
                        break
                    except Exception as exc:
                        error_category = classify_offline_error(exc, browser.last_result_context)
                        if (
                            attempts < max_attempts
                            and should_retry_offline_error(error_category, execution_config)
                            and not should_stop_store_on_error(error_category, execution_config)
                        ):
                            safe_console_print(
                                f"[WARN] Retry {attempts}/{max_attempts - 1} for "
                                f"{task.store_name} {task.product_id} {task.online_sku}: {exc}"
                            )
                            continue

                        summary["failed"] += 1
                        payload = build_run_report_payload(
                            task=task,
                            status="failed",
                            attempts=attempts,
                            result_context=browser.last_result_context,
                            exc=exc,
                            screenshot_path=browser.last_screenshot_path,
                            html_snapshot_path=browser.last_html_snapshot_path,
                        )
                        run_report.append(payload)
                        safe_console_print(
                            f"[ERROR] Offline task failed: {task.store_name} "
                            f"{task.product_id} {task.online_sku} -> {exc}"
                        )
                        send_failure_notification(system_config, payload, disabled=no_notify)
                        if should_stop_store_on_error(error_category, execution_config):
                            store_stopped = True
                            summary["stopped_stores"] += 1
                            summary["stopped_store_names"].append(store_name)
                            safe_console_print(
                                f"[ERROR] Stop store batch for {store_name}: safety category={error_category}"
                            )
                        break
                if store_stopped:
                    break
        finally:
            browser.close()

    summary["total"] = len(selected_tasks)
    summary["selected_count"] = len(selected_tasks)
    summary["duplicate_count"] = int(preview.get("duplicate_count", 0))
    summary["filtered_out_count"] = int(preview.get("filtered_out_count", 0))
    summary["report_path"] = run_report.info()["report_path"]
    summary["summary_path"] = run_report.info()["summary_path"]
    handoff_path = jushuitan_handoff_path or (
        project_root
        / "logs"
        / "sku_offline"
        / "jushuitan_handoffs"
        / (
            f"{getattr(run_report, 'session_id', 'session')}"
            f"_{datetime.now().strftime('%H%M%S_%f')}.jsonl"
        )
    )
    handoff_records = build_jushuitan_handoff_records(
        preview,
        successful_task_statuses=successful_task_statuses,
    )
    write_jushuitan_handoff(handoff_path, handoff_records)
    summary["jushuitan_handoff_path"] = str(handoff_path)
    summary["jushuitan_handoff_count"] = len(handoff_records)
    send_summary_notification(system_config, summary, disabled=no_notify)
    safe_console_print(json.dumps(summary, ensure_ascii=False, indent=2))
    return summary


def build_jushuitan_handoff_records(
    preview: dict[str, Any],
    *,
    successful_task_statuses: dict[tuple[str, str, str], str] | None = None,
) -> list[dict[str, Any]]:
    selected_tasks = list(preview.get("selected_tasks", []))
    selected_keys = {task.dedupe_key for task in selected_tasks}
    candidates = [
        *selected_tasks,
        *[
            task
            for task in list(preview.get("duplicate_tasks", []))
            if task.dedupe_key in selected_keys
        ],
    ]
    records: dict[str, dict[str, Any]] = {}

    for task in candidates:
        source_status = "pending_1688"
        if successful_task_statuses is not None:
            source_status = str(successful_task_statuses.get(task.dedupe_key, "")).strip()
            if source_status not in {"success", "already_offline"}:
                continue

        identity_parts = (
            task.store_name,
            task.product_id,
            task.online_sku,
            task.platform_store_item_code,
        )
        normalized_identity = "|".join(
            "".join(str(part).split()).lower() for part in identity_parts
        )
        task_id = hashlib.sha256(normalized_identity.encode("utf-8")).hexdigest()
        records[task_id] = {
            "task_id": task_id,
            "source": "1688_sku_offline",
            "source_status": source_status,
            "store_name": task.store_name,
            "platform": task.platform,
            "product_id": task.product_id,
            "online_sku": task.online_sku,
            "platform_store_item_code": task.platform_store_item_code,
            "handling": task.handling,
            "source_file": task.source_file,
            "source_row_number": task.source_row_number,
        }

    return list(records.values())


def write_jushuitan_handoff(path: Path, records: list[dict[str, Any]]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    return path


def resolve_store_account_binding(system_config: dict[str, Any], store_name: str) -> dict[str, Any]:
    execution_config = dict(system_config.get("execution", {}))
    raw_bindings = execution_config.get("store_accounts", system_config.get("store_accounts", []))
    bindings = [item for item in raw_bindings if isinstance(item, dict)]
    target_store = str(store_name or "").strip()
    for binding in bindings:
        binding_store = str(binding.get("store_name", binding.get("store", ""))).strip()
        aliases = [
            str(item).strip()
            for item in binding.get("store_aliases", binding.get("aliases", []))
            if str(item).strip()
        ]
        if binding_store and store_name_matches(target_store, binding_store, aliases=aliases):
            return dict(binding)
        if any(store_name_matches(target_store, alias) for alias in aliases):
            return dict(binding)

    if bool(execution_config.get("require_store_account_mapping", False)):
        raise OfflineAccountMappingError(f"No 1688 account/profile mapping configured for store '{target_store}'.")
    return {}


def build_store_operator_config(
    operator_config: dict[str, Any],
    *,
    account_binding: dict[str, Any],
    execution_config: dict[str, Any],
) -> dict[str, Any]:
    resolved = copy.deepcopy(operator_config)
    browser_config = resolved.setdefault("browser", {})
    if not account_binding:
        return resolved

    account_key = str(account_binding.get("account_key", "")).strip()
    profile_dir = str(
        account_binding.get("browser_profile_dir", account_binding.get("profile_dir", ""))
    ).strip()
    profile_directory = str(account_binding.get("profile_directory", "")).strip()
    debugger_address = str(account_binding.get("debugger_address", "")).strip()
    browser_type = str(account_binding.get("browser_type", "")).strip()

    if account_key:
        browser_config["sku_offline_account_key"] = account_key
    if browser_type:
        browser_config["browser_type"] = browser_type
    if debugger_address:
        browser_config["debugger_address"] = debugger_address
    elif profile_dir:
        if bool(execution_config.get("require_browser_profile_exists", False)) and not Path(profile_dir).exists():
            raise OfflineAccountMappingError(
                f"Configured browser profile does not exist for account '{account_key}': {profile_dir}"
            )
        browser_config["user_data_dir"] = profile_dir
        if "browser_type" not in browser_config:
            browser_config["browser_type"] = "edge"
    if profile_directory:
        browser_config["profile_directory"] = profile_directory

    return resolved


def should_stop_store_on_error(error_category: str, execution_config: dict[str, Any]) -> bool:
    configured = execution_config.get(
        "stop_store_on_error_categories",
        [
            "login_required",
            "risk_control",
            "store_mismatch",
            "identity_mismatch",
            "account_mapping",
            "browser_window_closed",
            "delivery_service_backfill_failed",
        ],
    )
    categories = {str(item).strip() for item in configured if str(item).strip()}
    return str(error_category or "").strip() in categories


def should_retry_offline_error(error_category: str, execution_config: dict[str, Any]) -> bool:
    configured = execution_config.get(
        "non_retryable_error_categories",
        [
            "task_not_found",
            "sku_not_found",
            "product_unavailable",
            "sole_sku_requires_product_offline",
            "campaign_restriction",
        ],
    )
    categories = {str(item).strip() for item in configured if str(item).strip()}
    return str(error_category or "").strip() not in categories


def process_scan_mode(
    *,
    project_root: Path,
    operator_config: dict[str, Any],
    system_config: dict[str, Any],
    watch_dir_override: str,
    skip_login: bool,
    limit: int,
    no_notify: bool,
    run_report: RunReportWriter,
) -> None:
    scan_config = dict(system_config.get("scan", {}))
    watch_dir = Path(str(watch_dir_override or "").strip() or scan_config.get("watch_dir", ""))
    if not str(watch_dir):
        raise ValueError("scan.watch_dir is not configured for 1688_sku_offline.")

    registry = ProcessedFileRegistry(
        scan_config.get("registry_path", project_root / "logs/sku_offline/processed_files.jsonl")
    )
    candidate_files = discover_scan_files(watch_dir)
    if not candidate_files:
        safe_console_print("[INFO] No offline task files found in watch_dir.")
        return

    for file_path in candidate_files:
        if registry.contains(file_path):
            safe_console_print(f"[INFO] Skip already processed file: {file_path.name}")
            continue

        identity = FileIdentity.from_path(file_path)
        try:
            preview = load_preview_for_files(system_config, [file_path], limit=limit)
            preview_path = write_preview_report(project_root, preview)
            print_preview(preview, preview_path)
            execute_preview(
                project_root=project_root,
                operator_config=operator_config,
                system_config=system_config,
                preview=preview,
                skip_login=skip_login,
                no_notify=no_notify,
                run_report=run_report,
            )
            moved_to = move_processed_file(file_path, Path(scan_config.get("processed_dir", watch_dir / "processed")))
            registry.record_identity(identity, status="processed", moved_to=str(moved_to))
        except Exception as exc:
            failed_target = move_processed_file(file_path, Path(scan_config.get("failed_dir", watch_dir / "failed")))
            registry.record_identity(identity, status="failed", moved_to=str(failed_target), note=str(exc))
            safe_console_print(f"[ERROR] File-level offline processing failed for {file_path.name}: {exc}")


def move_processed_file(source_path: Path, target_dir: Path) -> Path:
    target_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    target_path = target_dir / f"{timestamp}_{source_path.name}"
    shutil.move(str(source_path), str(target_path))
    return target_path


def build_run_report_payload(
    *,
    task,
    status: str,
    attempts: int,
    result_context: dict[str, Any] | None,
    exc: Exception | None = None,
    screenshot_path: str = "",
    html_snapshot_path: str = "",
) -> dict[str, Any]:
    result_context = result_context or {}
    payload = {
        "status": status,
        "store_name": task.store_name,
        "platform": task.platform,
        "product_id": task.product_id,
        "online_sku": task.online_sku,
        "platform_store_item_code": task.platform_store_item_code,
        "handling": task.handling,
        "source_file": task.source_file,
        "source_sheet": task.source_sheet,
        "source_row_number": task.source_row_number,
        "attempts": attempts,
        "result_context": result_context,
        "screenshot_path": screenshot_path,
        "html_snapshot_path": html_snapshot_path,
        "page_error_category": str(result_context.get("page_error_category", "")).strip(),
        "page_error_stage": str(result_context.get("page_error_stage", "")).strip(),
        "page_error_text": str(result_context.get("page_error_text", "")).strip(),
    }
    if exc:
        payload["error_type"] = exc.__class__.__name__
        payload["error_message"] = str(exc)
        payload["error_category"] = classify_offline_error(exc, result_context)
    return payload


def classify_offline_error(exc: Exception, result_context: dict[str, Any]) -> str:
    page_error_category = str(result_context.get("page_error_category", "")).strip()
    if page_error_category:
        return page_error_category

    error_type = exc.__class__.__name__
    error_message = str(exc or "")
    normalized_message = error_message.lower()
    if any(
        marker in normalized_message
        for marker in (
            "no such window",
            "target window already closed",
            "web view not found",
            "disconnected: not connected to devtools",
        )
    ):
        return "browser_window_closed"
    if error_type == "OfflineLoginRequiredError":
        return "login_required"
    if error_type == "OfflineRiskControlError":
        return "risk_control"
    if error_type == "OfflineIdentityMismatchError":
        return "identity_mismatch"
    if error_type == "OfflineAccountMappingError":
        return "account_mapping"
    if error_type == "PublishValidationError":
        return "business_validation"
    if error_type == "OfflineTaskNotFoundError":
        if "Available single-SKU codes:" in error_message:
            return "sku_not_found"
        return "task_not_found"
    if error_type == "OfflineStoreMismatchError":
        return "store_mismatch"
    if error_type == "OfflineTaskStateError":
        return "task_state"
    if error_type == "PublishSubmitError":
        return "submit_failed"
    return "automation_error"


def localize_error_category(error_category: str) -> str:
    mapping = {
        "login_required": "登录态失效",
        "risk_control": "验证码/风控拦截",
        "identity_mismatch": "身份校验不匹配",
        "account_mapping": "账号映射缺失",
        "business_validation": "业务校验失败",
        "sku_not_found": "SKU未匹配",
        "task_not_found": "任务未找到",
        "edit_route_rejected": "编辑入口被拒绝",
        "product_unavailable": "商品不可编辑",
        "sole_sku_requires_product_offline": "唯一在线SKU禁止单独下架",
        "campaign_restriction": "平台活动限制SKU下架",
        "store_mismatch": "店铺不匹配",
        "task_state": "状态识别失败",
        "submit_failed": "提交失败",
        "browser_window_closed": "浏览器窗口异常关闭",
        "delivery_service_backfill_failed": "配送服务自动补全失败",
        "automation_error": "脚本异常",
    }
    normalized = str(error_category or "").strip()
    return mapping.get(normalized, normalized or "未知异常")


def build_failure_notification_content(payload: dict[str, Any]) -> str:
    error_category = localize_error_category(str(payload.get("error_category", "")).strip())
    return (
        "1688 SKU下架执行失败\n"
        f"店铺名称：{payload.get('store_name', '')}\n"
        f"商品ID：{payload.get('product_id', '')}\n"
        f"单品货号：{payload.get('online_sku', '')}\n"
        f"尝试次数：{payload.get('attempts', '')}\n"
        f"错误分类：{error_category}\n"
        f"页面阶段：{payload.get('page_error_stage', '')}\n"
        f"页面提示：{payload.get('page_error_text', '')}\n"
        f"错误详情：{payload.get('error_message', '')}\n"
        f"截图路径：{payload.get('screenshot_path', '')}\n"
        f"页面快照：{payload.get('html_snapshot_path', '')}"
    )


def build_summary_notification_content(summary: dict[str, Any]) -> str:
    stopped_store_names = summary.get("stopped_store_names", [])
    if isinstance(stopped_store_names, list):
        stopped_store_text = "、".join(str(item) for item in stopped_store_names if str(item).strip())
    else:
        stopped_store_text = str(stopped_store_names or "")
    return (
        "1688 SKU下架批次完成\n"
        f"任务总数：{summary.get('total', 0)}\n"
        f"执行成功：{summary.get('success', 0)}\n"
        f"已是下架：{summary.get('already_offline', 0)}\n"
        f"执行失败：{summary.get('failed', 0)}\n"
        f"安全停止店铺：{summary.get('stopped_stores', 0)} {stopped_store_text}\n"
        f"重复数量：{summary.get('duplicate_count', 0)}\n"
        f"过滤数量：{summary.get('filtered_out_count', 0)}\n"
        f"报告路径：{summary.get('report_path', '')}\n"
        f"汇总路径：{summary.get('summary_path', '')}"
    )


def write_preview_report(project_root: Path, preview: dict[str, Any]) -> Path:
    preview_dir = project_root / "logs" / "sku_offline" / "previews"
    preview_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    target_path = preview_dir / f"{timestamp}.json"
    payload = dict(preview)
    payload["selected_tasks"] = [
        {
            "store_name": task.store_name,
            "product_id": task.product_id,
            "online_sku": task.online_sku,
            "source_row_number": task.source_row_number,
        }
        for task in preview.get("selected_tasks", [])
    ]
    payload["filtered_out_tasks"] = [
        {
            "store_name": task.store_name,
            "product_id": task.product_id,
            "online_sku": task.online_sku,
            "source_row_number": task.source_row_number,
        }
        for task in preview.get("filtered_out_tasks", [])
    ]
    payload["duplicate_tasks"] = [
        {
            "store_name": task.store_name,
            "product_id": task.product_id,
            "online_sku": task.online_sku,
            "source_row_number": task.source_row_number,
        }
        for task in preview.get("duplicate_tasks", [])
    ]
    target_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return target_path


def print_preview(preview: dict[str, Any], preview_path: Path) -> None:
    printable = {
        "source_files": preview.get("source_files", []),
        "loaded_count": preview.get("loaded_count", 0),
        "selected_count": preview.get("selected_count", 0),
        "filtered_out_count": preview.get("filtered_out_count", 0),
        "duplicate_count": preview.get("duplicate_count", 0),
        "stores": preview.get("stores", {}),
        "applied_filters": preview.get("applied_filters", {}),
        "loaded_store_names": preview.get("loaded_store_names", []),
        "filter_hint": preview.get("filter_hint", ""),
        "preview_path": str(preview_path),
    }
    safe_console_print(json.dumps(printable, ensure_ascii=False, indent=2))


def send_failure_notification(
    system_config: dict[str, Any],
    payload: dict[str, Any],
    *,
    disabled: bool,
) -> None:
    if disabled:
        return
    post_dingtalk_message(system_config, build_failure_notification_content(payload))


def send_summary_notification(
    system_config: dict[str, Any],
    summary: dict[str, Any],
    *,
    disabled: bool,
) -> None:
    if disabled:
        return
    post_dingtalk_message(system_config, build_summary_notification_content(summary))


def post_dingtalk_message(system_config: dict[str, Any], content: str) -> None:
    dingtalk = dict(system_config.get("notifications", {}).get("dingtalk", {}))
    if not dingtalk.get("enabled", False):
        return
    webhook, secret = resolve_dingtalk_credentials(dingtalk)
    if not webhook:
        safe_console_print("[WARN] DingTalk notification is enabled but webhook is empty.")
        return

    request_url = build_signed_webhook(webhook, secret)

    payload = json.dumps(
        {
            "msgtype": "text",
            "text": {
                "content": content,
            },
        },
        ensure_ascii=False,
    ).encode("utf-8")
    request = urllib.request.Request(
        request_url,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            response.read()
    except Exception as exc:
        safe_console_print(f"[WARN] DingTalk notification failed ({type(exc).__name__}).")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        safe_console_print(f"[ERROR] {exc}")
        sys.exit(1)
