from __future__ import annotations

import argparse
from dataclasses import dataclass

import pyodbc


TARGET_TABLES = [
    "platform_adapter",
    "mapping_version",
    "mapping_rule",
    "worker_node",
    "task",
    "task_item",
    "source_snapshot",
    "task_attempt",
    "task_artifact",
    "worker_lock",
    "reflow_event",
]


@dataclass(frozen=True)
class CheckArgs:
    server: str
    database: str
    username: str
    password: str
    driver: str


def parse_args() -> CheckArgs:
    parser = argparse.ArgumentParser(description="Check SQL Server connectivity and permissions.")
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
    return CheckArgs(
        server=ns.server,
        database=ns.database,
        username=ns.username,
        password=ns.password,
        driver=ns.driver,
    )


def main() -> None:
    args = parse_args()
    conn = pyodbc.connect(
        f"DRIVER={{{args.driver}}};"
        f"SERVER={args.server};"
        f"DATABASE={args.database};"
        f"UID={args.username};"
        f"PWD={args.password};"
        "Connection Timeout=5"
    )
    cursor = conn.cursor()

    cursor.execute("SELECT @@VERSION")
    version = cursor.fetchone()[0]

    table_list = ",".join(f"'{name}'" for name in TARGET_TABLES)
    cursor.execute(
        f"SELECT name FROM sys.tables WHERE name IN ({table_list}) ORDER BY name"
    )
    existing_tables = [row[0] for row in cursor.fetchall()]

    cursor.execute("SELECT HAS_PERMS_BY_NAME(DB_NAME(), 'DATABASE', 'CREATE TABLE')")
    can_create_table = bool(cursor.fetchone()[0])

    print("connection=ok")
    print(f"driver={args.driver}")
    print(f"database={args.database}")
    print(f"create_table_permission={can_create_table}")
    print(f"existing_tables={','.join(existing_tables) if existing_tables else '<none>'}")
    print("version=")
    print(version)

    conn.close()


if __name__ == "__main__":
    main()

