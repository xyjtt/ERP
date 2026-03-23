from __future__ import annotations

import argparse
import json
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

from config_loader import load_json_with_local_override
from doctor import build_doctor_report, report_to_json, write_local_selector_templates
from exceptions import UploaderError
from preflight import build_db_report, build_env_report
from run_report import RunReportWriter

if TYPE_CHECKING:
    from browser_rpa import BrowserRPA
    from database import SQLServerLogger, StoreDefaultTemplate
    from parser import ProductRecord


PLATFORM_ALIASES = {
    "1688": "1688",
    "alibaba": "1688",
    "alibaba1688": "1688",
    "taobao": "taobao",
    "jd": "jd",
    "pdd": "pdd",
}


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Furniture uploader automation entrypoint.")
    parser.add_argument(
        "--system",
        default="jushuitan",
        help="Execution system, defaults to jushuitan.",
    )
    parser.add_argument(
        "--platform",
        required=True,
        choices=sorted(PLATFORM_ALIASES.keys()),
        help="Target ecommerce platform.",
    )
    parser.add_argument(
        "--file",
        required=True,
        help="Path to the CSV/XLSX template file.",
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
        help="Only process the first N products. Useful for MVP testing.",
    )
    parser.add_argument(
        "--skip-login",
        action="store_true",
        help="Skip the login step when the browser session is already authenticated.",
    )
    parser.add_argument(
        "--db-config",
        default="",
        help="Optional path to SQL Server config JSON for logging.",
    )
    parser.add_argument(
        "--doctor",
        action="store_true",
        help="Print a config readiness report and exit.",
    )
    parser.add_argument(
        "--doctor-json",
        action="store_true",
        help="Print the doctor report as JSON.",
    )
    parser.add_argument(
        "--strict-config",
        action="store_true",
        help="Fail fast when doctor finds missing required selectors.",
    )
    parser.add_argument(
        "--init-local-config",
        action="store_true",
        help="Write local override templates for missing selectors and exit.",
    )
    parser.add_argument(
        "--check-env",
        action="store_true",
        help="Run environment readiness checks and exit.",
    )
    parser.add_argument(
        "--check-db",
        action="store_true",
        help="Run database connectivity checks and exit. Requires --db-config.",
    )
    parser.add_argument(
        "--check-json",
        action="store_true",
        help="Print preflight checks as JSON.",
    )
    parser.add_argument(
        "--dump-effective-config",
        action="store_true",
        help="Print the merged runtime config with local overrides applied and exit.",
    )
    parser.add_argument(
        "--validate-only",
        action="store_true",
        help="Validate template data and exit without opening the browser.",
    )
    parser.add_argument(
        "--init-db-config",
        action="store_true",
        help="Create config/database.local.json from the example template and exit.",
    )
    parser.add_argument(
        "--init-operator-local-config",
        action="store_true",
        help="Create config/operator_config.local.json with private account placeholders and exit.",
    )
    return parser


