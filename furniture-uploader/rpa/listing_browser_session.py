"""Account-bound browser session helpers for guarded 1688 listing tools."""

from __future__ import annotations

from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
from pathlib import Path
import re
import sys
from typing import Any, Callable, Iterator, Mapping, Type
import uuid

from cross_project_runtime import (
    ExecutorBinding,
    RuntimeLeaseGuard,
    RuntimeLeaseRepository,
    resolve_build_sha,
    resolve_executor_binding,
)
from exceptions import OfflineLoginRequiredError
from sku_offline_auth import (
    ensure_1688_authenticated_session,
    stop_owned_1688_account_runtime,
)
from stop_sale_audit import resolve_stop_sale_app_config


@dataclass(frozen=True)
class ListingBrowserIdentity:
    account_key: str
    shop_name: str


ProgressCallback = Callable[[str], None]


def resolve_listing_browser_identity(
    payload: Mapping[str, Any],
    *,
    expected_account_key: str = "",
    expected_shop_name: str = "",
) -> ListingBrowserIdentity:
    shop = payload.get("shop") or {}
    account_key = str(shop.get("account_key") or "").strip()
    shop_name = str(shop.get("shop_name") or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", account_key):
        raise ValueError("listing payload requires a valid shop.account_key")
    if not shop_name:
        raise ValueError("listing payload requires shop.shop_name")
    if expected_account_key and account_key != str(expected_account_key).strip():
        raise ValueError("listing payload account_key does not match the requested account")
    if expected_shop_name and shop_name != str(expected_shop_name).strip():
        raise ValueError("listing payload shop_name does not match the requested business shop")
    return ListingBrowserIdentity(account_key=account_key, shop_name=shop_name)


def build_account_bound_browser_config(
    operator_config: Mapping[str, Any],
    binding: ExecutorBinding,
) -> dict[str, Any]:
    if int(binding.cdp_port) <= 0:
        raise ValueError("account binding requires a positive cdp_port")
    browser_config = dict(operator_config.get("browser") or {})
    browser_config["browser_type"] = "edge"
    browser_config["debugger_address"] = f"127.0.0.1:{int(binding.cdp_port)}"
    return browser_config


def build_listing_tool_account_lock(
    *,
    shared_runtime_root: str | Path,
    identity: ListingBrowserIdentity,
    component: str,
    run_id: str,
    wait_timeout_seconds: int,
    stale_after_seconds: int,
    poll_interval_seconds: float,
) -> Any:
    runtime_root = Path(shared_runtime_root).resolve()
    lock_module = runtime_root / "src" / "runtime" / "global_lock.py"
    if not lock_module.is_file():
        raise FileNotFoundError(f"Shared 1688 runtime lock module not found: {lock_module}")
    if str(runtime_root) not in sys.path:
        sys.path.insert(0, str(runtime_root))
    from src.runtime.global_lock import GlobalFileLock

    return GlobalFileLock(
        runtime_root
        / "artifacts"
        / "locks"
        / f"ali1688_account_{identity.account_key}.lock",
        stale_after_seconds=stale_after_seconds,
        wait_timeout_seconds=wait_timeout_seconds,
        poll_interval_seconds=poll_interval_seconds,
        metadata={
            "cycle": component,
            "run_id": run_id,
            "account_key": identity.account_key,
            "shop_name": identity.shop_name,
        },
    )


@contextmanager
def open_account_bound_listing_browser(
    *,
    payload: Mapping[str, Any],
    browser_class: Type[Any],
    operator_config: Mapping[str, Any],
    project_root: str | Path,
    shared_runtime_root: str | Path,
    expected_account_key: str = "",
    expected_shop_name: str = "",
    expected_cdp_port: int = 0,
    component: str = "erp-listing-inspector",
    task_type: str = "listing",
    lock_wait_seconds: int = 0,
    lock_stale_seconds: int = 14400,
    lock_poll_seconds: float = 5.0,
    runtime_lease_wait_seconds: float = 0,
    runtime_lease_poll_seconds: float = 5.0,
    login_timeout_seconds: int = 300,
    progress_callback: ProgressCallback | None = None,
) -> Iterator[tuple[Any, ExecutorBinding, ListingBrowserIdentity]]:
    def report_progress(stage: str) -> None:
        if progress_callback is not None:
            progress_callback(stage)

    identity = resolve_listing_browser_identity(
        payload,
        expected_account_key=expected_account_key,
        expected_shop_name=expected_shop_name,
    )
    binding = resolve_executor_binding(identity.account_key)
    if expected_cdp_port and int(binding.cdp_port) != int(expected_cdp_port):
        raise ValueError(
            f"account CDP mismatch: account_key={identity.account_key}, "
            f"expected={int(expected_cdp_port)}, actual={int(binding.cdp_port)}"
        )
    if not binding.browser_profile_dir:
        raise OfflineLoginRequiredError(
            f"Account browser profile directory is missing: account_key={identity.account_key}."
        )
    if int(login_timeout_seconds) <= 0:
        raise ValueError("login_timeout_seconds must be greater than zero")
    report_progress("binding_validated")

    run_id = f"{task_type}_{uuid.uuid4().hex}"
    project_path = Path(project_root).resolve()
    app_config = resolve_stop_sale_app_config(shared_runtime_root)
    with ExitStack() as stack:
        stack.enter_context(
            build_listing_tool_account_lock(
                shared_runtime_root=shared_runtime_root,
                identity=identity,
                component=component,
                run_id=run_id,
                wait_timeout_seconds=lock_wait_seconds,
                stale_after_seconds=lock_stale_seconds,
                poll_interval_seconds=lock_poll_seconds,
            )
        )
        report_progress("account_lock_acquired")
        runtime_guard = stack.enter_context(
            RuntimeLeaseGuard(
                RuntimeLeaseRepository(app_config),
                binding=binding,
                task_type=task_type,
                run_id=run_id,
                request_key=f"{component}:{identity.account_key}:{run_id}",
                build_sha=resolve_build_sha(project_path.parent),
                component=component,
                wait_timeout_seconds=runtime_lease_wait_seconds,
                poll_interval_seconds=runtime_lease_poll_seconds,
            )
        )
        browser = None
        opened = False
        try:
            runtime_guard.assert_active()
            report_progress("runtime_lease_acquired")
            report_progress("login_subprocess_starting")
            login_result = ensure_1688_authenticated_session(
                shared_runtime_root,
                identity.account_key,
                identity.shop_name,
                keep_browser_open=True,
                allow_unconfirmed_identity=True,
                timeout_seconds=login_timeout_seconds,
            )
            report_progress("login_subprocess_completed")
            if str(login_result.get("status") or "") not in {"success", "identity_unconfirmed"}:
                raise OfflineLoginRequiredError("1688 automatic login did not return a usable session.")
            if not login_result.get("browser_runtime_preserved"):
                raise OfflineLoginRequiredError(
                    "1688 automatic login did not preserve the account browser runtime."
                )

            report_progress("browser_attach_starting")
            browser = browser_class(
                build_account_bound_browser_config(operator_config, binding),
                project_path,
            )
            if hasattr(browser, "set_runtime_action_guard"):
                browser.set_runtime_action_guard(runtime_guard.assert_active)
            runtime_guard.register_owned_browser_closer(browser.close)
            browser.open()
            opened = True
            report_progress("browser_attached")
            runtime_guard.assert_active()
            yield browser, binding, identity
        finally:
            if opened and browser is not None:
                browser.close()
            stop_owned_1688_account_runtime(
                shared_runtime_root,
                identity.account_key,
                binding.browser_profile_dir,
                binding.cdp_port,
            )
