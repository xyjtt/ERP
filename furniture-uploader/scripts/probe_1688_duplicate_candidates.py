from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
RPA_ROOT = PROJECT_ROOT / "rpa"
SCRIPTS_ROOT = PROJECT_ROOT / "scripts"
for import_root in (RPA_ROOT, SCRIPTS_ROOT):
    if str(import_root) not in sys.path:
        sys.path.insert(0, str(import_root))

from config_loader import load_json_with_local_override
from probe_1688_sku_rows import _open_authenticated_browser, _summarize_sku_codes
from run_1688_stop_sale_pipeline import assert_crawler_worker_paused
from sku_offline_main import build_store_operator_config, resolve_store_account_binding
from sku_offline_tasks import OfflineTask, filter_offline_tasks, load_offline_tasks, normalize_cell


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Scan real 1688 edit pages in one browser session and find an input SKU "
            "that appears in multiple specification rows without changing the page."
        )
    )
    parser.add_argument("--file", required=True)
    parser.add_argument("--store", required=True)
    parser.add_argument("--shared-runtime-root", required=True)
    parser.add_argument("--crawler-worker-task-name", default="YYDD-1688-Crawler-Worker")
    parser.add_argument("--max-products", type=int, default=10)
    parser.add_argument("--lock-wait-seconds", type=float, default=0)
    return parser


def _select_candidate_groups(
    tasks: list[OfflineTask], store_name: str, max_products: int
) -> list[list[OfflineTask]]:
    normalized_store = normalize_cell(store_name)
    product_groups: dict[str, list[OfflineTask]] = {}
    for task in tasks:
        if normalize_cell(task.store_name) != normalized_store:
            continue
        product_id = normalize_cell(task.product_id)
        if not product_id:
            continue
        product_groups.setdefault(product_id, []).append(task)
    ordered = sorted(product_groups.values(), key=lambda group: (-len(group), group[0].product_id))
    return ordered[:max_products]


def _find_target_duplicate_sku_codes(
    source_skus: list[str], duplicate_sku_codes: dict[str, int]
) -> dict[str, int]:
    source_values = {str(sku).strip() for sku in source_skus if str(sku).strip()}
    return {
        sku: int(count)
        for sku, count in duplicate_sku_codes.items()
        if sku in source_values and int(count) >= 2
    }


def probe_candidates(args: argparse.Namespace) -> dict[str, Any]:
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
            "cycle": "1688_duplicate_sku_candidate_probe",
            "store_name": str(args.store),
            "input_file": str(Path(args.file).resolve()),
        },
    )
    with lock:
        worker_state = assert_crawler_worker_paused(str(args.crawler_worker_task_name))
        config_dir = PROJECT_ROOT / "config"
        system_config = load_json_with_local_override(
            config_dir / "systems" / "1688_sku_offline.json"
        )
        input_config = dict(system_config.get("input", {}))
        loaded_tasks = load_offline_tasks(Path(args.file).resolve(), input_config)
        selected_tasks, _ = filter_offline_tasks(
            loaded_tasks, dict(input_config.get("filters", {}))
        )
        candidate_groups = _select_candidate_groups(
            selected_tasks, str(args.store), int(args.max_products)
        )
        if not candidate_groups:
            raise ValueError(f"No candidate products found for store {args.store!r}.")

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
        selectors = dict(system_config.get("workflow", {}).get("selectors", {}))
        safety_config = dict(execution_config.get("safety", {}))
        results: list[dict[str, Any]] = []
        matched_candidate: dict[str, Any] | None = None
        try:
            for group in candidate_groups:
                task = group[0]
                context = task.to_context()
                source_skus = sorted({item.online_sku.strip() for item in group if item.online_sku.strip()})
                main_window = browser.driver.current_window_handle if browser.driver else ""
                result: dict[str, Any] = {
                    "product_id": task.product_id,
                    "source_skus": source_skus,
                    "source_sku_count": len(source_skus),
                }
                try:
                    browser._apply_account_binding_context(context, account_binding)
                    browser._open_task_edit_page(system_config, selectors, context, safety_config)
                    browser._assert_not_redirected_to_login(context)
                    browser._assert_no_risk_control_block(context)
                    browser._assert_edit_page_identity(context, safety_config)
                    visible_sku_codes = browser._collect_visible_sku_codes()
                    sku_code_counts, duplicate_sku_codes = _summarize_sku_codes(visible_sku_codes)
                    target_duplicates = _find_target_duplicate_sku_codes(
                        source_skus, duplicate_sku_codes
                    )
                    result.update(
                        {
                            "status": "ok",
                            "visible_sku_row_count": len(visible_sku_codes),
                            "sku_code_counts": sku_code_counts,
                            "duplicate_sku_codes": duplicate_sku_codes,
                            "target_duplicate_sku_codes": target_duplicates,
                            "management_products_tab": context.get("management_products_tab", ""),
                            "management_products_tab_verified": bool(
                                context.get("management_products_tab_verified", False)
                            ),
                            "edit_page_product_id": context.get("edit_page_product_id", ""),
                        }
                    )
                    if target_duplicates:
                        matched_candidate = dict(result)
                except Exception as exc:
                    result.update(
                        {
                            "status": "error",
                            "error_type": type(exc).__name__,
                            "error_message": str(exc),
                            "management_products_tab": context.get("management_products_tab", ""),
                            "management_products_tab_verified": bool(
                                context.get("management_products_tab_verified", False)
                            ),
                        }
                    )
                finally:
                    if main_window:
                        browser._restore_management_window(main_window)
                results.append(result)
                if matched_candidate:
                    break
        finally:
            browser.close()

        return {
            "status": "match_found" if matched_candidate else "no_match",
            "probe_only": True,
            "store_name": str(args.store),
            "input_file": str(Path(args.file).resolve()),
            "candidate_product_count": len(candidate_groups),
            "scanned_product_count": len(results),
            "matched_candidate": matched_candidate,
            "products": results,
            "worker_state": worker_state,
            "shared_lock_path": str(lock_path),
            "checked_at": datetime.now().isoformat(timespec="seconds"),
        }


def main() -> int:
    args = build_argument_parser().parse_args()
    if args.max_products <= 0:
        raise ValueError("--max-products must be positive")
    if args.lock_wait_seconds < 0:
        raise ValueError("--lock-wait-seconds must be non-negative")
    print(json.dumps(probe_candidates(args), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