def main() -> None:
    args = build_argument_parser().parse_args()

    if args.check_json and not (args.check_env or args.check_db):
        raise ValueError("--check-json must be used with --check-env or --check-db")

    project_root = Path(__file__).resolve().parents[1]
    config_dir = Path(args.config_dir)
    template_path = Path(args.file)
    platform_key = normalize_platform_key(args.platform)
    system_key = args.system.strip().lower()
    doctor_report = build_doctor_report(config_dir, system=system_key, platform=platform_key)

    if args.check_env:
        env_report = build_env_report(project_root)
        print(env_report.to_json() if args.check_json else env_report.to_text())
        return

    if args.check_db:
        if not args.db_config:
            raise ValueError("--check-db requires --db-config")
        db_report = build_db_report(Path(args.db_config))
        print(db_report.to_json() if args.check_json else db_report.to_text())
        return

    if args.init_local_config:
        generated = write_local_selector_templates(
            config_dir,
            system=system_key,
            platform=platform_key,
        )
        print("Initialized local config templates:")
        for label, path in generated.items():
            print(f"- {label}: {path}")
        return

    if args.init_db_config:
        db_config_path = initialize_db_config_template(
            config_dir,
            Path(args.db_config) if args.db_config else None,
        )
        print(f"Initialized database config template: {db_config_path}")
        return

    if args.init_operator_local_config:
        operator_path = initialize_operator_local_config(config_dir)
        print(f"Initialized operator local config template: {operator_path}")
        return

    if args.doctor:
        if args.doctor_json:
            print(report_to_json(doctor_report))
        else:
            print(doctor_report.to_text())
        return

    if args.dump_effective_config:
        print(
            json.dumps(
                load_effective_configs(config_dir, system_key, platform_key),
                ensure_ascii=False,
                indent=2,
            )
        )
        return

    from parser import (
        collect_sanitization_warnings,
        load_products,
        validate_products,
    )

    system_config = load_json_with_local_override(config_dir / "systems" / f"{system_key}.json")
    platform_config = load_json_with_local_override(config_dir / "platforms" / f"{platform_key}.json")
    operator_config = load_json_with_local_override(config_dir / "operator_config.json")
    category_config = load_json_with_local_override(config_dir / "furniture_categories.json")
    products = load_products(template_path)
    db_logger = build_db_logger(Path(args.db_config)) if args.db_config else None
    runtime_config = operator_config.get("runtime", {})
    run_report = RunReportWriter(
        project_root=project_root,
        report_dir=runtime_config.get("run_report_dir", "logs/run_reports"),
    )

    if args.limit > 0:
        products = products[: args.limit]

    if not products:
        print("[INFO] No products found in template.")
        return

    products = apply_store_defaults(products, db_logger)
    category_config = apply_category_history(products, platform_key, category_config, db_logger)

    for warning in collect_sanitization_warnings(products):
        print(f"[WARN] {warning}")

    validation = validate_products(products, platform_key, platform_config, category_config)
    for warning in validation.warnings:
        print(f"[WARN] {warning}")
    if not validation.ok:
        for error in validation.errors:
            print(f"[ERROR] {error}")
        raise ValueError("Validation failed. Fix the template or config before running the uploader.")
    if args.strict_config and not doctor_report.ok:
        print(doctor_report.to_text())
        raise ValueError("Strict config mode failed. Fill the missing selectors before running.")
    if args.validate_only:
        print_validation_summary(
            products=products,
            platform_key=platform_key,
            validation=validation,
            doctor_report=doctor_report,
            run_report=run_report,
            db_logger=db_logger,
        )
        if db_logger:
            db_logger.close()
        return

    from browser_rpa import BrowserRPA

    browser = BrowserRPA(operator_config.get("browser", {}), project_root)

    print(f"[INFO] Project root: {project_root}")
    print(f"[INFO] System: {system_key}")
    print(f"[INFO] Loaded {len(products)} products for platform {platform_key}.")
    print(f"[INFO] Run report: {run_report.info()['report_path']}")
    print(f"[INFO] Run summary: {run_report.info()['summary_path']}")

    browser.open()
    try:
        if not args.skip_login:
            browser.run_system_workflow(system_config, build_runtime_context(products[0], platform_key))
        for index, product in enumerate(products, start=1):
            print(f"[INFO] Processing product {index}/{len(products)}: {product.title}")
            browser.reset_runtime_artifacts()
            if db_logger:
                db_logger.upsert_publish_task(
                    build_publish_task_payload(product, platform_key, status="running")
                )
            try:
                result_context = browser.publish_product(platform_config, product, category_config)
                run_report.append(
                    build_run_report_payload(
                        product=product,
                        platform_key=platform_key,
                        status="success",
                        result_context=result_context,
                    )
                )
                if db_logger:
                    db_logger.upsert_publish_task(
                        build_publish_task_payload(
                            product,
                            platform_key,
                            status="success",
                            result_context=result_context,
                        )
                    )
                    db_logger.log_publish_result(
                        build_publish_result_payload(product, platform_key, result_context)
                    )
                    db_logger.upsert_store_default_template(
                        build_store_default_template_payload(product, platform_key)
                    )
                    db_logger.upsert_category_mapping_history(
                        build_category_mapping_payload(
                            product,
                            platform_key,
                            result_context,
                            success=True,
                        )
                    )
                    for attempt in result_context.get("match_candidate_attempts", []):
                        db_logger.log_match_candidate(
                            build_match_candidate_payload(product, platform_key, attempt)
                        )
            except Exception as exc:
                print(f"[ERROR] Product failed: {product.title} -> {exc}")
                run_report.append(
                    build_run_report_payload(
                        product=product,
                        platform_key=platform_key,
                        status="failed",
                        result_context=browser.last_result_context,
                        exc=exc,
                        screenshot_path=browser.last_screenshot_path,
                        html_snapshot_path=browser.last_html_snapshot_path,
                    )
                )
                if db_logger:
                    db_logger.upsert_publish_task(
                        build_publish_task_payload(
                            product,
                            platform_key,
                            status="failed",
                            result_context=browser.last_result_context,
                        )
                    )
                    db_logger.log_publish_error(
                        build_publish_error_payload(
                            product,
                            platform_key,
                            exc,
                            browser.last_screenshot_path,
                            browser.last_html_snapshot_path,
                        )
                    )
                    db_logger.upsert_category_mapping_history(
                        build_category_mapping_payload(
                            product,
                            platform_key,
                            browser.last_result_context,
                            success=False,
                        )
                    )
                for attempt in build_failed_match_attempts_payloads(
                    product,
                    platform_key,
                    browser.last_result_context,
                ):
                    if db_logger:
                        db_logger.log_match_candidate(attempt)
                if not runtime_config.get("continue_on_error", True):
                    raise
    finally:
        run_report.write_summary(
            {
                "system": system_key,
                "platform": platform_key,
                "total_products": len(products),
                "doctor_ok": doctor_report.ok,
            }
        )
        if db_logger:
            db_logger.close()
        browser.close()


