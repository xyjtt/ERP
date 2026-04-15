from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from urllib.parse import quote_plus

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from platform_mediation.adapters.alibaba_1688_adapter import Alibaba1688Adapter
from platform_mediation.adapters.registry import AdapterRegistry
from platform_mediation.application.services.lock_service import InMemoryLockService
from platform_mediation.application.services.worker_service import WorkerService
from platform_mediation.config import Settings, get_settings
from platform_mediation.repositories.sqlalchemy_repo import SqlAlchemyPlatformMediationRepository


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run platform mediation background worker.")
    parser.add_argument(
        "--server",
        help="SQL Server hostname. Overrides PLATFORM_MEDIATION_DATABASE_URL.",
    )
    parser.add_argument(
        "--database",
        help="SQL Server database name. Overrides PLATFORM_MEDIATION_DATABASE_URL.",
    )
    parser.add_argument(
        "--username",
        help="SQL Server username. Overrides PLATFORM_MEDIATION_DATABASE_URL.",
    )
    parser.add_argument(
        "--password",
        help="SQL Server password. Overrides PLATFORM_MEDIATION_DATABASE_URL.",
    )
    parser.add_argument(
        "--driver",
        default="SQL Server Native Client 10.0",
        help="ODBC driver name.",
    )
    parser.add_argument(
        "--poll-interval",
        type=float,
        default=5.0,
        help="Poll interval in seconds.",
    )
    parser.add_argument(
        "--max-iterations",
        type=int,
        default=None,
        help="Maximum iterations before stopping. Useful for testing.",
    )
    parser.add_argument(
        "--adapter-mode",
        choices=["mock", "preview", "validate", "execute"],
        default=None,
        help="Adapter mode. Overrides PLATFORM_MEDIATION_ADAPTER_MODE.",
    )
    parser.add_argument(
        "--enable-real-execution",
        action="store_true",
        help="Enable real 1688 execution.",
    )
    return parser.parse_args()


def build_database_url_from_args(args: argparse.Namespace) -> str | None:
    if not all([args.server, args.database, args.username, args.password]):
        return None
    return (
        f"mssql+pyodbc://{quote_plus(args.username)}:{quote_plus(args.password)}@"
        f"{args.server}/{args.database}?driver={quote_plus(args.driver)}&TrustServerCertificate=yes"
    )


def main() -> None:
    args = parse_args()

    database_url = build_database_url_from_args(args)
    if database_url:
        os.environ["PLATFORM_MEDIATION_DATABASE_URL"] = database_url
        os.environ["PLATFORM_MEDIATION_REPOSITORY"] = "sqlalchemy"

    if args.adapter_mode:
        os.environ["PLATFORM_MEDIATION_ADAPTER_MODE"] = args.adapter_mode

    if args.enable_real_execution:
        os.environ["PLATFORM_MEDIATION_ENABLE_REAL_1688_EXECUTION"] = "1"

    settings = get_settings()

    repository = SqlAlchemyPlatformMediationRepository.from_database_url(settings.database_url)
    adapter_registry = AdapterRegistry([Alibaba1688Adapter(settings)])
    lock_service = InMemoryLockService()

    worker = WorkerService(
        repository,
        adapter_registry,
        lock_service,
        poll_interval_seconds=args.poll_interval,
        max_iterations=args.max_iterations,
        on_task_dispatched=lambda task_id: print(f"[Worker] Dispatched task: {task_id}"),
    )

    print(f"[Worker] Starting with adapter_mode={settings.adapter_mode}")
    print(f"[Worker] Poll interval: {args.poll_interval}s")
    if args.max_iterations:
        print(f"[Worker] Max iterations: {args.max_iterations}")

    worker.start()

    print("[Worker] Stopped.")


if __name__ == "__main__":
    main()