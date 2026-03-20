from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

from .browser_rpa import BrowserRPA
from .config_loader import load_json_with_local_override
from .database import DatabaseConfig, SQLServerLogger, choose_preferred_sqlserver_driver
from .doctor import build_doctor_report, write_local_selector_templates
from .exceptions import LinkOpsError
from .parser import collect_sanitization_warnings, load_tasks, validate_tasks
from .preflight import build_db_report, build_env_report
from .run_report import RunReportWriter


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Jushuitan link ops: batch modify online SKU codes.")
    parser.add_argument("--source", default="excel", choices=["excel", "api", "manual"], help="Input source type.")
    parser.add_argument("--file", default="", help="Excel or CSV file path.")
    parser.add_argument("--default-platform", default="", help="Default platform for Excel rows without platform field.")
    parser.add_argument("--default-shop-name", default="", help="Default shop_name for Excel rows without shop field.")
    parser.add_argument("--default-operator-name", default="", help="Default operator_name for Excel rows without operator field.")
    parser.add_argument("--discontinued-target-code", default="txcj", help="Replacement online SKU code for discontinued-off-shelf source.")
    parser.add_argument("--expected-stat-date", default="", help="Expected 统计日期新 for discontinued-off-shelf source. Defaults to today.")
    parser.add_argument("--discontinued-action", default="下架", help="Expected 处理说明 for discontinued-off-shelf source.")
    parser.add_argument("--discontinued-remark", default="京喜需下架", help="Expected 下架备注 for discontinued-off-shelf source.")
    parser.add_argument("--api-url", default="", help="API URL for API source.")
    parser.add_argument("--api-method", default="GET", help="API method.")
    parser.add_argument("--api-headers", default="", help="API headers JSON text.")
    parser.add_argument("--api-body", default="", help="API body text.")
    parser.add_argument("--api-records-path", default="", help="Dot path to records array inside API JSON.")
    parser.add_argument("--manual-json", default="", help="Manual input JSON object or array.")
    parser.add_argument("--config-dir", default="", help="Config directory path.")
    parser.add_argument("--db-config", default="", help="SQL Server local config path.")
    parser.add_argument("--skip-login", action="store_true", help="Skip login if session is already available.")
    parser.add_argument("--limit", type=int, default=0, help="Only process first N rows.")
    parser.add_argument("--validate-only", action="store_true", help="Validate input and exit.")
    parser.add_argument("--doctor", action="store_true", help="Inspect config readiness and exit.")
    parser.add_argument("--doctor-json", action="store_true", help="Print doctor report as JSON.")
    parser.add_argument("--check-env", action="store_true", help="Run environment preflight and exit.")
    parser.add_argument("--check-db", action="store_true", help="Run database preflight and exit.")
    parser.add_argument("--check-json", action="store_true", help="Print preflight report as JSON.")
    parser.add_argument("--init-local-config", action="store_true", help="Generate selector local override template and exit.")
    parser.add_argument("--init-db-config", action="store_true", help="Generate database.local.json and exit.")
    return parser


def main() -> None:
    args = build_argument_parser().parse_args()
    project_root = resolve_project_root()
    config_dir = Path(args.config_dir) if args.config_dir else project_root / "config"

    if args.check_json and not (args.check_env or args.check_db):
        raise ValueError("--check-json must be used with --check-env or --check-db")

    if args.check_env:
        report = build_env_report(project_root)
        print(report.to_json() if args.check_json else report.to_text())
        return

    if args.check_db:
        if not args.db_config:
            raise ValueError("--check-db requires --db-config")
        report = build_db_report(args.db_config)
        print(report.to_json() if args.check_json else report.to_text())
        return

    if args.init_local_config:
        result = write_local_selector_templates(config_dir)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return

    if args.init_db_config:
        output_path = initialize_db_config_template(config_dir)
        print(output_path)
        return

    doctor_report = build_doctor_report(config_dir)
    if args.doctor:
        print(doctor_report.to_json() if args.doctor_json else doctor_report.to_text())
        return

    tasks = load_tasks(
        source_type=args.source,
        file_path=args.file,
        api_url=args.api_url,
        api_method=args.api_method,
        api_headers=args.api_headers,
        api_body=args.api_body,
        api_records_path=args.api_records_path,
        manual_payload=args.manual_json,
        default_platform=args.default_platform,
        default_shop_name=args.default_shop_name,
        default_operator_name=args.default_operator_name,
        discontinued_target_code=args.discontinued_target_code,
        expected_stat_date=args.expected_stat_date,
        discontinued_action=args.discontinued_action,
        discontinued_remark=args.discontinued_remark,
    )

    if args.limit > 0:
        tasks = tasks[: args.limit]
    if not tasks:
        print("[INFO] No tasks found.")
        return

    for warning in collect_sanitization_warnings(tasks):
        print(f"[WARN] {warning}")

    validation = validate_tasks(tasks)
    for warning in validation.warnings:
        print(f"[WARN] {warning}")
    if not validation.ok:
        for error in validation.errors:
            print(f"[ERROR] {error}")
        raise ValueError("Validation failed")

    if args.validate_only:
        print(f"[INFO] Validation ok. Task count: {len(tasks)}")
        return

    system_config = load_json_with_local_override(config_dir / "systems" / "jushuitan.json")
    operation_config = load_json_with_local_override(config_dir / "operations" / "code_change.json")
    db_logger = build_db_logger(Path(args.db_config)) if args.db_config else None
    run_report = RunReportWriter(project_root=project_root)

    browser = BrowserRPA(system_config, operation_config, project_root)
    browser.open()
    try:
        browser.login(skip_login=args.skip_login)
        browser.open_manage_page()

        for index, task in enumerate(tasks, start=1):
            print(f"[INFO] Processing {index}/{len(tasks)} task_id={task.task_id}")
            if db_logger:
                db_logger.upsert_task(build_task_payload(task, status="running"))
            try:
                result = browser.process_task(task)
                run_report.append(build_run_payload(task, result=result))
                if db_logger:
                    db_logger.upsert_task(build_task_payload(task, status=result.status))
                    db_logger.log_result(build_result_payload(task, result))
                print(
                    f"[INFO] task={task.task_id} matched={result.matched_count_before} remaining={result.remaining_count_after} status={result.status}"
                )
            except Exception as exc:
                browser.capture_runtime_artifacts(task.task_id)
                run_report.append(
                    build_run_payload(
                        task,
                        status="failed",
                        exc=exc,
                        result_context=browser.last_result_context,
                        screenshot_path=browser.last_screenshot_path,
                        html_snapshot_path=browser.last_html_snapshot_path,
                    )
                )
                if db_logger:
                    db_logger.upsert_task(build_task_payload(task, status="failed"))
                    db_logger.log_error(
                        build_error_payload(
                            task,
                            exc,
                            browser.last_screenshot_path,
                            browser.last_html_snapshot_path,
                            browser.last_result_context,
                        )
                    )
                print(f"[ERROR] task={task.task_id} failed: {exc}")
    finally:
        run_report.write_summary(
            {
                "task_count": len(tasks),
                "doctor_ok": doctor_report.ok,
                "generated_at": datetime.now().isoformat(timespec="seconds"),
            }
        )
        if db_logger:
            db_logger.close()
        browser.close()