def normalize_platform_key(raw_value: str) -> str:
    return PLATFORM_ALIASES[raw_value.strip().lower()]


def load_effective_configs(
    config_dir: Path,
    system_key: str,
    platform_key: str,
) -> dict[str, object]:
    return {
        "system": load_json_with_local_override(config_dir / "systems" / f"{system_key}.json"),
        "platform": load_json_with_local_override(config_dir / "platforms" / f"{platform_key}.json"),
        "operator": load_json_with_local_override(config_dir / "operator_config.json"),
        "categories": load_json_with_local_override(config_dir / "furniture_categories.json"),
    }


def initialize_db_config_template(
    config_dir: Path,
    target_path: Path | None = None,
) -> str:
    example_path = config_dir / "database.example.json"
    target_path = target_path or (config_dir / "database.local.json")
    if target_path.exists():
        return str(target_path)
    from database import choose_preferred_sqlserver_driver

    payload = json.loads(example_path.read_text(encoding="utf-8"))
    payload["driver"] = choose_preferred_sqlserver_driver()
    target_path.parent.mkdir(parents=True, exist_ok=True)
    target_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return str(target_path)


def initialize_operator_local_config(config_dir: Path) -> str:
    target_path = config_dir / "operator_config.local.json"
    if target_path.exists():
        return str(target_path)
    payload = {
        "accounts": {
            "1688": {
                "username": "",
                "password": "",
            }
        },
        "browser": {
            "browser_type": "chrome",
            "headless": False,
            "debugger_address": "",
            "user_data_dir": "",
            "profile_directory": "Default",
            "browser_binary_path": "",
            "chrome_binary_path": "",
            "keep_browser_open_on_close": True,
        },
    }
    target_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return str(target_path)


def build_db_logger(config_path: Path):
    from database import DatabaseConfig, SQLServerLogger

    logger = SQLServerLogger(DatabaseConfig.from_json(config_path))
    logger.connect()
    return logger


def build_runtime_context(product, platform_key: str) -> dict[str, str]:
    context = dict(product.raw)
    context["store_name"] = product.store_name
    context["store_label"] = product.store_label
    context["channel"] = product.channel or platform_key
    context["ship_from_template"] = product.ship_from_template
    context["freight_template"] = product.freight_template
    context["ship_time_template"] = product.ship_time_template
    context["length_cm"] = product.length_cm
    context["width_cm"] = product.width_cm
    context["height_cm"] = product.height_cm
    context["weight_g"] = product.weight_g
    return context


def apply_store_defaults(
    products: list[ProductRecord],
    db_logger,
) -> list[ProductRecord]:
    if not db_logger:
        return products

    enriched_products: list[ProductRecord] = []
    for product in products:
        defaults = db_logger.get_store_default_template(
            channel=product.channel,
            store_name=product.store_name,
        )
        if not defaults:
            enriched_products.append(product)
            continue
        enriched_products.append(merge_store_defaults(product, defaults))
    return enriched_products


def apply_category_history(
    products: list[ProductRecord],
    platform_key: str,
    category_config: dict,
    db_logger,
) -> dict:
    if not db_logger:
        return category_config

    enriched_config = dict(category_config)
    for product in products:
        keyword_text = resolve_category_keyword(product)
        if not keyword_text:
            continue
        mapped_path = db_logger.get_category_mapping(
            channel=product.channel or platform_key,
            store_name=product.store_name,
            keyword_text=keyword_text,
        )
        if not mapped_path:
            continue

        category_entry = dict(enriched_config.get(product.platform_category, {}))
        platform_categories = dict(category_entry.get("platform_categories", {}))
        platform_categories[platform_key] = mapped_path
        category_entry["display_name"] = category_entry.get("display_name", product.platform_category)
        category_entry["platform_categories"] = platform_categories
        category_entry["required_fields"] = category_entry.get("required_fields", [])
        enriched_config[product.platform_category] = category_entry
    return enriched_config


