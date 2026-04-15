from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote_plus

import pyodbc

from platform_mediation.adapters.alibaba_1688_adapter import Alibaba1688Adapter
from platform_mediation.adapters.registry import AdapterRegistry
from platform_mediation.application.services.dispatch_service import DispatchService
from platform_mediation.application.services.lock_service import InMemoryLockService
from platform_mediation.application.services.mapping_service import MappingService
from platform_mediation.application.services.task_service import TaskService
from platform_mediation.config import Settings
from platform_mediation.domain.standard_product_model import DEFAULT_1688_MAPPING
from platform_mediation.repositories.sqlalchemy_repo import SqlAlchemyPlatformMediationRepository
from platform_mediation.schemas.task import CreateTaskItemRequest, CreateTaskRequest


@dataclass(frozen=True)
class SmokeArgs:
    server: str
    database: str
    username: str
    password: str
    driver: str
    payload_file: Path


def parse_args() -> SmokeArgs:
    parser = argparse.ArgumentParser(description="Run mapping smoke test on SQL Server.")
    parser.add_argument("--server", required=True)
    parser.add_argument("--database", required=True)
    parser.add_argument("--username", required=True)
    parser.add_argument("--password", required=True)
    parser.add_argument(
        "--driver",
        default="SQL Server Native Client 10.0",
        help="ODBC driver name.",
    )
    parser.add_argument(
        "--payload-file",
        default=str(
            Path(__file__).resolve().parents[2]
            / "furniture-uploader"
            / "templates"
            / "1688_variant_quickstart.json"
        ),
    )
    ns = parser.parse_args()
    return SmokeArgs(
        server=ns.server,
        database=ns.database,
        username=ns.username,
        password=ns.password,
        driver=ns.driver,
        payload_file=Path(ns.payload_file),
    )


def build_database_url(args: SmokeArgs) -> str:
    return (
        f"mssql+pyodbc://{quote_plus(args.username)}:{quote_plus(args.password)}@"
        f"{args.server}/{args.database}?driver={quote_plus(args.driver)}&TrustServerCertificate=yes"
    )


def build_pyodbc_connection(args: SmokeArgs) -> pyodbc.Connection:
    return pyodbc.connect(
        f"DRIVER={{{args.driver}}};"
        f"SERVER={args.server};"
        f"DATABASE={args.database};"
        f"UID={args.username};"
        f"PWD={args.password};"
        "Connection Timeout=5"
    )


def load_payload(payload_file: Path) -> dict:
    payload = json.loads(payload_file.read_text(encoding="utf-8"))
    if isinstance(payload, list):
        if not payload:
            raise ValueError(f"No payloads found in {payload_file}")
        return dict(payload[0])
    if isinstance(payload, dict):
        variants = payload.get("variants")
        if isinstance(variants, list) and variants:
            return dict(variants[0])
        return dict(payload)
    raise ValueError(f"Unsupported payload structure in {payload_file}")


def cleanup_task(conn: pyodbc.Connection, task_pk: int) -> None:
    cursor = conn.cursor()
    cursor.execute("DELETE FROM dbo.task_artifact WHERE task_id = ?", task_pk)
    cursor.execute("DELETE FROM dbo.reflow_event WHERE task_id = ?", task_pk)
    cursor.execute("DELETE FROM dbo.task_attempt WHERE task_id = ?", task_pk)
    cursor.execute("UPDATE dbo.task_item SET source_snapshot_id = NULL WHERE task_id = ?", task_pk)
    cursor.execute("DELETE FROM dbo.source_snapshot WHERE task_id = ?", task_pk)
    cursor.execute("DELETE FROM dbo.task_item WHERE task_id = ?", task_pk)
    cursor.execute("DELETE FROM dbo.task WHERE id = ?", task_pk)


