from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from urllib.parse import quote_plus

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from platform_mediation.application.services.reflow_service import MockReflowHandler, ReflowService
from platform_mediation.config import Settings, get_settings
from platform_mediation.repositories.sqlalchemy_repo import SqlAlchemyPlatformMediationRepository


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run platform mediation reflow worker.")
    parser.add_argument(
        "--server",
        help="SQL Server hostname. Overrides PLATFORM_MEDIATION_DATABASE_URL.",
    )
    parser.add_argument(
        "--database",
        help="SQL Server database name.",
    )
    parser.add_argument(
        "--username",
        help="SQL Server username.",
    )
    parser.add_argument(
        "--password",
        help="SQL Server password.",
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
        help="Maximum iterations before stopping.",
    )
    parser.add_argument(
        "--max-retry-count",
        type=int,
        default=3,
        help="Maximum retry count for failed events.",
    )
    parser.add_argument(
        "--retry-delay",
        type=int,
        default=60,
        help="Retry delay in seconds.",
    )
    parser.add_argument(
        "--mock",
        action="store_true",
        help="Use mock handler instead of real handler.",
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

    settings = get_settings()
    repository = SqlAlchemyPlatformMediationRepository.from_database_url(settings.database_url)

    handler = MockReflowHandler() if args.mock else MockReflowHandler()

    reflow_service = ReflowService(
        repository,
        handler,
        poll_interval_seconds=args.poll_interval,
        max_iterations=args.max_iterations,
        max_retry_count=args.max_retry_count,
        retry_delay_seconds=args.retry_delay,
        on_event_processed=lambda event_id, success: print(
            f"[Reflow] Processed event: {event_id}, success={success}"
        ),
    )

    print(f"[Reflow] Starting with poll_interval={args.poll_interval}s")
    print(f"[Reflow] Max retry count: {args.max_retry_count}")
    print(f"[Reflow] Retry delay: {args.retry_delay}s")
    if args.max_iterations:
        print(f"[Reflow] Max iterations: {args.max_iterations}")

    reflow_service.start()

    print("[Reflow] Stopped.")


if __name__ == "__main__":
    main()