def merge_store_defaults(
    product: ProductRecord,
    defaults,
) -> ProductRecord:
    merged_raw = dict(product.raw)
    merged_raw["ship_from_template"] = product.ship_from_template or defaults.ship_from_template
    merged_raw["freight_template"] = product.freight_template or defaults.freight_template
    merged_raw["ship_time_template"] = product.ship_time_template or defaults.ship_time_template
    merged_raw["length_cm"] = product.length_cm or defaults.default_length_cm
    merged_raw["width_cm"] = product.width_cm or defaults.default_width_cm
    merged_raw["height_cm"] = product.height_cm or defaults.default_height_cm
    merged_raw["weight_g"] = product.weight_g or defaults.default_weight_g
    return replace(
        product,
        ship_from_template=merged_raw["ship_from_template"],
        freight_template=merged_raw["freight_template"],
        ship_time_template=merged_raw["ship_time_template"],
        length_cm=merged_raw["length_cm"],
        width_cm=merged_raw["width_cm"],
        height_cm=merged_raw["height_cm"],
        weight_g=merged_raw["weight_g"],
        raw=merged_raw,
    )


def build_publish_result_payload(product, platform_key: str, result_context: dict[str, object]) -> dict[str, object]:
    return {
        "task_id": product.task_id or fallback_task_id(product),
        "channel": product.channel or platform_key,
        "store_name": product.store_name,
        "outer_sku": product.outer_sku,
        "platform_link_id": result_context.get("platform_link_id", ""),
        "platform_link_url": result_context.get("platform_link_url", ""),
        "platform_item_title": product.title,
        "link_owner": product.link_owner,
        "operator_name": product.operator_name,
        "published_at": datetime.now(),
        "source_type": "excel",
        "source_record_id": product.source_record_id,
        "match_source_id": result_context.get("match_source_id", ""),
        "category_path": result_context.get("resolved_category_name", product.platform_category),
        "status": "success",
    }


def build_store_default_template_payload(product, platform_key: str) -> dict[str, object]:
    return {
        "channel": product.channel or platform_key,
        "store_name": product.store_name,
        "ship_from_template": product.ship_from_template,
        "freight_template": product.freight_template,
        "ship_time_template": product.ship_time_template,
        "default_length_cm": product.length_cm,
        "default_width_cm": product.width_cm,
        "default_height_cm": product.height_cm,
        "default_weight_g": product.weight_g,
    }


def build_category_mapping_payload(
    product,
    platform_key: str,
    result_context: dict[str, object] | None,
    *,
    success: bool,
) -> dict[str, object]:
    context = result_context or {}
    category_path = str(context.get("resolved_category_name", "")).strip() or product.platform_category
    return {
        "channel": product.channel or platform_key,
        "store_name": product.store_name,
        "keyword_text": resolve_category_keyword(product),
        "category_path": category_path,
        "success": success,
    }


def build_publish_task_payload(
    product,
    platform_key: str,
    *,
    status: str,
    result_context: dict[str, object] | None = None,
) -> dict[str, object]:
    context = result_context or {}
    return {
        "task_id": product.task_id or fallback_task_id(product),
        "source_type": "excel",
        "source_record_id": product.source_record_id,
        "channel": product.channel or platform_key,
        "store_name": product.store_name,
        "store_label": product.store_label,
        "outer_sku": product.outer_sku,
        "title": product.title,
        "category_hint": context.get("resolved_category_name", product.platform_category),
        "brand": product.raw.get("brand", ""),
        "material": product.raw.get("material", ""),
        "color": product.raw.get("color", ""),
        "size": product.raw.get("size", ""),
        "price": product.price,
        "quantity": product.quantity,
        "main_image": product.raw.get("main_image", ""),
        "detail_images": product.raw.get("detail_images", ""),
        "description": product.raw.get("description", ""),
        "ship_from_template": product.ship_from_template,
        "freight_template": product.freight_template,
        "ship_time_template": product.ship_time_template,
        "length_cm": product.length_cm,
        "width_cm": product.width_cm,
        "height_cm": product.height_cm,
        "weight_g": product.weight_g,
        "link_owner": product.link_owner,
        "operator_name": product.operator_name,
        "status": status,
    }