def main() -> None:
    args = parse_args()
    payload = load_payload(args.payload_file)
    project_root = Path(__file__).resolve().parents[1]
    furniture_root = project_root.parent / "furniture-uploader"

    mapping_service = MappingService()

    print("=== Mapping Service Test ===")
    print(f"mapping_code={DEFAULT_1688_MAPPING.mapping_code}")
    print(f"mapping_version={DEFAULT_1688_MAPPING.version}")

    standard = mapping_service.transform_source_to_standard(payload, DEFAULT_1688_MAPPING)
    print(f"standard_sku_id={standard.sku_id}")
    print(f"standard_name={standard.name}")
    print(f"standard_brand={standard.brand}")
    print(f"standard_price={standard.price}")
    print(f"standard_quantity={standard.quantity}")

    is_valid, errors = mapping_service.validate_standard_product(standard, DEFAULT_1688_MAPPING)
    print(f"validation_valid={is_valid}")
    if errors:
        print(f"validation_errors={errors}")

    platform_model = mapping_service.transform_standard_to_platform(standard, DEFAULT_1688_MAPPING)
    print(f"platform_variant_id={platform_model.variant_id}")
    print(f"platform_title={platform_model.title}")
    print(f"platform_price_value={platform_model.price_value}")
    print(f"platform_quantity={platform_model.quantity}")
    print(f"platform_shop_id={platform_model.shop_id}")

    full_result = mapping_service.transform_full(payload, "1688_listing", "v1")
    print(f"full_transform_variant_id={full_result.variant_id}")
    print(f"full_transform_title={full_result.title}")

    settings = Settings(
        app_name="platform-mediation-platform",
        environment="local",
        api_prefix="/api/v1",
        adapter_mode="mock",
        repository_backend="sqlalchemy",
        database_url=build_database_url(args),
        auto_create_schema=False,
        enable_real_1688_execution=False,
        furniture_uploader_root=furniture_root,
    )
    repository = SqlAlchemyPlatformMediationRepository.from_database_url(settings.database_url)
    task_service = TaskService(repository)
    dispatch_service = DispatchService(
        repository,
        AdapterRegistry([Alibaba1688Adapter(settings)]),
        InMemoryLockService(),
    )

    conn = build_pyodbc_connection(args)
    conn.autocommit = False

    task = None
    try:
        transformed_payload = {
            "sku_id": full_result.variant_id or full_result.source_product_id or "MAPPING-SMOKE-SKU",
            "variant_id": full_result.variant_id,
            "name": full_result.title,
            "brand": full_result.attributes.get("brand"),
            "material": full_result.attributes.get("material"),
            "color": full_result.attributes.get("color"),
            "size": full_result.attributes.get("size"),
            "price": full_result.price_value,
            "quantity": full_result.quantity,
            "main_images": full_result.main_images,
            "detail_images": full_result.detail_images,
            "shop_id": full_result.shop_id,
            "shop_name": full_result.shop_name,
            "operator_name": full_result.operator_name,
            "mapping_version": DEFAULT_1688_MAPPING.version,
        }

        task = task_service.create_task(
            CreateTaskRequest(
                task_type="listing",
                platform="1688",
                shop_id=int(str(full_result.shop_id or 10001).strip() or 10001),
                executor_type="rpa",
                priority=1,
                mapping_version=DEFAULT_1688_MAPPING.version,
                created_by="mapping-smoke-test",
                items=[
                    CreateTaskItemRequest(
                        sku_id=full_result.variant_id or full_result.source_product_id or "MAPPING-SMOKE-SKU",
                        payload=transformed_payload,
                        source_type="mapping_transform",
                        source_record_key=full_result.variant_id or full_result.source_product_id or "mapping-smoke",
                        mapping_version=DEFAULT_1688_MAPPING.version,
                    )
                ],
            )
        )
        task = dispatch_service.dispatch_task(task.task_id)

        print("=== Task Execution Test ===")
        print("mapping_smoke=ok")
        print(f"task_id={task.task_id}")
        print(f"task_status={task.status.value}")
        print(f"mapping_version_used={DEFAULT_1688_MAPPING.version}")
    finally:
        if task and task.pk:
            try:
                cleanup_task(conn, task.pk)
                conn.commit()
            except Exception:
                conn.rollback()
                raise
        conn.close()


if __name__ == "__main__":
    main()