def resolve_project_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[1]


def initialize_db_config_template(config_dir: Path) -> str:
    target_path = config_dir / "database.local.json"
    if target_path.exists():
        return str(target_path)
    payload = {
        "driver": choose_preferred_sqlserver_driver(),
        "host": "192.168.151.76",
        "port": 1433,
        "database": "JianSun",
        "username": "sa",
        "password": "",
        "encrypt": False,
        "trust_server_certificate": True,
    }
    target_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return str(target_path)


def build_db_logger(config_path: Path) -> SQLServerLogger:
    logger = SQLServerLogger(DatabaseConfig.from_json(config_path))
    logger.connect()
    return logger


def build_task_payload(task, *, status: str) -> dict[str, object]:
    return {
        "task_id": task.task_id,
        "source_type": task.source_type,
        "source_record_id": task.source_record_id,
        "platform": task.platform,
        "shop_name": task.shop_name,
        "old_online_sku_code": task.old_online_sku_code,
        "new_online_sku_code": task.new_online_sku_code,
        "operator_name": task.operator_name,
        "remark": task.remark,
        "status": status,
    }


def build_result_payload(task, result) -> dict[str, object]:
    return {
        "task_id": task.task_id,
        "platform": task.platform,
        "shop_name": task.shop_name,
        "old_online_sku_code": task.old_online_sku_code,
        "new_online_sku_code": task.new_online_sku_code,
        "matched_count_before": result.matched_count_before,
        "remaining_count_after": result.remaining_count_after,
        "status": result.status,
        "operator_name": task.operator_name,
    }


def build_error_payload(
    task,
    exc: Exception,
    screenshot_path: str,
    html_snapshot_path: str,
    result_context: dict[str, object],
) -> dict[str, object]:
    return {
        "task_id": task.task_id,
        "platform": task.platform,
        "shop_name": task.shop_name,
        "old_online_sku_code": task.old_online_sku_code,
        "new_online_sku_code": task.new_online_sku_code,
        "step_name": exc.step_name if isinstance(exc, LinkOpsError) else "",
        "error_type": exc.error_type if isinstance(exc, LinkOpsError) else exc.__class__.__name__,
        "error_message": str(exc),
        "screenshot_path": screenshot_path,
        "html_snapshot_path": html_snapshot_path,
        "current_url": str(result_context.get("current_url", "")),
        "page_title": str(result_context.get("page_title", "")),
    }


def build_run_payload(
    task,
    *,
    result=None,
    status: str = "",
    exc: Exception | None = None,
    result_context: dict[str, object] | None = None,
    screenshot_path: str = "",
    html_snapshot_path: str = "",
) -> dict[str, object]:
    if result is not None:
        return {
            "task_id": task.task_id,
            "platform": task.platform,
            "shop_name": task.shop_name,
            "old_online_sku_code": task.old_online_sku_code,
            "new_online_sku_code": task.new_online_sku_code,
            "matched_count_before": result.matched_count_before,
            "remaining_count_after": result.remaining_count_after,
            "status": result.status,
            "current_url": result.current_url,
            "page_title": result.page_title,
            "success_message": result.success_message,
        }

    context = result_context or {}
    return {
        "task_id": task.task_id,
        "platform": task.platform,
        "shop_name": task.shop_name,
        "old_online_sku_code": task.old_online_sku_code,
        "new_online_sku_code": task.new_online_sku_code,
        "status": status or "failed",
        "error_type": exc.error_type if isinstance(exc, LinkOpsError) else exc.__class__.__name__,
        "error_message": str(exc) if exc else "",
        "step_name": exc.step_name if isinstance(exc, LinkOpsError) else "",
        "current_url": str(context.get("current_url", "")),
        "page_title": str(context.get("page_title", "")),
        "screenshot_path": screenshot_path,
        "html_snapshot_path": html_snapshot_path,
    }


if __name__ == "__main__":
    main()