def build_run_report_payload(
    *,
    product,
    platform_key: str,
    status: str,
    result_context: dict[str, object],
    exc: Exception | None = None,
    screenshot_path: str = "",
    html_snapshot_path: str = "",
) -> dict[str, object]:
    error_type = ""
    error_message = ""
    step_name = ""
    if exc:
        error_type = exc.error_type if isinstance(exc, UploaderError) else exc.__class__.__name__
        error_message = str(exc)
        step_name = exc.step_name if isinstance(exc, UploaderError) else ""

    return {
        "task_id": product.task_id or fallback_task_id(product),
        "channel": product.channel or platform_key,
        "store_name": product.store_name,
        "outer_sku": product.outer_sku,
        "title": product.title,
        "link_owner": product.link_owner,
        "operator_name": product.operator_name,
        "status": status,
        "step_name": step_name,
        "error_type": error_type,
        "error_message": error_message,
        "platform_link_id": result_context.get("platform_link_id", ""),
        "platform_link_url": result_context.get("platform_link_url", ""),
        "match_source_id": result_context.get("match_source_id", ""),
        "current_url": result_context.get("current_url", ""),
        "page_title": result_context.get("page_title", ""),
        "screenshot_path": screenshot_path,
        "html_snapshot_path": html_snapshot_path,
    }


def build_publish_error_payload(
    product,
    platform_key: str,
    exc: Exception,
    screenshot_path: str,
    html_snapshot_path: str,
) -> dict[str, object]:
    step_name = exc.step_name if isinstance(exc, UploaderError) else ""
    error_type = exc.error_type if isinstance(exc, UploaderError) else exc.__class__.__name__
    return {
        "task_id": product.task_id or fallback_task_id(product),
        "channel": product.channel or platform_key,
        "store_name": product.store_name,
        "outer_sku": product.outer_sku,
        "step_name": step_name,
        "error_type": error_type,
        "error_message": str(exc),
        "screenshot_path": screenshot_path,
        "html_snapshot_path": html_snapshot_path,
        "operator_name": product.operator_name,
        "failed_at": datetime.now(),
        "status": "failed",
    }


def build_match_candidate_payload(
    product,
    platform_key: str,
    attempt: dict[str, object],
) -> dict[str, object]:
    return {
        "task_id": product.task_id or fallback_task_id(product),
        "outer_sku": product.outer_sku,
        "channel": product.channel or platform_key,
        "store_name": product.store_name,
        "candidate_source_id": attempt.get("candidate_source_id", ""),
        "candidate_store_name": attempt.get("candidate_store_name", ""),
        "candidate_title": attempt.get("candidate_title", ""),
        "candidate_use_result": attempt.get("candidate_use_result", ""),
        "candidate_error_message": attempt.get("candidate_error_message", ""),
        "candidate_error_type": attempt.get("candidate_error_type", ""),
        "candidate_attempted_at": datetime.now(),
    }


def build_failed_match_attempts_payloads(
    product,
    platform_key: str,
    result_context: dict[str, object],
) -> list[dict[str, object]]:
    raw_attempts = result_context.get("match_candidate_attempts", [])
    if not isinstance(raw_attempts, list):
        return []
    return [
        build_match_candidate_payload(product, platform_key, attempt)
        for attempt in raw_attempts
    ]


def fallback_task_id(product) -> str:
    seed = product.outer_sku or product.title or "task"
    return f"task-{seed}"


def resolve_category_keyword(product) -> str:
    return (
        str(product.raw.get("category_hint", "")).strip()
        or str(product.platform_category).strip()
        or str(product.title).strip()
    )


def print_validation_summary(
    *,
    products,
    platform_key: str,
    validation,
    doctor_report,
    run_report: RunReportWriter,
    db_logger,
) -> None:
    print("[INFO] Validation completed.")
    print(f"[INFO] Platform: {platform_key}")
    print(f"[INFO] Product count: {len(products)}")
    print(f"[INFO] Validation warnings: {len(validation.warnings)}")
    print(f"[INFO] Doctor ok: {doctor_report.ok}")
    print(f"[INFO] Run report path: {run_report.info()['report_path']}")
    print(f"[INFO] Run summary path: {run_report.info()['summary_path']}")
    print(f"[INFO] Database defaults: {'enabled' if db_logger else 'disabled'}")


if __name__ == "__main__":
    main()
