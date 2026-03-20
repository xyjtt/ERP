from __future__ import annotations

import importlib.util
import json
from dataclasses import dataclass, field
from pathlib import Path


REQUIRED_PACKAGES = [
    "selenium",
    "pandas",
    "openpyxl",
    "webdriver_manager",
    "pyodbc",
]


@dataclass
class CheckItem:
    name: str
    ok: bool
    message: str


@dataclass
class PreflightReport:
    kind: str
    items: list[CheckItem] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return all(item.ok for item in self.items)

    def to_text(self) -> str:
        lines = [f"Preflight report: {self.kind}", f"status: {'ok' if self.ok else 'needs_attention'}"]
        for item in self.items:
            lines.append(f"[{'OK' if item.ok else 'FAIL'}] {item.name} - {item.message}")
        return "\n".join(lines)

    def to_json(self) -> str:
        return json.dumps(
            {
                "kind": self.kind,
                "ok": self.ok,
                "items": [
                    {"name": item.name, "ok": item.ok, "message": item.message}
                    for item in self.items
                ],
            },
            ensure_ascii=False,
            indent=2,
        )


def build_env_report(project_root: str | Path) -> PreflightReport:
    root = Path(project_root)
    report = PreflightReport(kind="environment")

    for package_name in REQUIRED_PACKAGES:
        spec = importlib.util.find_spec(package_name)
        report.items.append(
            CheckItem(
                name=f"python_package:{package_name}",
                ok=spec is not None,
                message="installed" if spec is not None else "missing",
            )
        )

    for relative_path in (
        "config/systems/jushuitan.json",
        "config/operations/code_change.json",
        "sql/001_init_sqlserver.sql",
        "templates/code_change_template.csv",
    ):
        path = root / relative_path
        report.items.append(
            CheckItem(
                name=relative_path,
                ok=path.exists(),
                message="present" if path.exists() else "missing",
            )
        )

    return report


def build_db_report(config_path: str | Path) -> PreflightReport:
    from .database import DatabaseConfig

    path = Path(config_path)
    report = PreflightReport(kind="database")
    report.items.append(CheckItem("db_config_path", path.exists(), str(path)))
    if not path.exists():
        return report

    try:
        config = DatabaseConfig.from_json(path)
        report.items.append(
            CheckItem("db_config_load", True, f"{config.host}:{config.port}/{config.database}")
        )
    except Exception as exc:
        report.items.append(CheckItem("db_config_load", False, str(exc)))
        return report

    try:
        import pyodbc  # type: ignore

        drivers = [str(driver).strip() for driver in pyodbc.drivers()]
        report.items.append(
            CheckItem(
                "db_driver",
                config.driver in drivers,
                ", ".join(drivers) if drivers else "no ODBC drivers found",
            )
        )
        if config.driver not in drivers:
            return report
    except Exception as exc:
        report.items.append(CheckItem("db_driver", False, str(exc)))
        return report

    try:
        import pyodbc  # type: ignore

        connection = pyodbc.connect(config.connection_string(), timeout=5)
        row = connection.cursor().execute("SELECT 1").fetchone()
        connection.close()
        report.items.append(CheckItem("db_connect", bool(row and row[0] == 1), "SELECT 1 ok"))
    except Exception as exc:
        report.items.append(CheckItem("db_connect", False, str(exc)))

    return report
