from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import UTC, datetime
from urllib.parse import quote_plus

import pyodbc

from platform_mediation.domain.enums import (
    AttemptStatus,
    ExecutorType,
    ReflowStatus,
    TaskItemStatus,
    TaskStatus,
    TaskType,
)
from platform_mediation.domain.models import (
    ReflowEvent,
    SourceSnapshot,
    Task,
    TaskArtifact,
    TaskAttempt,
    TaskItem,
)
from platform_mediation.repositories.sqlalchemy_repo import SqlAlchemyPlatformMediationRepository


@dataclass(frozen=True)
class SmokeArgs:
    server: str
    database: str
    username: str
    password: str
    driver: str


def parse_args() -> SmokeArgs:
    parser = argparse.ArgumentParser(description="Run a SQLAlchemy smoke test against SQL Server.")
    parser.add_argument("--server", required=True)
    parser.add_argument("--database", required=True)
    parser.add_argument("--username", required=True)
    parser.add_argument("--password", required=True)
    parser.add_argument(
        "--driver",
        default="SQL Server Native Client 10.0",
        help="ODBC driver name installed on the current Windows machine.",
    )
    ns = parser.parse_args()
    return SmokeArgs(
        server=ns.server,
        database=ns.database,
        username=ns.username,
        password=ns.password,
        driver=ns.driver,
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


def main() -> None:
    args = parse_args()
    repo = SqlAlchemyPlatformMediationRepository.from_database_url(build_database_url(args))
    run_id = datetime.now(UTC).strftime("%Y%m%d%H%M%S")
    now = datetime.now(UTC)
    task_code = f"smoke_{run_id}"
    snapshot_code = f"snapshot_{run_id}"
    attempt_code = f"attempt_{run_id}"
    artifact_code = f"artifact_{run_id}"
    event_code = f"event_{run_id}"
    sku_id = f"SMOKE-SKU-{run_id}"

    conn = build_pyodbc_connection(args)
    conn.autocommit = False
    cursor = conn.cursor()

    try:
        task = repo.save_task(
            Task(
                task_id=task_code,
                task_type=TaskType.LISTING,
                platform="1688",
                shop_id=999001,
                status=TaskStatus.CREATED,
                created_by="codex-smoke-test",
                executor_type=ExecutorType.RPA,
                adapter_code="1688_adapter",
                mapping_version="smoke-v1",
                payload_json={"mode": "smoke"},
                remark="sqlserver smoke test",
                total_count=1,
                created_at=now,
            )
        )
        item = repo.save_task_item(
            TaskItem(
                sku_id=sku_id,
                status=TaskItemStatus.CREATED,
                task_pk=task.pk,
                mapping_version="smoke-v1",
                source_version="source-v1",
                payload_json={"sku": sku_id},
            )
        )
        snapshot = repo.save_snapshot(
            SourceSnapshot(
                snapshot_id=snapshot_code,
                task_pk=task.pk,
                task_item_pk=item.pk,
                sku_id=sku_id,
                source_type="smoke",
                source_record_key=f"src_{run_id}",
                snapshot_json={"sku": sku_id, "title": "smoke"},
                version="source-v1",
                created_at=now,
            )
        )
        item.source_snapshot_pk = snapshot.pk
        item = repo.save_task_item(item)
        attempt = repo.save_attempt(
            TaskAttempt(
                attempt_id=attempt_code,
                task_pk=task.pk,
                task_item_pk=item.pk,
                attempt_no=1,
                adapter_code="1688_adapter",
                adapter_version="v1",
                mapping_version="smoke-v1",
                source_snapshot_pk=snapshot.pk,
                source_version="source-v1",
                status=AttemptStatus.SUCCESS,
                raw_result_json={"result": "ok"},
                started_at=now,
                completed_at=now,
                duration_ms=1200,
            )
        )
        artifact = repo.save_artifact(
            TaskArtifact(
                artifact_id=artifact_code,
                task_attempt_pk=attempt.pk,
                task_pk=task.pk,
                task_item_pk=item.pk,
                artifact_type="run_report",
                artifact_path=f"D:/tmp/{run_id}.json",
                metadata_json={"source": "smoke"},
                created_at=now,
            )
        )
        event = repo.save_reflow_event(
            ReflowEvent(
                event_id=event_code,
                event_type="item_listed",
                platform="1688",
                shop_id=task.shop_id,
                task_pk=task.pk,
                task_item_pk=item.pk,
                sku_id=sku_id,
                platform_item_id="platform-item-smoke",
                platform_sku_id="platform-sku-smoke",
                idempotency_key=f"reflow_{run_id}",
                payload_json={"task_id": task.task_id},
                status=ReflowStatus.PENDING,
                created_at=now,
                updated_at=now,
            )
        )

        loaded_task = repo.get_task(task.task_id)
        attempts = repo.list_attempts(task.pk)
        artifacts = repo.list_artifacts(task.pk)
        events = repo.list_reflow_events(task.pk)

        print("sqlalchemy_smoke=ok")
        print(f"task_pk={task.pk}")
        print(f"task_item_pk={item.pk}")
        print(f"snapshot_pk={snapshot.pk}")
        print(f"attempt_pk={attempt.pk}")
        print(f"artifact_pk={artifact.pk}")
        print(f"event_pk={event.pk}")
        print(f"loaded_task={loaded_task.task_id if loaded_task else '<none>'}")
        print(f"attempt_count={len(attempts)}")
        print(f"artifact_count={len(artifacts)}")
        print(f"reflow_count={len(events)}")
    finally:
        try:
            cursor.execute("DELETE FROM dbo.task_artifact WHERE artifact_id = ?", artifact_code)
            cursor.execute("DELETE FROM dbo.reflow_event WHERE event_id = ?", event_code)
            cursor.execute("DELETE FROM dbo.task_attempt WHERE attempt_id = ?", attempt_code)
            cursor.execute("UPDATE dbo.task_item SET source_snapshot_id = NULL WHERE sku_id = ?", sku_id)
            cursor.execute("DELETE FROM dbo.source_snapshot WHERE snapshot_id = ?", snapshot_code)
            cursor.execute("DELETE FROM dbo.task_item WHERE sku_id = ?", sku_id)
            cursor.execute("DELETE FROM dbo.task WHERE task_id = ?", task_code)
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()


if __name__ == "__main__":
    main()
