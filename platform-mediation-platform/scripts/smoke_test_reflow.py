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
from platform_mediation.application.services.reflow_service import MockReflowHandler, ReflowService
from platform_mediation.application.services.task_service import TaskService
from platform_mediation.config import Settings
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
    parser = argparse.ArgumentParser(description="Run reflow smoke test on SQL Server.")
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

    reflow_service = ReflowService(
        repository,
        MockReflowHandler(),
        poll_interval_seconds=1.0,
        max_iterations=3,
        max_retry_count=3,
        retry_delay_seconds=60,
        on_event_processed=lambda event_id, success: print(
            f"[Reflow] Processed event: {event_id}, success={success}"
        ),
    )

    conn = build_pyodbc_connection(args)
    conn.autocommit = False

    task = None
    try:
        task = task_service.create_task(
            CreateTaskRequest(
                task_type="listing",
                platform="1688",
                shop_id=int(str(payload.get("shop_id") or 10001).strip() or 10001),
                executor_type="rpa",
                priority=1,
                mapping_version="1688.v1",
                created_by="reflow-smoke-test",
                items=[
                    CreateTaskItemRequest(
                        sku_id=str(
                            payload.get("image_source_sku")
                            or payload.get("source_product_id")
                            or payload.get("variant_id")
                            or "REFLOW-SMOKE-SKU"
                        ).strip(),
                        payload=payload,
                        source_type="variant_json",
                        source_record_key=str(payload.get("variant_id") or "variant-reflow-smoke").strip(),
                    )
                ],
            )
        )
        task = dispatch_service.dispatch_task(task.task_id)
        print(f"task_id={task.task_id}")
        print(f"task_status={task.status.value}")

        reflow_events_before = repository.list_reflow_events(task.pk)
        print(f"reflow_count_before={len(reflow_events_before)}")

        if reflow_events_before:
            print(f"reflow_status_before={reflow_events_before[0].status.value}")

        reflow_service.start()

        reflow_events_after = repository.list_reflow_events(task.pk)
        print("reflow_smoke=ok")
        print(f"reflow_count_after={len(reflow_events_after)}")

        if reflow_events_after:
            print(f"reflow_status_after={reflow_events_after[0].status.value}")
            print(f"reflow_event_id={reflow_events_after[0].event_id}")
            print(f"reflow_event_type={reflow_events_after[0].event_type}")
            print(f"reflow_idempotency_key={reflow_events_after[0].idempotency_key}")
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