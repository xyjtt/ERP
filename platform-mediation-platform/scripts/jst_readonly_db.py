from __future__ import annotations

import os

import pyodbc


def connect_jst_readonly() -> pyodbc.Connection:
    password = (
        os.getenv("JST_READONLY_SQLSERVER_PASSWORD", "").strip()
        or os.getenv("FURNITURE_UPLOADER_DB_PASSWORD", "").strip()
    )
    if not password:
        raise RuntimeError(
            "Set JST_READONLY_SQLSERVER_PASSWORD or FURNITURE_UPLOADER_DB_PASSWORD."
        )

    driver = os.getenv("JST_READONLY_SQLSERVER_DRIVER", "SQL Server Native Client 10.0")
    host = os.getenv("JST_READONLY_SQLSERVER_HOST", "218.93.191.21")
    database = os.getenv("JST_READONLY_SQLSERVER_DATABASE", "JianSun")
    username = os.getenv("JST_READONLY_SQLSERVER_USER", "itread")
    timeout = max(1, int(os.getenv("JST_READONLY_SQLSERVER_TIMEOUT", "10")))
    return pyodbc.connect(
        f"DRIVER={{{driver}}};"
        f"SERVER={host};"
        f"DATABASE={database};"
        f"UID={username};"
        f"PWD={password};"
        "Encrypt=no;"
        f"Connection Timeout={timeout}",
        readonly=True,
        timeout=timeout,
    )
