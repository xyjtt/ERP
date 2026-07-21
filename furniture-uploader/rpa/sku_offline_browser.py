from __future__ import annotations

import json
import time
import re
from typing import Any
from urllib.parse import parse_qs, urlparse

from selenium.common.exceptions import TimeoutException
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import WebDriverWait

from browser_rpa import BY_MAPPING, BrowserRPA
from exceptions import (
    OfflineLoginRequiredError,
    OfflineIdentityMismatchError,
    OfflineRiskControlError,
    OfflineStoreMismatchError,
    OfflineTaskNotFoundError,
    OfflineTaskStateError,
    PublishSubmitError,
    PublishValidationError,
)
from jst_attribute_mapper import infer_platform_category_key
from sku_offline_tasks import OfflineTask, store_name_matches


class SkuOfflineBrowser(BrowserRPA):
    def open_management_page(self, system_config: dict[str, Any]) -> None:
        if not self.driver:
            raise RuntimeError("Browser has not been opened.")
        management_url = str(system_config.get("management_url", "")).strip()
        if not management_url:
            raise ValueError("1688 sku offline management_url is not configured.")
        self.driver.get(management_url)
        self._pause(self.browser_config.get("page_load_wait_seconds", 2))

    def prepare_session(self, system_config: dict[str, Any], *, skip_login: bool) -> None:
        if not skip_login:
            self.run_system_workflow(system_config, {})
        self.open_management_page(system_config)

    def execute_offline_task(
        self,
        system_config: dict[str, Any],
        task: OfflineTask,
        *,
        account_binding: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        outcome = self.execute_offline_group(
            system_config,
            [task],
            account_binding=account_binding,
        )[0]
        if outcome["status"] == "failed":
            error = outcome.get("error")
            if isinstance(error, BaseException):
                raise error
            raise RuntimeError(str(error or "1688 SKU offline task failed"))
        return dict(outcome["context"])

    def execute_offline_group(
        self,
        system_config: dict[str, Any],
        tasks: list[OfflineTask],
        *,
        account_binding: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        if not self.driver:
            raise RuntimeError("Browser has not been opened.")
        if not tasks:
            return []

        group_keys = {
            (str(task.store_name).strip(), str(task.product_id).strip())
            for task in tasks
        }
        if len(group_keys) != 1:
            raise ValueError("A grouped 1688 edit operation must contain one store and one product ID.")

        selectors = dict(system_config.get("workflow", {}).get("selectors", {}))
        success_detection = dict(system_config.get("workflow", {}).get("success_detection", {}))
        error_detection = dict(system_config.get("workflow", {}).get("error_detection", {}))
        pre_submit_backfill = dict(system_config.get("workflow", {}).get("pre_submit_backfill", {}))
        post_submit_verification = dict(system_config.get("workflow", {}).get("post_submit_verification", {}))
        safety_config = dict(system_config.get("execution", {}).get("safety", {}))
        contexts = [task.to_context() for task in tasks]
        for context in contexts:
            self._apply_account_binding_context(context, account_binding or {})
            context["group_sku_count"] = str(len(tasks))
            context["group_target_skus"] = [str(item.online_sku).strip() for item in tasks]
        first_context = contexts[0]
        active_context = first_context
        self.last_result_context = first_context
        main_window = self.driver.current_window_handle
        outcomes: list[dict[str, Any]] = []

        try:
            self._open_task_edit_page(
                system_config,
                selectors,
                first_context,
                safety_config,
            )
            self._assert_not_redirected_to_login()
            self._assert_no_risk_control_block(first_context)
            self._assert_edit_page_identity(first_context, safety_config)

            shared_context_keys = (
                "edit_entry_mode",
                "edit_entry_fallback",
                "edit_page_product_id",
                "edit_page_error_code",
            )
            for task, context in zip(tasks, contexts):
                active_context = context
                self.last_result_context = context
                for key in shared_context_keys:
                    if key in first_context:
                        context[key] = first_context[key]
                try:
                    changed = self._toggle_sku_offline(selectors, context)
                except Exception as exc:
                    if isinstance(
                        exc,
                        (
                            OfflineLoginRequiredError,
                            OfflineRiskControlError,
                            OfflineStoreMismatchError,
                            OfflineIdentityMismatchError,
                        ),
                    ):
                        for item in outcomes:
                            if item["status"] == "pending_submit":
                                self._revert_unsaved_sku_toggle(selectors, item["context"])
                        raise
                    self._record_page_metadata(context)
                    self._capture_screenshot(f"{task.product_id}_{task.online_sku}")
                    outcomes.append(
                        {
                            "task": task,
                            "status": "failed",
                            "context": context,
                            "error": exc,
                            "screenshot_path": self.last_screenshot_path,
                            "html_snapshot_path": self.last_html_snapshot_path,
                        }
                    )
                    continue

                if changed:
                    outcomes.append(
                        {
                            "task": task,
                            "status": "pending_submit",
                            "context": context,
                            "error": None,
                            "screenshot_path": "",
                            "html_snapshot_path": "",
                        }
                    )
                else:
                    context["execution_result"] = "already_offline"
                    context["success_detected"] = "true"
                    outcomes.append(
                        {
                            "task": task,
                            "status": "already_offline",
                            "context": context,
                            "error": None,
                            "screenshot_path": "",
                            "html_snapshot_path": "",
                        }
                    )

            changed_outcomes = [item for item in outcomes if item["status"] == "pending_submit"]
            if changed_outcomes:
                submit_context = dict(changed_outcomes[0]["context"])
                submit_context["group_changed_skus"] = [
                    str(item["task"].online_sku).strip() for item in changed_outcomes
                ]
                try:
                    for item in changed_outcomes:
                        self._ensure_target_sku_still_offline(selectors, item["context"])
                    self._submit_changes(
                        selectors,
                        success_detection,
                        error_detection,
                        pre_submit_backfill,
                        submit_context,
                    )
                    self._verify_group_persisted_offline(
                        selectors,
                        post_submit_verification,
                        [item["context"] for item in changed_outcomes],
                    )
                except Exception as exc:
                    self.last_result_context = submit_context
                    self._record_page_metadata(submit_context)
                    self._capture_screenshot(f"{tasks[0].product_id}_group_submit")
                    submit_metadata = {
                        key: value
                        for key, value in submit_context.items()
                        if key.startswith(("submit_", "success_", "page_error_", "pre_submit_"))
                    }
                    for item in changed_outcomes:
                        item["context"].update(submit_metadata)
                        item["context"]["group_submit_failed"] = "true"
                        item["status"] = "failed"
                        item["error"] = exc
                        item["screenshot_path"] = self.last_screenshot_path
                        item["html_snapshot_path"] = self.last_html_snapshot_path
                else:
                    submit_metadata = {
                        key: value
                        for key, value in submit_context.items()
                        if key.startswith(("submit_", "success_", "pre_submit_"))
                    }
                    for item in changed_outcomes:
                        item["context"].update(submit_metadata)
                        item["context"]["execution_result"] = "submitted"
                        item["status"] = "success"

            for item in outcomes:
                self._record_page_metadata(item["context"])
            if outcomes:
                self.last_result_context = outcomes[-1]["context"]
            return outcomes
        except Exception:
            self._record_page_metadata(active_context)
            self.last_result_context = active_context
            self._capture_screenshot(f"{tasks[0].product_id}_group")
            raise
        finally:
            self._restore_management_window(main_window)

    def _open_task_edit_page(
        self,
        system_config: dict[str, Any],
        selectors: dict[str, Any],
        context: dict[str, Any],
        safety_config: dict[str, Any],
    ) -> None:
        self.open_management_page(system_config)
        self._assert_store_context(
            selectors,
            context,
            required=bool(safety_config.get("require_store_context_selector", True)),
        )

        edit_entry_mode = str(safety_config.get("edit_entry_mode", "management")).strip().lower()
        if edit_entry_mode not in {"management", "direct"}:
            raise OfflineTaskStateError(f"Unsupported 1688 edit_entry_mode: {edit_entry_mode!r}.")

        if edit_entry_mode == "direct":
            try:
                self._open_edit_page(selectors, context, prefer_direct=True)
                return
            except OfflineTaskStateError:
                if context.get("page_error_category") != "edit_route_rejected":
                    raise
                context["direct_edit_rejected_code"] = context.pop("edit_page_error_code", "")
                context["direct_edit_rejected_text"] = context.pop("page_error_text", "")
                context.pop("page_error_category", None)
                context.pop("page_error_stage", None)
                context["edit_entry_fallback"] = "management"
                self.open_management_page(system_config)
                self._assert_store_context(
                    selectors,
                    context,
                    required=bool(safety_config.get("require_store_context_selector", True)),
                )

        context["edit_entry_mode"] = "management"
        self._switch_into_management_frame(selectors, context)
        self._search_product(selectors, context)
        self._open_edit_page(selectors, context)

    def _assert_store_context(
        self,
        selectors: dict[str, Any],
        context: dict[str, Any],
        *,
        required: bool = False,
    ) -> None:
        if not self.driver:
            raise RuntimeError("Browser has not been opened.")
        self.driver.switch_to.default_content()
        self._assert_not_redirected_to_login(context)
        selector = self._resolve_selector(selectors.get("current_store_name", {}), context)
        if not self._selector_is_configured(selector):
            if required:
                raise OfflineStoreMismatchError("current_store_name selector is required but not configured.")
            return
        try:
            current_store_name = self._wait_for_element(selector).text.strip()
        except Exception as exc:
            if required:
                raise OfflineStoreMismatchError("Unable to verify current 1688 store before offline execution.") from exc
            raise
        context["current_store_name"] = current_store_name
        expected_store_name = context.get("store_name", "").strip()
        raw_aliases = context.get("expected_store_aliases", [])
        expected_store_aliases = (
            [str(item).strip() for item in raw_aliases if str(item).strip()]
            if isinstance(raw_aliases, list)
            else []
        )
        if current_store_name and expected_store_name and not store_name_matches(
            current_store_name,
            expected_store_name,
            aliases=expected_store_aliases,
        ):
            message = f"Current store '{current_store_name}' does not match task store '{expected_store_name}'."
            self._annotate_page_error_context(
                context,
                stage_name="store_context_check",
                error_text=message,
                error_category="store_mismatch",
            )
            raise OfflineStoreMismatchError(message)

    def _apply_account_binding_context(
        self,
        context: dict[str, Any],
        account_binding: dict[str, Any],
    ) -> None:
        if not account_binding:
            return
        account_key = str(account_binding.get("account_key", "")).strip()
        browser_profile_dir = str(
            account_binding.get("browser_profile_dir", account_binding.get("profile_dir", ""))
        ).strip()
        if account_key:
            context["expected_account_key"] = account_key
        if browser_profile_dir:
            context["expected_browser_profile_dir"] = browser_profile_dir
        store_aliases = account_binding.get("store_aliases", account_binding.get("aliases", []))
        if isinstance(store_aliases, list):
            context["expected_store_aliases"] = [
                str(item).strip()
                for item in store_aliases
                if str(item).strip()
            ]

    def _assert_no_risk_control_block(self, context: dict[str, Any]) -> None:
        if not self.driver:
            raise RuntimeError("Browser has not been opened.")
        current_url = str(self.driver.current_url or "").strip().lower()
        url_markers = ["captcha", "punish", "risk", "security", "awsc"]
        if any(marker in current_url for marker in url_markers):
            self._annotate_page_error_context(
                context,
                stage_name="risk_control_check",
                error_text=f"risk/captcha marker detected in URL: {current_url[:180]}",
                error_category="risk_control",
            )
            raise OfflineRiskControlError("1688 页面进入验证码或风控校验，停止当前店铺。")

        body_text = self._extract_page_body_text()
        risk_keywords = [
            "请完成验证",
            "安全验证",
            "拖动滑块",
            "验证码",
            "账户安全",
            "访问受限",
            "验证身份",
        ]
        hit = next((keyword for keyword in risk_keywords if keyword in body_text), "")
        if hit:
            self._annotate_page_error_context(
                context,
                stage_name="risk_control_check",
                error_text=hit,
                error_category="risk_control",
            )
            raise OfflineRiskControlError("1688 页面要求验证码或安全验证，停止当前店铺。")

    def _assert_edit_page_identity(
        self,
        context: dict[str, Any],
        safety_config: dict[str, Any],
    ) -> None:
        if not bool(safety_config.get("verify_edit_page_identity", True)):
            return
        expected_product_id = str(context.get("product_id", "")).strip()
        if not expected_product_id:
            raise OfflineIdentityMismatchError("Missing product_id before opening 1688 edit page.")

        current_product_id = self._extract_product_id_from_current_url()
        context["edit_page_product_id"] = current_product_id
        if bool(safety_config.get("verify_edit_url_product_id", True)):
            if not current_product_id:
                self._annotate_page_error_context(
                    context,
                    stage_name="edit_page_identity",
                    error_text="missing product id in edit page URL",
                    error_category="identity_mismatch",
                )
                raise OfflineIdentityMismatchError("1688 edit page URL does not contain a product id.")
            if current_product_id != expected_product_id:
                self._annotate_page_error_context(
                    context,
                    stage_name="edit_page_identity",
                    error_text=f"expected product_id={expected_product_id}, actual={current_product_id}",
                    error_category="identity_mismatch",
                )
                raise OfflineIdentityMismatchError(
                    f"1688 edit page product id mismatch: expected {expected_product_id}, actual {current_product_id}."
                )

        if bool(safety_config.get("verify_product_id_in_page_body", False)):
            body_text = self._extract_page_body_text()
            if expected_product_id not in body_text:
                self._annotate_page_error_context(
                    context,
                    stage_name="edit_page_identity",
                    error_text=f"product id {expected_product_id} not found in page body",
                    error_category="identity_mismatch",
                )
                raise OfflineIdentityMismatchError(
                    f"Product id '{expected_product_id}' was not visible on the edit page."
                )

    def _extract_product_id_from_current_url(self) -> str:
        if not self.driver:
            raise RuntimeError("Browser has not been opened.")
        current_url = str(self.driver.current_url or "").strip()
        try:
            values = parse_qs(urlparse(current_url).query).get("id", [])
        except Exception:
            return ""
        return str(values[0]).strip() if values else ""

    def _switch_into_management_frame(
        self,
        selectors: dict[str, Any],
        context: dict[str, Any],
    ) -> None:
        if not self.driver:
            raise RuntimeError("Browser has not been opened.")
        self.driver.switch_to.default_content()
        frame_selector = self._resolve_selector(selectors.get("management_iframe", {}), context)
        if self._selector_is_configured(frame_selector):
            iframe = self._wait_for_element(frame_selector)
            self.driver.switch_to.frame(iframe)
            context["edit_entry_stage"] = "management_frame_entered"

    def _search_product(
        self,
        selectors: dict[str, Any],
        context: dict[str, Any],
    ) -> None:
        self._wait_for_management_search_ready(context)
        product_id_input = self._resolve_selector(selectors.get("product_id_input", {}), context)
        if not self._selector_is_configured(product_id_input):
            raise ValueError("product_id_input selector is not configured.")
        input_element = self._wait_for_element(product_id_input, clickable=True)
        self._fill_management_search_field(input_element, context.get("product_id", ""))

        title_sku_input = self._resolve_selector(selectors.get("title_sku_input", {}), context)
        if self._selector_is_configured(title_sku_input):
            title_sku_element = self._wait_for_element(title_sku_input, clickable=True)
            self._fill_management_search_field(title_sku_element, "")

        search_button = self._resolve_selector(selectors.get("search_button", {}), context)
        if not self._selector_is_configured(search_button):
            raise ValueError("search_button selector is not configured.")
        input_element.send_keys(Keys.ENTER)
        context["management_search_submit_method"] = "enter"
        context["edit_entry_stage"] = "management_search_submitted"
        self._pause(2.0)

        result_row = self._resolve_selector(selectors.get("product_result_row", {}), context)
        if self._selector_is_configured(result_row):
            try:
                self._wait_for_management_result_row(result_row, context)
            except TimeoutException:
                context["management_search_retry"] = "query_url"
                self._open_management_filter_query(context)
                self._pause(4.0)
                self._wait_for_management_search_ready(context)
                try:
                    self._wait_for_management_result_row(result_row, context)
                except TimeoutException as exc:
                    self._assert_not_redirected_to_login(context)
                    self._assert_no_risk_control_block(context)
                    body_text = self._extract_page_body_text()
                    if self._management_search_shows_no_data(body_text):
                        message = (
                            f"Product {context.get('product_id', '')} is unavailable after "
                            "management search returned an explicit no-data state."
                        )
                        self._annotate_page_error_context(
                            context,
                            stage_name="management_product_search",
                            error_text=message,
                            error_category="product_unavailable",
                        )
                        raise OfflineTaskStateError(message) from exc
                    message = (
                        f"Timed out locating product {context.get('product_id', '')} "
                        "after management search and query-url fallback."
                    )
                    self._annotate_page_error_context(
                        context,
                        stage_name="management_product_search",
                        error_text=message,
                        error_category="management_search_timeout",
                    )
                    raise OfflineTaskStateError(message) from exc
            context["edit_entry_stage"] = "management_product_matched"
        else:
            body_text = self.driver.find_element(By.TAG_NAME, "body").text
            if context.get("product_id", "") not in body_text:
                raise OfflineTaskNotFoundError(
                    f"Product '{context.get('product_id', '')}' was not found on the management page."
                )

    @staticmethod
    def _management_search_shows_no_data(body_text: str) -> bool:
        no_data_markers = (
            "暂无数据",
            "没有找到商品",
            "未找到相关商品",
            "没有符合条件的商品",
        )
        return any(marker in str(body_text or "") for marker in no_data_markers)

    def _wait_for_management_result_row(
        self,
        configured_selector: dict[str, str],
        context: dict[str, Any],
    ) -> Any:
        product_id = str(context.get("product_id", "")).strip()
        candidates = [configured_selector]
        if product_id:
            fallback = {
                "by": "xpath",
                "value": (
                    f"//tr[@data-row-key='{product_id}' or "
                    f".//td[normalize-space(.)='{product_id}' or contains(normalize-space(.), '{product_id}')]]"
                ),
            }
            if fallback != configured_selector:
                candidates.append(fallback)

        last_error: TimeoutException | None = None
        for selector in candidates:
            try:
                return self._wait_for_element(selector)
            except TimeoutException as exc:
                last_error = exc
        if last_error is not None:
            raise last_error
        raise TimeoutException("No management result-row selector was available.")

    def _wait_for_management_search_ready(self, context: dict[str, Any]) -> None:
        if not self.driver:
            raise RuntimeError("Browser has not been opened.")
        timeout = float(self.browser_config.get("explicit_wait_seconds", 20))
        WebDriverWait(self.driver, timeout).until(
            lambda driver: bool(
                driver.execute_script(
                    """
                    const button = document.querySelector('button[data-click="search"]');
                    const inputs = document.querySelectorAll('.search-field input.ant-input');
                    const tableReady = document.querySelector('table') || document.querySelector('.ant-table');
                    return button && inputs.length >= 2 && tableReady;
                    """
                )
            )
        )
        context["edit_entry_stage"] = "management_search_ready"

    def _open_management_filter_query(self, context: dict[str, Any]) -> None:
        if not self.driver:
            raise RuntimeError("Browser has not been opened.")
        product_id = str(context.get("product_id", "")).strip()
        if not product_id:
            raise OfflineTaskNotFoundError("Missing product_id for the 1688 management filter query.")
        target_url = self.driver.execute_script(
            """
            const productId = String(arguments[0] || '').trim();
            const url = new URL(window.location.href);
            url.searchParams.set('q', `filterOfferId=${productId}`);
            window.location.assign(url.toString());
            return url.toString();
            """,
            product_id,
        )
        context["management_filter_query_url"] = str(target_url or "")

    def _fill_management_search_field(self, element: Any, value: Any) -> None:
        if not self.driver:
            raise RuntimeError("Browser has not been opened.")
        target_value = str(value or "")
        try:
            self.driver.execute_script(
                "arguments[0].scrollIntoView({block: 'center', inline: 'nearest'});",
                element,
            )
            element.click()
            element.send_keys(Keys.CONTROL, "a")
            element.send_keys(Keys.DELETE)
            if target_value:
                element.send_keys(target_value)
            self._pause(0.3)
            if str(element.get_attribute("value") or "") == target_value:
                return
        except Exception:
            pass
        self._fill_text_field(element, target_value, clear=True)

    def _open_edit_page(
        self,
        selectors: dict[str, Any],
        context: dict[str, Any],
        *,
        prefer_direct: bool = False,
    ) -> None:
        product_id = str(context.get("product_id", "")).strip()
        if prefer_direct and product_id:
            if not self.driver:
                raise RuntimeError("Browser has not been opened.")
            context["edit_entry_mode"] = "direct"
            self.driver.switch_to.default_content()
            self.driver.get(f"https://offer-new.1688.com/popular/publish.htm?id={product_id}&operator=edit")
            self._pause(6.0)
            self._raise_if_edit_page_unavailable(context)
            self._activate_sales_info_section(selectors, context)
            context["edit_entry_stage"] = "edit_page_ready"
            return

        result_row_selector = self._resolve_selector(selectors.get("product_result_row", {}), context)
        edit_button_in_row = selectors.get("edit_button_in_row", {})
        before_handles = list(self.driver.window_handles)

        if self._selector_is_configured(result_row_selector) and edit_button_in_row:
            row_element = self._wait_for_management_result_row(result_row_selector, context)
            edit_button = row_element.find_element(
                BY_MAPPING.get(str(edit_button_in_row.get("by", "css")).strip().lower(), By.CSS_SELECTOR),
                str(edit_button_in_row.get("value", "")).strip(),
            )
            self.driver.execute_script("arguments[0].click();", edit_button)
            context["edit_entry_stage"] = "management_edit_clicked"
        else:
            edit_button_selector = self._resolve_selector(selectors.get("edit_button", {}), context)
            if not self._selector_is_configured(edit_button_selector):
                raise ValueError("Neither edit_button_in_row nor edit_button selector is configured.")
            self._wait_for_element(edit_button_selector, clickable=True).click()

        self._switch_to_newest_window(before_handles)
        self._pause(1.5)

        self._raise_if_edit_page_unavailable(context)
        self._activate_sales_info_section(selectors, context)
        context["edit_entry_stage"] = "edit_page_ready"

    def _raise_if_edit_page_unavailable(self, context: dict[str, Any]) -> None:
        if not self.driver:
            raise RuntimeError("Browser has not been opened.")

        body_text = self._extract_page_body_text()
        try:
            page_source = str(self.driver.page_source or "")
        except Exception:
            page_source = ""
        page_content = f"{body_text}\n{page_source}"
        product_id = str(context.get("product_id", "")).strip()

        if "PUB_BIZCHECK_PRIMARY_ITEM_DELETE" in page_content or "没有找到商品" in page_content:
            error_code = "PUB_BIZCHECK_PRIMARY_ITEM_DELETE"
            error_text = f"没有找到商品（{error_code}）"
            context["edit_page_error_code"] = error_code
            context["page_error_category"] = "task_not_found"
            context["page_error_stage"] = "edit_page_load"
            context["page_error_text"] = error_text
            raise OfflineTaskNotFoundError(
                f"Product '{product_id}' was not found or has been deleted on 1688 ({error_code})."
            )

        if "PUB_BIZCHECK_BIZ_IDENTITY_ERROR" in page_content:
            error_code = "PUB_BIZCHECK_BIZ_IDENTITY_ERROR"
            direct_entry = context.get("edit_entry_mode") == "direct"
            error_text = (
                f"直达编辑入口被拒绝，需从商品管理进入（{error_code}）"
                if direct_entry
                else f"从商品管理进入后仍无法编辑当前商品（{error_code}）"
            )
            context["edit_page_error_code"] = error_code
            context["page_error_category"] = (
                "edit_route_rejected" if direct_entry else "product_unavailable"
            )
            context["page_error_stage"] = "edit_page_load"
            context["page_error_text"] = error_text
            raise OfflineTaskStateError(
                f"Product '{product_id}' edit entry was rejected by 1688 ({error_code})."
            )

    def _activate_sales_info_section(
        self,
        selectors: dict[str, Any],
        context: dict[str, Any],
    ) -> None:
        sales_info_anchor = self._resolve_selector(selectors.get("sales_info_anchor", {}), context)
        if not self._selector_is_configured(sales_info_anchor):
            return
        try:
            if self._find_sku_row_by_runtime_value(context) is not None:
                context["sales_info_activation"] = "sku_table_already_visible"
                return
        except Exception:
            pass
        try:
            self._wait_for_element(sales_info_anchor, clickable=True).click()
        except TimeoutException:
            context["sales_info_activation_retry"] = "cdp_reload"
            try:
                self.driver.execute_cdp_cmd("Page.reload", {"ignoreCache": True})
            except Exception as exc:
                context["sales_info_activation_reload_error"] = str(exc)[:240]
            self._pause(5.0)
            try:
                if self._find_sku_row_by_runtime_value(context) is not None:
                    context["sales_info_activation"] = "sku_table_visible_after_reload"
                    return
            except Exception:
                pass
            self._wait_for_element(sales_info_anchor, clickable=True).click()
        context["sales_info_activation"] = "anchor_clicked"
        self._pause(0.5)

    def _assert_not_redirected_to_login(self, context: dict[str, Any] | None = None) -> None:
        if not self.driver:
            raise RuntimeError("Browser has not been opened.")
        current_url = str(self.driver.current_url or "").strip().lower()
        if "login.taobao.com" in current_url:
            message = "1688 登录态已失效，当前页面被重定向到登录页，请重新登录后重试。"
            if context is not None:
                self._annotate_page_error_context(
                    context,
                    stage_name="login_check",
                    error_text=message,
                    error_category="login_required",
                )
            raise OfflineLoginRequiredError(message)
        if "login.1688.com" in current_url and "publish.htm" not in current_url and "work.1688.com" not in current_url:
            message = "1688 登录态已失效，当前页面停留在登录流程，请重新登录后重试。"
            if context is not None:
                self._annotate_page_error_context(
                    context,
                    stage_name="login_check",
                    error_text=message,
                    error_category="login_required",
                )
            raise OfflineLoginRequiredError(message)

    def _toggle_sku_offline(
        self,
        selectors: dict[str, Any],
        context: dict[str, Any],
    ) -> bool:
        sku_row_selector = self._resolve_selector(selectors.get("sku_row", {}), context)
        if not self._selector_is_configured(sku_row_selector):
            raise ValueError("sku_row selector is not configured.")
        sku_row = self._find_sku_row_by_runtime_value(context)
        if sku_row is not None:
            return self._toggle_found_sku_row(selectors, context, sku_row)
        try:
            sku_row = self._wait_for_element(sku_row_selector)
        except TimeoutException as exc:
            available_skus = self._collect_visible_sku_codes()
            context["available_sku_count"] = str(len(available_skus))
            if available_skus:
                context["available_sku_codes"] = available_skus[:50]
                fallback_sku = self._match_available_sku(
                    str(context.get("online_sku", "")).strip(),
                    available_skus,
                )
                if fallback_sku:
                    fallback_context = dict(context)
                    fallback_context["online_sku"] = fallback_sku
                    sku_row = self._find_sku_row_by_runtime_value(fallback_context)
                    if sku_row is not None:
                        context["online_sku_requested"] = str(context.get("online_sku", "")).strip()
                        context["online_sku"] = fallback_sku
                        context["online_sku_match_mode"] = "normalized_exact"
                        return self._toggle_found_sku_row(selectors, context, sku_row)
                    fallback_selector = self._resolve_selector(selectors.get("sku_row", {}), fallback_context)
                    if self._selector_is_configured(fallback_selector):
                        try:
                            sku_row = self._wait_for_element(fallback_selector)
                            context["online_sku_requested"] = str(context.get("online_sku", "")).strip()
                            context["online_sku"] = fallback_sku
                            context["online_sku_match_mode"] = "normalized_exact"
                        except TimeoutException:
                            sku_row = None
                        if sku_row is not None:
                            return self._toggle_found_sku_row(selectors, context, sku_row)

                raise OfflineTaskNotFoundError(
                    self._build_sku_not_found_message(
                        str(context.get("online_sku", "")).strip(),
                        available_skus,
                    )
                ) from exc
            raise

        return self._toggle_found_sku_row(selectors, context, sku_row)

    def _find_sku_row_by_runtime_value(self, context: dict[str, Any]) -> Any | None:
        if not self.driver:
            raise RuntimeError("Browser has not been opened.")
        target_sku = str(context.get("online_sku", "")).strip()
        if not target_sku:
            return None
        return self.driver.execute_script(
            """
            const target = String(arguments[0] || '').trim();
            const normalizedTarget = target.replace(/[^A-Za-z0-9]/g, '').toUpperCase();
            if (!normalizedTarget) return null;
            const rows = Array.from(document.querySelectorAll('#guid-skuTable tbody tr, table tbody tr'));
            for (const row of rows) {
              const values = Array.from(row.querySelectorAll('input, textarea'))
                .map((node) => String(node.value || '').trim())
                .filter(Boolean);
              const rowText = String(row.innerText || '').trim();
              if (rowText) values.push(rowText);
              for (const value of values) {
                const normalizedValue = String(value || '').replace(/[^A-Za-z0-9]/g, '').toUpperCase();
                if (value === target || normalizedValue === normalizedTarget) {
                  return row;
                }
              }
            }
            return null;
            """,
            target_sku,
        )

    def _toggle_found_sku_row(
        self,
        selectors: dict[str, Any],
        context: dict[str, Any],
        sku_row: Any,
    ) -> bool:
        if not self.driver:
            raise RuntimeError("Browser has not been opened.")

        switch_selector = selectors.get("sku_switch", {})
        if not switch_selector:
            raise ValueError("sku_switch selector is not configured.")
        switch_element = sku_row.find_element(
            BY_MAPPING.get(str(switch_selector.get("by", "css")).strip().lower(), By.CSS_SELECTOR),
            str(switch_selector.get("value", "")).strip(),
        )
        switch_label_before = self._read_switch_label(switch_element)
        context["switch_label_before"] = switch_label_before

        if self._is_already_offline(switch_element, switch_label_before):
            return False
        if not self._is_online_state(switch_element, switch_label_before):
            raise OfflineTaskStateError(
                f"Unable to determine current switch state for SKU '{context.get('online_sku', '')}'."
            )

        self._assert_target_not_sole_online_sku(context)
        self.driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", switch_element)
        self.driver.execute_script("arguments[0].click();", switch_element)
        self._pause(0.8)
        self._wait_for_switch_change(sku_row, switch_selector, expect_offline=True)
        context["switch_label_after"] = self._read_switch_label(
            sku_row.find_element(
                BY_MAPPING.get(str(switch_selector.get("by", "css")).strip().lower(), By.CSS_SELECTOR),
                str(switch_selector.get("value", "")).strip(),
            )
        )
        context["execution_result"] = "offline_toggled"
        return True

    def _assert_target_not_sole_online_sku(self, context: dict[str, Any]) -> None:
        if not self.driver:
            raise RuntimeError("Browser has not been opened.")
        switch_elements = self.driver.execute_script(
            """
            return Array.from(document.querySelectorAll('button[role="switch"].ant-switch'))
              .filter((element) => {
                const row = element.closest('tr');
                if (!row) return false;
                return Array.from(row.querySelectorAll('input, textarea'))
                  .some((input) => String(input.value || '').trim());
              });
            """
        )
        if not isinstance(switch_elements, list):
            switch_elements = []
        visible_states: list[dict[str, Any]] = []
        for element in switch_elements:
            try:
                if not element.is_displayed():
                    continue
                label = self._read_switch_label(element)
                visible_states.append(
                    {
                        "label": label,
                        "aria_checked": str(element.get_attribute("aria-checked") or ""),
                        "online": self._is_online_state(element, label),
                    }
                )
            except Exception:
                continue

        summary = {
            "visible_switch_count": len(visible_states),
            "online_switch_count": sum(1 for item in visible_states if item["online"]),
            "states": visible_states,
        }
        context["sku_switch_summary_before"] = summary
        if summary["visible_switch_count"] != 1 or summary["online_switch_count"] != 1:
            return

        error_text = (
            "目标 SKU 是该商品唯一在线 SKU，1688 要求至少保留一个在线 SKU；"
            "脚本禁止自动整商品下架。"
        )
        context["automatic_product_offline_allowed"] = "false"
        self._annotate_page_error_context(
            context,
            stage_name="pre_sku_toggle",
            error_text=error_text,
            error_category="sole_sku_requires_product_offline",
        )
        raise PublishValidationError(error_text)

    def _collect_visible_sku_codes(self) -> list[str]:
        if not self.driver:
            raise RuntimeError("Browser has not been opened.")
        values = self.driver.execute_script(
            """
            return Array.from(document.querySelectorAll('#guid-skuTable tbody tr'))
              .map((row) => {
                const inputs = Array.from(row.querySelectorAll('input'));
                const cargoInput = inputs[inputs.length - 1];
                return cargoInput ? String(cargoInput.value || '').trim() : '';
              })
              .filter(Boolean);
            """
        )
        if not isinstance(values, list):
            return []
        return [str(item).strip() for item in values if str(item).strip()]

    def _match_available_sku(self, target_sku: str, available_skus: list[str]) -> str:
        normalized_target = self._normalize_sku_code(target_sku)
        if not normalized_target:
            return ""
        exact_match = [code for code in available_skus if self._normalize_sku_code(code) == normalized_target]
        if len(exact_match) == 1:
            return exact_match[0]
        return ""

    def _normalize_sku_code(self, value: str) -> str:
        text = str(value or "").strip().upper()
        if not text:
            return ""
        return re.sub(r"[^A-Z0-9]", "", text)

    def _build_sku_not_found_message(self, target_sku: str, available_skus: list[str]) -> str:
        sample = ", ".join(available_skus[:10])
        return (
            f"SKU '{target_sku}' was not found on the edit page. "
            f"Available single-SKU codes: {sample}. "
            "The input template SKU may be outdated or mismatched."
        )

    def _submit_changes(
        self,
        selectors: dict[str, Any],
        success_detection: dict[str, Any],
        error_detection: dict[str, Any],
        pre_submit_backfill: dict[str, Any],
        context: dict[str, Any],
    ) -> None:
        submit_button = self._resolve_selector(selectors.get("submit_button", {}), context)
        if not self._selector_is_configured(submit_button):
            raise ValueError("submit_button selector is not configured.")
        self._check_publish_error_state(
            error_detection,
            context=context,
            stage_name="pre_offline_submit",
            exception_cls=PublishSubmitError,
        )
        try:
            self._prepare_pre_submit_backfill(pre_submit_backfill, context)
            self._raise_if_inline_validation_present(context, stage_name="pre_offline_submit")
        except PublishValidationError:
            self._revert_unsaved_sku_toggle(selectors, context)
            raise
        self._ensure_target_sku_still_offline(selectors, context)
        self._install_offline_submit_trace()
        submit_element = self._wait_for_element(submit_button, clickable=True)
        context["submit_button_disabled_before_click"] = str(submit_element.get_attribute("disabled") or "")
        context["submit_button_class_before_click"] = str(submit_element.get_attribute("class") or "")
        self._click_submit_element(submit_element, context)
        self._pause(1.0)

        confirm_button = self._resolve_selector(selectors.get("confirm_button", {}), context)
        self._click_optional_confirm_button(confirm_button)

        if self._record_submit_success_if_detected(
            success_detection,
            context,
            phase="after_primary_click",
        ):
            return

        self._check_publish_error_state(
            error_detection,
            context=context,
            stage_name="post_offline_submit",
            exception_cls=PublishSubmitError,
        )
        self._raise_if_inline_validation_present(context, stage_name="post_offline_submit")
        trace_detected = self._assert_offline_submit_trace(context)
        if not trace_detected:
            context["submit_retry_mode"] = "dispatch_event_click"
            self._dispatch_submit_button_click(submit_button, context)
            self._pause(1.0)
            self._click_optional_confirm_button(confirm_button)
            if self._record_submit_success_if_detected(
                success_detection,
                context,
                phase="after_dispatch_retry",
            ):
                return
            self._check_publish_error_state(
                error_detection,
                context=context,
                stage_name="post_offline_submit_retry",
                exception_cls=PublishSubmitError,
            )
            self._raise_if_inline_validation_present(context, stage_name="post_offline_submit_retry")
            trace_detected = self._assert_offline_submit_trace(context)
        if not trace_detected:
            direct_submit_result = self._retry_submit_via_trace(context)
            context["submit_direct_retry"] = direct_submit_result
            trace_detected = bool(direct_submit_result.get("ok"))
        if not trace_detected:
            if self._record_submit_success_if_detected(
                success_detection,
                context,
                phase="after_trace_miss",
            ):
                return
            diagnostics = self._collect_submit_block_diagnostics(submit_button)
            context["submit_block_diagnostics"] = diagnostics
            if self._probe_persisted_offline_after_trace_miss(selectors, context):
                context["success_detected"] = "true"
                context["submit_success_phase"] = "persisted_state_after_trace_miss"
                context["execution_result"] = "submitted_untraced_verified"
                return
            assist_messages = [
                str(item).strip()
                for item in diagnostics.get("assist_messages", [])
                if str(item).strip()
            ]
            hidden_messages = [
                str(item.get("text", "")).strip()
                for item in diagnostics.get("validation_nodes", [])
                if isinstance(item, dict) and str(item.get("text", "")).strip()
            ]
            summary = " | ".join((assist_messages + hidden_messages)[:3])
            error_text = "提交按钮未产生平台请求" + (f"：{summary}" if summary else "")
            error_category = (
                "sole_sku_requires_product_offline"
                if self._contains_sole_online_sku_validation(summary)
                else "submit_blocked_before_request"
            )
            self._annotate_page_error_context(
                context,
                stage_name="submit_before_request",
                error_text=error_text,
                error_category=error_category,
            )
            raise PublishSubmitError(
                "Submit button was clicked but no submit request was captured. "
                "The page likely blocked submit due to hidden validation or disabled state."
                + (f" validation: {summary}" if summary else "")
            )
        success_detected = self._wait_for_success(success_detection, context)
        context["success_detected"] = "true" if success_detected else "false"
        if bool(success_detection.get("required", False)) and not success_detected:
            raise PublishSubmitError("Did not detect the configured success signal after submitting offline changes.")
        context["execution_result"] = "submitted"

    @staticmethod
    def _contains_sole_online_sku_validation(text: str) -> bool:
        normalized = str(text or "").lower()
        return any(
            marker in normalized
            for marker in (
                "\u81f3\u5c11\u8981\u6709\u4e00\u4e2a\u5728\u7ebf\u72b6\u6001\u7684sku",
                "\u81f3\u5c11\u4fdd\u7559\u4e00\u4e2a\u5728\u7ebfsku",
            )
        )

    def _collect_submit_block_diagnostics(self, submit_button: dict[str, str]) -> dict[str, Any]:
        if not self.driver:
            raise RuntimeError("Browser has not been opened.")
        assist_messages = self._collect_assist_messages()
        visible_inline_messages = self._collect_visible_inline_validation_messages()
        payload = self.driver.execute_script(
            r"""
            const buttonSelector = String(arguments[0] || '');
            const norm = (value) => String(value || '').replace(/\s+/g, ' ').trim();
            const isVisible = (node) => {
              if (!node) return false;
              const style = window.getComputedStyle(node);
              const rect = node.getBoundingClientRect();
              return style.display !== 'none' && style.visibility !== 'hidden' && rect.width > 0 && rect.height > 0;
            };
            const validationSelectors = [
              '[aria-invalid="true"]',
              '.ant-form-item-has-error',
              '.ant-form-item-explain-error',
              '.next-form-item.has-error',
              '.next-form-item-error',
              '.next-form-item-help',
              '.modern-message .message-span',
              '.prop-error-message .message-span'
            ];
            const validationNodes = [];
            const seen = new Set();
            for (const selector of validationSelectors) {
              for (const node of Array.from(document.querySelectorAll(selector))) {
                const text = norm(node.innerText || node.textContent || '');
                const key = `${selector}|${text}|${node.id || ''}`;
                if (seen.has(key)) continue;
                seen.add(key);
                validationNodes.push({
                  selector,
                  id: String(node.id || ''),
                  class_name: String(node.className || '').slice(0, 300),
                  text: text.slice(0, 500),
                  visible: isVisible(node),
                  aria_invalid: String(node.getAttribute('aria-invalid') || '')
                });
                if (validationNodes.length >= 40) break;
              }
              if (validationNodes.length >= 40) break;
            }

            const sdk = window.SellPublishSdk;
            const state = sdk && sdk.engine && sdk.engine.getJsonState ? sdk.engine.getJsonState() : {};
            const components = (state && state.components) || {};
            const componentDiagnostics = [];
            for (const [name, component] of Object.entries(components)) {
              const props = (component && component.props) || {};
              const diagnostic = {};
              for (const [key, value] of Object.entries(props)) {
                if (!/error|invalid|message|validate|required|assist/i.test(key)) continue;
                if (typeof value === 'string' || typeof value === 'number' || typeof value === 'boolean') {
                  diagnostic[key] = String(value).slice(0, 500);
                } else if (Array.isArray(value)) {
                  diagnostic[key] = { type: 'array', length: value.length };
                } else if (value && typeof value === 'object') {
                  diagnostic[key] = { type: 'object', keys: Object.keys(value).slice(0, 30) };
                }
              }
              if (Object.keys(diagnostic).length > 0) {
                componentDiagnostics.push({ name, diagnostic });
              }
              if (componentDiagnostics.length >= 40) break;
            }

            let button = null;
            try {
              button = buttonSelector ? document.querySelector(buttonSelector) : null;
            } catch (error) {
              button = null;
            }
            button = button || document.querySelector('#submitFormButton');
            return {
              validation_nodes: validationNodes,
              sdk_component_diagnostics: componentDiagnostics,
              button: button ? {
                id: String(button.id || ''),
                class_name: String(button.className || '').slice(0, 300),
                disabled: Boolean(button.disabled),
                aria_disabled: String(button.getAttribute('aria-disabled') || ''),
                visible: isVisible(button),
                text: norm(button.innerText || button.textContent || '').slice(0, 200)
              } : { missing: true }
            };
            """,
            submit_button.get("value", "") if submit_button.get("by") == "css" else "#submitFormButton",
        )
        if not isinstance(payload, dict):
            payload = {}
        payload["assist_messages"] = assist_messages[:30]
        payload["visible_inline_messages"] = visible_inline_messages[:30]
        return payload

    def _probe_persisted_offline_after_trace_miss(
        self,
        selectors: dict[str, Any],
        context: dict[str, Any],
    ) -> bool:
        if not self.driver:
            raise RuntimeError("Browser has not been opened.")
        try:
            self.driver.switch_to.default_content()
            self.driver.refresh()
            self._pause(5.0)
            self._assert_not_redirected_to_login(context)
            self._assert_no_risk_control_block(context)
            self._activate_sales_info_section(selectors, context)
            expected_product_id = str(context.get("product_id", "")).strip()
            actual_product_id = self._extract_product_id_from_current_url()
            context["trace_miss_probe_product_id"] = actual_product_id
            if expected_product_id and actual_product_id != expected_product_id:
                context["trace_miss_persistence_probe"] = "identity_mismatch"
                return False
            sku_row = self._find_sku_row_by_runtime_value(context)
            if sku_row is None:
                sku_row_selector = self._resolve_selector(selectors.get("sku_row", {}), context)
                if not self._selector_is_configured(sku_row_selector):
                    context["trace_miss_persistence_probe"] = "sku_selector_missing"
                    return False
                sku_row = self._wait_for_element(sku_row_selector)
            switch_selector = selectors.get("sku_switch", {})
            if not switch_selector:
                context["trace_miss_persistence_probe"] = "switch_selector_missing"
                return False
            switch_element = sku_row.find_element(
                BY_MAPPING.get(str(switch_selector.get("by", "css")).strip().lower(), By.CSS_SELECTOR),
                str(switch_selector.get("value", "")).strip(),
            )
            switch_label = self._read_switch_label(switch_element)
            aria_checked = str(switch_element.get_attribute("aria-checked") or "")
            context["trace_miss_probe_switch_label"] = switch_label
            context["trace_miss_probe_switch_aria_checked"] = aria_checked
            persisted = self._is_already_offline(switch_element, switch_label)
            context["trace_miss_persistence_probe"] = "offline" if persisted else "online"
            return persisted
        except Exception as exc:
            context["trace_miss_persistence_probe"] = "error"
            context["trace_miss_persistence_probe_error"] = str(exc)[:500]
            return False

    def _record_submit_success_if_detected(
        self,
        success_detection: dict[str, Any],
        context: dict[str, Any],
        *,
        phase: str,
    ) -> bool:
        if not success_detection or not bool(success_detection.get("enabled", True)):
            return False
        success_detected = self._wait_for_success(success_detection, context)
        context["success_detected"] = "true" if success_detected else "false"
        if not success_detected:
            return False
        context["submit_success_phase"] = phase
        context["execution_result"] = "submitted"
        return True

    def _ensure_target_sku_still_offline(
        self,
        selectors: dict[str, Any],
        context: dict[str, Any],
    ) -> None:
        if not self.driver:
            raise RuntimeError("Browser has not been opened.")
        sku_row_selector = self._resolve_selector(selectors.get("sku_row", {}), context)
        if not self._selector_is_configured(sku_row_selector):
            raise ValueError("sku_row selector is not configured.")
        sku_row = self._find_sku_row_by_runtime_value(context)
        if sku_row is None:
            sku_row = self._wait_for_element(sku_row_selector)

        switch_selector = selectors.get("sku_switch", {})
        if not switch_selector:
            raise ValueError("sku_switch selector is not configured.")
        switch_element = self._get_row_switch_element(sku_row, switch_selector)
        label_before_submit = self._read_switch_label(switch_element)
        context["switch_label_before_submit"] = label_before_submit
        if not self._is_already_offline(switch_element, label_before_submit):
            if not self._is_online_state(switch_element, label_before_submit):
                raise OfflineTaskStateError(
                    f"Unable to ensure offline state before submit for SKU '{context.get('online_sku', '')}'."
                )
            self.driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", switch_element)
            self.driver.execute_script("arguments[0].click();", switch_element)
            self._pause(0.8)
            self._wait_for_switch_change(sku_row, switch_selector, expect_offline=True)

        self._ensure_switch_state_stable(
            sku_row_selector=sku_row_selector,
            switch_selector=switch_selector,
            expect_offline=True,
            context=context,
            context_key="switch_label_stable_before_submit",
        )
        state_patch_result = self._force_target_sku_offline_in_runtime_state(context)
        context["runtime_sku_state_patch"] = state_patch_result
        if bool(state_patch_result.get("supported")) and bool(state_patch_result.get("found")):
            after_status = int(state_patch_result.get("after_status", 0) or 0)
            if after_status != -2:
                raise OfflineTaskStateError(
                    f"Runtime skuTable status is not offline before submit for SKU '{context.get('online_sku', '')}'."
                )
        refreshed_row = self._find_sku_row_by_runtime_value(context)
        if refreshed_row is None:
            refreshed_row = self._wait_for_element(sku_row_selector)
        refreshed_switch = self._get_row_switch_element(refreshed_row, switch_selector)
        context["switch_label_reasserted_before_submit"] = self._read_switch_label(refreshed_switch)

    def _raise_if_inline_validation_present(
        self,
        context: dict[str, Any],
        *,
        stage_name: str,
    ) -> None:
        messages = self._collect_visible_inline_validation_messages()
        if not messages:
            return
        normalized_messages = [str(item).strip() for item in messages if str(item).strip()]
        if not normalized_messages:
            return
        if stage_name.startswith("pre_offline_submit"):
            max_rounds = 4
            for round_index in range(1, max_rounds + 1):
                recovered = self._try_recover_inline_required_category_props(normalized_messages, context)
                if not recovered:
                    break
                context["inline_validation_recovery_rounds"] = round_index
                recheck = self._collect_visible_inline_validation_messages()
                normalized_recheck = [str(item).strip() for item in recheck if str(item).strip()]
                if not normalized_recheck:
                    context["inline_validation_recovered"] = "true"
                    return
                normalized_messages = normalized_recheck
        error_text = " | ".join(normalized_messages[:3])
        sole_sku_blocked = any(
            "至少要有一个在线状态的sku" in message.replace(" ", "").lower()
            for message in normalized_messages
        )
        campaign_blocked = any(
            "天天特卖" in message
            or (
                any(keyword in message for keyword in ("活动期间", "已报名", "参加活动", "活动商品"))
                and any(keyword in message for keyword in ("不允许", "不能", "无法", "限制", "请先退出"))
            )
            for message in normalized_messages
        )
        delivery_service_blocked = any(
            "配送服务" in message and any(keyword in message for keyword in ("必填", "请选择", "不能为空"))
            for message in normalized_messages
        )
        if sole_sku_blocked:
            error_category = "sole_sku_requires_product_offline"
        elif campaign_blocked:
            error_category = "campaign_restriction"
        elif delivery_service_blocked:
            error_category = "delivery_service_backfill_failed"
        else:
            error_category = "business_validation"
        if sole_sku_blocked:
            context["automatic_product_offline_allowed"] = "false"
        if campaign_blocked:
            context["campaign_restriction_detected"] = "true"
        self._annotate_page_error_context(
            context,
            stage_name=stage_name,
            error_text=error_text,
            error_category=error_category,
        )
        raise PublishValidationError(f"{stage_name} blocked by inline validation: {error_text}")

    def _collect_visible_inline_validation_messages(self) -> list[str]:
        if not self.driver:
            raise RuntimeError("Browser has not been opened.")
        try:
            values = self.driver.execute_script(
                """
                function norm(value) {
                  return String(value || '').replace(/\\s+/g, ' ').trim();
                }
                function isVisible(node) {
                  if (!node) return false;
                  const style = window.getComputedStyle(node);
                  const rect = node.getBoundingClientRect();
                  return style.display !== 'none' && style.visibility !== 'hidden' && rect.width > 0 && rect.height > 0;
                }
                function hasValidationKeyword(text) {
                  const keywords = [
                    '\\u4e0d\\u80fd\\u4e3a\\u7a7a',
                    '\\u5fc5\\u586b',
                    '\\u8bf7\\u9009\\u62e9',
                    '\\u8bf7\\u5b8c\\u5584',
                    '\\u8bf7\\u586b\\u5199',
                    '\\u4e0d\\u6ee1\\u8db3\\u8981\\u6c42',
                    '\\u81f3\\u5c11\\u8981\\u6709\\u4e00\\u4e2a\\u5728\\u7ebf\\u72b6\\u6001',
                    '\\u5929\\u5929\\u7279\\u5356',
                    '\\u6d3b\\u52a8\\u671f\\u95f4',
                    '\\u5df2\\u62a5\\u540d',
                    '\\u53c2\\u52a0\\u6d3b\\u52a8',
                    '\\u8bf7\\u4fee\\u6539',
                    '\\u9519\\u8bef',
                    '\\u6821\\u9a8c',
                  ];
                  return keywords.some((keyword) => text.includes(keyword));
                }
                const selectors = [
                  '#guid-catProp .prop-error-message .message-span',
                  '#guid-catProp .modern-message .message-span',
                  '#guid-processingSkuPropTable .modern-message .message-span',
                  '#guid-customExtraService .modern-message .message-span',
                  '#guid-buyerProtection .modern-message .message-span',
                  '#guid-logistics .modern-message .message-span',
                  '#guid-catProp .ant-form-item-explain-error',
                  '#guid-customExtraService .ant-form-item-explain-error',
                  '#guid-buyerProtection .ant-form-item-explain-error',
                  '#guid-logistics .ant-form-item-explain-error',
                  '.ant-form-item-explain-error',
                  '.ant-form-explain',
                  '.next-form-item-help',
                  '.next-form-item-error',
                  '.next-field-help',
                  '.ant-message-notice-content',
                  '.next-message-content',
                  '.next-message-title',
                  '.ant-modal-body',
                  '.next-dialog-body'
                ];
                const raw = selectors.flatMap((selector) => Array.from(document.querySelectorAll(selector)));
                const visible = raw
                  .filter(isVisible)
                  .map((node) => {
                    const text = norm(node.innerText || node.textContent || '');
                    const classText = `${node.className || ''} ${(node.parentElement && node.parentElement.className) || ''}`.toLowerCase();
                    const hasErrorClass = /error|warn|invalid|fail/.test(classText);
                    return {
                      text,
                      keep: hasErrorClass || hasValidationKeyword(text),
                    };
                  })
                  .filter((item) => item.keep)
                  .map((item) => item.text);
                const deduped = [];
                for (const item of visible) {
                  if (!item) continue;
                  if (deduped.includes(item)) continue;
                  deduped.push(item);
                }
                return deduped;
                """
            )
        except Exception:
            return []
        if not isinstance(values, list):
            return []
        return [str(item).strip() for item in values if str(item).strip()]

    def _try_recover_inline_required_category_props(
        self,
        messages: list[str],
        context: dict[str, Any],
    ) -> bool:
        if not messages:
            return False
        candidates = self._extract_inline_required_labels(messages)
        if not candidates:
            return False
        applied_labels: list[str] = []
        for label in candidates:
            current_state = self._read_category_prop_state(label)
            current_value = str(current_state.get("current_value", "")).strip()
            if not bool(current_state.get("exists", False)):
                patched = self._patch_category_prop_with_first_option(label)
                if bool(patched.get("ok", False)):
                    applied_labels.append(label)
                    context.setdefault("inline_validation_recovery_state_patch", []).append(patched)
                continue
            if not (current_value and not self._is_placeholder_category_prop_value(current_value)):
                recovery_rule = {
                    "label": label,
                    "type": "combobox",
                    "allow_keep_existing": False,
                    "select_first_if_empty": True,
                    "select_first_option_if_unmatched": True,
                }
                try:
                    self._apply_category_prop_rule(recovery_rule, context)
                    applied_labels.append(label)
                except Exception as exc:
                    context.setdefault("inline_validation_recovery_errors", []).append(
                        {"label": label, "error": str(exc)}
                    )
            patched = self._patch_category_prop_with_first_option(label)
            if bool(patched.get("ok", False)):
                if label not in applied_labels:
                    applied_labels.append(label)
                context.setdefault("inline_validation_recovery_state_patch", []).append(patched)
        if not applied_labels:
            return False
        context.setdefault("inline_validation_recovery_applied", []).extend(applied_labels)
        self._pause(0.5)
        return True

    def _extract_inline_required_labels(self, messages: list[str]) -> list[str]:
        candidates: list[str] = []
        for raw in messages:
            text = str(raw or "").strip()
            if not text:
                continue
            quoted = re.findall(r"[“\"]([^”\"]+)[”\"]", text)
            for label in quoted:
                normalized = str(label).strip()
                if normalized and normalized not in candidates:
                    candidates.append(normalized)
            if quoted:
                continue
            if "不能为空" in text:
                normalized = (
                    text.replace("不能为空", "")
                    .replace("不可以为空", "")
                    .replace("为必填项", "")
                    .replace("为必填", "")
                    .replace(":", " ")
                    .replace("：", " ")
                    .strip()
                )
                normalized = normalized.strip(" .。[]【】()（）")
                if normalized and normalized not in candidates:
                    candidates.append(normalized)
        return candidates

    def _patch_category_prop_with_first_option(self, label: str) -> dict[str, Any]:
        if not self.driver:
            raise RuntimeError("Browser has not been opened.")
        result = self.driver.execute_script(
            """
            const rawLabel = String(arguments[0] || '').trim();
            const normalize = (value) => String(value || '').replace(/\\s+/g, ' ').trim();
            const targetLabel = normalize(rawLabel);
            if (!targetLabel) {
              return { ok: false, reason: 'empty_label' };
            }
            const sdk = window.SellPublishSdk;
            const engine = sdk && sdk.engine;
            const core = engine && engine._engine && engine._engine._core;
            if (!engine || !engine.getJsonState || !core || typeof core.changeElementValue !== 'function') {
              return { ok: false, reason: 'runtime_unavailable' };
            }
            const state = engine.getJsonState() || {};
            const components = state.components || {};
            const catPropComponent = components.catProp || {};
            const catPropProps = catPropComponent.props || {};
            const dataSource = Array.isArray(catPropProps.dataSource) ? catPropProps.dataSource : [];
            const valueMap = JSON.parse(JSON.stringify(catPropProps.value || {}));
            const matchedProp =
              dataSource.find((item) => normalize(item && item.label) === targetLabel) ||
              dataSource.find((item) => normalize(item && item.label).includes(targetLabel)) ||
              dataSource.find((item) => targetLabel.includes(normalize(item && item.label)));
            if (!matchedProp) {
              return { ok: false, reason: 'prop_not_found', label: rawLabel };
            }
            const key = String(matchedProp.name || '').trim();
            if (!key) {
              return { ok: false, reason: 'prop_key_missing', label: rawLabel };
            }
            const options = Array.isArray(matchedProp.dataSource) ? matchedProp.dataSource : [];
            const firstOption = options.find((item) => item && String(item.text || '').trim()) || null;
            if (firstOption) {
              valueMap[key] = {
                value: firstOption.value,
                text: firstOption.text,
              };
            } else {
              valueMap[key] = '其他';
            }
            core.changeElementValue('catProp', valueMap, { isDepth: false });
            return {
              ok: true,
              label: rawLabel,
              key,
              mode: firstOption ? 'first_option' : 'fallback_text',
              value: firstOption ? String(firstOption.text || '') : '其他',
            };
            """,
            label,
        )
        if not isinstance(result, dict):
            return {"ok": False, "reason": "invalid_result", "label": label}
        return {str(key): value for key, value in result.items()}

    def _get_row_switch_element(
        self,
        sku_row: Any,
        switch_selector: dict[str, Any],
    ) -> Any:
        return sku_row.find_element(
            BY_MAPPING.get(str(switch_selector.get("by", "css")).strip().lower(), By.CSS_SELECTOR),
            str(switch_selector.get("value", "")).strip(),
        )

    def _ensure_switch_state_stable(
        self,
        *,
        sku_row_selector: dict[str, str],
        switch_selector: dict[str, Any],
        expect_offline: bool,
        context: dict[str, Any],
        context_key: str,
        timeout_seconds: float = 8.0,
        stable_seconds: float = 1.2,
    ) -> None:
        if not self.driver:
            raise RuntimeError("Browser has not been opened.")
        row_by = BY_MAPPING.get(str(sku_row_selector.get("by", "css")).strip().lower(), By.CSS_SELECTOR)
        row_value = str(sku_row_selector.get("value", "")).strip()
        if not row_value:
            raise ValueError("sku_row selector is not configured.")

        deadline = time.time() + max(timeout_seconds, 0.5)
        stable_since = 0.0
        last_label = ""
        while True:
            runtime_row = self._find_sku_row_by_runtime_value(context)
            candidate_rows = [runtime_row] if runtime_row is not None else self.driver.find_elements(row_by, row_value)
            switch_element = None
            for row in candidate_rows:
                try:
                    switch_candidate = self._get_row_switch_element(row, switch_selector)
                    if row.is_displayed() and switch_candidate.is_displayed():
                        switch_element = switch_candidate
                        break
                    if switch_element is None:
                        switch_element = switch_candidate
                except Exception:
                    continue

            if switch_element is not None:
                label = self._read_switch_label(switch_element)
                aria_checked = str(switch_element.get_attribute("aria-checked") or "").strip().lower()
                observed_state = label or (f"aria-checked={aria_checked}" if aria_checked else "")
                last_label = observed_state
                state_ok = (
                    self._is_already_offline(switch_element, label)
                    if expect_offline
                    else self._is_online_state(switch_element, label)
                )
                if state_ok:
                    if stable_since <= 0:
                        stable_since = time.time()
                    elif time.time() - stable_since >= max(stable_seconds, 0.2):
                        context[context_key] = observed_state
                        return
                else:
                    stable_since = 0.0
            else:
                stable_since = 0.0

            if time.time() >= deadline:
                expected = "offline" if expect_offline else "online"
                raise OfflineTaskStateError(
                    f"SKU '{context.get('online_sku', '')}' switch did not stay {expected} before submit. "
                    f"Last observed label: '{last_label}'."
                )
            time.sleep(0.2)

    def _force_target_sku_offline_in_runtime_state(self, context: dict[str, Any]) -> dict[str, Any]:
        if not self.driver:
            raise RuntimeError("Browser has not been opened.")
        target_sku = str(context.get("online_sku", "")).strip()
        if not target_sku:
            return {"supported": False, "reason": "missing_target_sku"}
        result = self.driver.execute_script(
            """
            const targetSku = String(arguments[0] || '').trim();
            const normalize = (value) => String(value || '').trim().toUpperCase().replace(/[^A-Z0-9]/g, '');
            const targetNormalized = normalize(targetSku);
            const sdk = window.SellPublishSdk;
            const engine = sdk && sdk.engine;
            const core = engine && engine._engine && engine._engine._core;
            if (!engine || !engine.getJsonState || !core || typeof core.changeElementValue !== 'function') {
              return { supported: false, reason: 'runtime_unavailable' };
            }
            const state = engine.getJsonState() || {};
            const components = state.components || {};
            const skuComponent = components.skuTable || {};
            const skuProps = skuComponent.props || {};
            const skuFields = skuComponent.fields || {};
            const skuValues = Array.isArray(skuProps.value)
              ? skuProps.value
              : (Array.isArray(skuFields.value) ? skuFields.value : []);
            if (!Array.isArray(skuValues) || skuValues.length === 0) {
              return { supported: true, found: false, reason: 'sku_table_empty' };
            }
            let matchedIndex = -1;
            let exactMatches = [];
            let normalizedMatches = [];
            skuValues.forEach((item, index) => {
              const cargo = String((item && item.sku_cargoNumber) || '').trim();
              if (!cargo) {
                return;
              }
              if (cargo === targetSku) {
                exactMatches.push(index);
                return;
              }
              if (targetNormalized && normalize(cargo) === targetNormalized) {
                normalizedMatches.push(index);
              }
            });
            if (exactMatches.length === 1) {
              matchedIndex = exactMatches[0];
            } else if (exactMatches.length === 0 && normalizedMatches.length === 1) {
              matchedIndex = normalizedMatches[0];
            }
            if (matchedIndex < 0) {
              return {
                supported: true,
                found: false,
                reason: 'target_sku_not_found',
              };
            }
            const matchedSku = skuValues[matchedIndex] || {};
            const beforeStatus = Number(matchedSku.sku_status);
            const nextValues = skuValues.map((item, index) => {
              if (index !== matchedIndex) {
                return item;
              }
              return {
                ...(item || {}),
                sku_status: -2,
              };
            });
            core.changeElementValue('skuTable', nextValues, { isDepth: false });
            const refreshedState = engine.getJsonState() || {};
            const refreshedComponents = refreshedState.components || {};
            const refreshedSkuComponent = refreshedComponents.skuTable || {};
            const refreshedProps = refreshedSkuComponent.props || {};
            const refreshedFields = refreshedSkuComponent.fields || {};
            const refreshedValues = Array.isArray(refreshedProps.value)
              ? refreshedProps.value
              : (Array.isArray(refreshedFields.value) ? refreshedFields.value : []);
            const refreshedSku = Array.isArray(refreshedValues) ? refreshedValues[matchedIndex] || {} : {};
            return {
              supported: true,
              found: true,
              matched_cargo: String(matchedSku.sku_cargoNumber || ''),
              before_status: Number.isFinite(beforeStatus) ? beforeStatus : 0,
              after_status: Number(refreshedSku.sku_status),
              changed: beforeStatus !== -2,
            };
            """,
            target_sku,
        )
        if not isinstance(result, dict):
            return {"supported": False, "reason": "invalid_runtime_result"}
        return {str(key): value for key, value in result.items()}

    def _click_submit_element(self, submit_element: Any, context: dict[str, Any]) -> None:
        if not self.driver:
            raise RuntimeError("Browser has not been opened.")
        try:
            rect = self.driver.execute_script(
                """
                const target = arguments[0];
                target.scrollIntoView({block: 'center', inline: 'nearest'});
                const rect = target.getBoundingClientRect();
                return {
                  x: rect.left + rect.width / 2,
                  y: rect.top + rect.height / 2,
                  width: rect.width,
                  height: rect.height,
                };
                """,
                submit_element,
            )
            if isinstance(rect, dict) and float(rect.get("width", 0) or 0) > 0 and float(rect.get("height", 0) or 0) > 0:
                x = float(rect.get("x", 0) or 0)
                y = float(rect.get("y", 0) or 0)
                self.driver.execute_cdp_cmd("Input.dispatchMouseEvent", {"type": "mouseMoved", "x": x, "y": y})
                self.driver.execute_cdp_cmd(
                    "Input.dispatchMouseEvent",
                    {"type": "mousePressed", "x": x, "y": y, "button": "left", "clickCount": 1},
                )
                self.driver.execute_cdp_cmd(
                    "Input.dispatchMouseEvent",
                    {"type": "mouseReleased", "x": x, "y": y, "button": "left", "clickCount": 1},
                )
                context["submit_click_mode"] = "cdp_mouse"
                return
        except Exception as exc:
            context["submit_cdp_click_error"] = str(exc)[:240]

        submit_element.click()
        context["submit_click_mode"] = "webdriver_click"

    def _dispatch_submit_button_click(self, submit_button: dict[str, str], context: dict[str, Any]) -> None:
        if not self.driver:
            raise RuntimeError("Browser has not been opened.")
        element = self._wait_for_element(submit_button)
        context["submit_button_class_before_retry_click"] = str(element.get_attribute("class") or "")
        self.driver.execute_script(
            """
            const target = arguments[0];
            if (!target) {
              return;
            }
            target.scrollIntoView({block: 'center', inline: 'nearest'});
            ['mouseenter', 'mouseover', 'mousemove', 'mousedown', 'mouseup', 'click'].forEach((eventType) => {
              target.dispatchEvent(
                new MouseEvent(eventType, {
                  bubbles: true,
                  cancelable: true,
                  view: window,
                  buttons: 1,
                })
              );
            });
            if (typeof target.click === 'function') {
              target.click();
            }
            """,
            element,
        )

    def _click_optional_confirm_button(self, confirm_button: dict[str, str]) -> None:
        if not self._selector_is_configured(confirm_button):
            return
        try:
            self._wait_for_element(confirm_button, clickable=True).click()
            self._pause(0.8)
        except Exception:
            return

    def _retry_submit_via_trace(self, context: dict[str, Any]) -> dict[str, Any]:
        if not self.driver:
            raise RuntimeError("Browser has not been opened.")

        trace = context.get("submit_request_trace", {})
        if not isinstance(trace, dict):
            return {"ok": False, "attempted": False, "reason": "missing_submit_trace"}
        request_url = str(trace.get("url", "")).strip()
        request_method = str(trace.get("method", "POST")).strip().upper() or "POST"
        request_body = str(trace.get("requestBodyPreview", ""))
        if not request_url:
            return {"ok": False, "attempted": False, "reason": "missing_trace_url"}
        if bool(trace.get("requestBodyTruncated", False)):
            return {"ok": False, "attempted": False, "reason": "trace_body_truncated"}

        try:
            response = self.driver.execute_script(
                """
                const url = String(arguments[0] || '').trim();
                const method = String(arguments[1] || 'POST').trim().toUpperCase() || 'POST';
                const body = String(arguments[2] || '');
                if (!url) {
                  return { ok: false, reason: 'missing_url' };
                }
                try {
                  const xhr = new XMLHttpRequest();
                  xhr.open(method, url, false);
                  xhr.withCredentials = true;
                  xhr.setRequestHeader('content-type', 'application/json');
                  xhr.send(method === 'GET' ? null : body);
                  return {
                    ok: true,
                    status: Number(xhr.status || 0),
                    statusText: String(xhr.statusText || ''),
                    responseText: String(xhr.responseText || ''),
                  };
                } catch (error) {
                  return {
                    ok: false,
                    reason: 'xhr_send_error',
                    error: String(error || ''),
                  };
                }
                """,
                request_url,
                request_method,
                request_body,
            )
        except Exception as exc:
            return {
                "ok": False,
                "attempted": True,
                "reason": "execute_script_failed",
                "error": str(exc),
            }

        if not isinstance(response, dict):
            return {"ok": False, "attempted": True, "reason": "invalid_fetch_response"}
        if not bool(response.get("ok")):
            return {
                "ok": False,
                "attempted": True,
                "reason": str(response.get("reason", "fetch_failed")),
                "error": str(response.get("error", "")),
            }

        status = int(response.get("status", 0) or 0)
        response_text = str(response.get("responseText", "") or "")
        parsed_json: dict[str, Any] | None = None
        try:
            loaded = json.loads(response_text) if response_text else None
            if isinstance(loaded, dict):
                parsed_json = loaded
        except Exception:
            parsed_json = None

        if status >= 400:
            raise PublishSubmitError(f"submit request failed with HTTP status {status}.")
        if isinstance(parsed_json, dict) and parsed_json.get("success") is False:
            message = ""
            data = parsed_json.get("data")
            if isinstance(data, dict):
                message = str(data.get("message", "")).strip()
            message = message or str(parsed_json.get("message", "")).strip()
            raise PublishSubmitError(f"submit backend rejected request: {message or parsed_json}")
        if '"success":false' in response_text.lower():
            raise PublishSubmitError(f"submit backend rejected request: {response_text[:240]}")

        return {
            "ok": True,
            "attempted": True,
            "status": status,
            "status_text": str(response.get("statusText", "")),
            "response_text_sample": response_text[:500],
        }

    def _install_offline_submit_trace(self) -> None:
        if not self.driver:
            raise RuntimeError("Browser has not been opened.")
        capture_body_chars = max(
            50000,
            int(self.browser_config.get("offline_submit_trace_capture_body_chars", 300000) or 300000),
        )
        capture_response_chars = max(
            2000,
            int(self.browser_config.get("offline_submit_trace_capture_response_chars", 12000) or 12000),
        )
        self.driver.execute_script(
            """
            const requestBodyCaptureChars = Number(arguments[0] || 300000);
            const responseCaptureChars = Number(arguments[1] || 12000);
            window.__codexOfflineSubmitRecords = [];
            if (window.__codexOfflineSubmitTraceInstalled) {
              return;
            }
            window.__codexOfflineSubmitTraceInstalled = true;

            const normalizeUrl = (rawUrl) => String(rawUrl || '').trim();
            const isSubmitUrl = (rawUrl) => {
              const text = normalizeUrl(rawUrl);
              if (!text) {
                return false;
              }
              try {
                const parsed = new URL(text, window.location.href);
                const hostname = String(parsed.hostname || '').toLowerCase();
                const pathname = String(parsed.pathname || '');
                const is1688Host = hostname === 'offer-new.1688.com' || hostname.endsWith('.1688.com');
                return is1688Host && /\\/popular\\/(draftSubmit|submit)\\.htm$/i.test(pathname);
              } catch (error) {
                return /(^|\\/)(draftSubmit|submit)\\.htm(?:$|[?#])/i.test(text);
              }
            };

            const previewBody = (value) => {
              const maxChars = Number.isFinite(requestBodyCaptureChars) ? requestBodyCaptureChars : 300000;
              const result = {
                text: '',
                truncated: false,
                length: 0,
              };
              if (value == null) {
                return result;
              }
              let raw = '';
              if (typeof value === 'string') {
                raw = value;
              } else if (typeof FormData !== 'undefined' && value instanceof FormData) {
                raw = Array.from(value.entries())
                  .map(([key, bodyValue]) => `${key}=${typeof bodyValue === 'string' ? bodyValue : '[binary]'}`)
                  .join('&');
              } else {
                try {
                  raw = JSON.stringify(value);
                } catch (error) {
                  raw = String(value);
                }
              }
              result.length = raw.length;
              if (raw.length > maxChars) {
                result.text = raw.slice(0, maxChars);
                result.truncated = true;
                return result;
              }
              result.text = raw;
              return result;
            };

            const updateResponse = (record, status, responseText) => {
              record.status = Number(status || 0);
              const limit = Number.isFinite(responseCaptureChars) ? responseCaptureChars : 12000;
              record.responseText = String(responseText || '').slice(0, limit);
              try {
                record.responseJson = JSON.parse(record.responseText);
              } catch (error) {
                record.responseJson = null;
              }
            };

            const originalOpen = XMLHttpRequest.prototype.open;
            const originalSend = XMLHttpRequest.prototype.send;
            XMLHttpRequest.prototype.open = function(method, url) {
              this.__codexOfflineSubmitMeta = { method, url };
              return originalOpen.apply(this, arguments);
            };
            XMLHttpRequest.prototype.send = function(body) {
              const meta = this.__codexOfflineSubmitMeta || {};
              const requestUrl = normalizeUrl(meta.url || '');
              if (!isSubmitUrl(requestUrl)) {
                return originalSend.call(this, body);
              }
              const record = {
                transport: 'xhr',
                method: String(meta.method || ''),
                url: requestUrl,
              };
              const preview = previewBody(body);
              record.requestBodyPreview = preview.text;
              record.requestBodyTruncated = preview.truncated;
              record.requestBodyLength = preview.length;
              this.addEventListener('loadend', function() {
                updateResponse(record, this.status, this.responseText || '');
              });
              window.__codexOfflineSubmitRecords.push(record);
              return originalSend.call(this, body);
            };

            if (typeof window.fetch === 'function') {
              const originalFetch = window.fetch.bind(window);
              window.fetch = function() {
                const args = Array.from(arguments);
                const input = args[0];
                const requestUrl = typeof input === 'string' ? input : String((input && input.url) || '');
                if (!isSubmitUrl(requestUrl)) {
                  return originalFetch.apply(window, args);
                }
                const init = args[1] || {};
                const record = {
                  transport: 'fetch',
                  method: String(init.method || 'GET'),
                  url: normalizeUrl(requestUrl),
                };
                const preview = previewBody(init.body);
                record.requestBodyPreview = preview.text;
                record.requestBodyTruncated = preview.truncated;
                record.requestBodyLength = preview.length;
                window.__codexOfflineSubmitRecords.push(record);
                return originalFetch.apply(window, args).then(async (response) => {
                  try {
                    const clone = response.clone();
                    updateResponse(record, clone.status, await clone.text());
                  } catch (error) {
                    updateResponse(record, 0, String(error || ''));
                  }
                  return response;
                });
              };
            }
            """,
            capture_body_chars,
            capture_response_chars,
        )

    def _assert_offline_submit_trace(self, context: dict[str, Any]) -> bool:
        if not self.driver:
            raise RuntimeError("Browser has not been opened.")
        timeout_seconds = max(0.0, float(self.browser_config.get("submit_trace_timeout_seconds", 5) or 5))
        deadline = time.time() + timeout_seconds
        latest_record: dict[str, Any] | None = None
        while time.time() <= deadline:
            try:
                records = self.driver.execute_script(
                    "return (window.__codexOfflineSubmitRecords || []).map((item) => ({...item}));"
                )
            except Exception as exc:
                context["submit_request_trace_error"] = str(exc)
                return False
            if isinstance(records, list) and records:
                latest_record = dict(records[-1] or {})
                has_response = bool(str(latest_record.get("responseText", "")).strip())
                response_json = latest_record.get("responseJson")
                if has_response or response_json is not None:
                    break
            time.sleep(0.2)

        if latest_record is None:
            context["submit_request_trace_present"] = False
            return False

        context["submit_request_trace_present"] = True
        context["submit_request_trace"] = latest_record

        raw_status = latest_record.get("status", 0)
        try:
            status = int(raw_status or 0)
        except (TypeError, ValueError):
            status = 0
        response_json = latest_record.get("responseJson")
        response_text = str(latest_record.get("responseText", "")).strip()
        has_response_payload = bool(response_text) or response_json is not None or status > 0
        if not has_response_payload:
            context["submit_request_trace_complete"] = False
            return False
        context["submit_request_trace_complete"] = True

        if status >= 400:
            raise PublishSubmitError(f"submit request failed with HTTP status {status}.")

        if isinstance(response_json, dict):
            backend_code = 0
            try:
                backend_code = int(response_json.get("code", 0) or 0)
            except (TypeError, ValueError):
                backend_code = 0
            if backend_code >= 400:
                backend_message = self._extract_submit_backend_error_message(response_json)
                context["submit_backend_error_code"] = str(backend_code)
                context["submit_backend_error_message"] = backend_message
                invalid_props = self._extract_submit_backend_invalid_props(response_json)
                if invalid_props:
                    context["submit_backend_invalid_props"] = invalid_props
                self._annotate_page_error_context(
                    context,
                    stage_name="post_offline_submit",
                    error_text=backend_message,
                    error_category="business_validation",
                )
                raise PublishValidationError(f"submit backend validation failed: {backend_message}")
            if response_json.get("success") is False:
                message = ""
                data = response_json.get("data")
                if isinstance(data, dict):
                    message = str(data.get("message", "")).strip()
                message = message or str(response_json.get("message", "")).strip()
                raise PublishSubmitError(f"submit backend rejected request: {message or response_json}")

        if '"success":false' in response_text.lower():
            raise PublishSubmitError(f"submit backend rejected request: {response_text[:240]}")
        return True

    def _extract_submit_backend_invalid_props(self, response_json: dict[str, Any]) -> list[str]:
        if not isinstance(response_json, dict):
            return []
        data = response_json.get("data")
        if not isinstance(data, dict):
            return []
        nested = data.get("data")
        if not isinstance(nested, dict):
            return []
        models = nested.get("models")
        if not isinstance(models, dict):
            return []
        form_error = models.get("formError")
        if not isinstance(form_error, dict):
            return []
        cat_prop = form_error.get("catProp")
        if not isinstance(cat_prop, dict):
            return []
        item_message = cat_prop.get("itemMessage")
        if not isinstance(item_message, dict):
            return []
        keys: list[str] = []
        for key in item_message.keys():
            normalized = str(key or "").strip()
            if normalized and normalized not in keys:
                keys.append(normalized)
        return keys

    def _extract_submit_backend_error_message(self, response_json: dict[str, Any]) -> str:
        if not isinstance(response_json, dict):
            return ""
        parts: list[str] = []
        top_message = str(response_json.get("message", "")).strip()
        if top_message:
            parts.append(top_message)

        data = response_json.get("data")
        if isinstance(data, dict):
            nested = data.get("data")
            if isinstance(nested, dict):
                models = nested.get("models")
                if isinstance(models, dict):
                    form_error = models.get("formError")
                    if isinstance(form_error, dict):
                        cat_prop = form_error.get("catProp")
                        if isinstance(cat_prop, dict):
                            item_message = cat_prop.get("itemMessage")
                            if isinstance(item_message, dict):
                                for prop_name, payload in item_message.items():
                                    messages: list[str] = []
                                    if isinstance(payload, dict):
                                        raw_messages = payload.get("message")
                                        if isinstance(raw_messages, list):
                                            for item in raw_messages:
                                                if isinstance(item, dict):
                                                    msg = str(item.get("msg", "")).strip()
                                                    if msg:
                                                        messages.append(msg)
                                    if messages:
                                        parts.append(f"{str(prop_name).strip()}: {'; '.join(messages)}")

        if parts:
            return " | ".join(parts)
        try:
            return json.dumps(response_json, ensure_ascii=False)[:600]
        except Exception:
            return str(response_json)

    def _prepare_pre_submit_backfill(
        self,
        backfill_config: dict[str, Any],
        context: dict[str, Any],
    ) -> None:
        if not backfill_config or not backfill_config.get("enabled", False):
            context["pre_submit_backfill_status"] = "disabled"
            return

        mode = str(backfill_config.get("mode", "full")).strip().lower() or "full"
        if mode == "delivery_service_only":
            delivery_service_action = self._ensure_required_delivery_service(context)
            context["pre_submit_backfill_actions"] = (
                [delivery_service_action]
                if delivery_service_action is not None
                else []
            )
            if delivery_service_action and delivery_service_action.get("status") == "apply_failed":
                error_text = "提交前必填属性缺失: 配送服务"
                self._annotate_page_error_context(
                    context,
                    stage_name="pre_submit_backfill",
                    error_text=error_text,
                    error_category="delivery_service_backfill_failed",
                )
                context["pre_submit_backfill_status"] = "blocked"
                raise PublishValidationError(error_text)
            context["pre_submit_backfill_status"] = "ready"
            return

        self._resolve_current_page_category_context(context)
        self._apply_pre_submit_state_patch(backfill_config, context)
        enum_sanitize_actions = self._sanitize_invalid_enum_category_props(context)
        context["pre_submit_enum_sanitization"] = enum_sanitize_actions
        rules = self._resolve_pre_submit_backfill_rules(backfill_config, context)
        field_rules = self._resolve_pre_submit_field_rules(backfill_config)
        shipment_table_rules = self._resolve_pre_submit_shipment_table_rules(backfill_config)
        delivery_service_action = self._ensure_required_delivery_service(context)
        if delivery_service_action and delivery_service_action.get("status") == "apply_failed":
            error_text = "提交前必填属性缺失: 配送服务"
            self._annotate_page_error_context(
                context,
                stage_name="pre_submit_backfill",
                error_text=error_text,
                error_category="delivery_service_backfill_failed",
            )
            context["pre_submit_backfill_status"] = "blocked"
            context["pre_submit_backfill_actions"] = [delivery_service_action]
            raise PublishValidationError(error_text)
        if not rules and not field_rules and not shipment_table_rules:
            context["pre_submit_backfill_actions"] = (
                [delivery_service_action] if delivery_service_action is not None else []
            )
            context["pre_submit_backfill_status"] = (
                "ready"
                if delivery_service_action and delivery_service_action.get("status") != "skipped"
                else "no_rules"
            )
            return

        before_scan = self._scan_pre_submit_backfill_rules(rules) if rules else []
        field_before_scan = self._scan_pre_submit_field_rules(field_rules, context) if field_rules else []
        shipment_before_scan = (
            self._scan_pre_submit_shipment_table_rules(shipment_table_rules, context)
            if shipment_table_rules
            else []
        )
        context["pre_submit_backfill_before"] = before_scan
        context["pre_submit_field_backfill_before"] = field_before_scan
        context["pre_submit_shipment_table_before"] = shipment_before_scan

        applied_actions: list[dict[str, Any]] = (
            [delivery_service_action] if delivery_service_action is not None else []
        )
        for rule, state in zip(rules, before_scan):
            if str(state.get("current_value", "")).strip():
                continue

            rule_value = self._resolve_profile_rule_value(rule, context)
            select_first_if_empty = bool(rule.get("select_first_if_empty", False))
            if not rule_value and not select_first_if_empty:
                continue

            try:
                self._run_with_stale_retry(
                    lambda current_rule=rule: self._apply_category_prop_rule(current_rule, context),
                    retries=int(rule.get("stale_retry_count", 2)),
                    wait_seconds=float(rule.get("stale_retry_wait_seconds", 0.5)),
                )
            except Exception as exc:
                applied_actions.append(
                    {
                        "label": str(rule.get("label", "")).strip(),
                        "status": "failed",
                        "error": str(exc),
                    }
                )
                continue

            applied_actions.append(
                {
                    "label": str(rule.get("label", "")).strip(),
                    "status": "applied",
                    "value": rule_value,
                }
            )

        # Some category props render lazily after initial interaction; retry missing ones once.
        retry_actions = self._retry_missing_category_prop_rules(rules, context)
        applied_actions.extend(retry_actions)

        shipment_actions = self._apply_pre_submit_shipment_table_rules(shipment_table_rules, context)
        field_actions = self._apply_pre_submit_field_rules(field_rules, context)

        after_scan = self._scan_pre_submit_backfill_rules(rules) if rules else []
        field_after_scan = self._scan_pre_submit_field_rules(field_rules, context) if field_rules else []
        shipment_after_scan = (
            self._scan_pre_submit_shipment_table_rules(shipment_table_rules, context)
            if shipment_table_rules
            else []
        )
        context["pre_submit_backfill_after"] = after_scan
        context["pre_submit_field_backfill_after"] = field_after_scan
        context["pre_submit_shipment_table_after"] = shipment_after_scan
        context["pre_submit_backfill_actions"] = applied_actions + shipment_actions + field_actions + enum_sanitize_actions

        missing_labels = [
            str(item.get("label", "")).strip()
            for item in after_scan
            if bool(item.get("exists")) and not str(item.get("current_value", "")).strip()
        ]
        missing_labels.extend(
            str(item.get("label", "")).strip()
            for item, rule in zip(field_after_scan, field_rules)
            if bool(rule.get("required", False))
            and bool(item.get("exists"))
            and not str(item.get("current_value", "")).strip()
        )
        missing_labels.extend(
            str(item.get("label", "")).strip()
            for item, rule in zip(shipment_after_scan, shipment_table_rules)
            if bool(rule.get("required", False))
            and bool(item.get("exists"))
            and not str(item.get("current_value", "")).strip()
        )
        context["pre_submit_missing_attributes"] = missing_labels
        context["pre_submit_backfill_status"] = "ready" if not missing_labels else "blocked"

        if missing_labels and bool(backfill_config.get("block_on_missing", True)):
            error_text = f"提交前必填属性缺失: {', '.join(missing_labels)}"
            self._annotate_page_error_context(
                context,
                stage_name="pre_submit_backfill",
                error_text=error_text,
                error_category="business_validation",
            )
            raise PublishValidationError(error_text)

    def _sanitize_invalid_enum_category_props(self, context: dict[str, Any]) -> list[dict[str, Any]]:
        if not self.driver:
            return []
        result = self.driver.execute_script(
            """
            const sdk = window.SellPublishSdk;
            const engine = sdk && sdk.engine;
            const core = engine && engine._engine && engine._engine._core;
            if (!engine || !engine.getJsonState || !core || typeof core.changeElementValue !== 'function') {
              return { ok: false, reason: 'runtime_unavailable', changed: [] };
            }
            const state = engine.getJsonState() || {};
            const components = state.components || {};
            const catPropComponent = components.catProp || {};
            const catPropProps = catPropComponent.props || {};
            const dataSource = Array.isArray(catPropProps.dataSource) ? catPropProps.dataSource : [];
            const valueMap = JSON.parse(JSON.stringify(catPropProps.value || {}));
            const changed = [];
            const hasOwn = (obj, key) => Object.prototype.hasOwnProperty.call(obj || {}, key);
            const isCustomNode = (item) => Boolean(item && typeof item === 'object' && item.custom === true);

            for (const prop of dataSource) {
              const key = String((prop && prop.name) || '').trim();
              if (!key || !hasOwn(valueMap, key)) {
                continue;
              }
              if (!Boolean(prop && prop.enumProp)) {
                continue;
              }
              if (prop && prop.customizable === true) {
                continue;
              }
              const options = Array.isArray(prop && prop.dataSource)
                ? prop.dataSource.filter((item) => item && Object.prototype.hasOwnProperty.call(item, 'value'))
                : [];
              if (!options.length) {
                continue;
              }
              const current = valueMap[key];
              const hasCustom = Array.isArray(current) ? current.some(isCustomNode) : isCustomNode(current);
              if (!hasCustom) {
                continue;
              }
              const first = options[0];
              const nextText = String((first && first.text) || (first && first.value) || '').trim();
              if (!nextText) {
                continue;
              }
              const uiType = String((prop && prop.uiType) || '').toLowerCase();
              let nextValue;
              if (Array.isArray(current) || uiType.includes('checkbox') || uiType.includes('multi')) {
                nextValue = [{ value: first.value, text: nextText }];
              } else if (current && typeof current === 'object') {
                nextValue = { value: first.value, text: nextText };
              } else {
                nextValue = first.value;
              }
              valueMap[key] = nextValue;
              changed.push({
                name: key,
                label: String((prop && prop.label) || ''),
                next_text: nextText,
              });
            }

            if (!changed.length) {
              return { ok: true, changed: [] };
            }
            core.changeElementValue('catProp', valueMap, { isDepth: false });
            return { ok: true, changed };
            """
        )
        if not isinstance(result, dict):
            return []
        changed = result.get("changed", [])
        if not isinstance(changed, list):
            return []

        normalized: list[dict[str, Any]] = []
        for item in changed:
            if not isinstance(item, dict):
                continue
            normalized.append(
                {
                    "label": str(item.get("label", "")).strip(),
                    "name": str(item.get("name", "")).strip(),
                    "status": "applied",
                    "value": str(item.get("next_text", "")).strip(),
                }
            )
        return normalized

    def _retry_missing_category_prop_rules(
        self,
        rules: list[dict[str, Any]],
        context: dict[str, Any],
    ) -> list[dict[str, Any]]:
        if not rules:
            return []
        actions: list[dict[str, Any]] = []
        current_states = self._scan_pre_submit_backfill_rules(rules)
        for rule, state in zip(rules, current_states):
            rule_label = str(rule.get("label", "")).strip()
            if not bool(state.get("exists")):
                continue
            if str(state.get("current_value", "")).strip():
                continue
            rule_value = self._resolve_profile_rule_value(rule, context)
            select_first_if_empty = bool(rule.get("select_first_if_empty", False))
            if not rule_value and not select_first_if_empty:
                continue
            retry_rounds = max(1, int(rule.get("missing_retry_rounds", 2)))
            last_error: Exception | None = None
            recovered = False

            for attempt in range(retry_rounds):
                current_rule = dict(rule)
                if attempt > 0:
                    # Fallback path for stubborn combobox fields:
                    # prefer selecting the first available option.
                    current_rule["value"] = ""
                    current_rule["select_first_if_empty"] = True
                    current_rule["select_first_option_if_unmatched"] = True

                try:
                    self._run_with_stale_retry(
                        lambda active_rule=current_rule: self._apply_category_prop_rule(active_rule, context),
                        retries=int(current_rule.get("stale_retry_count", 2)),
                        wait_seconds=float(current_rule.get("stale_retry_wait_seconds", 0.5)),
                    )
                except Exception as exc:
                    last_error = exc
                verified_state = self._read_category_prop_state(rule_label)
                if str(verified_state.get("current_value", "")).strip():
                    recovered = True
                    actions.append(
                        {
                            "label": rule_label,
                            "status": "retry_applied",
                            "value": str(verified_state.get("current_value", "")).strip(),
                            "attempt": attempt + 1,
                        }
                    )
                    break

            if not recovered:
                actions.append(
                    {
                        "label": rule_label,
                        "status": "retry_failed",
                        "error": str(last_error) if last_error else "value_remains_empty_after_retry",
                    }
                )
        return actions

    def _ensure_required_delivery_service(self, context: dict[str, Any]) -> dict[str, Any] | None:
        if not self.driver:
            return {"label": "配送服务", "status": "skipped", "reason": "no_driver"}
        state_before = self._wait_for_delivery_service_state()
        context["delivery_service_state_before"] = state_before

        if not bool(state_before.get("exists")):
            return {"label": "配送服务", "status": "skipped", "reason": "not_found"}
        if bool(state_before.get("selected")):
            return {
                "label": "配送服务",
                "status": "already_selected",
                "value": "|".join(state_before.get("selected_options", [])),
            }

        enabled_auto_switch = self._enable_delivery_service_auto_switch()
        self._pause(0.5)
        state_after_switch = self._read_delivery_service_state()
        if bool(state_after_switch.get("selected")):
            context["delivery_service_state_after"] = state_after_switch
            return {
                "label": "配送服务",
                "status": "applied",
                "value": "|".join(state_after_switch.get("selected_options", [])),
            }

        clicked = self._select_first_delivery_service_option()
        self._pause(0.5)
        state_after = self._read_delivery_service_state()
        context["delivery_service_state_after"] = state_after
        if bool(state_after.get("selected")):
            return {
                "label": "配送服务",
                "status": "applied",
                "value": "|".join(state_after.get("selected_options", [])),
            }

        return {
            "label": "配送服务",
            "status": "apply_failed",
            "error": (
                "delivery_service_still_empty"
                if (clicked or enabled_auto_switch)
                else "delivery_service_not_clickable"
            ),
        }

    def _wait_for_delivery_service_state(self, timeout_seconds: float = 4.0) -> dict[str, Any]:
        deadline = time.time() + max(0.5, timeout_seconds)
        state = self._read_delivery_service_state()
        while not bool(state.get("exists")) and time.time() < deadline:
            self._scroll_delivery_service_into_view()
            self._pause(0.5)
            state = self._read_delivery_service_state()
        return state

    def _scroll_delivery_service_into_view(self) -> None:
        if not self.driver:
            return
        try:
            self.driver.execute_script(
                """
                const nodes = Array.from(document.querySelectorAll(
                  '#guid-customExtraService, [id*="customExtraService"], .special-service-wrapper'
                ));
                const target = nodes.find((node) =>
                  String(node.innerText || node.textContent || '').includes('配送服务')
                );
                if (target) {
                  target.scrollIntoView({block: 'center', inline: 'nearest'});
                }
                """
            )
        except Exception:
            return

    def _read_delivery_service_state(self) -> dict[str, Any]:
        if not self.driver:
            raise RuntimeError("Browser has not been opened.")
        try:
            state = self.driver.execute_script(
                """
                const root = document.querySelector('#guid-customExtraService, [id*="customExtraService"]') || document;
                function norm(value) {
                  return String(value || '').replace(/\\s+/g, ' ').trim();
                }
                function isVisible(node) {
                  if (!node) return false;
                  const style = window.getComputedStyle(node);
                  const rect = node.getBoundingClientRect();
                  return style.display !== 'none' && style.visibility !== 'hidden' && rect.width > 0 && rect.height > 0;
                }

                const wrappers = Array.from(root.querySelectorAll('.special-service-wrapper'));
                const target = wrappers.find((wrapper) => {
                  const titleNode = wrapper.querySelector('.service-item-title-value');
                  const title = norm(titleNode ? titleNode.innerText || titleNode.textContent || '' : '');
                  return title.includes('配送服务') || norm(wrapper.innerText || wrapper.textContent || '').includes('配送服务');
                });
                if (!target) {
                  return { exists: false };
                }

                const labels = Array.from(target.querySelectorAll('label.ant-checkbox-wrapper')).filter(isVisible);
                const autoSwitch = target.querySelector('button.default-config-switch');
                const autoSwitchChecked = autoSwitch ? String(autoSwitch.getAttribute('aria-checked') || '') === 'true' : false;
                const selectedOptions = [];
                for (const label of labels) {
                    const input = label.querySelector('input.ant-checkbox-input[type=\"checkbox\"]');
                  if (!input) {
                    continue;
                  }
                  const text = norm(label.innerText || label.textContent || '');
                  if (input.checked || label.classList.contains('ant-checkbox-wrapper-checked') || Boolean(label.querySelector('.ant-checkbox-checked'))) {
                    selectedOptions.push(text);
                  }
                }
                const messageNode = target.querySelector('.modern-message .message-span');
                const messageText = norm(messageNode ? messageNode.innerText || messageNode.textContent || '' : '');
                return {
                  exists: true,
                  selected: selectedOptions.length > 0,
                  selected_options: selectedOptions,
                  option_count: labels.length,
                  auto_switch_checked: autoSwitchChecked,
                  message_text: messageText,
                };
                """
            )
        except Exception:
            return {"exists": False}
        if not isinstance(state, dict):
            return {"exists": False}
        selected_options = state.get("selected_options", [])
        if not isinstance(selected_options, list):
            selected_options = []
        return {
            "exists": bool(state.get("exists", False)),
            "selected": bool(state.get("selected", False)),
            "selected_options": [str(item).strip() for item in selected_options if str(item).strip()],
            "option_count": int(state.get("option_count", 0) or 0),
            "auto_switch_checked": bool(state.get("auto_switch_checked", False)),
            "message_text": str(state.get("message_text", "")).strip(),
        }

    def _enable_delivery_service_auto_switch(self) -> bool:
        if not self.driver:
            raise RuntimeError("Browser has not been opened.")
        try:
            clicked = self.driver.execute_script(
                """
                const root = document.querySelector('#guid-customExtraService, [id*="customExtraService"]') || document;
                function norm(value) {
                  return String(value || '').replace(/\\s+/g, ' ').trim();
                }
                function isVisible(node) {
                  if (!node) return false;
                  const style = window.getComputedStyle(node);
                  const rect = node.getBoundingClientRect();
                  return style.display !== 'none' && style.visibility !== 'hidden' && rect.width > 0 && rect.height > 0;
                }
                const wrappers = Array.from(root.querySelectorAll('.special-service-wrapper'));
                const target = wrappers.find((wrapper) => {
                  const titleNode = wrapper.querySelector('.service-item-title-value');
                  const title = norm(titleNode ? titleNode.innerText || titleNode.textContent || '' : '');
                  return title.includes('配送服务') || norm(wrapper.innerText || wrapper.textContent || '').includes('配送服务');
                });
                if (!target) {
                  return false;
                }
                const autoSwitch = target.querySelector('button.default-config-switch, button[role="switch"], [role="switch"]');
                if (!autoSwitch || !isVisible(autoSwitch)) {
                  return false;
                }
                const checked = String(autoSwitch.getAttribute('aria-checked') || '') === 'true';
                if (checked) {
                  return true;
                }
                autoSwitch.scrollIntoView({block: 'center', inline: 'nearest'});
                autoSwitch.click();
                return true;
                """
            )
        except Exception:
            return False
        return bool(clicked)

    def _select_first_delivery_service_option(self) -> bool:
        if not self.driver:
            raise RuntimeError("Browser has not been opened.")
        try:
            clicked = self.driver.execute_script(
                """
                const root = document.querySelector('#guid-customExtraService, [id*="customExtraService"]') || document;
                function norm(value) {
                  return String(value || '').replace(/\\s+/g, ' ').trim();
                }
                function isVisible(node) {
                  if (!node) return false;
                  const style = window.getComputedStyle(node);
                  const rect = node.getBoundingClientRect();
                  return style.display !== 'none' && style.visibility !== 'hidden' && rect.width > 0 && rect.height > 0;
                }
                const wrappers = Array.from(root.querySelectorAll('.special-service-wrapper'));
                const target = wrappers.find((wrapper) => {
                  const titleNode = wrapper.querySelector('.service-item-title-value');
                  const title = norm(titleNode ? titleNode.innerText || titleNode.textContent || '' : '');
                  return title.includes('配送服务') || norm(wrapper.innerText || wrapper.textContent || '').includes('配送服务');
                });
                if (!target) {
                  return false;
                }
                const labels = Array.from(target.querySelectorAll('label.ant-checkbox-wrapper')).filter(isVisible);
                const label = labels[0];
                if (!label) {
                  return false;
                }
                const input = label.querySelector('input.ant-checkbox-input[type=\"checkbox\"]');
                if (!input || input.disabled) {
                  return false;
                }
                if (input.checked) {
                  return true;
                }
                label.scrollIntoView({block: 'center', inline: 'nearest'});
                label.click();
                if (!input.checked) {
                  input.click();
                }
                input.dispatchEvent(new Event('change', {bubbles: true}));
                return true;
                """
            )
        except Exception:
            return False
        return bool(clicked)

    def _resolve_pre_submit_backfill_rules(
        self,
        backfill_config: dict[str, Any],
        context: dict[str, Any],
    ) -> list[dict[str, Any]]:
        profiles = backfill_config.get("profiles", {})
        category_key = str(context.get("platform_category", "")).strip()
        if isinstance(profiles, dict):
            matched_profile_name = ""
            profile = {}
            if category_key and isinstance(profiles.get(category_key), dict):
                matched_profile_name = category_key
                profile = dict(profiles.get(category_key, {}))
            elif isinstance(profiles.get("*"), dict):
                matched_profile_name = "*"
                profile = dict(profiles.get("*", {}))

            rules = [dict(item) for item in profile.get("rules", []) if isinstance(item, dict)]
            if rules:
                context["pre_submit_backfill_rule_source"] = f"profile:{matched_profile_name}"
                return rules

        rules = [dict(item) for item in backfill_config.get("rules", []) if isinstance(item, dict)]
        if rules:
            context["pre_submit_backfill_rule_source"] = "rules"
        return rules

    def _resolve_pre_submit_field_rules(
        self,
        backfill_config: dict[str, Any],
    ) -> list[dict[str, Any]]:
        return [dict(item) for item in backfill_config.get("field_rules", []) if isinstance(item, dict)]

    def _resolve_pre_submit_shipment_table_rules(
        self,
        backfill_config: dict[str, Any],
    ) -> list[dict[str, Any]]:
        return [dict(item) for item in backfill_config.get("shipment_table_rules", []) if isinstance(item, dict)]

    def _apply_pre_submit_state_patch(
        self,
        backfill_config: dict[str, Any],
        context: dict[str, Any],
    ) -> None:
        patch_config = backfill_config.get("draft_page_state_patch", {})
        if not isinstance(patch_config, dict) or not patch_config.get("enabled", False):
            context["pre_submit_state_patch_status"] = "disabled"
            return

        self._apply_draft_page_state_patch({"draft_page_state_patch": patch_config}, context)
        patch_result = context.get("draft_page_state_patch", {})
        if not isinstance(patch_result, dict) or not patch_result:
            context["pre_submit_state_patch_status"] = "empty"
            return
        context["pre_submit_state_patch_status"] = "applied" if patch_result.get("ok", True) else "failed"

    def _resolve_current_page_category_context(self, context: dict[str, Any]) -> None:
        category_path = str(context.get("resolved_category_name", "")).strip()
        if not category_path:
            category_path = self._read_current_page_category_path()
        if not category_path:
            return

        context["resolved_category_name"] = category_path
        resolved_levels = self._split_category_path(category_path)
        if resolved_levels:
            context["resolved_category_levels"] = resolved_levels
            context["resolved_category_level_count"] = len(resolved_levels)
            for index, level in enumerate(resolved_levels, start=1):
                context[f"resolved_category_level_{index}"] = level

        platform_category = str(context.get("platform_category", "")).strip()
        if not platform_category:
            platform_category = infer_platform_category_key(
                category_path,
                str(context.get("title", "")).strip(),
            )
        if platform_category:
            context["platform_category"] = platform_category

    def _read_current_page_category_path(self) -> str:
        if not self.driver:
            raise RuntimeError("Browser has not been opened.")
        try:
            values = self.driver.execute_script(
                """
                const root = document.querySelector('#guid-catNamer .current-namer');
                if (!root) {
                  return [];
                }
                const spans = Array.from(root.querySelectorAll(':scope > span'))
                  .map((node) => String(node.innerText || node.textContent || '').trim())
                  .filter(Boolean);
                if (spans.length > 0) {
                  return spans;
                }
                const text = String(root.innerText || root.textContent || '').replace('您选择的类目：', '').trim();
                return text ? [text] : [];
                """
            )
        except Exception:
            return ""
        if not isinstance(values, list):
            return ""
        levels = [str(item).strip() for item in values if str(item).strip()]
        if not levels:
            return ""
        return " > ".join(levels)

    def _scan_pre_submit_backfill_rules(self, rules: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [self._read_category_prop_state(str(rule.get("label", "")).strip()) for rule in rules]

    def _scan_pre_submit_field_rules(
        self,
        rules: list[dict[str, Any]],
        context: dict[str, Any],
    ) -> list[dict[str, Any]]:
        return [self._read_pre_submit_field_state(rule, context) for rule in rules]

    def _scan_pre_submit_shipment_table_rules(
        self,
        rules: list[dict[str, Any]],
        context: dict[str, Any],
    ) -> list[dict[str, Any]]:
        return [self._read_shipment_table_rule_state(rule, context) for rule in rules]

    def _read_pre_submit_field_state(
        self,
        rule: dict[str, Any],
        context: dict[str, Any],
    ) -> dict[str, Any]:
        label = str(rule.get("label") or rule.get("name") or rule.get("source") or "").strip()
        selector = self._resolve_selector(rule.get("selector", {}), context)
        if not self._selector_is_configured(selector):
            return {"label": label, "exists": False, "current_value": ""}
        element = self._find_first_visible_element(selector)
        if not element:
            return {"label": label, "exists": False, "current_value": ""}
        raw_value = self._read_pre_submit_field_value(element)
        current_value = "" if self._is_placeholder_form_value(raw_value) else raw_value
        return {
            "label": label,
            "exists": True,
            "current_value": current_value,
            "raw_value": raw_value,
        }

    def _find_first_visible_element(self, selector: dict[str, str]) -> Any | None:
        if not self.driver:
            raise RuntimeError("Browser has not been opened.")
        try:
            elements = self.driver.find_elements(
                BY_MAPPING.get(str(selector.get("by", "css")).strip().lower(), By.CSS_SELECTOR),
                str(selector.get("value", "")).strip(),
            )
        except Exception:
            return None
        for element in elements:
            try:
                if element.is_displayed():
                    return element
            except Exception:
                continue
        return None

    def _read_pre_submit_field_value(self, element: Any) -> str:
        if not self.driver:
            raise RuntimeError("Browser has not been opened.")
        try:
            value = self.driver.execute_script(
                """
                const root = arguments[0];
                function norm(value) {
                  return String(value || '').replace(/\\s+/g, ' ').trim();
                }
                const directValue = norm(root.value || root.getAttribute('value') || '');
                if (directValue) {
                  return directValue;
                }
                const inputs = Array.from(root.querySelectorAll('input, textarea')).filter((node) => {
                  const style = window.getComputedStyle(node);
                  const rect = node.getBoundingClientRect();
                  return style.display !== 'none' && style.visibility !== 'hidden' && rect.width > 0 && rect.height > 0;
                });
                for (const input of inputs) {
                  const current = norm(input.value || input.getAttribute('value') || '');
                  if (current) {
                    return current;
                  }
                }
                return norm(root.innerText || root.textContent || '');
                """,
                element,
            )
        except Exception:
            value = ""
        return str(value or "").strip()

    def _is_placeholder_form_value(self, value: str) -> bool:
        normalized = str(value or "").strip()
        if not normalized:
            return True
        placeholder_tokens = {
            "请选择",
            "请选择/输入",
            "请选择规格",
            "请选择属性",
            "请选择后输入",
            "请选择或输入",
            "请选择后可输入",
            "请填写",
            "请输入",
        }
        if normalized in placeholder_tokens:
            return True
        return normalized.startswith("请选择")

    def _apply_pre_submit_field_rules(
        self,
        rules: list[dict[str, Any]],
        context: dict[str, Any],
    ) -> list[dict[str, Any]]:
        actions: list[dict[str, Any]] = []
        for rule in rules:
            rule_label = str(rule.get("label") or rule.get("name") or rule.get("source") or "").strip()
            current_state = self._read_pre_submit_field_state(rule, context)
            current_value = str(current_state.get("current_value", "")).strip()
            desired_value = self._resolve_profile_rule_value(rule, context)
            overwrite_if_mismatch = bool(rule.get("overwrite_if_mismatch", False))
            allow_keep_existing = bool(rule.get("allow_keep_existing", True))
            select_first_if_empty = bool(rule.get("select_first_if_empty", False))

            if current_value and allow_keep_existing and not overwrite_if_mismatch:
                continue
            if desired_value and self._shipment_value_matches(current_value, desired_value):
                continue
            if not desired_value and not select_first_if_empty:
                continue

            try:
                self._apply_pre_submit_field_rule(rule, context, desired_value)
            except Exception as exc:
                actions.append(
                    {
                        "label": rule_label,
                        "status": "failed",
                        "error": str(exc),
                    }
                )
                continue

            actions.append(
                {
                    "label": rule_label,
                    "status": "applied",
                    "value": desired_value,
                }
            )
        return actions

    def _apply_pre_submit_shipment_table_rules(
        self,
        rules: list[dict[str, Any]],
        context: dict[str, Any],
    ) -> list[dict[str, Any]]:
        actions: list[dict[str, Any]] = []
        for rule in rules:
            rule_label = str(rule.get("label") or rule.get("name") or "").strip()
            current_state = self._read_shipment_table_rule_state(rule, context)
            current_value = str(current_state.get("current_value", "")).strip()
            desired_value = self._resolve_profile_rule_value(rule, context)
            overwrite_if_mismatch = bool(rule.get("overwrite_if_mismatch", False))
            allow_keep_existing = bool(rule.get("allow_keep_existing", True))
            select_first_if_empty = bool(rule.get("select_first_if_empty", False))

            if current_value and allow_keep_existing and not overwrite_if_mismatch:
                continue
            if desired_value and current_value == desired_value:
                continue
            if not desired_value and not select_first_if_empty:
                continue

            try:
                self._apply_shipment_table_rule(rule, context, desired_value)
            except Exception as exc:
                actions.append(
                    {
                        "label": rule_label,
                        "status": "failed",
                        "error": str(exc),
                    }
                )
                continue

            actions.append(
                {
                    "label": rule_label,
                    "status": "applied",
                    "value": desired_value,
                }
            )
        return actions

    def _apply_pre_submit_field_rule(
        self,
        rule: dict[str, Any],
        context: dict[str, Any],
        desired_value: str,
    ) -> None:
        selector = self._resolve_selector(rule.get("selector", {}), context)
        if not self._selector_is_configured(selector):
            return
        action_type = str(rule.get("type", "input")).strip().lower()
        step = dict(rule)
        step["name"] = str(rule.get("name") or rule.get("label") or rule.get("source") or "pre_submit_field").strip()

        if action_type == "ant_select":
            self._select_ant_option(selector, step, desired_value)
            return
        if action_type == "combobox":
            self._fill_combobox(selector, step, desired_value)
            return

        element = self._wait_for_element(selector, clickable=True)
        self._fill_text_field(element, desired_value, clear=True)

    def _read_shipment_table_rule_state(
        self,
        rule: dict[str, Any],
        context: dict[str, Any],
    ) -> dict[str, Any]:
        label = str(rule.get("label") or rule.get("name") or "").strip()
        element = self._resolve_shipment_table_trigger(rule, context, wait_for_element=False)
        if not element:
            return {"label": label, "exists": False, "current_value": ""}
        raw_value = self._read_pre_submit_field_value(element)
        current_value = "" if self._is_placeholder_form_value(raw_value) else raw_value
        return {
            "label": label,
            "exists": True,
            "current_value": current_value,
            "raw_value": raw_value,
        }

    def _apply_shipment_table_rule(
        self,
        rule: dict[str, Any],
        context: dict[str, Any],
        desired_value: str,
    ) -> None:
        trigger = self._resolve_shipment_table_trigger(rule, context, wait_for_element=True)
        if not trigger:
            raise ValueError(f"Failed to locate shipment table trigger for '{rule.get('label', '')}'.")

        step = dict(rule)
        step["name"] = str(rule.get("name") or rule.get("label") or "shipment_table").strip()
        if not self.driver:
            raise RuntimeError("Browser has not been opened.")
        self.driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", trigger)
        try:
            trigger.click()
        except Exception:
            self.driver.execute_script("arguments[0].click();", trigger)
        self._pause(float(step.get("open_wait_seconds", 0.4)))

        state_fallback: dict[str, Any] = {}
        matched = self._click_visible_dropdown_option_native(str(desired_value).strip())
        if not matched and step.get("select_first_option_if_unmatched", False):
            matched = self._click_first_visible_dropdown_option_native()
        if not matched:
            state_fallback = self._apply_shipment_table_rule_by_state(str(desired_value).strip())
            matched = bool(state_fallback.get("ok"))
        if not matched and step.get("required", False):
            available = state_fallback.get("available_service_names", [])
            available_hint = f" available={available}" if isinstance(available, list) and available else ""
            raise ValueError(
                f"Failed to match shipment table option '{desired_value}' for step '{step.get('name')}'.{available_hint}"
            )
        self._pause(float(step.get("select_wait_seconds", 0.5)))

        expected_value = str(desired_value).strip()
        if expected_value and not self._wait_for_shipment_table_rule_value(rule, context, expected_value):
            fallback_name = str(state_fallback.get("matched_service_name", "")).strip()
            if fallback_name and self._shipment_value_matches(fallback_name, expected_value):
                return
            current_state = self._read_shipment_table_rule_state(rule, context)
            current_value = str(current_state.get("current_value", "")).strip()
            raise ValueError(
                f"Shipment table field '{rule.get('label', '')}' expected '{expected_value}', got '{current_value}'."
            )

    def _apply_shipment_table_rule_by_state(self, desired_value: str) -> dict[str, Any]:
        if not self.driver:
            raise RuntimeError("Browser has not been opened.")
        expected = str(desired_value or "").strip()
        if not expected:
            return {}
        try:
            result = self.driver.execute_script(
                """
                const expected = String(arguments[0] || '').replace(/\\s+/g, ' ').trim();
                function norm(value) {
                  return String(value || '').replace(/\\s+/g, ' ').trim();
                }
                function compact(value) {
                  return norm(value).replace(/\\s+/g, '').replace(/内/g, '');
                }
                function extractDays(value) {
                  const match = String(value || '').match(/(\\d+)\\s*天/);
                  return match ? Number(match[1]) : 0;
                }
                function extractHours(value) {
                  const match = String(value || '').match(/(\\d+)\\s*小时/);
                  return match ? Number(match[1]) : 0;
                }
                function rankService(serviceName, expectedName) {
                  const left = norm(serviceName);
                  const right = norm(expectedName);
                  if (!left || !right) {
                    return -1;
                  }
                  if (left === right) {
                    return 0;
                  }
                  if (left.includes(right) || right.includes(left)) {
                    return 1;
                  }
                  const leftCompact = compact(left);
                  const rightCompact = compact(right);
                  if (
                    leftCompact === rightCompact ||
                    leftCompact.includes(rightCompact) ||
                    rightCompact.includes(leftCompact)
                  ) {
                    return 2;
                  }
                  if (left.includes('发货') && right.includes('发货')) {
                    const leftDays = extractDays(left);
                    const rightDays = extractDays(right);
                    if (leftDays > 0 && rightDays > 0 && leftDays === rightDays) {
                      return 3;
                    }
                    const leftHours = extractHours(left);
                    const rightHours = extractHours(right);
                    if (leftHours > 0 && rightHours > 0 && leftHours === rightHours) {
                      return 3;
                    }
                  }
                  return -1;
                }

                const sdk = window.SellPublishSdk;
                const engine = sdk && sdk.engine;
                const core = engine && engine._engine && engine._engine._core;
                if (!engine || !engine.getJsonState || !core || !core.changeElementValue) {
                  return { ok: false, reason: 'runtime_unavailable', available_service_names: [] };
                }

                const state = engine.getJsonState() || {};
                const components = state.components || {};
                const buyerComponent = components.buyerProtection || {};
                const buyerProps = buyerComponent.props || {};
                const renderGroups = (((buyerProps.channelRenderMap || {}).dsc) || []);
                const shipmentGroup =
                  renderGroups.find((item) => String((item || {}).groupId || '') === '1') ||
                  renderGroups[0] ||
                  {};
                const services = Array.isArray(shipmentGroup.ptsOfferTagModels)
                  ? shipmentGroup.ptsOfferTagModels
                  : [];
                let matchedService = null;
                let bestRank = Number.POSITIVE_INFINITY;
                services.forEach((item) => {
                  const rank = rankService(item && item.serviceName, expected);
                  if (rank >= 0 && rank < bestRank) {
                    matchedService = item;
                    bestRank = rank;
                  }
                });

                const available = services
                  .map((item) => norm(item && item.serviceName))
                  .filter((item) => item);
                if (!matchedService) {
                  return {
                    ok: false,
                    reason: 'service_not_found',
                    available_service_names: available.slice(0, 20),
                  };
                }

                const serviceCode = String(matchedService.serviceCode || '').trim();
                const serviceName = norm(matchedService.serviceName || '');
                if (!serviceCode) {
                  return {
                    ok: false,
                    reason: 'service_code_missing',
                    available_service_names: available.slice(0, 20),
                  };
                }

                const cloneValue = (value) => JSON.parse(JSON.stringify(value || {}));
                const nextValue = cloneValue(buyerProps.value || {});
                const selectedServices = cloneValue(nextValue.selectedServices || {});
                const dscGroups = Array.isArray(selectedServices.dsc) ? selectedServices.dsc : [];
                let shipmentIndex = dscGroups.findIndex((item) => String((item || {}).logicGroupId || '') === '1');
                if (shipmentIndex < 0) {
                  dscGroups.push({ logicGroupId: '1', offerMode: 'step' });
                  shipmentIndex = dscGroups.length - 1;
                }
                const currentShipmentGroup = dscGroups[shipmentIndex] || {};
                dscGroups[shipmentIndex] = {
                  ...currentShipmentGroup,
                  logicGroupId: '1',
                  offerMode: 'step',
                  offerRapidProcess: false,
                  steps: [
                    {
                      from: 1,
                      value: serviceCode,
                    },
                  ],
                };

                selectedServices.dsc = dscGroups;
                nextValue.selectedServices = selectedServices;
                const nextSpsCode = Array.isArray(nextValue.spsCode)
                  ? nextValue.spsCode.map((item) => String(item || '').trim()).filter((item) => item)
                  : [];
                if (!nextSpsCode.includes(serviceCode)) {
                  nextSpsCode.unshift(serviceCode);
                }
                nextValue.spsCode = Array.from(new Set(nextSpsCode));

                core.changeElementValue('buyerProtection', nextValue, { isDepth: false });
                return {
                  ok: true,
                  matched_service_name: serviceName,
                  matched_service_code: serviceCode,
                  available_service_names: available.slice(0, 20),
                };
                """,
                expected,
            )
        except Exception:
            return {}
        if isinstance(result, dict):
            return result
        return {}

    def _resolve_shipment_table_trigger(
        self,
        rule: dict[str, Any],
        context: dict[str, Any],
        *,
        wait_for_element: bool,
    ) -> Any | None:
        if not self.driver:
            raise RuntimeError("Browser has not been opened.")

        selector = self._resolve_selector(rule.get("selector", {}), context)
        selector_match: Any | None = None
        if self._selector_is_configured(selector):
            try:
                selector_match = self._wait_for_element(selector, clickable=wait_for_element)
            except TimeoutException:
                selector_match = self._find_first_visible_element(selector)
            if selector_match:
                return selector_match

        root_selector = self._resolve_selector(
            rule.get("root_selector", {"by": "css", "value": "#guid-buyerProtection"}),
            context,
        )
        if not self._selector_is_configured(root_selector):
            return None
        try:
            root = self._wait_for_element(root_selector)
        except TimeoutException:
            return None

        header_text = str(rule.get("header_text") or rule.get("label") or "").strip()
        row_index = max(0, int(rule.get("row_index", 0) or 0))
        preferred_selector = str(rule.get("preferred_selector", "")).strip()
        try:
            element = self.driver.execute_script(
                """
                const root = arguments[0];
                const headerText = String(arguments[1] || '').replace(/\\s+/g, ' ').trim();
                const rowIndex = Math.max(0, Number(arguments[2]) || 0);
                const preferredSelector = String(arguments[3] || '').trim();
                function norm(value) {
                  return String(value || '').replace(/\\s+/g, ' ').trim();
                }
                function isVisible(node) {
                  if (!node) {
                    return false;
                  }
                  const style = window.getComputedStyle(node);
                  const rect = node.getBoundingClientRect();
                  return style.display !== 'none' && style.visibility !== 'hidden' && rect.width > 0 && rect.height > 0;
                }
                function findTrigger(container) {
                  if (!container) {
                    return null;
                  }
                  const selectors = [
                    '.ant-select-selector',
                    '.ant-select',
                    '[role=\"combobox\"]',
                    '.next-select',
                    '.next-select-inner',
                    '.next-select-trigger',
                    '[aria-haspopup=\"listbox\"]'
                  ];
                  for (const selector of selectors) {
                    if (container.matches && container.matches(selector) && isVisible(container)) {
                      return container;
                    }
                  }
                  for (const selector of selectors) {
                    const candidates = Array.from(container.querySelectorAll(selector)).filter(isVisible);
                    if (candidates.length > 0) {
                      return candidates[0];
                    }
                  }
                  return null;
                }

                if (preferredSelector) {
                  const preferredNode = root.querySelector(preferredSelector);
                  const preferredTrigger = findTrigger(preferredNode);
                  if (preferredTrigger) {
                    return preferredTrigger;
                  }
                }

                const tables = Array.from(root.querySelectorAll('table')).filter(isVisible);
                for (const table of tables) {
                  const headerCells = Array.from(table.querySelectorAll('thead th, thead td')).filter(isVisible);
                  let columnIndex = -1;
                  if (headerText && headerCells.length > 0) {
                    columnIndex = headerCells.findIndex((cell) => norm(cell.innerText || cell.textContent || '').includes(headerText));
                  }
                  let bodyRows = Array.from(table.querySelectorAll('tbody tr')).filter(isVisible);
                  if (bodyRows.length === 0) {
                    bodyRows = Array.from(table.querySelectorAll('tr')).filter(isVisible);
                    if (headerCells.length > 0 && bodyRows.length > 1) {
                      bodyRows = bodyRows.slice(1);
                    }
                  }
                  if (rowIndex >= bodyRows.length) {
                    continue;
                  }
                  const row = bodyRows[rowIndex];
                  const cells = Array.from(row.querySelectorAll('td, th')).filter(isVisible);
                  if (columnIndex >= 0 && columnIndex < cells.length) {
                    const trigger = findTrigger(cells[columnIndex]);
                    if (trigger) {
                      return trigger;
                    }
                  }
                  for (const cell of cells) {
                    const trigger = findTrigger(cell);
                    if (trigger) {
                      return trigger;
                    }
                  }
                }

                return null;
                """,
                root,
                header_text,
                row_index,
                preferred_selector,
            )
        except Exception:
            return None
        return element

    def _wait_for_shipment_table_rule_value(
        self,
        rule: dict[str, Any],
        context: dict[str, Any],
        expected_value: str,
    ) -> bool:
        timeout_seconds = max(0.5, float(rule.get("verify_timeout_seconds", 4)))
        interval_seconds = max(0.2, float(rule.get("verify_interval_seconds", 0.4)))
        deadline = time.time() + timeout_seconds
        normalized_expected = str(expected_value).strip()

        while True:
            current_state = self._read_shipment_table_rule_state(rule, context)
            current_value = str(current_state.get("current_value", "")).strip()
            if self._shipment_value_matches(current_value, normalized_expected):
                return True
            if time.time() >= deadline:
                return False
            self._pause(interval_seconds)

    def _shipment_value_matches(self, current_value: str, expected_value: str) -> bool:
        actual = str(current_value or "").strip()
        expected = str(expected_value or "").strip()
        if actual == expected:
            return True
        if not actual or not expected:
            return False

        compact_actual = re.sub(r"\s+", "", actual).replace("内", "")
        compact_expected = re.sub(r"\s+", "", expected).replace("内", "")
        if (
            compact_actual == compact_expected
            or compact_actual in compact_expected
            or compact_expected in compact_actual
        ):
            return True

        if "发货" in actual and "发货" in expected:
            actual_day = re.search(r"(\d+)\s*天", actual)
            expected_day = re.search(r"(\d+)\s*天", expected)
            if actual_day and expected_day and actual_day.group(1) == expected_day.group(1):
                return True

            actual_hour = re.search(r"(\d+)\s*小时", actual)
            expected_hour = re.search(r"(\d+)\s*小时", expected)
            if actual_hour and expected_hour and actual_hour.group(1) == expected_hour.group(1):
                return True

        return False

    def _read_category_prop_state(self, label: str) -> dict[str, Any]:
        if not label:
            return {"label": "", "exists": False, "current_value": ""}
        container = self._find_category_prop_container(label)
        if not container:
            return {"label": label, "exists": False, "current_value": ""}
        raw_value = self._read_category_prop_value(container, label)
        current_value = "" if self._is_placeholder_category_prop_value(raw_value) else raw_value
        return {
            "label": label,
            "exists": True,
            "current_value": current_value,
            "raw_value": raw_value,
        }

    def _find_category_prop_container(self, label: str) -> Any | None:
        if not self.driver:
            raise RuntimeError("Browser has not been opened.")
        selector = {
            "by": "xpath",
            "value": (
                f"//div[@id='guid-catProp']//div[contains(@class,'decorate-cat-prop')]"
                f"[.//span[contains(@class,'cat-prop-label')][contains(normalize-space(.), {self._xpath_literal(label)})]]"
            ),
        }
        by = BY_MAPPING.get(str(selector.get("by", "css")).strip().lower(), By.CSS_SELECTOR)
        candidates = self.driver.find_elements(by, str(selector.get("value", "")).strip())
        for candidate in candidates:
            if candidate.is_displayed():
                return candidate
        return None

    def _read_category_prop_value(self, container: Any, label: str) -> str:
        if not self.driver:
            raise RuntimeError("Browser has not been opened.")
        value = self.driver.execute_script(
            """
            const container = arguments[0];
            const label = String(arguments[1] || '').replace(/\\s+/g, ' ').trim();
            function norm(value) {
              return String(value || '').replace(/\\s+/g, ' ').trim();
            }
            function isVisible(node) {
              if (!node) return false;
              const style = window.getComputedStyle(node);
              const rect = node.getBoundingClientRect();
              return style.display !== 'none' && style.visibility !== 'hidden' && rect.width > 0 && rect.height > 0;
            }

            const inputCandidates = Array.from(container.querySelectorAll('input, textarea')).filter(isVisible);
            for (const input of inputCandidates) {
              const current = norm(input.value || input.getAttribute('value') || '');
              if (current) {
                return current;
              }
            }

            const textCandidates = [
              '.ant-select-selection-item',
              '.next-select-values',
              '.next-tag-body',
              '.next-select-inner',
              '.ant-select-selector',
              '.next-input-control'
            ].flatMap((selector) => Array.from(container.querySelectorAll(selector))).filter(isVisible);
            for (const node of textCandidates) {
              const current = norm(node.innerText || node.textContent || '');
              if (current && current !== label) {
                return current;
              }
            }

            const fullText = norm(container.innerText || container.textContent || '');
            if (!fullText) {
              return '';
            }
            if (label && fullText.startsWith(label)) {
              return norm(fullText.slice(label.length));
            }
            return fullText === label ? '' : fullText;
            """,
            container,
            label,
        )
        return str(value or "").strip()

    def _is_placeholder_category_prop_value(self, value: str) -> bool:
        normalized = str(value or "").strip()
        if not normalized:
            return True
        placeholder_tokens = {
            "请选择",
            "请选择/",
            "请选择规格",
            "请选择属性",
            "请选择后输入",
            "请选择或输入",
            "请填写",
            "请选择后可输入",
        }
        if normalized in placeholder_tokens:
            return True
        return normalized.startswith("请选择")

    def _revert_unsaved_sku_toggle(
        self,
        selectors: dict[str, Any],
        context: dict[str, Any],
    ) -> None:
        if not self.driver:
            raise RuntimeError("Browser has not been opened.")
        if str(context.get("switch_label_before", "")).strip() != "上架":
            return
        try:
            sku_row_selector = self._resolve_selector(selectors.get("sku_row", {}), context)
            if not self._selector_is_configured(sku_row_selector):
                return
            sku_row = self._wait_for_element(sku_row_selector)
            switch_selector = selectors.get("sku_switch", {})
            if not switch_selector:
                return
            switch_element = sku_row.find_element(
                BY_MAPPING.get(str(switch_selector.get("by", "css")).strip().lower(), By.CSS_SELECTOR),
                str(switch_selector.get("value", "")).strip(),
            )
            switch_label = self._read_switch_label(switch_element)
            if self._is_already_offline(switch_element, switch_label):
                self.driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", switch_element)
                self.driver.execute_script("arguments[0].click();", switch_element)
                self._pause(0.8)
                self._wait_for_switch_change(sku_row, switch_selector, expect_offline=False)
                context["switch_label_reverted"] = self._read_switch_label(
                    sku_row.find_element(
                        BY_MAPPING.get(str(switch_selector.get("by", "css")).strip().lower(), By.CSS_SELECTOR),
                        str(switch_selector.get("value", "")).strip(),
                    )
                )
        except Exception as exc:
            context["switch_revert_error"] = str(exc)

    def _verify_persisted_offline(
        self,
        selectors: dict[str, Any],
        verification_config: dict[str, Any],
        context: dict[str, Any],
    ) -> None:
        self._verify_group_persisted_offline(selectors, verification_config, [context])

    def _verify_group_persisted_offline(
        self,
        selectors: dict[str, Any],
        verification_config: dict[str, Any],
        contexts: list[dict[str, Any]],
    ) -> None:
        if not self.driver:
            raise RuntimeError("Browser has not been opened.")
        if not contexts:
            return
        if not verification_config or not verification_config.get("enabled", True):
            for context in contexts:
                context["post_submit_verified"] = "skipped"
            return

        if self._is_review_submission_success(verification_config, contexts[0]):
            for context in contexts:
                context["post_submit_verified"] = "review_submitted"
                context["execution_result"] = "review_submitted"
            return

        product_ids = {str(context.get("product_id", "")).strip() for context in contexts}
        product_ids.discard("")
        if len(product_ids) != 1:
            raise PublishSubmitError("Grouped post-submit verification requires one product_id.")
        product_id = next(iter(product_ids))
        if not product_id:
            raise PublishSubmitError("Missing product_id for post-submit verification.")

        verify_url = f"https://offer-new.1688.com/popular/publish.htm?id={product_id}&operator=edit"
        initial_wait_seconds = float(verification_config.get("initial_wait_seconds", 8))
        timeout_seconds = float(verification_config.get("timeout_seconds", 150))
        refresh_interval_seconds = max(1.0, float(verification_config.get("refresh_interval_seconds", 15)))
        deadline = time.time() + max(timeout_seconds, 0)
        attempts_by_sku: dict[str, list[dict[str, Any]]] = {
            str(context.get("online_sku", "")).strip(): [] for context in contexts
        }

        self._pause(initial_wait_seconds)
        while True:
            self.driver.get(verify_url)
            self._pause(5.0)
            self._assert_not_redirected_to_login(contexts[0])
            self._assert_no_risk_control_block(contexts[0])

            unresolved: list[str] = []
            for context in contexts:
                online_sku = str(context.get("online_sku", "")).strip()
                attempts = attempts_by_sku.setdefault(online_sku, [])
                try:
                    switch_element, switch_label, aria_checked = self._read_current_sku_switch_state(
                        selectors,
                        context,
                    )
                except Exception as exc:
                    attempts.append(
                        {
                            "checked_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                            "error": str(exc),
                        }
                    )
                    unresolved.append(online_sku)
                    continue

                attempts.append(
                    {
                        "checked_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                        "switch_label": switch_label,
                        "aria_checked": aria_checked,
                    }
                )
                context["post_submit_switch_label"] = switch_label
                context["post_submit_switch_aria_checked"] = aria_checked
                if self._is_already_offline(switch_element, switch_label):
                    context["post_submit_verified"] = "true"
                    context["post_submit_checks"] = attempts
                else:
                    unresolved.append(online_sku)

            if not unresolved:
                return

            if time.time() >= deadline:
                for context in contexts:
                    online_sku = str(context.get("online_sku", "")).strip()
                    context["post_submit_checks"] = attempts_by_sku.get(online_sku, [])
                raise PublishSubmitError(
                    "Grouped SKU submit did not persist offline state for: " + ", ".join(unresolved)
                )
            self._pause(refresh_interval_seconds)

    def _read_current_sku_switch_state(
        self,
        selectors: dict[str, Any],
        context: dict[str, Any],
    ) -> tuple[Any, str, str]:
        sku_row = self._find_sku_row_by_runtime_value(context)
        if sku_row is None:
            sku_row_selector = self._resolve_selector(selectors.get("sku_row", {}), context)
            if not self._selector_is_configured(sku_row_selector):
                raise PublishSubmitError("sku_row selector is not configured for post-submit verification.")
            sku_row = self._wait_for_element(sku_row_selector)

        switch_selector = selectors.get("sku_switch", {})
        if not switch_selector:
            raise PublishSubmitError("sku_switch selector is not configured for post-submit verification.")
        switch_element = sku_row.find_element(
            BY_MAPPING.get(str(switch_selector.get("by", "css")).strip().lower(), By.CSS_SELECTOR),
            str(switch_selector.get("value", "")).strip(),
        )
        switch_label = self._read_switch_label(switch_element)
        aria_checked = str(switch_element.get_attribute("aria-checked") or "")
        return switch_element, switch_label, aria_checked

    def _wait_for_success(
        self,
        detection_config: dict[str, Any],
        context: dict[str, Any],
    ) -> bool:
        if not detection_config or not detection_config.get("enabled", True):
            return True
        timeout_seconds = float(detection_config.get("timeout_seconds", 8))
        keywords = [
            str(item).strip()
            for item in detection_config.get("keywords", [])
            if str(item).strip()
        ]
        page_keywords = [
            str(item).strip()
            for item in detection_config.get("page_keywords", keywords)
            if str(item).strip()
        ]
        message_selector = self._resolve_selector(detection_config.get("message_selector", {}), context)
        deadline = time.time() + max(timeout_seconds, 0)
        while True:
            message_text = self._extract_message_text(message_selector)
            if any(keyword in message_text for keyword in keywords):
                context["success_message"] = message_text
                context["success_signal_source"] = "message"
                return True
            page_text = self._extract_page_body_text()
            if any(keyword in page_text for keyword in page_keywords):
                context["success_message"] = page_text
                context["success_signal_source"] = "page"
                return True
            if time.time() >= deadline:
                return False
            time.sleep(0.2)

    def _extract_message_text(self, selector: dict[str, str]) -> str:
        if not self.driver:
            raise RuntimeError("Browser has not been opened.")
        if self._selector_is_configured(selector):
            try:
                elements = self.driver.find_elements(
                    BY_MAPPING.get(selector.get("by", "css").strip().lower(), By.CSS_SELECTOR),
                    selector.get("value", "").strip(),
                )
                visible_texts = [
                    " ".join((element.text or "").split())
                    for element in elements
                    if " ".join((element.text or "").split())
                ]
                if visible_texts:
                    return " | ".join(visible_texts)
            except Exception:
                return ""
        return self._extract_page_body_text()

    def _extract_page_body_text(self) -> str:
        if not self.driver:
            raise RuntimeError("Browser has not been opened.")
        try:
            return " ".join(self.driver.find_element(By.TAG_NAME, "body").text.split())
        except Exception:
            return ""

    def _is_review_submission_success(
        self,
        verification_config: dict[str, Any],
        context: dict[str, Any],
    ) -> bool:
        if not bool(verification_config.get("accept_review_submission", True)):
            return False

        success_signal_source = str(context.get("success_signal_source", "")).strip().lower()
        success_message = str(context.get("success_message", "")).strip()
        if success_signal_source != "page" and success_message:
            return False

        candidate_text = success_message or self._extract_page_body_text()
        if not candidate_text:
            return False

        review_keywords = [
            str(item).strip()
            for item in verification_config.get(
                "review_submission_keywords",
                ["修改成功", "商品已提交审核", "提交审核", "审核中", "未上架产品-审核中"],
            )
            if str(item).strip()
        ]
        if not review_keywords:
            return False
        if not all(keyword in candidate_text for keyword in review_keywords[:2]):
            return False
        context["review_submission_message"] = candidate_text
        return True

    def _switch_to_newest_window(self, before_handles: list[str]) -> None:
        if not self.driver:
            raise RuntimeError("Browser has not been opened.")

        before = set(before_handles)
        wait = WebDriverWait(self.driver, self.browser_config.get("explicit_wait_seconds", 20))

        def has_new_window(driver: Any) -> bool:
            return len(set(driver.window_handles) - before) > 0

        try:
            wait.until(has_new_window)
            new_handles = [handle for handle in self.driver.window_handles if handle not in before]
            if new_handles:
                self.driver.switch_to.window(new_handles[-1])
            return
        except TimeoutException:
            if self.driver.window_handles:
                self.driver.switch_to.window(self.driver.window_handles[-1])

    def _restore_management_window(self, main_window: str) -> None:
        if not self.driver:
            return
        try:
            current_handle = self.driver.current_window_handle
        except Exception:
            return
        if current_handle != main_window:
            try:
                self.driver.close()
            except Exception:
                pass
            try:
                handles = list(self.driver.window_handles)
            except Exception:
                return
            if main_window in handles:
                try:
                    self.driver.switch_to.window(main_window)
                except Exception:
                    return
        try:
            self.driver.switch_to.default_content()
        except Exception:
            return

    def _wait_for_switch_change(
        self,
        sku_row: Any,
        switch_selector: dict[str, Any],
        *,
        expect_offline: bool,
    ) -> None:
        if not self.driver:
            raise RuntimeError("Browser has not been opened.")
        wait = WebDriverWait(self.driver, self.browser_config.get("explicit_wait_seconds", 20))

        def switch_updated(_driver: Any) -> bool:
            element = sku_row.find_element(
                BY_MAPPING.get(str(switch_selector.get("by", "css")).strip().lower(), By.CSS_SELECTOR),
                str(switch_selector.get("value", "")).strip(),
            )
            label = self._read_switch_label(element)
            return self._is_already_offline(element, label) if expect_offline else self._is_online_state(element, label)

        wait.until(switch_updated)

    def _read_switch_label(self, element: Any) -> str:
        text = " ".join((element.text or "").split()).strip()
        if text:
            return text
        for attribute in ("aria-label", "title", "innerText", "textContent"):
            try:
                value = " ".join((element.get_attribute(attribute) or "").split()).strip()
            except Exception:
                value = ""
            if value:
                return value
        return ""

    def _is_already_offline(self, element: Any, label: str) -> bool:
        aria_checked = str(element.get_attribute("aria-checked") or "").strip().lower()
        if aria_checked == "false":
            return True
        normalized = label.replace(" ", "")
        return "下架" in normalized and "上架" not in normalized

    def _is_online_state(self, element: Any, label: str) -> bool:
        aria_checked = str(element.get_attribute("aria-checked") or "").strip().lower()
        if aria_checked == "true":
            return True
        normalized = label.replace(" ", "")
        return "上架" in normalized and "下架" not in normalized
