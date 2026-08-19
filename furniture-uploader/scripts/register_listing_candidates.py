"""Register generated listing candidates into the app task table as approved tasks
so the workbench (listing-items API) can pull them. Idempotent: skips task_id
already present. Reads the executor .env for app database credentials.
"""
import glob
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

env = {}
with open(r"E:\1688\1688-script-new\.env", encoding="utf-8") as fh:
    for line in fh:
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, _, v = line.partition("=")
            env[k.strip()] = v.strip()

import pyodbc

INBOX = Path(r"C:\ProgramData\YYDD\1688-listing\inbox")


def build_record(payload):
    shop = payload.get("shop") or {}
    source = payload.get("source") or {}
    product = payload.get("product") or {}
    workflow = payload.get("workflow") or {}
    identity = "|".join(
        [
            str(payload.get("platform", "")).lower(),
            str(shop.get("account_key", "")).lower(),
            str(source.get("company_sku", "")).upper(),
            str(source.get("novelty_type", "")).lower(),
            str(bool(workflow.get("independent_link_required"))).lower(),
        ]
    )
    return {
        "task_id": str(payload.get("task_id", "")),
        "schema_version": str(payload.get("schema_version", "")),
        "platform": str(payload.get("platform", "")),
        "shop_name": str(shop.get("shop_name", "")),
        "account_key": str(shop.get("account_key", "")),
        "company_sku": str(source.get("company_sku", "")),
        "company_spu": str(source.get("company_spu", "")),
        "novelty_type": str(source.get("novelty_type", "")),
        "selected_title": str(product.get("selected_title", "")),
        "workflow_state": str(workflow.get("state", "")),
        "approval_status": "approved",
        "approved_by": "auto-listing-generator",
        "approved_at": datetime.now(timezone.utc).replace(tzinfo=None).isoformat(),
        "idempotency_key": hashlib.sha256(identity.encode("utf-8")).hexdigest(),
        "payload_json": json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str),
    }


def main():
    conn_str = (
        f"DRIVER={{SQL Server}};SERVER={env.get('SQLSERVER_HOST')},{env.get('SQLSERVER_PORT', '1433')};"
        f"DATABASE={env.get('SQLSERVER_DATABASE')};UID={env.get('SQLSERVER_USERNAME')};"
        f"PWD={env.get('SQLSERVER_PASSWORD')};Encrypt=no;TrustServerCertificate=yes"
    )
    conn = pyodbc.connect(conn_str, timeout=20)
    cur = conn.cursor()
    candidates = sorted(glob.glob(str(INBOX / "*.json")))
    registered = 0
    skipped = 0
    errors = []
    for path in candidates:
        try:
            with open(path, encoding="utf-8-sig") as fh:
                payload = json.load(fh)
            record = build_record(payload)
            if not record["task_id"]:
                skipped += 1
                continue
            cur.execute(
                "SELECT COUNT_BIG(1) FROM app.ali1688_listing_task WHERE task_id = ?",
                record["task_id"],
            )
            if cur.fetchone()[0] > 0:
                skipped += 1
                continue
            cur.execute(
                """INSERT INTO app.ali1688_listing_task (
                    task_id, schema_version, platform, shop_name, account_key,
                    company_sku, company_spu, novelty_type, selected_title,
                    workflow_state, approval_status, approved_by, approved_at,
                    idempotency_key, payload_json, preflight_json, claimed_by, claim_until
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL,
                    DATEADD(SECOND, 3600, SYSUTCDATETIME()))""",
                (
                    record["task_id"],
                    record["schema_version"],
                    record["platform"],
                    record["shop_name"],
                    record["account_key"],
                    record["company_sku"],
                    record["company_spu"],
                    record["novelty_type"],
                    record["selected_title"],
                    record["workflow_state"],
                    record["approval_status"],
                    record["approved_by"],
                    record["approved_at"],
                    record["idempotency_key"],
                    record["payload_json"],
                ),
            )
            registered += 1
            print("registered", record["task_id"], flush=True)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{Path(path).name}: {str(exc)[:120]}")
            print("ERROR", Path(path).name, str(exc)[:120], flush=True)
    conn.commit()
    conn.close()
    print(f"=== registered={registered} skipped={skipped} errors={len(errors)} ===", flush=True)
    for e in errors[:8]:
        print("ERR:", e, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
