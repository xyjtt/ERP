from __future__ import annotations

import importlib.util
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


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
            prefix = "OK" if item.ok else "FAIL"
            lines.append(f"[{prefix}] {item.name} - {item.message}")
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

    report.items.append(
        CheckItem(
            name="config:systems/jushuitan.json",
            ok=(root / "config" / "systems" / "jushuitan.json").exists(),
            message="present" if (root / "config" / "systems" / "jushuitan.json").exists() else "missing",
        )
    )
    report.items.append(
        CheckItem(
            name="config:platforms/1688.json",
            ok=(root / "config" / "platforms" / "1688.json").exists(),
            message="present" if (root / "config" / "platforms" / "1688.json").exists() else "missing",
        )
    )
    report.items.append(
        CheckItem(
            name="sql:001_init_sqlserver.sql",
            ok=(root / "sql" / "001_init_sqlserver.sql").exists(),
            message="present" if (root / "sql" / "001_init_sqlserver.sql").exists() else "missing",
        )
    )

    pyodbc_spec = importlib.util.find_spec("pyodbc")
    if pyodbc_spec is None:
        report.items.append(
            CheckItem(
                name="odbc_driver",
                ok=False,
                message="pyodbc missing, cannot inspect installed ODBC drivers",
            )
        )
    else:
        import pyodbc  # type: ignore

        drivers = list(pyodbc.drivers())
        has_sqlserver_driver = any("SQL Server" in driver for driver in drivers)
        report.items.append(
            CheckItem(
                name="odbc_driver",
                ok=has_sqlserver_driver,
                message=", ".join(drivers) if drivers else "no ODBC drivers found",
            )
        )

    return report


def build_db_report(config_path: str | Path) -> PreflightReport:
    report = PreflightReport(kind="database")
    path = Path(config_path)
    report.items.append(
        CheckItem(
            name="db_config_path",
            ok=path.exists(),
            message=str(path),
        )
    )
    if not path.exists():
        return report

    try:
        from database import DatabaseConfig, choose_preferred_sqlserver_driver

        config = DatabaseConfig.from_json(path)
        report.items.append(
            CheckItem(
                name="db_config_load",
                ok=True,
                message=f"{config.host}:{config.port}/{config.database}",
            )
        )
    except Exception as exc:
        report.items.append(
            CheckItem(
                name="db_config_load",
                ok=False,
                message=str(exc),
            )
        )
        return report

    try:
        import pyodbc  # type: ignore

        installed_drivers = [str(driver).strip() for driver in pyodbc.drivers()]
        driver_ok = config.driver in installed_drivers
        preferred_driver = choose_preferred_sqlserver_driver()
        report.items.append(
            CheckItem(
                name="db_driver",
                ok=driver_ok,
                message=(
                    f"configured={config.driver}; preferred={preferred_driver}; installed={', '.join(installed_drivers)}"
                    if installed_drivers
                    else f"configured={config.driver}; no ODBC drivers found"
                ),
            )
        )
        if not driver_ok:
            return report
    except Exception as exc:
        report.items.append(
            CheckItem(
                name="db_driver",
                ok=False,
                message=str(exc),
            )
        )
        return report

    try:
        import pyodbc  # type: ignore

        connection = pyodbc.connect(config.connection_string(), timeout=5)
        cursor = connection.cursor()
        row = cursor.execute("SELECT 1 AS ok_value").fetchone()
        ok_value = getattr(row, "ok_value", row[0] if row else None)
        connection.close()
        report.items.append(
            CheckItem(
                name="db_connect",
                ok=ok_value == 1,
                message=f"SELECT 1 returned {ok_value}",
            )
        )
    except Exception as exc:
        report.items.append(
            CheckItem(
                name="db_connect",
                ok=False,
                message=str(exc),
            )
        )

    return report
