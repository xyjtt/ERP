from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
RPA_ROOT = PROJECT_ROOT / "rpa"
if str(RPA_ROOT) not in sys.path:
    sys.path.insert(0, str(RPA_ROOT))

from config_loader import load_json_with_local_override
from exceptions import OfflineLoginRequiredError
from sku_offline_auth import ensure_1688_authenticated_session
from sku_offline_browser import SkuOfflineBrowser
from sku_offline_main import build_store_operator_config, resolve_store_account_binding
from sku_offline_tasks import OfflineTask

from run_1688_stop_sale_pipeline import assert_crawler_worker_paused


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Read a real 1688 edit page and count rows matching one SKU without changing it."
    )
    parser.add_argument("--store", required=True)
    parser.add_argument("--product-id", required=True)
    parser.add_argument("--sku", required=True)
    parser.add_argument("--shared-runtime-root", required=True)
    parser.add_argument("--crawler-worker-task-name", default="YYDD-1688-Crawler-Worker")
    parser.add_argument("--lock-wait-seconds", type=float, default=0)
    return parser


def _build_task(args: argparse.Namespace) -> OfflineTask:
    return OfflineTask(
        source_file="live_probe",
        source_sheet="live_probe",
        source_row_number=1,
        store_name=str(args.store).strip(),
        platform="Alibaba",
        product_id=str(args.product_id).strip(),
        online_sku=str(args.sku).strip(),
        handling="probe_only",
        replacement_sku="",
        change_image="",
        platform_store_item_code="",
        raw={},
    )


def _open_authenticated_browser(
    *,
    operator_config: dict[str, Any],
    system_config: dict[str, Any],
    account_binding: dict[str, Any],
    shared_runtime_root: Path,
    store_name: str,
) -> SkuOfflineBrowser:
    browser = SkuOfflineBrowser(operator_config.get("browser", {}), PROJECT_ROOT)
    browser.open()
    try:
        browser.prepare_session(system_config, skip_login=True)
        return browser
    except OfflineLoginRequiredError:
        browser.close()
    except Exception:
        browser.close()
        raise

    ensure_1688_authenticated_session(
        shared_runtime_root,
        str(account_binding.get("account_key") or ""),
        store_name,
        timeout_seconds=int(
            system_config.get("execution", {})
            .get("auto_login_fallback", {})
            .get("timeout_seconds", 300)
        ),
    )
    browser = SkuOfflineBrowser(operator_config.get("browser", {}), PROJECT_ROOT)
    browser.open()
    try:
        browser.prepare_session(system_config, skip_login=True)
    except Exception:
        browser.close()
        raise
    return browser


def probe(args: argparse.Namespace) -> dict[str, Any]:
    runtime_root = Path(args.shared_runtime_root).resolve()
    if str(runtime_root) not in sys.path:
        sys.path.insert(0, str(runtime_root))
    from src.runtime.global_lock import GlobalFileLock

    lock_path = runtime_root / "artifacts" / "locks" / "ali1688_full_cycle.lock"
    lock = GlobalFileLock(
        lock_path,
        stale_after_seconds=6 * 60 * 60,
        wait_timeout_seconds=float(args.lock_wait_seconds),
        poll_interval_seconds=1,
        metadata={
            "cycle": "1688_sku_row_probe",
            "product_id": str(args.product_id),
            "sku": str(args.sku),
        },
    )
    with lock:
        worker_state = assert_crawler_worker_paused(str(args.crawler_worker_task_name))
        config_dir = PROJECT_ROOT / "config"
        system_config = load_json_with_local_override(
            config_dir / "systems" / "1688_sku_offline.json"
        )
        base_operator_config = load_json_with_local_override(config_dir / "operator_config.json")
        execution_config = dict(system_config.get("execution", {}))
        account_binding = resolve_store_account_binding(system_config, str(args.store))
        operator_config = build_store_operator_config(
            base_operator_config,
            account_binding=account_binding,
            execution_config=execution_config,
        )
        browser = _open_authenticated_browser(
            operator_config=operator_config,
            system_config=system_config,
            account_binding=account_binding,
            shared_runtime_root=runtime_root,
            store_name=str(args.store),
        )
        task = _build_task(args)
        context = task.to_context()
        selectors = dict(system_config.get("workflow", {}).get("selectors", {}))
        safety_config = dict(execution_config.get("safety", {}))
        try:
            browser._apply_account_binding_context(context, account_binding)
            browser._open_task_edit_page(system_config, selectors, context, safety_config)
            browser._assert_not_redirected_to_login(context)
            browser._assert_no_risk_control_block(context)
            browser._assert_edit_page_identity(context, safety_config)
            rows = browser._find_matching_sku_rows(selectors, context)
            switch_selector = selectors.get("sku_switch", {})
            labels = [
                browser._read_switch_label(browser._get_row_switch_element(row, switch_selector))
                for row in rows
            ]
            return {
                "status": "ok",
                "probe_only": True,
                "store_name": str(args.store),
                "product_id": str(args.product_id),
                "sku": str(args.sku),
                "matching_sku_row_count": len(rows),
                "matching_switch_labels": labels,
                "management_products_tab": context.get("management_products_tab", ""),
                "management_products_tab_verified": bool(
                    context.get("management_products_tab_verified", False)
                ),
                "edit_page_product_id": context.get("edit_page_product_id", ""),
                "worker_state": worker_state,
                "shared_lock_path": str(lock_path),
                "checked_at": datetime.now().isoformat(timespec="seconds"),
            }
        finally:
            browser.close()


def main() -> int:
    args = build_argument_parser().parse_args()
    if args.lock_wait_seconds < 0:
        raise ValueError("--lock-wait-seconds must be non-negative")
    print(json.dumps(probe(args), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
