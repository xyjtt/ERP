from __future__ import annotations

import base64
import html
import json
import mimetypes
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from selenium.common.exceptions import (
    ElementClickInterceptedException,
    ElementNotInteractableException,
    StaleElementReferenceException,
    TimeoutException,
    WebDriverException,
)
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.remote.webelement import WebElement
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.select import Select
from selenium.webdriver.support.ui import WebDriverWait

from exceptions import MatchCandidateInvalidError, ProductMatchNotFoundError, PublishSubmitError, PublishValidationError
from parser import ProductRecord, strip_emoji
from webdriver_factory import open_webdriver


BY_MAPPING = {
    "css": By.CSS_SELECTOR,
    "id": By.ID,
    "name": By.NAME,
    "xpath": By.XPATH,
}


class BrowserRPA:
    def __init__(self, browser_config: dict, project_root: str | Path) -> None:
        self.browser_config = browser_config
        self.project_root = Path(project_root)
        self.driver: Any | None = None
        self.attached_to_existing_browser = False
        self.last_screenshot_path = ""
        self.last_html_snapshot_path = ""
        self.last_result_context: dict[str, Any] = {}

    def open(self) -> None:
        debugger_address = str(self.browser_config.get("debugger_address", "")).strip()
        browser_binary_path = str(
            self.browser_config.get("browser_binary_path", self.browser_config.get("chrome_binary_path", ""))
        ).strip()
        user_data_dir = str(self.browser_config.get("user_data_dir", "")).strip()
        profile_directory = str(self.browser_config.get("profile_directory", "")).strip()
        browser_type = str(self.browser_config.get("browser_type", "")).strip()
        page_load_strategy = str(self.browser_config.get("page_load_strategy", "normal")).strip().lower()
        if page_load_strategy not in {"normal", "eager", "none"}:
            page_load_strategy = "normal"

        self.driver, self.attached_to_existing_browser = open_webdriver(
            headless=bool(self.browser_config.get("headless")),
            debugger_address=debugger_address,
            user_data_dir=user_data_dir,
            profile_directory=profile_directory,
            browser_binary_path=browser_binary_path,
            browser_type=browser_type,
            page_load_strategy=page_load_strategy,
        )
        self.driver.implicitly_wait(self.browser_config.get("implicit_wait_seconds", 10))
        page_load_timeout = float(self.browser_config.get("page_load_timeout_seconds", 60))
        if page_load_timeout > 0:
            self.driver.set_page_load_timeout(page_load_timeout)

    def close(self) -> None:
        if self.driver:
            if self.attached_to_existing_browser and self.browser_config.get("keep_browser_open_on_close", True):
                self.driver = None
                return
            driver = self.driver
            self.driver = None
            try:
                driver.quit()
            except Exception as exc:
                print(f"[WARN] Browser driver was already unavailable during close: {exc}")

    def reset_runtime_artifacts(self) -> None:
        self.last_screenshot_path = ""
        self.last_html_snapshot_path = ""
        self.last_result_context = {}

    def login(self, platform_config: dict, account_config: dict) -> None:
        if not self.driver:
            raise RuntimeError("Browser has not been opened.")

        login_url = platform_config.get("login_url")
        if login_url:
            self.driver.get(login_url)
            self._pause(self.browser_config.get("page_load_wait_seconds", 2))

        login_config = platform_config.get("login", {})
        mode = login_config.get("mode", "manual")
        if mode == "manual":
            prompt = login_config.get(
                "completion_prompt",
                f"Complete login for {platform_config['platform']} and press Enter to continue...",
            )
            self._wait_for_manual_confirmation(prompt)
            return

        username = account_config.get("username", "").strip()
        password = account_config.get("password", "").strip()
        if not username or not password:
            raise ValueError(f"Missing credentials for platform '{platform_config['platform']}'")

        for field_name, value in (("username", username), ("password", password)):
            selector = login_config.get("selectors", {}).get(field_name, {})
            if not self._selector_is_configured(selector):
                raise ValueError(
                    f"Login selector '{field_name}' is not configured for {platform_config['platform']}"
                )
            element = self._wait_for_element(selector, clickable=True)
            element.clear()
            element.send_keys(value)

        submit_selector = login_config.get("selectors", {}).get("submit", {})
        if self._selector_is_configured(submit_selector):
            self._wait_for_element(submit_selector, clickable=True).click()

    def run_system_workflow(self, system_config: dict, context: dict[str, Any]) -> None:
        if not self.driver:
            raise RuntimeError("Browser has not been opened.")

        login_url = system_config.get("login_url")
        if login_url:
            self.driver.get(login_url)
            self._pause(self.browser_config.get("page_load_wait_seconds", 2))

        login_config = system_config.get("login", {})
        if login_config.get("mode", "manual") == "manual":
            self._wait_for_manual_confirmation(
                login_config.get(
                    "completion_prompt",
                    f"Complete login for {system_config.get('display_name', system_config.get('system', 'system'))} and press Enter to continue...",
                )
            )

        self._run_publish_steps(system_config.get("workflow", {}).get("steps", []), context)
        self._record_page_metadata(context)

    def publish_product(
        self,
        platform_config: dict,
        product: ProductRecord,
        category_config: dict,
    ) -> dict[str, Any]:
        if not self.driver:
            raise RuntimeError("Browser has not been opened.")

        publish_config = platform_config.get("publish", {})
        publish_url = platform_config.get("publish_url")
        context = self._build_context(platform_config["platform"], product, category_config)
        self.last_result_context = context

        try:
            if publish_url and publish_config.get("open_each_product", True):
                self.driver.get(publish_url)
                self._pause(self.browser_config.get("page_load_wait_seconds", 2))

            print(
                f"[INFO] Ready to publish {product.title} to {platform_config['platform']} "
                f"(category: {context['display_category_name']})"
            )
            self._run_publish_steps(publish_config.get("steps", []), context)
            final_action_mode = self._resolve_publish_mode(publish_config)
            context["final_action_mode"] = final_action_mode
            if final_action_mode == "draft":
                self._ensure_draft_send_address_selected(context)
                self._ensure_draft_required_delivery_service(context)
                self._apply_draft_page_state_patch(publish_config, context)
            self._check_publish_error_state(
                publish_config.get("pre_submit_error_detection", {}),
                context=context,
                stage_name="pre_submit",
                exception_cls=PublishValidationError,
            )
            if publish_config.get("pause_before_submit", False):
                action_label = {
                    "draft": "保存草稿",
                    "submit": "提交发布",
                }.get(final_action_mode, "继续后续流程")
                self._wait_for_manual_confirmation(f"请确认页面填写无误，准备{action_label}时按回车...")

            if final_action_mode == "draft":
                context["draft_submit_retry_count"] = 0
                context["draft_submit_retry_errors"] = []
                context["draft_request_patch_mode_history"] = []
                context["draft_request_patch_mode"] = self._resolve_initial_draft_request_patch_mode(
                    publish_config
                )
                if context["draft_request_patch_mode"]:
                    context["draft_request_patch_mode_history"] = [context["draft_request_patch_mode"]]
                max_verify_attempts = max(1, int(publish_config.get("draft_verify_retry_count", 2) or 2))
                retry_wait_seconds = float(publish_config.get("draft_verify_retry_wait_seconds", 1.0))
                draft_submit_retry_wait_seconds = float(
                    publish_config.get("draft_submit_retry_wait_seconds", max(retry_wait_seconds, 3.0))
                )
                draft_submit_retry_backoff_seconds = float(
                    publish_config.get("draft_submit_retry_backoff_seconds", 2.0)
                )
                draft_submit_retry_refresh_page = bool(publish_config.get("draft_submit_retry_refresh_page", True))
                draft_submit_retry_refresh_wait_seconds = float(
                    publish_config.get("draft_submit_retry_refresh_wait_seconds", 2.5)
                )
                backend_reject_fast_fail_count = max(
                    0,
                    int(publish_config.get("draft_submit_backend_reject_fast_fail_count", 0) or 0),
                )
                backend_reject_consecutive_count = 0
                for verify_attempt in range(max_verify_attempts):
                    context["draft_verify_attempt"] = verify_attempt + 1
                    self._ensure_draft_send_address_selected(context)
                    self._ensure_draft_required_delivery_service(context)
                    self._ensure_draft_logistics_dimensions_before_save(publish_config, context)
                    self._ensure_required_cat_props_before_draft_save(publish_config, context)
                    self._ensure_buyer_protection_ship_time_before_draft_save(publish_config, context)
                    try:
                        self._save_draft_once(publish_config, context)
                    except PublishSubmitError as exc:
                        if verify_attempt >= max_verify_attempts - 1:
                            raise
                        if not self._should_retry_draft_submit(exc):
                            raise
                        context["draft_submit_retry_count"] = int(context.get("draft_submit_retry_count", 0) or 0) + 1
                        retry_errors = list(context.get("draft_submit_retry_errors", []) or [])
                        retry_errors.append(str(exc))
                        context["draft_submit_retry_errors"] = retry_errors
                        context["draft_submit_retry_reason"] = str(exc)
                        if self._is_draft_submit_backend_reject_error(exc):
                            backend_reject_consecutive_count += 1
                            context["draft_submit_backend_reject_consecutive_count"] = (
                                backend_reject_consecutive_count
                            )
                            if (
                                backend_reject_fast_fail_count > 0
                                and backend_reject_consecutive_count >= backend_reject_fast_fail_count
                            ):
                                context["draft_submit_fast_fail_triggered"] = True
                                context["draft_submit_fast_fail_threshold"] = backend_reject_fast_fail_count
                                raise PublishSubmitError(
                                    "draft_submit backend rejected request repeatedly "
                                    f"({backend_reject_consecutive_count}/{backend_reject_fast_fail_count}): {exc}"
                                ) from exc
                            next_patch_mode = self._resolve_next_draft_request_patch_mode(publish_config, context)
                            if next_patch_mode:
                                previous_patch_mode = str(context.get("draft_request_patch_mode", "")).strip()
                                context["draft_request_patch_mode"] = next_patch_mode
                                context["draft_submit_retry_mode"] = f"switch_patch_mode:{next_patch_mode}"
                                patch_mode_history = list(context.get("draft_request_patch_mode_history", []) or [])
                                if not patch_mode_history or patch_mode_history[-1] != next_patch_mode:
                                    patch_mode_history.append(next_patch_mode)
                                context["draft_request_patch_mode_history"] = patch_mode_history
                                print(
                                    f"[WARN] Switch draft request patch mode "
                                    f"from '{previous_patch_mode or 'unknown'}' to '{next_patch_mode}'."
                                )
                        else:
                            backend_reject_consecutive_count = 0
                        assist_messages = self._collect_assist_messages()
                        context["draft_assist_messages"] = assist_messages
                        required_labels = self._extract_required_labels_from_assist_messages(assist_messages)
                        if required_labels:
                            context["draft_required_field_labels"] = required_labels
                            context["draft_submit_retry_missing_labels"] = required_labels
                        if draft_submit_retry_refresh_page:
                            try:
                                self.driver.refresh()
                                self._pause(max(0.3, draft_submit_retry_refresh_wait_seconds))
                            except WebDriverException as refresh_exc:
                                context["draft_submit_retry_refresh_warning"] = str(refresh_exc)
                                print(f"[WARN] Skip draft retry refresh due webdriver error: {refresh_exc}")
                        submit_retry_wait = max(
                            0.5,
                            draft_submit_retry_wait_seconds
                            + (draft_submit_retry_backoff_seconds * verify_attempt),
                        )
                        print(
                            f"[WARN] Retry draft save after backend transient error "
                            f"({verify_attempt + 1}/{max_verify_attempts - 1}, wait={submit_retry_wait:.1f}s): {exc}"
                        )
                        self._pause(submit_retry_wait)
                        continue
                    try:
                        self._verify_saved_draft(publish_config, context)
                        break
                    except PublishValidationError as exc:
                        if verify_attempt >= max_verify_attempts - 1:
                            raise
                        if not self._should_retry_draft_verify(exc, context):
                            raise
                        assist_messages = [
                            str(item).strip()
                            for item in list(context.get("draft_assist_messages", []) or [])
                            if str(item).strip()
                        ]
                        missing_required_labels = self._extract_required_labels_from_assist_messages(assist_messages)
                        if missing_required_labels:
                            context["draft_verify_retry_mode"] = "required_fields_repair"
                            context["draft_verify_retry_missing_labels"] = missing_required_labels
                            context["draft_required_field_labels"] = missing_required_labels
                        else:
                            context["draft_verify_retry_mode"] = "buyer_protection_ship_time_repair"
                        context["draft_verify_retry_reason"] = str(exc)
                        fallback_service = self._resolve_fallback_buyer_protection_service()
                        fallback_service_name = str(fallback_service.get("service_name", "")).strip()
                        fallback_service_code = str(fallback_service.get("service_code", "")).strip()
                        if fallback_service_name:
                            current_service_name = str(context.get("buyer_protection_ship_time", "")).strip()
                            if fallback_service_name != current_service_name:
                                context["buyer_protection_ship_time"] = fallback_service_name
                                if fallback_service_code:
                                    context["buyer_protection_ship_time_code"] = fallback_service_code
                                context["buyer_protection_step_template_runtime"] = [
                                    {
                                        "from": 1,
                                        "service_name": fallback_service_name,
                                        "service_code": fallback_service_code,
                                    }
                                ]
                                context["draft_buyer_protection_retry_fallback"] = fallback_service_name
                                print(
                                    f"[WARN] Switch buyer protection ship time fallback to '{fallback_service_name}' "
                                    "for draft retry."
                                )
                        self._ensure_draft_send_address_selected(context)
                        self._ensure_draft_required_delivery_service(context)
                        self._ensure_draft_logistics_dimensions_before_save(publish_config, context)
                        self._ensure_required_cat_props_before_draft_save(publish_config, context)
                        self._ensure_buyer_protection_ship_time_before_draft_save(publish_config, context)
                        print(
                            f"[WARN] Retry draft save after buyer protection verify failure "
                            f"({verify_attempt + 1}/{max_verify_attempts - 1}): {exc}"
                        )
                        self._apply_draft_page_state_patch(publish_config, context)
                        self._pause(retry_wait_seconds)
                print(f"[INFO] Saved draft for product: {product.title}")
            elif final_action_mode == "submit":
                submit_selector = publish_config.get("submit_selector", {})
                if not self._selector_is_configured(submit_selector):
                    raise ValueError("Auto submit is enabled but submit_selector is not configured.")
                self._install_submit_request_trace(publish_config)
                self._wait_for_element(submit_selector, clickable=True).click()
                self._check_publish_error_state(
                    publish_config.get("submit_error_detection", {}),
                    context=context,
                    stage_name="post_submit",
                    exception_cls=PublishSubmitError,
                )
                trace_detected = self._assert_submit_request_trace(publish_config, context)
                if not trace_detected:
                    context["submit_retry_mode"] = "dispatch_event_click"
                    self._dispatch_click_with_events(submit_selector)
                    self._pause(1.0)
                    self._check_publish_error_state(
                        publish_config.get("submit_error_detection", {}),
                        context=context,
                        stage_name="post_submit_retry",
                        exception_cls=PublishSubmitError,
                    )
                    trace_detected = self._assert_submit_request_trace(publish_config, context)
                if not trace_detected:
                    raise PublishSubmitError(
                        "Submit button was clicked but no submit request was captured. "
                        "The page likely blocked submit due to hidden validation or disabled state."
                    )
                self._verify_submit_result(publish_config, context)
                print(f"[INFO] Submitted product: {product.title}")
            else:
                print(f"[INFO] Final action disabled for {product.title}.")
            self._apply_extractors(publish_config.get("success_extractors", []), context)
            self._record_page_metadata(context)
            self.last_result_context = context
            return context
        except Exception:
            self._record_page_metadata(context)
            self.last_result_context = context
            self._capture_screenshot(product.title)
            raise

    def _run_publish_steps(self, steps: list[dict[str, Any]], context: dict[str, Any]) -> None:
        for step in steps:
            step_name = str(step.get("name", "")).strip() or "unnamed_step"
            action = step.get("action", "").strip()
            if not action:
                continue

            print(f"[INFO] Running step: {step_name} ({action})")

            if action == "manual":
                self._wait_for_manual_confirmation(
                    step.get("message", "Manual check required. Press Enter to continue...")
                )
                continue

            if action == "sleep":
                self._pause(float(step.get("seconds", 1)))
                continue

            if action == "sanitize_inputs":
                changed_count = self._sanitize_inputs(step)
                print(
                    f"[INFO] Sanitized {changed_count} spec/title inputs for step "
                    f"'{step.get('name', 'sanitize_inputs')}'."
                )
                self._pause(self.browser_config.get("action_wait_seconds", 0.5))
                continue

            if action == "assert_no_errors":
                self._check_publish_error_state(
                    step.get("error_detection", {}),
                    context=context,
                    stage_name=step.get("name", "assert_no_errors"),
                    exception_cls=PublishValidationError,
                )
                continue

            if action == "extract":
                self._extract_value(step, context)
                continue

            if action == "category_path":
                selector = self._resolve_selector(step.get("selector", {}), context)
                retry_count = max(1, int(step.get("retry", 2) or 2))
                completed = False
                for attempt_index in range(retry_count):
                    try:
                        self._select_category_path(step, selector, context)
                        completed = True
                        break
                    except TimeoutException as exc:
                        if attempt_index < retry_count - 1:
                            print(
                                f"[WARN] Retry step '{step_name}' after timeout "
                                f"({attempt_index + 1}/{retry_count}) - {exc}"
                            )
                            self._pause(0.8)
                            continue
                        if step.get("required", False):
                            raise
                        print(f"[WARN] Skip optional step '{step_name}' because category selection timed out.")
                        completed = True
                        break
                if not completed:
                    if step.get("required", False):
                        raise TimeoutException(f"Required category step '{step_name}' did not complete.")
                    print(f"[WARN] Skip optional step '{step_name}' because category selection did not complete.")
                self._pause(self.browser_config.get("action_wait_seconds", 0.5))
                continue

            if action == "match_candidates":
                self._match_candidates(step, context)
                self._pause(self.browser_config.get("action_wait_seconds", 0.5))
                continue

            if action == "category_prop_defaults":
                self._fill_category_prop_defaults(step, context)
                self._pause(self.browser_config.get("action_wait_seconds", 0.5))
                continue

            if action == "spec_values":
                self._fill_spec_values(step, context)
                self._pause(self.browser_config.get("action_wait_seconds", 0.5))
                continue

            if action == "logistics_dimensions":
                filled = self._fill_logistics_dimensions(step, context)
                if not filled and bool(step.get("required", False)):
                    raise ValueError(f"Required logistics dimensions step '{step_name}' did not fill any fields.")
                self._pause(self.browser_config.get("action_wait_seconds", 0.5))
                continue

            selector = self._resolve_selector(step.get("selector", {}), context)
            required = bool(step.get("required", False))
            if not self._selector_is_configured(selector):
                if required:
                    raise ValueError(f"Required selector not configured for step '{step.get('name')}'")
                print(f"[WARN] Skip step '{step.get('name')}' because selector is empty.")
                continue

            timeout_retry_count = max(0, int(step.get("retry_count", 0) or 0))
            timeout_retry_wait_seconds = float(step.get("retry_wait_seconds", 1.5))
            stale_retry_count = max(0, int(step.get("stale_retry_count", 2) or 2))
            stale_retry_wait_seconds = float(step.get("stale_retry_wait_seconds", 0.5))
            timeout_attempts_used = 0
            stale_attempts_used = 0
            completed = False
            while True:
                try:
                    if action in {"input", "textarea"}:
                        value = self._resolve_value(step, context)
                        if not value:
                            if required:
                                raise ValueError(f"Required value missing for step '{step.get('name')}'")
                            completed = True
                            break
                        element = self._wait_for_element(selector, clickable=True)
                        self._fill_text_field(element, str(value), clear=bool(step.get("clear", True)))
                    elif action == "combobox":
                        value = self._resolve_value(step, context)
                        if not value:
                            if required:
                                raise ValueError(f"Required combobox value missing for step '{step.get('name')}'")
                            completed = True
                            break
                        self._fill_combobox(selector, step, value)
                    elif action == "ant_select":
                        value = self._resolve_value(step, context)
                        if not value:
                            if required:
                                raise ValueError(f"Required ant_select value missing for step '{step.get('name')}'")
                            completed = True
                            break
                        self._select_ant_option(selector, step, value)
                    elif action == "select":
                        value = self._resolve_value(step, context)
                        if not value:
                            if required:
                                raise ValueError(f"Required select value missing for step '{step.get('name')}'")
                            completed = True
                            break
                        element = self._wait_for_element(selector, clickable=True)
                        self._select_option(element, step, value)
                    elif action == "file":
                        values = self._resolve_file_values(step, context)
                        if not values:
                            if required:
                                raise ValueError(f"Required file value missing for step '{step.get('name')}'")
                            completed = True
                            break
                        element = self._wait_for_element(selector)
                        payload = "\n".join(values) if step.get("multiple") else values[0]
                        element.send_keys(payload)
                    elif action == "picker_upload":
                        values = self._resolve_file_values(step, context)
                        if not values:
                            if required:
                                raise ValueError(f"Required picker_upload value missing for step '{step.get('name')}'")
                            completed = True
                            break
                        self._run_picker_upload(step, selector, values, context)
                    elif action == "picker_url_upload":
                        values = self._resolve_file_values(step, context)
                        if not values:
                            if required:
                                raise ValueError(
                                    f"Required picker_url_upload value missing for step '{step.get('name')}'"
                                )
                            completed = True
                            break
                        self._run_picker_url_upload(step, selector, values, context)
                    elif action == "tinymce":
                        value = self._resolve_value(step, context)
                        if not value:
                            if required:
                                raise ValueError(f"Required TinyMCE value missing for step '{step.get('name')}'")
                            completed = True
                            break
                        self._write_tinymce_content(step, selector, value)
                    elif action == "tinymce_image_html":
                        values = self._resolve_file_values(step, context)
                        if not values:
                            if required:
                                raise ValueError(
                                    f"Required TinyMCE image HTML value missing for step '{step.get('name')}'"
                                )
                            completed = True
                            break
                        html_value = self._build_tinymce_image_html(values)
                        if not html_value:
                            if required:
                                raise ValueError(
                                    f"Step '{step.get('name')}' requires valid remote image URLs for TinyMCE."
                                )
                            completed = True
                            break
                        self._write_tinymce_content(step, selector, html_value)
                    elif action == "tinymce_images":
                        values = self._resolve_file_values(step, context)
                        if not values:
                            if required:
                                raise ValueError(f"Required TinyMCE image value missing for step '{step.get('name')}'")
                            completed = True
                            break
                        self._insert_tinymce_images(step, selector, values, context)
                    elif action == "click":
                        self._wait_for_element(selector, clickable=True).click()
                        if step.get("check_errors_after"):
                            self._check_publish_error_state(
                                self._resolve_error_detection(step.get("error_detection", {}), context),
                                stage_name=step.get("name", "click"),
                                exception_cls=PublishValidationError,
                            )
                    else:
                        raise ValueError(f"Unsupported action type: {action}")
                    completed = True
                    break
                except TimeoutException:
                    if (
                        action in {"input", "textarea"}
                        and step_name == "quantity"
                        and self._fill_quantity_via_sku_table(self._resolve_value(step, context))
                    ):
                        print("[WARN] Applied quantity fallback via SKU table editable stock column.")
                        completed = True
                        break
                    if action == "picker_upload" and step_name == "main_image" and self._draft_main_image_present():
                        print("[WARN] Main image picker did not open, but an existing main image is already present.")
                        completed = True
                        break
                    if timeout_attempts_used < timeout_retry_count:
                        timeout_attempts_used += 1
                        print(
                            f"[WARN] Retry step '{step_name}' after timeout "
                            f"({timeout_attempts_used}/{timeout_retry_count})."
                        )
                        self._pause(timeout_retry_wait_seconds)
                        continue
                    if required:
                        raise TimeoutException(
                            f"Step '{step_name}' timed out. selector={selector}"
                        )
                    print(
                        f"[WARN] Skip optional step '{step_name}' because the element did not appear in time."
                    )
                    completed = True
                    break
                except Exception as exc:
                    if not self._is_stale_driver_error(exc):
                        raise
                    if stale_attempts_used < stale_retry_count:
                        stale_attempts_used += 1
                        print(
                            f"[WARN] Retry step '{step_name}' after stale element "
                            f"({stale_attempts_used}/{stale_retry_count})."
                        )
                        self._pause(stale_retry_wait_seconds)
                        continue
                    if required:
                        raise
                    print(
                        f"[WARN] Skip optional step '{step_name}' because stale element persisted."
                    )
                    completed = True
                    break

            if not completed:
                continue

            self._pause(self.browser_config.get("action_wait_seconds", 0.5))

    def _fill_quantity_via_sku_table(self, value: str) -> bool:
        if not self.driver:
            raise RuntimeError("Browser has not been opened.")
        normalized = str(value or "").strip()
        if not normalized:
            return False
        try:
            applied = self.driver.execute_script(
                """
                const rawValue = arguments[0];
                function isVisible(node) {
                  if (!node) return false;
                  const style = window.getComputedStyle(node);
                  const rect = node.getBoundingClientRect();
                  return style.display !== 'none' && style.visibility !== 'hidden' && rect.width > 0 && rect.height > 0;
                }
                const root = document.querySelector('#guid-skuTable');
                if (!root || !isVisible(root)) {
                  return false;
                }
                const headerCells = Array.from(
                  root.querySelectorAll('.next-table-header [data-next-table-col], .next-table-header th')
                );
                let targetCol = '';
                for (const cell of headerCells) {
                  const text = String(cell.innerText || cell.textContent || '').replace(/\\s+/g, ' ').trim();
                  if (!text.includes('可售数量')) {
                    continue;
                  }
                  const col = String(cell.getAttribute('data-next-table-col') || '').trim();
                  if (col) {
                    targetCol = col;
                    break;
                  }
                }
                if (!targetCol) {
                  targetCol = '5';
                }
                const inputs = Array.from(
                  root.querySelectorAll(`td[data-next-table-col="${targetCol}"] input`)
                ).filter((node) => isVisible(node) && !node.disabled && !node.readOnly);
                if (inputs.length === 0) {
                  return false;
                }
                const nextValue = String(rawValue);
                const setValue = (element, value) => {
                  const descriptor = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value');
                  if (descriptor && descriptor.set) {
                    descriptor.set.call(element, value);
                  } else {
                    element.value = value;
                  }
                  element.dispatchEvent(new InputEvent('input', { bubbles: true, data: value }));
                  element.dispatchEvent(new Event('change', { bubbles: true }));
                };
                inputs.forEach((element) => {
                  element.scrollIntoView({block: 'center', inline: 'nearest'});
                  element.focus();
                  setValue(element, nextValue);
                });
                return true;
                """,
                normalized,
            )
        except Exception:
            return False
        return bool(applied)

    def _fill_logistics_dimensions(self, step: dict[str, Any], context: dict[str, Any]) -> bool:
        if not self.driver:
            raise RuntimeError("Browser has not been opened.")

        length_value = self._resolve_context_preferred_value(
            context=context,
            source=str(step.get("length_source", "length_cm")).strip(),
            default_value=str(step.get("length_default", "")).strip(),
        )
        width_value = self._resolve_context_preferred_value(
            context=context,
            source=str(step.get("width_source", "width_cm")).strip(),
            default_value=str(step.get("width_default", "")).strip(),
        )
        height_value = self._resolve_context_preferred_value(
            context=context,
            source=str(step.get("height_source", "height_cm")).strip(),
            default_value=str(step.get("height_default", "")).strip(),
        )
        weight_value = self._resolve_context_preferred_value(
            context=context,
            source=str(step.get("weight_source", "weight_g")).strip(),
            default_value=str(step.get("weight_default", "")).strip(),
        )

        payload = self.driver.execute_script(
            """
            const lengthValue = String(arguments[0] || '').trim();
            const widthValue = String(arguments[1] || '').trim();
            const heightValue = String(arguments[2] || '').trim();
            const weightValue = String(arguments[3] || '').trim();

            const result = {
              ok: true,
              applied: 0,
              reason: '',
              columns: {},
            };

            const values = {
              length: lengthValue,
              width: widthValue,
              height: heightValue,
              weight: weightValue,
            };

            const hasAnyValue = Object.values(values).some((item) => String(item || '').trim());
            if (!hasAnyValue) {
              result.reason = 'no_values';
              return result;
            }

            const norm = (value) => String(value || '').replace(/\\s+/g, '').trim();
            const isVisible = (node) => {
              if (!node) return false;
              const style = window.getComputedStyle(node);
              const rect = node.getBoundingClientRect();
              return style.display !== 'none' && style.visibility !== 'hidden' && rect.width > 0 && rect.height > 0;
            };
            const setInputValue = (inputNode, rawValue) => {
              const nextValue = String(rawValue || '').trim();
              if (!nextValue) return false;
              if (!inputNode || inputNode.disabled || inputNode.readOnly) return false;
              const descriptor = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value');
              if (descriptor && descriptor.set) {
                descriptor.set.call(inputNode, nextValue);
              } else {
                inputNode.value = nextValue;
              }
              inputNode.dispatchEvent(new Event('input', { bubbles: true }));
              inputNode.dispatchEvent(new Event('change', { bubbles: true }));
              inputNode.dispatchEvent(new Event('blur', { bubbles: true }));
              return true;
            };

            const root = document.querySelector('#guid-officialLogistics') || document;
            const tableCandidates = Array.from(root.querySelectorAll('table')).filter(isVisible);
            if (tableCandidates.length === 0) {
              result.reason = 'table_not_found';
              return result;
            }

            let targetTable = tableCandidates.find((tableNode) => {
              const headerText = norm(tableNode.innerText || '');
              const hasShapeHeader =
                headerText.includes('长(cm)') &&
                headerText.includes('宽(cm)') &&
                headerText.includes('高(cm)') &&
                headerText.includes('重量(g)');
              const inputCount = tableNode.querySelectorAll('tbody input, .next-table-body input').length;
              return hasShapeHeader && inputCount > 0;
            });
            if (!targetTable) {
              targetTable =
                tableCandidates.find(
                  (tableNode) => tableNode.querySelectorAll('tbody input, .next-table-body input').length >= 4
                ) ||
                tableCandidates[0];
            }
            if (!targetTable) {
              result.reason = 'target_table_missing';
              return result;
            }

            const headerNodes = Array.from(
              targetTable.querySelectorAll('thead [data-next-table-col], .next-table-header [data-next-table-col], thead th')
            );
            const colTextByKey = {};
            headerNodes.forEach((node, index) => {
              const colKey = String(node.getAttribute('data-next-table-col') || '').trim() || String(index + 1);
              const text = norm(node.innerText || node.textContent || '');
              if (text) {
                colTextByKey[colKey] = text;
              }
            });

            const findColumnKey = (keywords, fallbackIndex) => {
              const entries = Object.entries(colTextByKey);
              for (const [key, text] of entries) {
                if ((Array.isArray(keywords) ? keywords : []).every((token) => text.includes(norm(token)))) {
                  return key;
                }
              }
              if (fallbackIndex > 0) {
                return String(fallbackIndex);
              }
              return '';
            };

            const colMap = {
              length: findColumnKey(['长'], 3),
              width: findColumnKey(['宽'], 4),
              height: findColumnKey(['高'], 5),
              weight: findColumnKey(['重量'], 7),
            };
            result.columns = colMap;

            const bodyRow =
              targetTable.querySelector('tbody tr') ||
              targetTable.querySelector('.next-table-body tr');
            if (!bodyRow) {
              result.reason = 'body_row_missing';
              return result;
            }

            const findInputByCol = (colKey) => {
              const normalizedKey = String(colKey || '').trim();
              if (!normalizedKey) return null;
              let node = bodyRow.querySelector(`td[data-next-table-col="${normalizedKey}"] input`);
              if (!node && /^\\d+$/.test(normalizedKey)) {
                node = bodyRow.querySelector(`td:nth-child(${normalizedKey}) input`);
              }
              if (!node) {
                return null;
              }
              return node;
            };

            for (const [key, inputValue] of Object.entries(values)) {
              const colKey = colMap[key];
              const inputNode = findInputByCol(colKey);
              if (!inputNode) continue;
              if (setInputValue(inputNode, inputValue)) {
                result.applied += 1;
              }
            }

            if (result.applied <= 0) {
              result.reason = 'no_editable_cells';
            }
            return result;
            """,
            length_value,
            width_value,
            height_value,
            weight_value,
        )
        payload_dict = payload if isinstance(payload, dict) else {}
        context["logistics_dimension_fill"] = payload_dict
        return int(payload_dict.get("applied", 0) or 0) > 0

    def _build_context(
        self,
        platform_key: str,
        product: ProductRecord,
        category_config: dict,
    ) -> dict[str, Any]:
        category_entry = category_config.get(product.platform_category, {})
        detail_images = [
            image.strip()
            for image in product.raw.get("detail_images", "").split("|")
            if image.strip()
        ]
        context = dict(product.raw)
        context["display_category_name"] = category_entry.get(
            "display_name", product.platform_category
        )
        resolved_category_name = category_entry.get("platform_categories", {}).get(platform_key, "")
        context["resolved_category_name"] = resolved_category_name
        resolved_category_levels = self._split_category_path(resolved_category_name)
        context["resolved_category_levels"] = resolved_category_levels
        context["resolved_category_level_count"] = len(resolved_category_levels)
        for index, level in enumerate(resolved_category_levels, start=1):
            context[f"resolved_category_level_{index}"] = level
        context["detail_images_list"] = detail_images
        return context

    def _resolve_publish_mode(self, publish_config: dict[str, Any]) -> str:
        if publish_config.get("auto_save_draft", False):
            return "draft"
        if publish_config.get("auto_submit", False):
            return "submit"
        return "manual"

    def _resolve_value(self, step: dict[str, Any], context: dict[str, Any]) -> str:
        if "value" in step:
            return str(step.get("value", "")).strip()

        source = step.get("source", "")
        value = context.get(source, "")
        normalized = str(value).strip()
        if normalized:
            return normalized
        return str(step.get("default_value", "")).strip()

    def _resolve_file_values(self, step: dict[str, Any], context: dict[str, Any]) -> list[str]:
        source = step.get("source", "")
        value = context.get(source, "")

        if source == "detail_images":
            return [str(item) for item in context.get("detail_images_list", [])]

        if isinstance(value, list):
            return [str(item).strip() for item in value if str(item).strip()]

        if not value:
            return []

        return [item.strip() for item in str(value).split("|") if item.strip()]

    def _sanitize_inputs(self, step: dict[str, Any]) -> int:
        if not self.driver:
            raise RuntimeError("Browser has not been opened.")

        container_selector = step.get("selector", {})
        if self._selector_is_configured(container_selector):
            container = self._wait_for_element(container_selector)
            elements = container.find_elements(
                By.CSS_SELECTOR,
                step.get("input_selector", "input, textarea"),
            )
        else:
            elements = self.driver.find_elements(
                By.CSS_SELECTOR,
                step.get("input_selector", "input, textarea"),
            )

        changed_count = 0
        for element in elements:
            if not element.is_displayed():
                continue
            if step.get("skip_readonly", True) and (
                element.get_attribute("readonly") or element.get_attribute("disabled")
            ):
                continue

            original = element.get_attribute("value") or ""
            cleaned = " ".join(strip_emoji(original).split())
            if cleaned == original:
                continue

            element.clear()
            if cleaned:
                element.send_keys(cleaned)
            changed_count += 1

        return changed_count

    def _select_category_path(
        self,
        step: dict[str, Any],
        selector: dict[str, str],
        context: dict[str, Any],
    ) -> None:
        if not self.driver:
            raise RuntimeError("Browser has not been opened.")

        levels = self._resolve_category_levels(step, context)
        if not levels:
            if step.get("required", False):
                raise ValueError(f"Required category levels missing for step '{step.get('name')}'")
            return

        force_reselect = bool(step.get("force_reselect", False))
        if not force_reselect and self._category_matches_current_page(levels):
            return

        select_url = self._build_category_select_url(step)
        current_url = self.driver.current_url or ""
        if force_reselect or "select.htm" not in current_url:
            reused_existing_page = self._activate_existing_category_page(select_url)
            if not reused_existing_page:
                self.driver.get(select_url)
            self._pause(float(step.get("category_page_wait_seconds", 2)))

        for index, level in enumerate(levels):
            self._click_category_option(level)
            if index < len(levels) - 1:
                self._wait_for_category_level(levels[index + 1], timeout_seconds=float(step.get("category_level_timeout_seconds", 10)))
            else:
                self._wait_for_category_confirmation_v2(
                    levels,
                    timeout_seconds=float(step.get("category_confirm_wait_seconds", 10)),
                )

        confirm_selector = self._resolve_selector(
            step.get("confirm_selector", {"by": "css", "value": "#submitButton"}),
            context,
        )
        category_return_timeout_seconds = float(step.get("category_return_timeout_seconds", 20))
        window_handles_before_confirm = set(self.driver.window_handles)
        self._click_category_confirm_button(confirm_selector)
        try:
            self._wait_for_publish_page_after_category_confirm(
                timeout_seconds=category_return_timeout_seconds,
                existing_handles=window_handles_before_confirm,
                expected_levels=levels,
                prefer_new_publish=force_reselect,
            )
        except TimeoutException:
            if not self._activate_existing_publish_page(
                expected_levels=levels,
                prefer_new_publish=force_reselect,
            ):
                raise
        self._pause(float(step.get("after_category_return_wait_seconds", 2)))

    def _click_category_confirm_button(self, confirm_selector: dict[str, str]) -> None:
        if not self.driver:
            raise RuntimeError("Browser has not been opened.")
        try:
            confirm_button = self._wait_for_element(confirm_selector, clickable=True)
            self.driver.execute_script("arguments[0].click();", confirm_button)
            return
        except TimeoutException:
            pass

        selector_value = str(confirm_selector.get("value", "")).strip()
        clicked = self.driver.execute_script(
            """
            const configuredSelector = String(arguments[0] || '').trim();
            const selectorCandidates = [
              configuredSelector,
              '#submitButton',
              'button#submitButton',
              'button.ant-btn-primary',
              '.next-btn.next-btn-primary',
            ].filter(Boolean);
            function norm(value) {
              return String(value || '').replace(/\\s+/g, '').trim();
            }
            function isVisible(node) {
              if (!node) return false;
              const style = window.getComputedStyle(node);
              const rect = node.getBoundingClientRect();
              return style.display !== 'none' && style.visibility !== 'hidden' && rect.width > 0 && rect.height > 0;
            }
            const textCandidates = ['确定', '确认', '提交'];
            for (const selector of selectorCandidates) {
              const nodes = Array.from(document.querySelectorAll(selector)).filter((node) => {
                if (!isVisible(node)) return false;
                const disabled =
                  node.disabled ||
                  node.getAttribute('disabled') !== null ||
                  node.getAttribute('aria-disabled') === 'true';
                if (disabled) return false;
                if (selector === '#submitButton' || selector === 'button#submitButton') return true;
                const text = norm(node.innerText || node.textContent || '');
                return textCandidates.some((item) => text.includes(item));
              });
              const node = nodes[0];
              if (!node) {
                continue;
              }
              node.scrollIntoView({block: 'center', inline: 'nearest'});
              try {
                node.click();
              } catch (error) {
                ['mousedown', 'mouseup', 'click'].forEach((eventName) => {
                  node.dispatchEvent(new MouseEvent(eventName, {
                    bubbles: true,
                    cancelable: true,
                    view: window,
                    buttons: 1,
                  }));
                });
              }
              return true;
            }
            return false;
            """,
            selector_value,
        )
        if not clicked:
            raise TimeoutException("Category confirm button is not available for click.")

    def _activate_existing_publish_page(
        self,
        *,
        expected_levels: list[str] | None = None,
        prefer_new_publish: bool = False,
    ) -> bool:
        if not self.driver:
            raise RuntimeError("Browser has not been opened.")

        levels = list(expected_levels or [])
        original_handle = self.driver.current_window_handle
        candidates: list[tuple[int, str]] = []
        for handle in list(self.driver.window_handles):
            try:
                self.driver.switch_to.window(handle)
                candidate_url = str(self.driver.current_url or "").strip()
                if "publish.htm" not in candidate_url or "login.1688.com" in candidate_url:
                    continue
                if levels and not self._category_matches_current_page(levels):
                    continue
                candidate_rank = 0 if self._is_preferred_new_publish_url(candidate_url) else 1
                candidates.append((candidate_rank, handle))
            except Exception:
                continue

        if candidates:
            candidates.sort(key=lambda item: item[0])
            best_rank, best_handle = candidates[0]
            if not prefer_new_publish or best_rank == 0:
                self.driver.switch_to.window(best_handle)
                return True

        try:
            self.driver.switch_to.window(original_handle)
        except Exception:
            pass
        return False

    def _resolve_category_levels(self, step: dict[str, Any], context: dict[str, Any]) -> list[str]:
        source = str(step.get("source", "resolved_category_levels")).strip()
        value = context.get(source, [])
        if isinstance(value, list):
            return [str(item).strip() for item in value if str(item).strip()]
        if not value:
            return []
        return self._split_category_path(str(value))

    def _category_matches_current_page(self, levels: list[str]) -> bool:
        if not self.driver:
            raise RuntimeError("Browser has not been opened.")
        if "publish.htm" not in (self.driver.current_url or ""):
            return False
        try:
            text = self.driver.execute_script(
                """
                const root = document.querySelector('#guid-catNamer .current-namer');
                return root ? (root.innerText || root.textContent || '') : '';
                """
            )
        except Exception:
            return False
        current_text = str(text).replace("您选择的类目：", "").strip()
        current = self._split_category_path(current_text)
        if current == levels:
            return True

        normalized_current = re.sub(r"[\s>]+", "", current_text)
        normalized_target = "".join(levels)
        return normalized_current == normalized_target

    def _build_category_select_url(self, step: dict[str, Any]) -> str:
        if not self.driver:
            raise RuntimeError("Browser has not been opened.")

        configured = str(step.get("select_url", "")).strip()
        if configured:
            return configured

        current_url = self.driver.current_url or ""
        if "publish.htm" in current_url:
            return re.sub(r"/popular/publish\.htm.*$", "/select.htm", current_url)
        if "select.htm" in current_url:
            return current_url
        return "https://offer-new.1688.com/select.htm"

    def _is_preferred_new_publish_url(self, url: str) -> bool:
        normalized_url = str(url or "").strip()
        if not normalized_url:
            return False
        try:
            parsed = urlparse(normalized_url)
            path = str(parsed.path or "")
            if "publish.htm" not in path:
                return False
            query = parse_qs(parsed.query, keep_blank_values=True)
            operator = str((query.get("operator") or [""])[0]).strip().lower()
            if operator == "edit":
                return False
            existing_id = str((query.get("id") or [""])[0]).strip()
            if existing_id:
                return False
            return True
        except Exception:
            compact = normalized_url.lower()
            if "publish.htm" not in compact:
                return False
            if "operator=edit" in compact:
                return False
            if re.search(r"[?&]id=[^&#]+", compact):
                return False
            return True

    def _activate_existing_category_page(self, select_url: str) -> bool:
        if not self.driver:
            raise RuntimeError("Browser has not been opened.")

        target_prefix = "https://offer-new.1688.com/select.htm"
        expected_url = str(select_url or "").strip()
        original_handle = self.driver.current_window_handle
        candidate_handle = ""

        for handle in list(self.driver.window_handles):
            try:
                self.driver.switch_to.window(handle)
                current_url = str(self.driver.current_url or "").strip()
                if not current_url.startswith(target_prefix):
                    continue
                if "login.taobao.com" in current_url:
                    continue
                if expected_url and current_url.startswith(expected_url):
                    candidate_handle = handle
                    break
                if not candidate_handle:
                    candidate_handle = handle
            except Exception:
                continue

        if candidate_handle:
            self.driver.switch_to.window(candidate_handle)
            return True

        try:
            self.driver.switch_to.window(original_handle)
        except Exception:
            pass
        return False

    def _wait_for_publish_page_after_category_confirm(
        self,
        *,
        timeout_seconds: float,
        existing_handles: set[str] | None = None,
        expected_levels: list[str] | None = None,
        prefer_new_publish: bool = False,
    ) -> None:
        if not self.driver:
            raise RuntimeError("Browser has not been opened.")
        original_handle = self.driver.current_window_handle
        known_handles = set(existing_handles or set())
        expected = list(expected_levels or [])

        def collect_publish_candidates(driver: Any, handles: list[str]) -> list[tuple[int, str]]:
            candidates: list[tuple[int, str]] = []
            for handle in handles:
                try:
                    driver.switch_to.window(handle)
                    candidate_url = str(driver.current_url or "")
                    if "publish.htm" not in candidate_url or "login.1688.com" in candidate_url:
                        continue
                    if expected and not self._category_matches_current_page(expected):
                        continue
                    candidate_rank = 0 if self._is_preferred_new_publish_url(candidate_url) else 1
                    candidates.append((candidate_rank, handle))
                except Exception:
                    continue
            candidates.sort(key=lambda item: item[0])
            return candidates

        def switch_to_best_candidate(driver: Any, candidates: list[tuple[int, str]]) -> bool:
            if not candidates:
                return False
            best_rank, best_handle = candidates[0]
            if prefer_new_publish and best_rank > 0:
                return False
            try:
                driver.switch_to.window(best_handle)
            except Exception:
                return False
            return True

        def locate_publish_page(driver: Any) -> bool:
            current_url = str(driver.current_url or "")
            if "publish.htm" in current_url and "login.1688.com" not in current_url:
                if (
                    (not expected or self._category_matches_current_page(expected))
                    and (not prefer_new_publish or self._is_preferred_new_publish_url(current_url))
                ):
                    return True

            candidate_new_handles = [handle for handle in list(driver.window_handles) if handle not in known_handles]
            if switch_to_best_candidate(driver, collect_publish_candidates(driver, candidate_new_handles)):
                return True

            if switch_to_best_candidate(driver, collect_publish_candidates(driver, list(driver.window_handles))):
                return True

            try:
                driver.switch_to.window(original_handle)
            except Exception:
                pass
            return False

        WebDriverWait(self.driver, timeout_seconds).until(locate_publish_page)

    def _click_category_option(self, label: str) -> None:
        if not self.driver:
            raise RuntimeError("Browser has not been opened.")

        clicked = self.driver.execute_script(
            """
            const label = arguments[0];
            function norm(value) {
              return (value || '').replace(/\\s+/g, ' ').trim();
            }
            function isVisible(node) {
              if (!node) return false;
              const style = window.getComputedStyle(node);
              const rect = node.getBoundingClientRect();
              return style.display !== 'none' && style.visibility !== 'hidden' && rect.width > 0 && rect.height > 0;
            }
            const options = Array.from(document.querySelectorAll('.next-cascader-menu-wrapper li[role="option"]'));
            const target = options
              .filter(isVisible)
              .find((node) => norm(node.getAttribute('title') || node.innerText) === label);
            if (!target) {
              return false;
            }
            target.scrollIntoView({block: 'center', inline: 'nearest'});
            target.click();
            return true;
            """,
            label,
        )
        if not clicked:
            raise TimeoutException(f"Category option not found: {label}")

    def _wait_for_category_level(self, label: str, *, timeout_seconds: float) -> None:
        if not self.driver:
            raise RuntimeError("Browser has not been opened.")
        wait = WebDriverWait(self.driver, timeout_seconds)
        wait.until(
            lambda driver: driver.execute_script(
                """
                const label = arguments[0];
                function norm(value) {
                  return (value || '').replace(/\\s+/g, ' ').trim();
                }
                function isVisible(node) {
                  if (!node) return false;
                  const style = window.getComputedStyle(node);
                  const rect = node.getBoundingClientRect();
                  return style.display !== 'none' && style.visibility !== 'hidden' && rect.width > 0 && rect.height > 0;
                }
                return Array.from(document.querySelectorAll('.next-cascader-menu-wrapper li[role="option"]'))
                  .filter(isVisible)
                  .some((node) => norm(node.getAttribute('title') || node.innerText) === label);
                """,
                label,
            )
        )

    def _wait_for_category_confirmation(self, levels: list[str], *, timeout_seconds: float) -> None:
        if not self.driver:
            raise RuntimeError("Browser has not been opened.")
        expected = ">".join(levels)
        wait = WebDriverWait(self.driver, timeout_seconds)
        wait.until(
            lambda driver: driver.execute_script(
                """
                const expected = arguments[0];
                const bodyText = document.body.innerText || '';
                return bodyText.includes(`已选类目：${expected}`);
                """,
                expected,
            )
        )

    def _wait_for_category_confirmation_v2(self, levels: list[str], *, timeout_seconds: float) -> None:
        if not self.driver:
            raise RuntimeError("Browser has not been opened.")
        expected = ">".join(levels)
        expected_compact = "".join(levels)
        last_level = levels[-1] if levels else ""
        wait = WebDriverWait(self.driver, timeout_seconds)
        wait.until(
            lambda driver: driver.execute_script(
                """
                const expected = arguments[0];
                const expectedCompact = arguments[1];
                const lastLevel = arguments[2];
                function norm(value) {
                  return (value || '').replace(/\\s+/g, ' ').trim();
                }
                const selectedNode = document.querySelector('.current-selected strong');
                const selectedText = norm(selectedNode ? (selectedNode.innerText || selectedNode.textContent || '') : '');
                if (selectedText) {
                  const compactSelected = selectedText.replace(/[>＞]/g, '').replace(/\\s+/g, '');
                  if (selectedText === expected || compactSelected === expectedCompact) {
                    return true;
                  }
                }
                const confirmButton = document.querySelector('#submitButton');
                if (!confirmButton) {
                  return false;
                }
                const disabled =
                  confirmButton.disabled ||
                  confirmButton.getAttribute('disabled') !== null ||
                  confirmButton.getAttribute('aria-disabled') === 'true';
                if (!disabled && (!lastLevel || selectedText.includes(lastLevel))) {
                  return true;
                }
                return false;
                """,
                expected,
                expected_compact,
                last_level,
            )
        )

    def _extract_value(self, step: dict[str, Any], context: dict[str, Any]) -> None:
        if not self.driver:
            raise RuntimeError("Browser has not been opened.")

        target_key = step.get("target", step.get("name", "extracted_value"))
        source_mode = str(step.get("from", "selector")).strip().lower()
        pattern = str(step.get("pattern", "")).strip()

        if source_mode == "current_url":
            raw_value = self.driver.current_url or ""
        elif source_mode == "body_text":
            raw_value = self._extract_error_text({})
        elif source_mode == "page_source":
            raw_value = self.driver.page_source or ""
        else:
            selector = self._resolve_selector(step.get("selector", {}), context)
            if not self._selector_is_configured(selector):
                if step.get("required", False):
                    raise ValueError(f"Required selector not configured for extractor '{target_key}'")
                return

            element = self._wait_for_element(selector)
            attribute = step.get("attribute", "").strip()
            if attribute:
                raw_value = (element.get_attribute(attribute) or "").strip()
            else:
                raw_value = " ".join(element.text.split())

        if pattern:
            match = re.search(pattern, raw_value)
            if not match:
                if step.get("required", False):
                    raise ValueError(f"Extractor pattern did not match for '{target_key}'")
                return
            group_index = int(step.get("group", 1))
            context[target_key] = (match.group(group_index) or "").strip()
            return

        context[target_key] = str(raw_value).strip()

    def _apply_extractors(self, extractors: list[dict[str, Any]], context: dict[str, Any]) -> None:
        for extractor in extractors:
            self._extract_value(extractor, context)

    def _match_candidates(self, step: dict[str, Any], context: dict[str, Any]) -> None:
        if not self.driver:
            raise RuntimeError("Browser has not been opened.")

        row_selector = step.get("row_selector", {})
        if not self._selector_is_configured(row_selector):
            if step.get("required", False):
                raise ValueError(f"Required row_selector not configured for step '{step.get('name')}'")
            return

        rows = self.driver.find_elements(
            BY_MAPPING.get(row_selector.get("by", "css").strip().lower(), By.CSS_SELECTOR),
            row_selector.get("value", "").strip(),
        )
        if not rows:
            if step.get("fallback_selector", {}).get("value"):
                context["match_candidate_attempts"] = [
                    {
                        "candidate_source_id": "",
                        "candidate_store_name": "",
                        "candidate_title": "",
                        "candidate_use_result": "fallback_self_edit",
                        "candidate_error_message": "No match candidate rows found.",
                        "candidate_error_type": ProductMatchNotFoundError.__name__,
                    }
                ]
                self._wait_for_element(step["fallback_selector"], clickable=True).click()
                context["match_source_id"] = ""
                return
            raise ProductMatchNotFoundError("No match candidate rows found.", step_name=step.get("name"))

        attempts: list[dict[str, str]] = []
        ranked_rows = self._rank_candidate_rows(rows, step, context)
        for row in ranked_rows:
            metadata = self._extract_candidate_metadata(row, step)
            try:
                use_button = self._find_in_row(row, step.get("use_button_selector", {}), clickable=True)
                use_button.click()
                self._check_publish_error_state(
                    step.get("invalid_error_detection", {}),
                    context=context,
                    stage_name=step.get("name", "match_candidates"),
                    exception_cls=MatchCandidateInvalidError,
                )
                metadata["candidate_use_result"] = "success"
                attempts.append(metadata)
                context["match_source_id"] = metadata.get("candidate_source_id", "")
                context["match_candidate_attempts"] = attempts
                return
            except MatchCandidateInvalidError as exc:
                metadata["candidate_use_result"] = "invalid"
                metadata["candidate_error_message"] = str(exc)
                metadata["candidate_error_type"] = exc.__class__.__name__
                attempts.append(metadata)
                continue

        if step.get("fallback_selector", {}).get("value"):
            self._wait_for_element(step["fallback_selector"], clickable=True).click()
            attempts.append(
                {
                    "candidate_source_id": "",
                    "candidate_store_name": "",
                    "candidate_title": "",
                    "candidate_use_result": "fallback_self_edit",
                    "candidate_error_message": "All ranked candidates were invalid.",
                    "candidate_error_type": MatchCandidateInvalidError.__name__,
                }
            )
            context["match_candidate_attempts"] = attempts
            context["match_source_id"] = ""
            return
        context["match_candidate_attempts"] = attempts
        raise ProductMatchNotFoundError(
            "All match candidates failed and no fallback selector is configured.",
            step_name=step.get("name"),
        )

    def _rank_candidate_rows(
        self,
        rows: list[WebElement],
        step: dict[str, Any],
        context: dict[str, Any],
    ) -> list[WebElement]:
        preferred_store = context.get("store_name", "")
        preferred_title = context.get("title", "")

        def score(row: WebElement) -> tuple[int, int]:
            metadata = self._extract_candidate_metadata(row, step)
            score_store = 1 if preferred_store and preferred_store in metadata.get("candidate_store_name", "") else 0
            score_title = 1 if preferred_title and preferred_title in metadata.get("candidate_title", "") else 0
            return (score_store, score_title)

        return sorted(rows, key=score, reverse=True)

    def _extract_candidate_metadata(self, row: WebElement, step: dict[str, Any]) -> dict[str, str]:
        metadata = {
            "candidate_source_id": self._extract_row_text(row, step.get("source_id_selector", {})),
            "candidate_store_name": self._extract_row_text(row, step.get("store_name_selector", {})),
            "candidate_title": self._extract_row_text(row, step.get("title_selector", {})),
            "candidate_use_result": "success",
            "candidate_error_message": "",
            "candidate_error_type": "",
        }
        return metadata

    def _extract_row_text(self, row: WebElement, selector: dict[str, str]) -> str:
        if not selector or not selector.get("value", "").strip():
            return ""
        try:
            element = row.find_element(
                BY_MAPPING.get(selector.get("by", "css").strip().lower(), By.CSS_SELECTOR),
                selector.get("value", "").strip(),
            )
            return " ".join(element.text.split())
        except Exception:
            return ""

    def _find_in_row(
        self,
        row: WebElement,
        selector: dict[str, str],
        *,
        clickable: bool = False,
    ) -> WebElement:
        if not selector or not selector.get("value", "").strip():
            raise ValueError("Row selector is not configured.")
        element = row.find_element(
            BY_MAPPING.get(selector.get("by", "css").strip().lower(), By.CSS_SELECTOR),
            selector.get("value", "").strip(),
        )
        if clickable and not element.is_enabled():
            raise ValueError("Target row element is not enabled.")
        return element

    def _select_option(self, element: WebElement, step: dict[str, Any], value: str) -> None:
        select_mode = step.get("select_by", "text").strip().lower()
        select_control = Select(element)
        if select_mode == "value":
            select_control.select_by_value(value)
        elif select_mode == "index":
            select_control.select_by_index(int(value))
        else:
            select_control.select_by_visible_text(value)

    def _fill_text_field(self, element: WebElement, value: str, *, clear: bool = True) -> None:
        if not self.driver:
            raise RuntimeError("Browser has not been opened.")

        next_value = str(value)
        attempts = 3
        last_value = ""
        for attempt in range(attempts):
            target_value = next_value if clear else f"{element.get_attribute('value') or ''}{next_value}"
            self.driver.execute_script(
                """
                const element = arguments[0];
                const nextValue = arguments[1];
                const tagName = (element.tagName || '').toUpperCase();
                const prototype = tagName === 'TEXTAREA'
                  ? window.HTMLTextAreaElement.prototype
                  : window.HTMLInputElement.prototype;
                const descriptor = Object.getOwnPropertyDescriptor(prototype, 'value');
                element.scrollIntoView({block: 'center', inline: 'nearest'});
                element.focus();
                if (descriptor && descriptor.set) {
                  descriptor.set.call(element, nextValue);
                } else {
                  element.value = nextValue;
                }
                element.dispatchEvent(new InputEvent('input', {bubbles: true, data: nextValue}));
                element.dispatchEvent(new Event('change', {bubbles: true}));
                """,
                element,
                target_value,
            )
            self._pause(0.2)

            current_value = str(element.get_attribute("value") or "")
            if current_value == target_value:
                break

            try:
                element.click()
                self._pause(0.1)
                if clear:
                    element.send_keys(Keys.CONTROL, "a")
                    element.send_keys(Keys.DELETE)
                if next_value:
                    element.send_keys(next_value)
                self._pause(0.3)
            except ElementClickInterceptedException:
                # Sticky bottom banners (for example submit-agreement) can block native click/send_keys.
                # Retry with JS-driven value write in the next loop instead of failing the whole product.
                self._pause(0.25)

            current_value = str(element.get_attribute("value") or "")
            if current_value == target_value:
                break

            last_value = current_value
            self._pause(0.2 * (attempt + 1))
        else:
            raise ValueError(
                f"Failed to set text field value. Expected '{target_value}', got '{last_value}'."
            )

        self.driver.execute_script(
            """
            const element = arguments[0];
            element.dispatchEvent(new Event('change', {bubbles: true}));
            """,
            element,
        )
        self._pause(0.1)

    def _fill_combobox(self, selector: dict[str, str], step: dict[str, Any], value: str) -> None:
        if not self.driver:
            raise RuntimeError("Browser has not been opened.")
        self._run_with_stale_retry(
            lambda: self._fill_combobox_input(
                self._resolve_combobox_input_element(self._wait_for_element(selector)),
                step,
                value,
            ),
            retries=int(step.get("stale_retry_count", 2)),
            wait_seconds=float(step.get("stale_retry_wait_seconds", 0.5)),
        )

    def _resolve_combobox_input_element(self, element: WebElement) -> WebElement:
        if not self.driver:
            raise RuntimeError("Browser has not been opened.")
        tag_name = (element.tag_name or "").strip().lower()
        if tag_name in {"input", "textarea"}:
            return element
        candidates = element.find_elements(
            By.CSS_SELECTOR,
            "input[role='combobox'], input[type='search'], input[type='text'], textarea",
        )
        for candidate in candidates:
            if candidate.is_displayed():
                return candidate
        return element

    def _fill_combobox_input(self, input_element: WebElement, step: dict[str, Any], value: str) -> None:
        if not self.driver:
            raise RuntimeError("Browser has not been opened.")

        self.driver.execute_script(
            """
            const input = arguments[0];
            const antSelect = input.closest('.ant-select');
            const container =
              (antSelect && antSelect.querySelector('.ant-select-selector')) ||
              input.closest('.ant-select-selector') ||
              input.closest('.next-select') ||
              input.closest('.next-select-inner') ||
              input.closest('.next-input') ||
              input;
            const readonly =
              input.hasAttribute('readonly') ||
              input.getAttribute('aria-readonly') === 'true' ||
              input.getAttribute('unselectable') === 'on';
            input.scrollIntoView({block: 'center', inline: 'nearest'});
            container.click();
            if (!readonly) {
              input.focus();
            }
            """,
            input_element,
        )
        self._pause(float(step.get("open_wait_seconds", 0.3)))

        normalized_value = str(value).strip()
        readonly = bool(input_element.get_attribute("readonly")) or bool(input_element.get_attribute("disabled"))
        if normalized_value and not readonly:
            try:
                input_element.send_keys(Keys.CONTROL, "a")
                input_element.send_keys(Keys.DELETE)
            except Exception:
                self.driver.execute_script(
                    """
                    const input = arguments[0];
                    input.value = '';
                    input.dispatchEvent(new Event('input', {bubbles: true}));
                    """,
                    input_element,
                )

            try:
                input_element.send_keys(normalized_value)
            except Exception:
                self.driver.execute_script(
                    """
                    const input = arguments[0];
                    const nextValue = arguments[1];
                    input.value = nextValue;
                    input.dispatchEvent(new Event('input', {bubbles: true}));
                    input.dispatchEvent(new Event('change', {bubbles: true}));
                    """,
                    input_element,
                    normalized_value,
                )
            self._pause(float(step.get("type_wait_seconds", 0.5)))

        matched = False
        if normalized_value:
            matched = self.driver.execute_script(
                """
                const expected = (arguments[0] || '').replace(/\\s+/g, ' ').trim();
                function norm(value) {
                  return (value || '').replace(/\\s+/g, ' ').trim();
                }
                function isVisible(node) {
                  if (!node) return false;
                  const style = window.getComputedStyle(node);
                  const rect = node.getBoundingClientRect();
                  return style.display !== 'none' && style.visibility !== 'hidden' && rect.width > 0 && rect.height > 0;
                }
                const selectors = [
                  '.ant-select-dropdown .ant-select-item-option',
                  '.next-overlay-wrapper .next-menu-item',
                  '.next-overlay-wrapper li'
                ];
                const nodes = selectors.flatMap((selector) => Array.from(document.querySelectorAll(selector)));
                const visible = nodes.filter(isVisible);
                const exact = visible.find((node) => norm(node.innerText) === expected);
                const fuzzy = visible.find((node) => norm(node.innerText).includes(expected));
                const target = exact || fuzzy;
                if (!target) {
                  return false;
                }
                target.click();
                return true;
                """,
                normalized_value,
            )
        force_first_on_readonly_steps = {
            "ship_from_template",
            "freight_template",
            "ship_time_template",
        }
        if (
            not matched
            and readonly
            and str(step.get("name", "")).strip() in force_first_on_readonly_steps
        ):
            matched = self._click_first_visible_dropdown_option()
        if not matched and step.get("select_first_option_if_unmatched", False):
            matched = self._click_first_visible_dropdown_option()
        if not matched and normalized_value and not readonly:
            try:
                input_element.send_keys(Keys.ENTER)
            except ElementNotInteractableException:
                pass
        self._pause(float(step.get("select_wait_seconds", 0.5)))

    def _click_first_visible_dropdown_option(self) -> bool:
        if not self.driver:
            raise RuntimeError("Browser has not been opened.")
        return bool(
            self.driver.execute_script(
                """
                function isVisible(node) {
                  if (!node) return false;
                  const style = window.getComputedStyle(node);
                  const rect = node.getBoundingClientRect();
                  return style.display !== 'none' && style.visibility !== 'hidden' && rect.width > 0 && rect.height > 0;
                }
                const selectors = [
                  '.ant-select-dropdown .ant-select-item-option',
                  '.next-overlay-wrapper .next-menu-item',
                  '.next-overlay-wrapper li'
                ];
                const nodes = selectors.flatMap((selector) => Array.from(document.querySelectorAll(selector)));
                const target = nodes.filter(isVisible)[0];
                if (!target) {
                  return false;
                }
                target.click();
                return true;
                """
            )
        )

    def _click_visible_dropdown_option(self, value: str) -> bool:
        if not self.driver:
            raise RuntimeError("Browser has not been opened.")
        return bool(
            self.driver.execute_script(
                """
                const expected = (arguments[0] || '').replace(/\\s+/g, ' ').trim();
                function norm(value) {
                  return (value || '').replace(/\\s+/g, ' ').trim();
                }
                function isVisible(node) {
                  if (!node) return false;
                  const style = window.getComputedStyle(node);
                  const rect = node.getBoundingClientRect();
                  return style.display !== 'none' && style.visibility !== 'hidden' && rect.width > 0 && rect.height > 0;
                }
                const selectors = [
                  '.ant-select-dropdown .ant-select-item-option',
                  '.next-overlay-wrapper .next-menu-item',
                  '.next-overlay-wrapper li'
                ];
                const nodes = selectors.flatMap((selector) => Array.from(document.querySelectorAll(selector)));
                const visible = nodes.filter(isVisible);
                const exact = visible.find((node) => norm(node.innerText) === expected);
                const fuzzy = visible.find((node) => norm(node.innerText).includes(expected));
                const target = exact || fuzzy;
                if (!target) {
                  return false;
                }
                target.click();
                return true;
                """,
                value,
            )
        )

    def _click_visible_dropdown_option_native(self, value: str, *, dropdown_id: str = "") -> bool:
        if not self.driver:
            raise RuntimeError("Browser has not been opened.")
        target_value = str(value).strip()
        target_compact = re.sub(r"\s+", "", target_value).replace("内", "")
        target_day_match = re.search(r"(\d+)\s*天", target_value)
        target_hour_match = re.search(r"(\d+)\s*小时", target_value)

        def match_rank(option_text: str) -> int | None:
            normalized = " ".join((option_text or "").split())
            if not normalized:
                return None
            if normalized == target_value:
                return 0
            if target_value and (target_value in normalized or normalized in target_value):
                return 1

            compact = re.sub(r"\s+", "", normalized).replace("内", "")
            if target_compact and (compact == target_compact or target_compact in compact or compact in target_compact):
                return 2

            if "发货" in normalized and "发货" in target_value:
                day_match = re.search(r"(\d+)\s*天", normalized)
                if target_day_match and day_match and day_match.group(1) == target_day_match.group(1):
                    return 3
                hour_match = re.search(r"(\d+)\s*小时", normalized)
                if target_hour_match and hour_match and hour_match.group(1) == target_hour_match.group(1):
                    return 3
            return None

        candidates = self.driver.find_elements(
            By.CSS_SELECTOR,
            ".ant-select-dropdown .ant-select-item-option, .next-overlay-wrapper .next-menu-item, .next-overlay-wrapper li",
        )
        best_match: WebElement | None = None
        best_rank: int | None = None
        for candidate in candidates:
            try:
                if not candidate.is_displayed():
                    continue
                if dropdown_id:
                    belongs_to_dropdown = bool(
                        self.driver.execute_script(
                            """
                            const node = arguments[0];
                            const expectedId = String(arguments[1] || '').trim();
                            if (!node || !expectedId) {
                              return false;
                            }
                            const root = node.closest('.ant-select-dropdown');
                            return Boolean(root && String(root.id || '').trim() === expectedId);
                            """,
                            candidate,
                            dropdown_id,
                        )
                    )
                    if not belongs_to_dropdown:
                        continue
                text = " ".join((candidate.text or "").split())
                rank = match_rank(text)
                if rank is None:
                    continue
                if best_rank is None or rank < best_rank:
                    best_match = candidate
                    best_rank = rank
                    if rank == 0:
                        break
            except Exception:
                continue
        target = best_match
        if not target:
            return False
        try:
            content_nodes = target.find_elements(By.CSS_SELECTOR, ".ant-select-item-option-content")
            click_target = content_nodes[0] if content_nodes else target
            click_target.click()
        except Exception:
            self.driver.execute_script(
                """
                const element = arguments[0];
                ['mouseenter', 'mouseover', 'mousemove', 'mousedown', 'mouseup', 'click'].forEach((type) => {
                  element.dispatchEvent(new MouseEvent(type, {
                    bubbles: true,
                    cancelable: true,
                    view: window,
                    buttons: 1,
                  }));
                });
                """,
                click_target if "click_target" in locals() else target,
            )
        return True

    def _click_first_visible_dropdown_option_native(self) -> bool:
        if not self.driver:
            raise RuntimeError("Browser has not been opened.")
        candidates = self.driver.find_elements(
            By.CSS_SELECTOR,
            ".ant-select-dropdown .ant-select-item-option, .next-overlay-wrapper .next-menu-item, .next-overlay-wrapper li",
        )
        for candidate in candidates:
            try:
                if not candidate.is_displayed():
                    continue
                try:
                    candidate.click()
                except Exception:
                    self.driver.execute_script("arguments[0].click();", candidate)
                return True
            except Exception:
                continue
        return False

    def _read_ant_select_selected_text(self, trigger: WebElement) -> str:
        if not self.driver:
            raise RuntimeError("Browser has not been opened.")
        return str(
            self.driver.execute_script(
                """
                const trigger = arguments[0];
                if (!trigger) return '';
                const root = trigger.closest('.ant-select') || trigger;
                const selectedNode = Array.from(root.querySelectorAll('.ant-select-selection-item')).find(isVisible) || null;
                if (selectedNode) {
                  return (selectedNode.innerText || selectedNode.textContent || '').trim();
                }
                return '';
                """,
                trigger,
            )
            or ""
        ).strip()

    def _read_buyer_protection_row_selected_text(self) -> str:
        if not self.driver:
            raise RuntimeError("Browser has not been opened.")
        selected_text = str(
            self.driver.execute_script(
                """
                const isVisible = (node) => {
                  if (!node) return false;
                  const style = window.getComputedStyle(node);
                  const rect = node.getBoundingClientRect();
                  return style.display !== 'none' && style.visibility !== 'hidden' && rect.width > 0 && rect.height > 0;
                };
                const rows = Array.from(
                  document.querySelectorAll('#guid-buyerProtection .ant-table-tbody tr')
                ).filter(isVisible);
                const firstRow = rows[0] || null;
                if (!firstRow) {
                  return '';
                }
                const selectedNodes = Array.from(
                  firstRow.querySelectorAll('td:nth-child(3) .ant-select-selection-item')
                ).filter(isVisible);
                const selected = selectedNodes[0] || null;
                return selected ? (selected.innerText || selected.textContent || '').trim() : '';
                """
            )
            or ""
        ).strip()
        if selected_text in {"\u8bf7\u9009\u62e9", "Please select"}:
            return ""
        return selected_text

    def _open_buyer_protection_row_dropdown(self) -> str:
        if not self.driver:
            raise RuntimeError("Browser has not been opened.")
        dropdown_id = self.driver.execute_script(
            """
            const isVisible = (node) => {
              if (!node) return false;
              const style = window.getComputedStyle(node);
              const rect = node.getBoundingClientRect();
              return style.display !== 'none' && style.visibility !== 'hidden' && rect.width > 0 && rect.height > 0;
            };
            const dispatchMouseSequence = (element) => {
              if (!element) return;
              ['mouseenter', 'mouseover', 'mousemove', 'mousedown', 'mouseup', 'click'].forEach((type) => {
                element.dispatchEvent(new MouseEvent(type, {
                  bubbles: true,
                  cancelable: true,
                  view: window,
                  buttons: 1,
                }));
              });
            };
            const rows = Array.from(
              document.querySelectorAll('#guid-buyerProtection .ant-table-tbody tr')
            ).filter(isVisible);
            const firstRow = rows[0] || null;
            if (!firstRow) {
              return '';
            }
            const visibleSelects = Array.from(
              firstRow.querySelectorAll('td:nth-child(3) .ant-select')
            ).filter(isVisible);
            const root = visibleSelects[0] || null;
            const selector = root ? root.querySelector('.ant-select-selector') : null;
            const input = root
              ? root.querySelector('.ant-select-selection-search input[role="combobox"]')
              : null;
            const anchor = selector || root || input;
            if (!anchor) {
              return '';
            }
            anchor.scrollIntoView({ block: 'center' });
            dispatchMouseSequence(root);
            dispatchMouseSequence(selector);
            dispatchMouseSequence(input);
            if (input) {
              input.focus();
              input.dispatchEvent(new KeyboardEvent('keydown', {
                key: 'ArrowDown',
                bubbles: true,
                cancelable: true,
              }));
            }
            return String((root && root.getAttribute('aria-controls')) || '').trim();
            """
        )
        return str(dropdown_id or "").strip()

    def _click_ant_dropdown_option_via_script(
        self,
        value: str,
        *,
        dropdown_id: str = "",
        select_first_option_if_unmatched: bool = False,
    ) -> bool:
        if not self.driver:
            raise RuntimeError("Browser has not been opened.")
        return bool(
            self.driver.execute_script(
                """
                const expected = String(arguments[0] || '').trim();
                const dropdownId = String(arguments[1] || '').trim();
                const selectFirst = Boolean(arguments[2]);
                const norm = (value) => String(value || '').replace(/\\s+/g, ' ').trim();
                const compact = (value) => norm(value).replace(/\\s+/g, '');
                const expectedCompact = compact(expected);
                const rankOption = (text) => {
                  const normalized = norm(text);
                  if (!normalized) return null;
                  if (normalized === expected) return 0;
                  if (expected && (normalized.includes(expected) || expected.includes(normalized))) return 1;
                  const normalizedCompact = compact(normalized);
                  if (
                    expectedCompact &&
                    (
                      normalizedCompact === expectedCompact ||
                      normalizedCompact.includes(expectedCompact) ||
                      expectedCompact.includes(normalizedCompact)
                    )
                  ) {
                    return 2;
                  }
                  return null;
                };
                const isVisible = (node) => {
                  if (!node) return false;
                  const style = window.getComputedStyle(node);
                  const rect = node.getBoundingClientRect();
                  return (
                    style.display !== 'none' &&
                    style.visibility !== 'hidden' &&
                    rect.width > 0 &&
                    rect.height > 0
                  );
                };
                const dispatchMouseSequence = (element) => {
                  if (!element) return;
                  ['mouseenter', 'mouseover', 'mousemove', 'mousedown', 'mouseup', 'click'].forEach((type) => {
                    element.dispatchEvent(new MouseEvent(type, {
                      bubbles: true,
                      cancelable: true,
                      view: window,
                      buttons: 1,
                    }));
                  });
                };
                const dropdownRoots = Array.from(document.querySelectorAll('.ant-select-dropdown')).filter(isVisible);
                const explicitRoot = dropdownId ? document.getElementById(dropdownId) : null;
                const activeDropdown =
                  (explicitRoot && isVisible(explicitRoot) ? explicitRoot : null) ||
                  dropdownRoots[0] ||
                  null;
                if (!activeDropdown) {
                  return false;
                }
                const options = Array.from(activeDropdown.querySelectorAll('.ant-select-item-option')).filter(isVisible);
                if (options.length === 0) {
                  return false;
                }
                let bestOption = null;
                let bestRank = null;
                options.forEach((node) => {
                  const rank = rankOption(node.innerText || node.textContent || '');
                  if (rank == null) {
                    return;
                  }
                  if (bestRank == null || rank < bestRank) {
                    bestRank = rank;
                    bestOption = node;
                  }
                });
                const targetOption = bestOption || (selectFirst ? options[0] : null);
                if (!targetOption) {
                  return false;
                }
                const targetContent = targetOption.querySelector('.ant-select-item-option-content') || targetOption;
                dispatchMouseSequence(targetContent);
                return true;
                """,
                value,
                dropdown_id,
                select_first_option_if_unmatched,
            )
        )

    def _select_buyer_protection_ship_time_from_row(
        self,
        value: str,
        *,
        select_first_option_if_unmatched: bool,
        force_reselect: bool = False,
        attempt_count: int = 3,
        open_wait_seconds: float = 0.4,
        select_wait_seconds: float = 0.5,
    ) -> str:
        if not self.driver:
            raise RuntimeError("Browser has not been opened.")
        expected_value = str(value).strip()
        selected_text = self._read_buyer_protection_row_selected_text()
        if (not force_reselect) and selected_text and (
            selected_text == expected_value
            or (expected_value and expected_value in selected_text)
            or (selected_text and selected_text in expected_value)
        ):
            return selected_text

        for _ in range(max(1, int(attempt_count))):
            dropdown_id = self._open_buyer_protection_row_dropdown()
            self._pause(max(0.05, float(open_wait_seconds)))
            try:
                dropdown_options_selector = (
                    f"#{dropdown_id} .ant-select-item-option"
                    if dropdown_id
                    else ".ant-select-dropdown:not(.ant-select-dropdown-hidden) .ant-select-item-option"
                )
                WebDriverWait(self.driver, max(0.6, float(open_wait_seconds) + 0.8)).until(
                    lambda driver: any(
                        item.is_displayed()
                        for item in driver.find_elements(By.CSS_SELECTOR, dropdown_options_selector)
                    )
                )
            except Exception:
                pass

            matched = self._click_visible_dropdown_option_native(expected_value, dropdown_id=dropdown_id)
            if not matched:
                matched = self._click_ant_dropdown_option_via_script(
                    expected_value,
                    dropdown_id=dropdown_id,
                    select_first_option_if_unmatched=select_first_option_if_unmatched,
                )
            if not matched and select_first_option_if_unmatched:
                matched = self._click_first_visible_dropdown_option_native() or self._click_ant_dropdown_option_via_script(
                    expected_value,
                    dropdown_id=dropdown_id,
                    select_first_option_if_unmatched=True,
                )

            self._pause(max(0.05, float(select_wait_seconds)))
            selected_text = self._read_buyer_protection_row_selected_text()
            if selected_text:
                return selected_text
        return selected_text

    def _select_ant_option(self, selector: dict[str, str], step: dict[str, Any], value: str) -> None:
        if not self.driver:
            raise RuntimeError("Browser has not been opened.")
        step_name = str(step.get("name", "")).strip()
        if step_name == "buyer_protection_ship_time":
            selected_text = self._select_buyer_protection_ship_time_from_row(
                str(value).strip(),
                select_first_option_if_unmatched=bool(step.get("select_first_option_if_unmatched", False)),
                force_reselect=bool(step.get("force_reselect", False)),
                attempt_count=max(2, int(step.get("buyer_protection_row_retry_count", 3) or 3)),
                open_wait_seconds=float(step.get("open_wait_seconds", 0.4)),
                select_wait_seconds=float(step.get("select_wait_seconds", 0.5)),
            )
            if not selected_text and step.get("required", False):
                raise ValueError(
                    f"Failed to match ant_select option '{value}' for step '{step.get('name')}' (buyer protection row)."
                )
            self._pause(float(step.get("select_wait_seconds", 0.5)))
            return

        trigger = self._wait_for_element(selector, clickable=True)
        self.driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", trigger)
        try:
            trigger.click()
        except Exception:
            self.driver.execute_script("arguments[0].click();", trigger)
        self._pause(float(step.get("open_wait_seconds", 0.4)))

        matched = self._click_visible_dropdown_option_native(str(value).strip())
        if not matched and step.get("select_first_option_if_unmatched", False):
            matched = self._click_first_visible_dropdown_option_native()
        selected_text = self._read_ant_select_selected_text(trigger)
        if not matched and step.get("required", False):
            raise ValueError(f"Failed to match ant_select option '{value}' for step '{step.get('name')}'.")
        if not selected_text and step.get("required", False):
            raise ValueError(f"ant_select value did not persist for required step '{step.get('name')}'.")
        self._pause(float(step.get("select_wait_seconds", 0.5)))

    def _fill_category_prop_defaults(self, step: dict[str, Any], context: dict[str, Any]) -> None:
        rules = self._resolve_profile_rules(step, context)
        for rule in rules:
            self._run_with_stale_retry(
                lambda current_rule=rule: self._apply_category_prop_rule(current_rule, context),
                retries=int(rule.get("stale_retry_count", step.get("stale_retry_count", 2))),
                wait_seconds=float(rule.get("stale_retry_wait_seconds", step.get("stale_retry_wait_seconds", 0.5))),
            )

    def _fill_spec_values(self, step: dict[str, Any], context: dict[str, Any]) -> None:
        rules = self._resolve_profile_rules(step, context)
        for rule in rules:
            self._run_with_stale_retry(
                lambda current_rule=rule: self._apply_spec_rule(current_rule, context),
                retries=int(rule.get("stale_retry_count", step.get("stale_retry_count", 2))),
                wait_seconds=float(rule.get("stale_retry_wait_seconds", step.get("stale_retry_wait_seconds", 0.5))),
            )

    def _apply_category_prop_rule(self, rule: dict[str, Any], context: dict[str, Any]) -> None:
        label = str(rule.get("label", "")).strip()
        if not label:
            return
        required = bool(rule.get("required", False))
        try:
            container = self._wait_for_category_prop_container(label)
        except TimeoutException:
            if required:
                raise
            print(f"[WARN] Skip category prop rule '{label}' because the field is not present on current page.")
            return
        input_element = self._find_first_enabled_input(container)
        if not input_element:
            if required:
                raise ValueError(f"Category prop '{label}' has no enabled input.")
            print(f"[WARN] Skip category prop rule '{label}' because no enabled input is available.")
            return
        value = self._resolve_profile_rule_value(rule, context)
        action_type = str(rule.get("type", "combobox")).strip().lower()
        if action_type == "input":
            if value:
                self._fill_text_field(input_element, value, clear=True)
            return
        if action_type == "multiselect":
            raw_values = self._split_profile_values(value)
            if raw_values:
                for item in raw_values:
                    self._fill_combobox_input(
                        input_element,
                        {
                            **rule,
                            "select_first_option_if_unmatched": bool(
                                rule.get("select_first_option_if_unmatched", True)
                            ),
                        },
                        item,
                    )
            elif rule.get("select_first_if_empty", True):
                self._fill_combobox_input(
                    input_element,
                    {
                        **rule,
                        "select_first_option_if_unmatched": True,
                    },
                    "",
                )
            return
        if value:
            self._fill_combobox_input(
                input_element,
                {
                    **rule,
                    "select_first_option_if_unmatched": bool(
                        rule.get("select_first_option_if_unmatched", True)
                    ),
                },
                value,
            )
        elif rule.get("select_first_if_empty", True):
            self._fill_combobox_input(
                input_element,
                {
                    **rule,
                    "select_first_option_if_unmatched": True,
                },
                "",
            )

    def _apply_spec_rule(self, rule: dict[str, Any], context: dict[str, Any]) -> None:
        label = str(rule.get("label", "")).strip()
        value = self._resolve_profile_rule_value(rule, context)
        if not label or not value:
            return
        required = bool(rule.get("required", False))
        try:
            input_element = self._wait_for_spec_input(label)
        except TimeoutException:
            if required:
                raise
            print(f"[WARN] Skip spec rule '{label}' because the spec input is not present on current page.")
            return
        self._fill_text_field(input_element, value, clear=True)
        input_element.send_keys(Keys.ENTER)
        self._pause(float(rule.get("select_wait_seconds", 0.4)))

    def _resolve_profile_rules(self, step: dict[str, Any], context: dict[str, Any]) -> list[dict[str, Any]]:
        profiles = step.get("profiles", {})
        category_key = str(context.get("platform_category", "")).strip()
        profile = profiles.get(category_key) or profiles.get("*") or {}
        rules = profile.get("rules", [])
        return [dict(item) for item in rules if isinstance(item, dict)]

    def _resolve_profile_rule_value(self, rule: dict[str, Any], context: dict[str, Any]) -> str:
        if "value" in rule:
            return str(rule.get("value", "")).strip()
        source = str(rule.get("source", "")).strip()
        if source:
            value = context.get(source, "")
            normalized = str(value).strip()
            if normalized:
                return normalized
        return str(rule.get("default_value", "")).strip()

    def _split_profile_values(self, value: str) -> list[str]:
        raw_value = str(value).strip()
        if not raw_value:
            return []
        return [item.strip() for item in re.split(r"\s*\|\s*", raw_value) if item.strip()]

    def _wait_for_category_prop_container(self, label: str) -> WebElement:
        selector = {
            "by": "xpath",
            "value": (
                f"//div[@id='guid-catProp']//div[contains(@class,'decorate-cat-prop')]"
                f"[.//span[contains(@class,'cat-prop-label')][contains(normalize-space(.), {self._xpath_literal(label)})]]"
            ),
        }
        return self._wait_for_element(selector)

    def _wait_for_spec_input(self, label: str) -> WebElement:
        selector = {
            "by": "xpath",
            "value": (
                f"//div[@id='guid-saleProp']//div[contains(@class,'module-spec-decorator')]"
                f"[.//div[contains(@class,'nak-label')][contains(normalize-space(.), {self._xpath_literal(label)})]]"
                "//input[1]"
            ),
        }
        return self._wait_for_element(selector)

    def _find_first_enabled_input(self, container: WebElement) -> WebElement | None:
        candidates = container.find_elements(By.CSS_SELECTOR, "input, textarea")
        for element in candidates:
            if not element.is_displayed():
                continue
            if element.get_attribute("disabled"):
                continue
            return element
        return None

    def _xpath_literal(self, value: str) -> str:
        if "'" not in value:
            return f"'{value}'"
        if '"' not in value:
            return f'"{value}"'
        parts = value.split("'")
        return "concat(" + ", \"'\", ".join(f"'{part}'" for part in parts) + ")"

    def _run_with_stale_retry(
        self,
        callback: Any,
        *,
        retries: int = 2,
        wait_seconds: float = 0.5,
    ) -> Any:
        attempts = max(retries, 0) + 1
        last_error: Exception | None = None
        for attempt in range(attempts):
            try:
                return callback()
            except Exception as exc:
                if not self._is_stale_driver_error(exc):
                    raise
                last_error = exc
                if attempt >= attempts - 1:
                    raise
                self._pause(wait_seconds)
        if last_error:
            raise last_error
        return None

    def _is_stale_driver_error(self, exc: Exception) -> bool:
        if isinstance(exc, StaleElementReferenceException):
            return True
        if isinstance(exc, WebDriverException):
            return "stale element reference" in str(exc).lower()
        return False

    def _run_picker_upload(
        self,
        step: dict[str, Any],
        selector: dict[str, str],
        values: list[str],
        context: dict[str, Any],
    ) -> None:
        if not self.driver:
            raise RuntimeError("Browser has not been opened.")

        dialog_selector = self._resolve_selector(
            step.get("dialog_selector", {"by": "css", "value": "div.ibank-picker-dialog"}),
            context,
        )
        cleanup_selector = self._resolve_selector(
            step.get(
                "cleanup_selector",
                {"by": "css", "value": "div.ibank-picker-dialog, div.ui-dialog, div.ui-dialog-overlay"},
            ),
            context,
        )
        frame_selector = self._resolve_selector(
            step.get("frame_selector", {"by": "css", "value": "iframe.picker-frame"}),
            context,
        )
        upload_tab_selector = self._resolve_selector(
            step.get("upload_tab_selector", {"by": "css", "value": "li.tab-upload"}),
            context,
        )
        album_select_selector = self._resolve_selector(
            step.get("album_select_selector", {"by": "css", "value": "select"}),
            context,
        )
        file_input_selector = self._resolve_selector(
            step.get("file_input_selector", {"by": "css", "value": "input[type='file'][accept*='image']"}),
            context,
        )
        insert_selector = self._resolve_selector(
            step.get("insert_selector", {"by": "css", "value": "a.button.submit"}),
            context,
        )
        insert_count_selector = self._resolve_selector(
            step.get("insert_count_selector", {"by": "css", "value": ".insert-header em"}),
            context,
        )

        if step.get("upload_via_react_bridge", False):
            self._upload_images_via_primary_picture_bridge(step, selector, values, context)
            return

        if not step.get("skip_open", False):
            self._remove_elements(cleanup_selector)
            opener = self._wait_for_element(selector, clickable=True)
            self._trigger_picker_opener(opener)

        dialog_element = self._wait_for_dialog(dialog_selector)
        self._pause(float(step.get("dialog_wait_seconds", 0.5)))
        frame_element = self._wait_for_element(frame_selector)
        self.driver.switch_to.frame(frame_element)
        try:
            if self._selector_is_configured(upload_tab_selector):
                try:
                    upload_tab = self._wait_for_element(upload_tab_selector, clickable=True)
                    self.driver.execute_script("arguments[0].click();", upload_tab)
                    self._pause(float(step.get("upload_tab_wait_seconds", 0.5)))
                except TimeoutException:
                    pass
                self._select_picker_album(album_select_selector)

            self._install_picker_upload_trace()
            file_input = self._wait_for_element(file_input_selector)
            payload = "\n".join(values) if step.get("multiple", len(values) > 1) else values[0]
            file_input.send_keys(payload)

            wait = WebDriverWait(self.driver, float(step.get("upload_timeout_seconds", 30)))
            try:
                wait.until(
                    lambda driver: self._picker_insert_count(insert_count_selector) >= min(
                        len(values),
                        int(step.get("max_insert_count", len(values))),
                    )
                )
            except TimeoutException:
                self._pause(float(step.get("upload_settle_seconds", 2)))

            uploaded_urls = self._collect_picker_upload_urls()
            if uploaded_urls:
                context[f"{step.get('name', 'picker_upload')}_uploaded_urls"] = uploaded_urls
                if step.get("name") == "main_image":
                    context["main_image_uploaded_urls"] = uploaded_urls
                elif step.get("name") == "detail_images":
                    context["detail_images_uploaded_urls"] = uploaded_urls

            if not step.get("capture_uploaded_urls_only", False):
                insert_button = self._wait_for_element(insert_selector, clickable=True)
                try:
                    insert_button.click()
                except Exception:
                    self.driver.execute_script("arguments[0].click();", insert_button)
        finally:
            self.driver.switch_to.default_content()

        self._pause(float(step.get("after_insert_wait_seconds", 1.5)))
        self._remove_elements(cleanup_selector)
        self._pause(float(step.get("after_cleanup_wait_seconds", 0.3)))

    def _install_picker_upload_trace(self) -> None:
        if not self.driver:
            raise RuntimeError("Browser has not been opened.")

        self.driver.execute_script(
            """
            window.__codexPickerUploadRecords = [];
            if (window.__codexPickerUploadTraceInstalled) {
              return;
            }
            window.__codexPickerUploadTraceInstalled = true;

            const isPickerUploadUrl = (rawUrl) => {
              const text = String(rawUrl || '').trim();
              if (!text) {
                return false;
              }
              try {
                const parsed = new URL(text, window.location.href);
                return /picman\\.1688\\.com$/i.test(String(parsed.hostname || '')) &&
                  /\\/album\\/ajax\\/image_upload_ajax\\.json$/i.test(String(parsed.pathname || ''));
              } catch (error) {
                return /image_upload_ajax\\.json/i.test(text);
              }
            };

            const pushRecord = (record) => {
              window.__codexPickerUploadRecords.push(record);
            };

            const originalOpen = XMLHttpRequest.prototype.open;
            const originalSend = XMLHttpRequest.prototype.send;
            XMLHttpRequest.prototype.open = function(method, url) {
              this.__codexPickerMeta = { method, url };
              return originalOpen.apply(this, arguments);
            };
            XMLHttpRequest.prototype.send = function(body) {
              const meta = this.__codexPickerMeta || {};
              const url = String(meta.url || '');
              if (!isPickerUploadUrl(url)) {
                return originalSend.call(this, body);
              }

              const record = {
                transport: 'xhr',
                method: String(meta.method || ''),
                url,
              };
              this.addEventListener('loadend', function() {
                record.status = this.status;
                record.responseText = String(this.responseText || '');
                try {
                  record.responseJson = JSON.parse(record.responseText);
                } catch (error) {
                  record.responseJson = null;
                }
              });
              pushRecord(record);
              return originalSend.call(this, body);
            };

            if (typeof window.fetch === 'function') {
              const originalFetch = window.fetch.bind(window);
              window.fetch = function() {
                const args = Array.from(arguments);
                const input = args[0];
                const url = typeof input === 'string' ? input : String((input && input.url) || '');
                if (!isPickerUploadUrl(url)) {
                  return originalFetch.apply(window, args);
                }

                const requestInit = args[1] || {};
                const record = {
                  transport: 'fetch',
                  method: String(requestInit.method || 'GET'),
                  url,
                };
                pushRecord(record);
                return originalFetch.apply(window, args).then(async (response) => {
                  try {
                    const clone = response.clone();
                    record.status = clone.status;
                    record.responseText = await clone.text();
                    try {
                      record.responseJson = JSON.parse(record.responseText);
                    } catch (error) {
                      record.responseJson = null;
                    }
                  } catch (error) {
                    record.responseText = String(error || '');
                    record.responseJson = null;
                  }
                  return response;
                });
              };
            }
            """
        )

    def _collect_picker_upload_urls(self) -> list[str]:
        if not self.driver:
            raise RuntimeError("Browser has not been opened.")

        records = self.driver.execute_script(
            "return (window.__codexPickerUploadRecords || []).map((item) => ({...item}));"
        )
        uploaded_urls: list[str] = []
        for record in records or []:
            response_json = record.get("responseJson")
            if not isinstance(response_json, dict):
                continue
            for url in response_json.get("imgUrls", []) or []:
                normalized = str(url or "").strip()
                if normalized and normalized not in uploaded_urls:
                    uploaded_urls.append(normalized)
        return uploaded_urls

    def _run_picker_url_upload(
        self,
        step: dict[str, Any],
        selector: dict[str, str],
        values: list[str],
        context: dict[str, Any],
    ) -> None:
        if not self.driver:
            raise RuntimeError("Browser has not been opened.")

        dialog_selector = self._resolve_selector(
            step.get("dialog_selector", {"by": "css", "value": "div.ibank-picker-dialog"}),
            context,
        )
        cleanup_selector = self._resolve_selector(
            step.get(
                "cleanup_selector",
                {"by": "css", "value": "div.ibank-picker-dialog, div.ui-dialog, div.ui-dialog-overlay"},
            ),
            context,
        )
        frame_selector = self._resolve_selector(
            step.get("frame_selector", {"by": "css", "value": "iframe.picker-frame"}),
            context,
        )
        online_tab_selector = self._resolve_selector(
            step.get("online_tab_selector", {"by": "css", "value": "li.tab-online"}),
            context,
        )
        url_input_selector = self._resolve_selector(
            step.get("url_input_selector", {"by": "css", "value": "div.online-adder input[type='text']"}),
            context,
        )
        add_selector = self._resolve_selector(
            step.get("add_selector", {"by": "css", "value": "div.online-adder a.button"}),
            context,
        )
        insert_selector = self._resolve_selector(
            step.get("insert_selector", {"by": "css", "value": "a.button.submit"}),
            context,
        )
        insert_count_selector = self._resolve_selector(
            step.get("insert_count_selector", {"by": "css", "value": ".insert-header"}),
            context,
        )

        self._remove_elements(cleanup_selector)
        opener = self._wait_for_element(selector, clickable=True)
        self._trigger_picker_opener(opener)

        self._wait_for_dialog(dialog_selector)
        self._pause(float(step.get("dialog_wait_seconds", 0.5)))
        frame_element = self._wait_for_element(frame_selector)
        self.driver.switch_to.frame(frame_element)
        try:
            if self._selector_is_configured(online_tab_selector):
                self._click_with_javascript(online_tab_selector)
                self._pause(float(step.get("online_tab_wait_seconds", 0.4)))

            for index, value in enumerate(values, start=1):
                next_value = str(value).strip()
                if not next_value.lower().startswith(("http://", "https://")):
                    raise ValueError(f"picker_url_upload only supports remote URLs, got: {value}")
                url_input = self._wait_for_element(url_input_selector)
                self._fill_text_field(url_input, next_value, clear=True)
                self._click_with_javascript(add_selector)
                self._wait_for_picker_insert_count(
                    insert_count_selector,
                    minimum_count=index,
                    timeout_seconds=float(step.get("upload_timeout_seconds", 20)),
                )

            insert_button = self._wait_for_element(insert_selector, clickable=True)
            try:
                insert_button.click()
            except Exception:
                self.driver.execute_script("arguments[0].click();", insert_button)
        finally:
            self.driver.switch_to.default_content()

        self._pause(float(step.get("after_insert_wait_seconds", 1.5)))
        self._remove_elements(cleanup_selector)
        self._pause(float(step.get("after_cleanup_wait_seconds", 0.3)))

    def _wait_for_picker_insert_count(
        self,
        selector: dict[str, str],
        *,
        minimum_count: int,
        timeout_seconds: float,
    ) -> None:
        if not self.driver:
            raise RuntimeError("Browser has not been opened.")
        wait = WebDriverWait(self.driver, timeout_seconds)
        wait.until(lambda _driver: self._picker_insert_count(selector) >= minimum_count)

    def _insert_tinymce_images(
        self,
        step: dict[str, Any],
        selector: dict[str, str],
        values: list[str],
        context: dict[str, Any],
    ) -> None:
        if not self.driver:
            raise RuntimeError("Browser has not been opened.")

        self._ensure_old_tinymce_mode(step)
        if step.get("upload_via_react_bridge", False):
            uploaded_urls = self._upload_images_via_primary_picture_bridge(step, selector, values, {})
            html_value = self._build_tinymce_image_html(uploaded_urls)
            if not html_value:
                return
            self._write_tinymce_content(
                {
                    **step,
                    "append_mode": step.get("append_mode", "append"),
                },
                selector,
                html_value,
            )
            return

        editor_id = self._resolve_tinymce_editor_id(step, selector)
        executed = self.driver.execute_script(
            """
            const editorId = arguments[0];
            const editor = window.tinyMCE && window.tinyMCE.get && window.tinyMCE.get(editorId);
            if (!editor) {
              return false;
            }
            editor.execCommand('mceImage');
            return true;
            """,
            editor_id,
        )
        if not executed:
            fallback_selector = self._resolve_selector(step.get("fallback_picker_selector", {}), context)
            if not self._selector_is_configured(fallback_selector):
                raise ValueError(f"TinyMCE editor '{editor_id}' is not available.")
            self._run_picker_upload(
                {
                    **step,
                    "name": step.get("name", "detail_images"),
                    "capture_uploaded_urls_only": True,
                },
                fallback_selector,
                values,
                context,
            )
            uploaded_urls = [str(item).strip() for item in context.get("detail_images_uploaded_urls", []) if str(item).strip()]
            html_value = self._build_tinymce_image_html(uploaded_urls)
            if not html_value:
                raise ValueError("Detail image upload completed without any remote image URLs.")
            self._write_tinymce_content(
                {
                    **step,
                    "append_mode": step.get("append_mode", "append"),
                },
                selector,
                html_value,
            )
            return

        self._run_picker_upload(
            {
                **step,
                "skip_open": True,
                "dialog_selector": step.get("dialog_selector", {"by": "css", "value": "div.ibank-picker-dialog"}),
                "frame_selector": step.get("frame_selector", {"by": "css", "value": "iframe.picker-frame"}),
                "upload_tab_selector": step.get("upload_tab_selector", {"by": "css", "value": "li.tab-upload"}),
                "file_input_selector": step.get(
                    "file_input_selector",
                    {"by": "css", "value": "input[type='file'][accept*='image']"},
                ),
                "insert_selector": step.get("insert_selector", {"by": "css", "value": "a.button.submit"}),
                "insert_count_selector": step.get(
                    "insert_count_selector",
                    {"by": "css", "value": ".insert-header em"},
                ),
            },
            {"by": "css", "value": "body"},
            values,
            {},
        )

    def _write_tinymce_content(
        self,
        step: dict[str, Any],
        selector: dict[str, str],
        value: str,
    ) -> None:
        if not self.driver:
            raise RuntimeError("Browser has not been opened.")

        self._ensure_old_tinymce_mode(step)
        editor_id = self._resolve_tinymce_editor_id(step, selector)
        html_value = self._build_tinymce_html(value)
        append_mode = str(step.get("append_mode", "replace")).strip().lower()
        result = self.driver.execute_script(
            """
            const editorId = arguments[0];
            const htmlValue = arguments[1];
            const appendMode = arguments[2];
            const editor = window.tinyMCE && window.tinyMCE.get && window.tinyMCE.get(editorId);
            if (!editor) {
              return {ok: false, reason: 'editor_not_found'};
            }
            const current = editor.getContent() || '';
            let nextValue = htmlValue;
            if (appendMode === 'append' && current) {
              nextValue = current + htmlValue;
            } else if (appendMode === 'prepend' && current) {
              nextValue = htmlValue + current;
            }
            editor.setContent(nextValue);
            editor.save();
            return {
              ok: true,
              textareaValue: document.querySelector('#' + editorId) ? document.querySelector('#' + editorId).value : '',
            };
            """,
            editor_id,
            html_value,
            append_mode,
        )
        if not result.get("ok"):
            raise ValueError(f"Failed to write TinyMCE content for editor '{editor_id}'.")
        self.driver.execute_script(
            """
            const editorId = arguments[0];
            const textarea = document.querySelector('#' + editorId);
            if (!textarea) {
              return false;
            }
            textarea.dispatchEvent(new Event('input', {bubbles: true}));
            textarea.dispatchEvent(new Event('change', {bubbles: true}));
            textarea.dispatchEvent(new Event('blur', {bubbles: true}));
            textarea.dispatchEvent(new KeyboardEvent('keyup', {bubbles: true, key: 'Enter'}));
            return true;
            """,
            editor_id,
        )
        self._pause(float(step.get("editor_wait_seconds", 0.5)))

    def _ensure_old_tinymce_mode(self, step: dict[str, Any]) -> None:
        if not self.driver:
            raise RuntimeError("Browser has not been opened.")

        toggle_selector = step.get(
            "mode_toggle_selector",
            {"by": "css", "value": ".editor-type-toggle-btn button"},
        )
        resolved_toggle_selector = self._resolve_selector(toggle_selector, {})
        if not self._selector_is_configured(resolved_toggle_selector):
            return

        toggle_buttons = self.driver.find_elements(
            BY_MAPPING.get(resolved_toggle_selector.get("by", "css"), By.CSS_SELECTOR),
            resolved_toggle_selector.get("value", ""),
        )
        if not toggle_buttons:
            return

        if self._tinymce_ready(step):
            return

        toggle_button = toggle_buttons[0]
        self.driver.execute_script("arguments[0].click();", toggle_button)
        self._pause(float(step.get("toggle_wait_seconds", 0.5)))
        confirm_buttons = self.driver.find_elements(By.CSS_SELECTOR, ".ant-modal-root button")
        if confirm_buttons:
            self.driver.execute_script("arguments[0].click();", confirm_buttons[-1])
        self._pause(float(step.get("confirm_wait_seconds", 1.5)))

        if not self._tinymce_ready(step):
            raise TimeoutException("TinyMCE editor did not become ready after switching to old mode.")
        return

        toggle_button = toggle_buttons[0]
        toggle_text = " ".join(toggle_button.text.split())
        if "返回到旧版" in toggle_text:
            self.driver.execute_script("arguments[0].click();", toggle_button)
            self._pause(float(step.get("toggle_wait_seconds", 0.5)))
            confirm_buttons = self.driver.find_elements(By.CSS_SELECTOR, ".ant-modal-root button")
            if confirm_buttons:
                self.driver.execute_script("arguments[0].click();", confirm_buttons[-1])
            self._pause(float(step.get("confirm_wait_seconds", 1.5)))
        if "返回到旧版" in toggle_text:
            self.driver.execute_script("arguments[0].click();", toggle_button)
            self._pause(float(step.get("toggle_wait_seconds", 0.5)))
            confirm_buttons = self.driver.find_elements(By.CSS_SELECTOR, ".ant-modal-root button")
            if confirm_buttons:
                self.driver.execute_script("arguments[0].click();", confirm_buttons[-1])
            self._pause(float(step.get("confirm_wait_seconds", 1.5)))

        if not self._tinymce_ready(step):
            raise TimeoutException("TinyMCE editor did not become ready after switching to old mode.")

    def _tinymce_ready(self, step: dict[str, Any]) -> bool:
        if not self.driver:
            raise RuntimeError("Browser has not been opened.")

        frame_selector = step.get(
            "editor_frame_selector",
            {"by": "css", "value": "#tinyMCE-0_ifr"},
        )
        resolved_frame_selector = self._resolve_selector(frame_selector, {})
        wait = WebDriverWait(self.driver, float(step.get("editor_timeout_seconds", 15)))
        try:
            wait.until(
                lambda driver: driver.execute_script(
                    """
                    const selector = arguments[0];
                    const node = document.querySelector(selector);
                    if (!node) return false;
                    const rect = node.getBoundingClientRect();
                    const style = window.getComputedStyle(node);
                    return style.display !== 'none' && style.visibility !== 'hidden' && rect.width > 0 && rect.height > 0;
                    """,
                    resolved_frame_selector.get("value", ""),
                )
            )
            return True
        except TimeoutException:
            return False

    def _resolve_tinymce_editor_id(self, step: dict[str, Any], selector: dict[str, str]) -> str:
        configured = str(step.get("editor_id", "")).strip()
        if configured:
            return configured
        raw_selector = selector.get("value", "").strip()
        if raw_selector.startswith("#"):
            return raw_selector[1:]
        return "tinyMCE-0"

    def _build_tinymce_html(self, value: str) -> str:
        raw_value = str(value).strip()
        if not raw_value:
            return ""
        if bool(re.search(r"<[a-zA-Z][^>]*>", raw_value)):
            return raw_value
        paragraphs = [
            f"<p>{html.escape(line)}</p>"
            for line in raw_value.splitlines()
            if line.strip()
        ]
        return "".join(paragraphs) or f"<p>{html.escape(raw_value)}</p>"

    def _build_tinymce_image_html(self, image_urls: list[str]) -> str:
        blocks = [
            f'<p><img src="{html.escape(str(image_url).strip(), quote=True)}" /></p>'
            for image_url in image_urls
            if str(image_url).strip().lower().startswith(("http://", "https://"))
        ]
        return "".join(blocks)

    def _upload_images_via_primary_picture_bridge(
        self,
        step: dict[str, Any],
        selector: dict[str, str],
        values: list[str],
        context: dict[str, Any],
    ) -> list[str]:
        if not self.driver:
            raise RuntimeError("Browser has not been opened.")

        bridge_selector = self._resolve_selector(step.get("bridge_selector", selector), context)
        if not self._selector_is_configured(bridge_selector):
            bridge_selector = selector

        slot_index = self._resolve_bridge_slot_index(step)
        clear_after_upload = bool(step.get("clear_bridge_slot_after_upload", False))
        clear_wait_seconds = float(step.get("bridge_clear_wait_seconds", 0.2))
        upload_timeout_seconds = float(step.get("bridge_upload_timeout_seconds", 60))
        require_remote_url = bool(step.get("bridge_require_remote_url", True))

        uploaded_urls: list[str] = []
        for value in values:
            data_url = self._encode_file_as_data_url(value)
            self._clear_primary_picture_bridge_slot(bridge_selector, slot_index)
            self._pause(clear_wait_seconds)

            result = self._invoke_primary_picture_bridge_upload(
                bridge_selector,
                data_url,
                slot_index,
            )
            if not result.get("ok"):
                raise ValueError(
                    "Failed to upload image via primary picture bridge: "
                    f"{result.get('reason', 'unknown error')}"
                )

            wait = WebDriverWait(self.driver, upload_timeout_seconds)
            wait.until(
                lambda _driver: bool(
                    str(
                        self._read_primary_picture_bridge_slot(
                            bridge_selector,
                            slot_index,
                        ).get("url", "")
                    ).strip()
                    or (
                        not require_remote_url
                        and str(
                            self._read_primary_picture_bridge_slot(
                                bridge_selector,
                                slot_index,
                            ).get("key", "")
                        ).strip()
                    )
                )
            )

            slot_state = self._read_primary_picture_bridge_slot(bridge_selector, slot_index)
            remote_url = str(slot_state.get("url", "")).strip()
            remote_key = str(slot_state.get("key", "")).strip()
            if require_remote_url and not remote_url:
                raise TimeoutException("Primary picture bridge upload did not produce a remote image URL.")
            if not remote_url and not remote_key:
                raise TimeoutException("Primary picture bridge upload completed without a remote image reference.")
            if not remote_url and remote_key:
                remote_url = f"bridge-key:{remote_key}"
            uploaded_urls.append(remote_url)

            if clear_after_upload:
                self._clear_primary_picture_bridge_slot(bridge_selector, slot_index)
                self._pause(clear_wait_seconds)

        return uploaded_urls

    def _encode_file_as_data_url(self, raw_path: str) -> str:
        file_path = Path(raw_path)
        mime_type = mimetypes.guess_type(file_path.name)[0] or "image/png"
        encoded = base64.b64encode(file_path.read_bytes()).decode("ascii")
        return f"data:{mime_type};base64,{encoded}"

    def _resolve_bridge_slot_index(self, step: dict[str, Any]) -> int:
        try:
            return max(0, int(step.get("bridge_slot_index", 0)))
        except (TypeError, ValueError):
            return 0

    def _invoke_primary_picture_bridge_upload(
        self,
        selector: dict[str, str],
        data_url: str,
        slot_index: int,
    ) -> dict[str, Any]:
        return self._execute_primary_picture_bridge_script(
            selector,
            """
            const dataUrl = arguments[1];
            const slotIndex = arguments[2];
            try {
              bridge.uploadAnImageBank(null, dataUrl, slotIndex, 'imageList');
              return {ok: true};
            } catch (error) {
              return {
                ok: false,
                reason: String((error && error.message) || error),
              };
            }
            """,
            data_url,
            slot_index,
        )

    def _read_primary_picture_bridge_slot(
        self,
        selector: dict[str, str],
        slot_index: int,
    ) -> dict[str, Any]:
        return self._execute_primary_picture_bridge_script(
            selector,
            """
            const slotIndex = arguments[1];
            const value = bridge.props && bridge.props.value ? bridge.props.value : {};
            const imageList = Array.isArray(value.imageList) ? value.imageList : [];
            const slot = imageList[slotIndex] || {};
            return {
              ok: true,
              url: slot.url || '',
              key: slot.key || '',
              isAiTaskLoading: Boolean(slot.isAiTaskLoading),
            };
            """,
            slot_index,
        )

    def _clear_primary_picture_bridge_slot(
        self,
        selector: dict[str, str],
        slot_index: int,
    ) -> None:
        result = self._execute_primary_picture_bridge_script(
            selector,
            """
            const slotIndex = arguments[1];
            try {
              bridge.handleUpdateWith('imageList', function(imageList) {
                if (!Array.isArray(imageList) || !imageList[slotIndex]) {
                  return;
                }
                const current = imageList[slotIndex] || {};
                imageList[slotIndex] = {
                  ...current,
                  url: null,
                  isAiTaskLoading: false,
                };
              });
              return {ok: true};
            } catch (error) {
              return {
                ok: false,
                reason: String((error && error.message) || error),
              };
            }
            """,
            slot_index,
        )
        if not result.get("ok"):
            raise ValueError(
                "Failed to clear primary picture bridge slot: "
                f"{result.get('reason', 'unknown error')}"
            )

    def _execute_primary_picture_bridge_script(
        self,
        selector: dict[str, str],
        script_body: str,
        *args: Any,
    ) -> dict[str, Any]:
        if not self.driver:
            raise RuntimeError("Browser has not been opened.")

        target = self._wait_for_element(selector)
        return self.driver.execute_script(
            f"""
            const target = arguments[0];
            function findPrimaryPictureBridge(node) {{
              const reactKey = Object.keys(node || {{}}).find(
                (key) => key.startsWith('__reactInternalInstance') || key.startsWith('__reactFiber')
              );
              let fiber = reactKey ? node[reactKey] : null;
              while (fiber) {{
                const stateNode = fiber.stateNode;
                if (
                  stateNode &&
                  typeof stateNode.uploadAnImageBank === 'function' &&
                  typeof stateNode.handleUpdateWith === 'function'
                ) {{
                  return stateNode;
                }}
                fiber = fiber.return;
              }}
              return null;
            }}
            const bridge = findPrimaryPictureBridge(target);
            if (!bridge) {{
              return {{
                ok: false,
                reason: 'primary_picture_bridge_not_found',
              }};
            }}
            {script_body}
            """,
            target,
            *args,
        )

    def _picker_insert_count(self, selector: dict[str, str]) -> int:
        if not self.driver:
            raise RuntimeError("Browser has not been opened.")
        if not self._selector_is_configured(selector):
            return 0
        try:
            element = self.driver.find_element(
                BY_MAPPING.get(selector.get("by", "css").strip().lower(), By.CSS_SELECTOR),
                selector.get("value", "").strip(),
            )
        except Exception:
            return 0
        digits = re.findall(r"\d+", element.text or "")
        return int(digits[0]) if digits else 0

    def _select_picker_album(self, selector: dict[str, str]) -> None:
        if not self.driver:
            raise RuntimeError("Browser has not been opened.")
        if not self._selector_is_configured(selector):
            return
        try:
            album_selects = self.driver.find_elements(
                BY_MAPPING.get(selector.get("by", "css").strip().lower(), By.CSS_SELECTOR),
                selector.get("value", "").strip(),
            )
        except Exception:
            return

        album_select: WebElement | None = None
        for candidate in album_selects:
            try:
                if candidate.is_displayed():
                    album_select = candidate
                    break
            except Exception:
                continue
        if album_select is None and album_selects:
            album_select = album_selects[0]
        if album_select is None:
            return

        select_control = Select(album_select)
        viable_options = [
            option
            for option in select_control.options
            if option.is_enabled() and "已满" not in option.text
        ]
        if not viable_options:
            return

        selected_options = [option for option in select_control.options if option.is_selected()]
        if selected_options and selected_options[0] in viable_options:
            return

        target_option = viable_options[0]
        try:
            select_control.select_by_value(target_option.get_attribute("value"))
        except ElementNotInteractableException:
            if not self.driver:
                raise RuntimeError("Browser has not been opened.")
            self.driver.execute_script(
                """
                const select = arguments[0];
                const value = arguments[1];
                select.value = value;
                select.dispatchEvent(new Event('change', { bubbles: true }));
                """,
                album_select,
                target_option.get_attribute("value"),
            )
        self._pause(0.3)

    def _apply_draft_page_state_patch(
        self,
        publish_config: dict[str, Any],
        context: dict[str, Any],
    ) -> None:
        if not self.driver:
            raise RuntimeError("Browser has not been opened.")

        patch_payload = self._build_draft_page_state_patch_payload(publish_config, context)
        if (
            not patch_payload.get("catPropPatches")
            and not patch_payload.get("buyerProtectionServiceName")
            and not patch_payload.get("buyerProtectionStepTemplate")
            and not patch_payload.get("deliveryServiceIds")
        ):
            return

        result = self.driver.execute_script(
            """
            const payload = arguments[0] || {};
            const normalizeText = (value) => String(value || '').replace(/\\s+/g, ' ').trim();
            const cloneValue = (value) => JSON.parse(JSON.stringify(value || {}));
            const normalizeServiceIds = (values) =>
              Array.from(
                new Set(
                  (Array.isArray(values) ? values : [])
                    .map((item) => {
                      const text = String(item == null ? '' : item).trim();
                      if (!text) return null;
                      const asNumber = Number(text);
                      if (!Number.isFinite(asNumber)) {
                        return null;
                      }
                      const normalized = String(Math.trunc(asNumber));
                      if (normalized !== text) {
                        return null;
                      }
                      return Math.trunc(asNumber);
                    })
                    .filter((item) => item != null)
                )
              );

            const sdk = window.SellPublishSdk;
            const engine = sdk && sdk.engine;
            const core = engine && engine._engine && engine._engine._core;
            if (!engine || !engine.getJsonState || !core || !core.changeElementValue) {
              return {
                ok: false,
                reason: '1688 runtime core is unavailable',
              };
            }

            const state = engine.getJsonState() || {};
            const components = state.components || {};
            const result = {
              ok: true,
              catPropsApplied: [],
              buyerProtectionApplied: '',
              catPropMissingLabels: [],
              buyerProtectionMatchedCode: '',
              deliveryServiceApplied: [],
              buyerProtectionRowSelectAttempted: false,
              buyerProtectionRowOptionSelected: false,
              buyerProtectionRowSelectedText: '',
            };

            const catPropComponent = components.catProp || {};
            const catPropProps = catPropComponent.props || {};
            const catPropDataSource = Array.isArray(catPropProps.dataSource) ? catPropProps.dataSource : [];
            const nextCatPropValue = cloneValue(catPropProps.value || {});
            const catPropPatches = Array.isArray(payload.catPropPatches) ? payload.catPropPatches : [];

            catPropPatches.forEach((patchItem) => {
              const expectedLabel = normalizeText(patchItem.label);
              const expectedText = normalizeText(patchItem.value);
              if (!expectedLabel) {
                return;
              }

              const matchedProp =
                catPropDataSource.find((item) => normalizeText(item && item.label) === expectedLabel) ||
                catPropDataSource.find((item) => normalizeText(item && item.label).includes(expectedLabel));
              if (!matchedProp) {
                result.catPropMissingLabels.push(expectedLabel);
                return;
              }

              const options = Array.isArray(matchedProp.dataSource) ? matchedProp.dataSource : [];
              const matchedOption =
                (expectedText
                  ? (
                      options.find((item) => normalizeText(item && item.text) === expectedText) ||
                      options.find((item) => normalizeText(item && item.text).includes(expectedText)) ||
                      options.find((item) => expectedText.includes(normalizeText(item && item.text)))
                    )
                  : null) ||
                options[0] ||
                null;
              const nextValue = matchedOption
                ? {
                    value: matchedOption.value,
                    text: matchedOption.text,
                  }
                : (expectedText
                    ? {
                        value: expectedText,
                        text: expectedText,
                        custom: true,
                      }
                    : null);
              if (!nextValue) {
                result.catPropMissingLabels.push(expectedLabel);
                return;
              }
              const propKey = String(matchedProp.name || '');
              const currentValue = nextCatPropValue[propKey];
              const uiType = String((matchedProp && matchedProp.uiType) || '').trim().toLowerCase();
              const isMultiSelect = uiType === 'checkbox' || Array.isArray(currentValue);
              if (isMultiSelect) {
                const currentList = Array.isArray(currentValue) ? currentValue : [];
                const exists = currentList.some(
                  (item) => String(((item || {}).value) ?? '') === String(nextValue.value ?? '')
                );
                nextCatPropValue[propKey] = exists ? currentList : currentList.concat([nextValue]);
              } else {
                nextCatPropValue[propKey] = nextValue;
              }
              result.catPropsApplied.push({
                key: propKey,
                label: String(matchedProp.label || expectedLabel),
                value: nextValue,
              });
            });

            const ensureDefaultMultiCatProp = (propKey, preferredTexts) => {
              const matchedProp = catPropDataSource.find((item) => String((item && item.name) || '').trim() === propKey);
              if (!matchedProp) {
                return;
              }
              const currentValue = nextCatPropValue[propKey];
              if (Array.isArray(currentValue) && currentValue.length > 0) {
                return;
              }
              const options = Array.isArray(matchedProp.dataSource) ? matchedProp.dataSource : [];
              if (options.length === 0) {
                return;
              }
              const preferred = (Array.isArray(preferredTexts) ? preferredTexts : [])
                .map((item) => normalizeText(item))
                .filter(Boolean);
              const matchedOption =
                options.find((item) => preferred.includes(normalizeText(item && item.text))) || options[0];
              if (!matchedOption) {
                return;
              }
              nextCatPropValue[propKey] = [
                {
                  value: matchedOption.value,
                  text: matchedOption.text,
                },
              ];
              result.catPropsApplied.push({
                key: propKey,
                label: String(matchedProp.label || propKey),
                value: {
                  value: matchedOption.value,
                  text: matchedOption.text,
                },
              });
            };
            ensureDefaultMultiCatProp('p-2561', ['成人']);
            ensureDefaultMultiCatProp('p-1141', ['收纳储物']);

            if (result.catPropsApplied.length > 0) {
              core.changeElementValue('catProp', nextCatPropValue, { isDepth: false });
            }

            const requestedDeliveryServiceIds = normalizeServiceIds(payload.deliveryServiceIds);
            if (requestedDeliveryServiceIds.length > 0) {
              const customExtraComponent = components.customExtraService || {};
              const customExtraProps = customExtraComponent.props || {};
              const customExtraValue = cloneValue(customExtraProps.value || {});
              const nextViewModelMap =
                customExtraValue.viewModelMap && typeof customExtraValue.viewModelMap === 'object'
                  ? { ...customExtraValue.viewModelMap }
                  : {};
              const deliveryMapKey =
                Object.keys(nextViewModelMap).find((key) => normalizeText(key).includes('配送')) ||
                '配送服务';
              nextViewModelMap[deliveryMapKey] = requestedDeliveryServiceIds[0];
              customExtraValue.viewModelMap = nextViewModelMap;
              customExtraValue.customServices = requestedDeliveryServiceIds.slice();
              core.changeElementValue('customExtraService', customExtraValue, { isDepth: false });
              result.deliveryServiceApplied = requestedDeliveryServiceIds;
            }

            const buyerProtectionServiceName = normalizeText(payload.buyerProtectionServiceName);
            const buyerProtectionServiceCode = String(payload.buyerProtectionServiceCode || '').trim();
            const buyerProtectionStepTemplate = Array.isArray(payload.buyerProtectionStepTemplate)
              ? payload.buyerProtectionStepTemplate
              : [];
            const includeBuyerProtectionSpsCode = Boolean(payload.includeBuyerProtectionSpsCode);
            const buyerProtectionComponent = components.buyerProtection || {};
            const buyerProtectionProps = buyerProtectionComponent.props || {};
            const buyerProtectionValue = cloneValue(buyerProtectionProps.value || {});
            const buyerGroups = (((buyerProtectionProps.channelRenderMap || {}).dsc) || []);
            const shipmentGroup =
              buyerGroups.find((item) => String((item || {}).groupId || '') === '1') ||
              buyerGroups[0] ||
              {};
            const availableServices = Array.isArray(shipmentGroup.ptsOfferTagModels)
              ? shipmentGroup.ptsOfferTagModels
              : [];
            const matchedService =
              (buyerProtectionServiceCode
                ? availableServices.find(
                    (item) => String((item && item.serviceCode) || '').trim() === buyerProtectionServiceCode
                  )
                : null) ||
              (buyerProtectionServiceName
                ? (
                    availableServices.find(
                      (item) => normalizeText(item && item.serviceName) === buyerProtectionServiceName
                    ) ||
                    availableServices.find(
                      (item) =>
                        normalizeText(item && item.serviceName).includes(buyerProtectionServiceName) ||
                        buyerProtectionServiceName.includes(normalizeText(item && item.serviceName))
                    )
                  )
                : null);
            const selectedSceneStepModels =
              ((((shipmentGroup || {}).scenePtsOfferStepModels || {}).xhdz) || [])
                .filter((item) => item && item.selected);
            const cloneSteps = (steps) =>
              (Array.isArray(steps) ? steps : [])
                .map((step) => {
                  if (!step || typeof step !== 'object') {
                    return null;
                  }
                  return JSON.parse(JSON.stringify(step));
                })
                .filter(Boolean);
            const buildExplicitBuyerProtectionSteps = () =>
              buyerProtectionStepTemplate
                .map((item) => {
                  const from = Number(item && item.from);
                  const endRaw = item && item.end;
                  const end = endRaw == null || endRaw === '' ? null : Number(endRaw);
                  const serviceName = normalizeText(item && item.serviceName);
                  const serviceCode = String((item && item.serviceCode) || '').trim();
                  if (!Number.isFinite(from) || from <= 0 || (!serviceName && !serviceCode)) {
                    return null;
                  }
                  const matched =
                    (serviceCode
                      ? availableServices.find(
                          (serviceItem) => String((serviceItem && serviceItem.serviceCode) || '').trim() === serviceCode
                        )
                      : null) ||
                    (serviceName
                      ? (
                          availableServices.find(
                            (serviceItem) => normalizeText(serviceItem && serviceItem.serviceName) === serviceName
                          ) ||
                          availableServices.find(
                            (serviceItem) =>
                              normalizeText(serviceItem && serviceItem.serviceName).includes(serviceName) ||
                              serviceName.includes(normalizeText(serviceItem && serviceItem.serviceName))
                          )
                        )
                      : null);
                  if (!matched && !serviceCode) {
                    return null;
                  }
                  const nextStep = {
                    from,
                    value: String((matched && matched.serviceCode) || serviceCode || '').trim(),
                    serviceName: String((matched && matched.serviceName) || serviceName || serviceCode || '').trim(),
                  };
                  if (Number.isFinite(end) && end >= from) {
                    nextStep.end = end;
                  }
                  return nextStep.value && nextStep.serviceName ? nextStep : null;
                })
                .filter(Boolean);
            const explicitBuyerProtectionSteps = buildExplicitBuyerProtectionSteps();
            const shouldPreferExplicitBuyerProtectionSteps = Boolean(buyerProtectionProps.processOffer);
            const resolveBuyerProtectionSteps = () => {
              if (shouldPreferExplicitBuyerProtectionSteps && explicitBuyerProtectionSteps.length > 0) {
                return explicitBuyerProtectionSteps;
              }
              const sceneSteps = selectedSceneStepModels
                .map((item) => {
                  const selectedModel = item && item.selectedPtsOfferTagModel;
                  const serviceCode = String((selectedModel && selectedModel.serviceCode) || '').trim();
                  const serviceName = String((selectedModel && selectedModel.serviceName) || '').trim();
                  const from = Number(item && item.selectedStartQuantity);
                  const endRaw = item && item.selectedEndQuantity;
                  const end = endRaw == null || endRaw === '' ? null : Number(endRaw);
                  if (!serviceCode || !serviceName || !Number.isFinite(from) || from <= 0) {
                    return null;
                  }
                  const nextStep = {
                    from,
                    value: serviceCode,
                    serviceName,
                  };
                  if (Number.isFinite(end) && end >= from) {
                    nextStep.end = end;
                  }
                  return nextStep;
                })
                .filter(Boolean);
              if (sceneSteps.length > 0) {
                return sceneSteps;
              }

              if (buyerProtectionServiceName && matchedService) {
                return [
                  {
                    from: 1,
                    value: String(matchedService.serviceCode || '').trim(),
                    serviceName: String(matchedService.serviceName || '').trim(),
                  },
                ];
              }
              return [];
            };
            let expectedBuyerSteps = resolveBuyerProtectionSteps();
            if (!buyerProtectionProps.processOffer && expectedBuyerSteps.length > 0) {
              const firstStep = expectedBuyerSteps[0] || {};
              expectedBuyerSteps = [
                {
                  from: 1,
                  value: String(firstStep.value || '').trim(),
                  serviceName: String(firstStep.serviceName || '').trim(),
                },
              ].filter((item) => item.value && item.serviceName);
            }
            const supplyTypeComponent = components.supplyType || {};
            const currentSupplyTypes = Array.isArray((supplyTypeComponent.props || {}).value)
              ? (supplyTypeComponent.props || {}).value
                  .map((item) => Number(item))
                  .filter((item) => Number.isFinite(item) && item > 0)
              : [];
            const requiresProcessSupplyType =
              expectedBuyerSteps.length > 1 ||
              expectedBuyerSteps.some((item) => Number(item && item.from) > 1);
            const expectedSupplyTypes = Array.from(
              new Set(
                (requiresProcessSupplyType ? currentSupplyTypes.concat([1, 2]) : currentSupplyTypes)
                  .filter((item) => Number.isFinite(item) && item > 0)
              )
            ).sort((left, right) => left - right);
            const simplifyBuyerSteps = (steps) =>
              (Array.isArray(steps) ? steps : [])
                .map((step) => {
                  const value = String((step && step.value) || '').trim();
                  const from = Number(step && step.from);
                  const endRaw = step && step.end;
                  const end = endRaw == null || endRaw === '' ? null : Number(endRaw);
                  if (!value || !Number.isFinite(from) || from <= 0) {
                    return null;
                  }
                  const nextStep = { from, value };
                  if (Number.isFinite(end) && end >= from) {
                    nextStep.end = end;
                  }
                  return nextStep;
                })
                .filter(Boolean);
            const buildBuyerProtectionGroups = (sourceSelectedServices) => {
              const currentDscGroups = Array.isArray((sourceSelectedServices || {}).dsc)
                ? sourceSelectedServices.dsc
                : [];
              const nextGroups = buyerGroups.map((groupItem) => {
                const logicGroupId = String((groupItem && groupItem.groupId) || '');
                const currentGroup =
                  currentDscGroups.find((item) => String((item || {}).logicGroupId || '') === logicGroupId) ||
                  {};
                const tagMode = String((groupItem && groupItem.tagMode) || '').trim();
                const nextGroup = {
                  ...(currentGroup || {}),
                  logicGroupId,
                  offerMode:
                    logicGroupId === '1'
                      ? 'step'
                      : (String((currentGroup || {}).offerMode || '').trim() || (tagMode === 'multi' ? 'multi' : 'single')),
                };
                if (logicGroupId === '1') {
                  nextGroup.offerRapidProcess = false;
                  nextGroup.steps = expectedBuyerSteps.map((stepItem, index) => {
                    const existingStep = (Array.isArray(currentGroup.steps) ? currentGroup.steps[index] : null) || {};
                    const nextStep = {
                      ...(existingStep || {}),
                      from: Number(stepItem.from),
                      value: String(stepItem.value || ''),
                    };
                    if (stepItem.end != null && Number(stepItem.end) >= Number(stepItem.from)) {
                      nextStep.end = Number(stepItem.end);
                    } else {
                      delete nextStep.end;
                    }
                    return nextStep;
                  });
                  return nextGroup;
                }

                const existingSteps = cloneSteps(currentGroup.steps);
                if (existingSteps.length > 0) {
                  nextGroup.steps = existingSteps;
                  return nextGroup;
                }

                const selectedModels = Array.isArray(groupItem && groupItem.ptsOfferTagModels)
                  ? groupItem.ptsOfferTagModels.filter((item) => item && item.selected)
                  : [];
                const shouldForceDefaultService = ['4', '6'].includes(logicGroupId);
                const fallbackModels = Array.isArray(groupItem && groupItem.ptsOfferTagModels)
                  ? groupItem.ptsOfferTagModels
                  : [];
                const effectiveModels =
                  selectedModels.length > 0
                    ? selectedModels
                    : (shouldForceDefaultService ? fallbackModels.slice(0, 1) : []);
                nextGroup.steps = effectiveModels
                  .map((item) => {
                    const value = String((item && item.serviceCode) || '').trim();
                    return value ? { value } : null;
                  })
                  .filter(Boolean);
                return nextGroup;
              });
              if (nextGroups.length > 0) {
                return nextGroups;
              }
              return currentDscGroups;
            };
            const buildBuyerProtectionJgdzGroups = (sourceSelectedServices) => {
              const buyerJgdzGroups = (((buyerProtectionProps.channelRenderMap || {}).jgdz) || []);
              const currentJgdzGroups = Array.isArray((sourceSelectedServices || {}).jgdz)
                ? sourceSelectedServices.jgdz
                : [];
              const nextGroups = buyerJgdzGroups.map((groupItem) => {
                const logicGroupId = String((groupItem && groupItem.groupId) || '');
                const currentGroup =
                  currentJgdzGroups.find((item) => String((item || {}).logicGroupId || '') === logicGroupId) ||
                  {};
                const tagMode = String((groupItem && groupItem.tagMode) || '').trim();
                const nextGroup = {
                  ...(currentGroup || {}),
                  logicGroupId,
                  offerMode:
                    String((currentGroup || {}).offerMode || '').trim() || (tagMode === 'multi' ? 'multi' : 'single'),
                };
                const existingSteps = cloneSteps(currentGroup.steps);
                if (existingSteps.length > 0) {
                  nextGroup.steps = existingSteps;
                  return nextGroup;
                }

                const selectedModels = Array.isArray(groupItem && groupItem.ptsOfferTagModels)
                  ? groupItem.ptsOfferTagModels.filter((item) => item && item.selected)
                  : [];
                const shouldForceDefaultService = ['4', '6'].includes(logicGroupId);
                const fallbackModels = Array.isArray(groupItem && groupItem.ptsOfferTagModels)
                  ? groupItem.ptsOfferTagModels
                  : [];
                const effectiveModels =
                  selectedModels.length > 0
                    ? selectedModels
                    : (shouldForceDefaultService ? fallbackModels.slice(0, 1) : []);
                nextGroup.steps = effectiveModels
                  .map((item) => {
                    const value = String((item && item.serviceCode) || '').trim();
                    return value ? { value } : null;
                  })
                  .filter(Boolean);
                return nextGroup;
              });
              const meaningfulNextGroups = nextGroups.filter(
                (groupItem) => Array.isArray(groupItem && groupItem.steps) && groupItem.steps.length > 0
              );
              if (meaningfulNextGroups.length > 0) {
                return meaningfulNextGroups;
              }
              return (Array.isArray(currentJgdzGroups) ? currentJgdzGroups : []).filter(
                (groupItem) => Array.isArray(groupItem && groupItem.steps) && groupItem.steps.length > 0
              );
            };
            const buildBuyerProtectionSpsCode = (dscGroups, jgdzGroups) => {
              const values = [];
              const pushValue = (rawValue) => {
                const value = String(rawValue || '').trim();
                if (value && !values.includes(value)) {
                  values.push(value);
                }
              };
              (Array.isArray(dscGroups) ? dscGroups : []).forEach((groupItem) => {
                const logicGroupId = String((groupItem && groupItem.logicGroupId) || (groupItem && groupItem.groupId) || '').trim();
                const steps = Array.isArray(groupItem && groupItem.steps) ? groupItem.steps : [];
                if (logicGroupId === '1') {
                  pushValue(((steps[0] || {}).value));
                  return;
                }
                steps.forEach((stepItem) => pushValue(stepItem && stepItem.value));
              });
              (Array.isArray(jgdzGroups) ? jgdzGroups : []).forEach((groupItem) => {
                const steps = Array.isArray(groupItem && groupItem.steps) ? groupItem.steps : [];
                steps.forEach((stepItem) => pushValue(stepItem && stepItem.value));
              });
              return values;
            };
            if (requiresProcessSupplyType && expectedSupplyTypes.length > 0) {
              if (JSON.stringify(currentSupplyTypes) !== JSON.stringify(expectedSupplyTypes)) {
                core.changeElementValue('supplyType', expectedSupplyTypes, { isDepth: false });
              }
              result.supplyTypeApplied = expectedSupplyTypes;
            }
            const currentSelectedGroups = (((buyerProtectionProps.value || {}).selectedServices || {}).dsc) || [];
            const currentShipmentGroup =
              currentSelectedGroups.find((item) => String((item || {}).logicGroupId || '') === '1') ||
              currentSelectedGroups[0] ||
              {};
            const currentSimplifiedSteps = simplifyBuyerSteps(currentShipmentGroup.steps);
            const expectedSimplifiedSteps = simplifyBuyerSteps(expectedBuyerSteps);

            if (expectedBuyerSteps.length > 0) {
              const currentItemMessage = String(((buyerProtectionProps.itemMessage || {})['dsc|1'] || '')).trim();
              if (
                JSON.stringify(currentSimplifiedSteps) === JSON.stringify(expectedSimplifiedSteps) &&
                !currentItemMessage
              ) {
                result.buyerProtectionApplied =
                  expectedBuyerSteps.map((item) => String(item.serviceName || '').trim()).filter(Boolean).join(' | ') ||
                  buyerProtectionServiceName;
                result.buyerProtectionMatchedCode =
                  expectedBuyerSteps.map((item) => String(item.value || '').trim()).filter(Boolean).join('|');
              } else {
                const dispatchMouseSequence = (element) => {
                  if (!element) {
                    return;
                  }
                  ['mouseenter', 'mouseover', 'mousemove', 'mousedown', 'mouseup', 'click'].forEach((type) => {
                    element.dispatchEvent(new MouseEvent(type, {
                      bubbles: true,
                      cancelable: true,
                      view: window,
                      buttons: 1,
                    }));
                  });
                };
                const targetBuyerServiceName =
                  normalizeText((expectedBuyerSteps[0] || {}).serviceName) || buyerProtectionServiceName;
                const chooseBuyerProtectionRowOption = (serviceName) => {
                  const isVisibleNode = (node) => {
                    if (!node) return false;
                    const style = window.getComputedStyle(node);
                    const rect = node.getBoundingClientRect();
                    return (
                      style.display !== 'none' &&
                      style.visibility !== 'hidden' &&
                      rect.width > 0 &&
                      rect.height > 0
                    );
                  };
                  const resolveFirstVisibleBuyerRow = () => {
                    const rows = Array.from(
                      document.querySelectorAll('#guid-buyerProtection .ant-table-tbody tr')
                    ).filter(isVisibleNode);
                    return rows[0] || null;
                  };
                  result.buyerProtectionRowSelectAttempted = true;
                  if (!serviceName) {
                    return false;
                  }
                  const buyerRow = resolveFirstVisibleBuyerRow();
                  const rowSelectRoot = buyerRow
                    ? (
                        Array.from(buyerRow.querySelectorAll('td:nth-child(3) .ant-select')).find(isVisibleNode) ||
                        null
                      )
                    : null;
                  const rowSelector =
                    (rowSelectRoot && (
                      rowSelectRoot.querySelector('.ant-select-selector') ||
                      rowSelectRoot
                    )) ||
                    null;
                  const rowInput = rowSelectRoot
                    ? (
                        rowSelectRoot.querySelector('.ant-select-selection-search input[role="combobox"]') ||
                        null
                      )
                    : null;
                  if (!rowSelector && !rowInput) {
                    return false;
                  }
                  if (rowSelector) {
                    dispatchMouseSequence(rowSelector);
                  }
                  dispatchMouseSequence(rowInput);
                  if (rowInput) {
                    rowInput.dispatchEvent(new KeyboardEvent('keydown', {
                      key: 'ArrowDown',
                      bubbles: true,
                      cancelable: true,
                    }));
                  }
                  const optionNodes = Array.from(
                    document.querySelectorAll(
                      '.ant-select-dropdown:not(.ant-select-dropdown-hidden) .ant-select-item-option, ' +
                      '.ant-select-dropdown .ant-select-item-option'
                    )
                  );
                  const targetOption =
                    optionNodes.find((item) => normalizeText(item && item.innerText) === serviceName) ||
                    optionNodes.find((item) => normalizeText(item && item.innerText).includes(serviceName));
                  const targetOptionContent =
                    (targetOption && targetOption.querySelector('.ant-select-item-option-content')) ||
                    targetOption;
                  if (!targetOptionContent) {
                    return false;
                  }
                  dispatchMouseSequence(targetOptionContent);
                  result.buyerProtectionRowOptionSelected = true;
                  return true;
                };
                if (targetBuyerServiceName) {
                  chooseBuyerProtectionRowOption(targetBuyerServiceName);
                }
                const rowSelectedNode = (() => {
                  const isVisibleNode = (node) => {
                    if (!node) return false;
                    const style = window.getComputedStyle(node);
                    const rect = node.getBoundingClientRect();
                    return (
                      style.display !== 'none' &&
                      style.visibility !== 'hidden' &&
                      rect.width > 0 &&
                      rect.height > 0
                    );
                  };
                  const rows = Array.from(
                    document.querySelectorAll('#guid-buyerProtection .ant-table-tbody tr')
                  ).filter(isVisibleNode);
                  const buyerRow = rows[0] || null;
                  if (!buyerRow) return null;
                  const selectedNodes = Array.from(
                    buyerRow.querySelectorAll('td:nth-child(3) .ant-select-selection-item')
                  ).filter(isVisibleNode);
                  return selectedNodes[0] || null;
                })();
                result.buyerProtectionRowSelectedText = normalizeText(
                  (rowSelectedNode && (rowSelectedNode.innerText || rowSelectedNode.textContent)) || ''
                );

                const refreshedBuyerProps = ((((engine.getJsonState() || {}).components || {}).buyerProtection || {}).props || {});
                const refreshedSelectedGroups = (((refreshedBuyerProps.value || {}).selectedServices || {}).dsc) || [];
                const refreshedShipmentGroup =
                  refreshedSelectedGroups.find((item) => String((item || {}).logicGroupId || '') === '1') ||
                  refreshedSelectedGroups[0] ||
                  {};
                const refreshedSimplifiedSteps = simplifyBuyerSteps(refreshedShipmentGroup.steps);
                const refreshedItemMessage = String(((refreshedBuyerProps.itemMessage || {})['dsc|1'] || '')).trim();
                if (
                  JSON.stringify(refreshedSimplifiedSteps) === JSON.stringify(expectedSimplifiedSteps) &&
                  !refreshedItemMessage
                ) {
                  result.buyerProtectionApplied =
                    expectedBuyerSteps.map((item) => String(item.serviceName || '').trim()).filter(Boolean).join(' | ') ||
                    buyerProtectionServiceName;
                  result.buyerProtectionMatchedCode =
                    expectedBuyerSteps.map((item) => String(item.value || '').trim()).filter(Boolean).join('|');
                } else {
                  const selectedServices = buyerProtectionValue.selectedServices || {};
                  const nextDscGroups = buildBuyerProtectionGroups(selectedServices);
                  const nextJgdzGroups = buildBuyerProtectionJgdzGroups(selectedServices);
                  buyerProtectionValue.suggestBuyerProtectionDeliveryTime =
                    expectedBuyerSteps.length === 1
                      ? String((expectedBuyerSteps[0] || {}).serviceName || '')
                      : '';
                  const nextSelectedServices = {
                    ...(selectedServices || {}),
                    dsc: nextDscGroups,
                  };
                  if (nextJgdzGroups.length > 0) {
                    nextSelectedServices.jgdz = nextJgdzGroups;
                  } else {
                    delete nextSelectedServices.jgdz;
                  }
                  buyerProtectionValue.selectedServices = nextSelectedServices;
                  if (includeBuyerProtectionSpsCode) {
                    buyerProtectionValue.spsCode = buildBuyerProtectionSpsCode(
                      nextDscGroups,
                      nextSelectedServices.jgdz || []
                    );
                  } else if (Object.prototype.hasOwnProperty.call(buyerProtectionValue, 'spsCode')) {
                    delete buyerProtectionValue.spsCode;
                  }
                  const findBuyerProtectionHandleNode = () => {
                    const target = document.querySelector('#guid-buyerProtection .module-cbu-buyer-protection');
                    const reactKey = Object.keys(target || {}).find(
                      (key) => key.startsWith('__reactInternalInstance') || key.startsWith('__reactFiber')
                    );
                    let fiber = reactKey ? target[reactKey] : null;
                    while (fiber) {
                      const stateNode = fiber.stateNode;
                      if (
                        stateNode &&
                        typeof stateNode.handleChange === 'function' &&
                        stateNode.props &&
                        String(stateNode.props.UUID || '') === 'buyerProtection'
                      ) {
                        return stateNode;
                      }
                      fiber = fiber.return;
                    }
                    return null;
                  };
                  const buyerProtectionHandleNode = findBuyerProtectionHandleNode();
                  if (buyerProtectionHandleNode) {
                    buyerProtectionHandleNode.handleChange(false, buyerProtectionValue);
                  } else {
                    core.changeElementValue('buyerProtection', buyerProtectionValue, { isDepth: false });
                  }
                  result.buyerProtectionApplied =
                    expectedBuyerSteps.map((item) => String(item.serviceName || '').trim()).filter(Boolean).join(' | ') ||
                    buyerProtectionServiceName;
                  result.buyerProtectionMatchedCode =
                    expectedBuyerSteps.map((item) => String(item.value || '').trim()).filter(Boolean).join('|');
                }
              }
            }

            const nextState = engine.getJsonState() || {};
            const nextComponents = nextState.components || {};
            result.nextCatPropValue = (((nextComponents.catProp || {}).props || {}).value) || {};
            result.nextBuyerProtectionValue = (((nextComponents.buyerProtection || {}).props || {}).value) || {};
            result.buyerProtectionDesiredSteps = expectedBuyerSteps;
            result.assistMessages = Array.from(
              document.querySelectorAll('#guid-assistBoard .info-list li div, #guid-assistBoard .info-list li')
            )
              .map((node) => normalizeText(node.innerText))
              .filter(Boolean);
            return result;
            """,
            patch_payload,
        )
        context["draft_page_state_patch"] = result
        if isinstance(result, dict):
            next_cat_prop_value = result.get("nextCatPropValue")
            if isinstance(next_cat_prop_value, dict):
                context["draft_cat_prop_value_runtime"] = next_cat_prop_value
            assist_messages = [
                str(item).strip()
                for item in list(result.get("assistMessages", []) or [])
                if str(item).strip()
            ]
            if assist_messages:
                context["draft_assist_messages"] = assist_messages
                required_labels = self._extract_required_labels_from_assist_messages(assist_messages)
                if required_labels:
                    context["draft_required_field_labels"] = required_labels
        self._pause(0.2)

    def _install_draft_request_patch(
        self,
        publish_config: dict[str, Any],
        context: dict[str, Any],
    ) -> None:
        if not self.driver:
            raise RuntimeError("Browser has not been opened.")

        patch_config = publish_config.get("draft_request_patch", {})
        if not patch_config or not patch_config.get("enabled", False):
            return

        patch_mode = str(context.get("draft_request_patch_mode", "full")).strip().lower() or "full"
        apply_patch = patch_mode not in {"capture_only"}
        buyer_protection_value = self._resolve_context_preferred_value(
            context=context,
            source=str(patch_config.get("buyer_protection_source", "")).strip(),
            default_value=str(patch_config.get("buyer_protection_default_value", "")).strip(),
        )
        buyer_protection_code = self._resolve_context_preferred_value(
            context=context,
            source=str(patch_config.get("buyer_protection_service_code_source", "")).strip(),
            default_value=str(patch_config.get("buyer_protection_service_code", "")).strip(),
        )
        if not buyer_protection_code:
            buyer_protection_code = str(context.get("buyer_protection_ship_time_code", "")).strip()
        delivery_service_ids = self._resolve_delivery_service_ids(
            patch_config=patch_config,
            context=context,
        )
        runtime_step_template = context.get("buyer_protection_step_template_runtime")
        step_template_config = (
            runtime_step_template
            if isinstance(runtime_step_template, list) and runtime_step_template
            else patch_config.get("buyer_protection_step_template", [])
        )
        buyer_protection_steps = self._build_buyer_protection_step_template(
            step_template_config,
            context,
        )
        if not buyer_protection_code and buyer_protection_steps:
            buyer_protection_code = str((buyer_protection_steps[0] or {}).get("serviceCode", "")).strip()
        detail_image_html = self._build_tinymce_image_html(
            [
                str(item).strip()
                for item in context.get("detail_images_uploaded_urls", [])
                if str(item).strip()
            ]
        )
        detail_text_html = self._build_tinymce_html(str(context.get("description", "")).strip())
        config_payload = {
            "captureBodyChars": max(256, int(patch_config.get("capture_body_chars", 4000) or 4000)),
            "buyerProtectionServiceName": buyer_protection_value,
            "buyerProtectionServiceCode": buyer_protection_code,
            "buyerProtectionStepTemplate": buyer_protection_steps,
            "includeBuyerProtectionSpsCode": bool(patch_config.get("buyer_protection_include_sps_code", False)),
            "title": str(context.get("title", "")).strip(),
            "price": str(context.get("price", "")).strip(),
            "quantity": str(context.get("quantity", "")).strip(),
            "sendAddressId": str(context.get("send_address_id", "")).strip(),
            "deliveryServiceIds": delivery_service_ids,
            "deliveryServiceLabelIdMap": patch_config.get("delivery_service_label_id_map", {}),
            "catPropValue": context.get("draft_cat_prop_value_runtime", {}),
            "primaryPictureUrls": [
                str(item).strip()
                for item in context.get("main_image_uploaded_urls", [])
                if str(item).strip()
            ],
            "detailHtml": detail_image_html or detail_text_html,
            "patchMode": patch_mode,
            "applyPatch": apply_patch,
        }
        self.driver.execute_script(
            """
            const config = arguments[0] || {};
            window.__codexDraftPatchConfig = {
              ...(window.__codexDraftPatchConfig || {}),
              ...config,
            };
            window.__codexDraftSubmitRecords = [];

            if (window.__codexDraftPatchInstalled) {
              return;
            }

            window.__codexDraftPatchInstalled = true;

            const isDraftSubmitUrl = (rawUrl) => {
              const text = String(rawUrl || '').trim();
              if (!text) {
                return false;
              }
              try {
                const parsed = new URL(text, window.location.href);
                const hostname = String(parsed.hostname || '').toLowerCase();
                const pathname = String(parsed.pathname || '');
                const is1688Host =
                  hostname === 'offer-new.1688.com' ||
                  hostname.endsWith('.1688.com');
                return is1688Host && /\\/popular\\/draftSubmit\\.htm$/i.test(pathname);
              } catch (error) {
                return /(^|\\/)draftSubmit\\.htm(?:$|[?#])/i.test(text) && !/arms-retcode/i.test(text);
              }
            };

            const previewValue = (value) => {
              if (value == null) {
                return '';
              }
              if (typeof value === 'string') {
                return value.slice(0, window.__codexDraftPatchConfig.captureBodyChars || 4000);
              }
              if (typeof FormData !== 'undefined' && value instanceof FormData) {
                return Array.from(value.entries())
                  .map(([key, entryValue]) => `${key}=${typeof entryValue === 'string' ? entryValue : '[binary]'}`)
                  .join('&')
                  .slice(0, window.__codexDraftPatchConfig.captureBodyChars || 4000);
              }
              try {
                return JSON.stringify(value).slice(0, window.__codexDraftPatchConfig.captureBodyChars || 4000);
              } catch (error) {
                return String(value).slice(0, window.__codexDraftPatchConfig.captureBodyChars || 4000);
              }
            };

            const normalizeIbankUrl = (rawValue) => {
              const text = String(rawValue || '').trim();
              if (!text) {
                return '';
              }
              if (text.startsWith('img/ibank/')) {
                return text;
              }
              const match = text.match(/\\/(img\\/ibank\\/[^?#]+)/i);
              if (match && match[1]) {
                return match[1];
              }
              return text;
            };
            const normalizeServiceIds = (values) =>
              Array.from(
                new Set(
                  (Array.isArray(values) ? values : [])
                    .map((item) => {
                      const text = String(item == null ? '' : item).trim();
                      if (!text) return null;
                      const asNumber = Number(text);
                      if (!Number.isFinite(asNumber)) {
                        return null;
                      }
                      const normalized = String(Math.trunc(asNumber));
                      if (normalized !== text) {
                        return null;
                      }
                      return Math.trunc(asNumber);
                    })
                    .filter((item) => item != null)
                )
              );
            const parsePositiveNumber = (value) => {
              const text = String(value == null ? '' : value).trim();
              if (!text) return null;
              const normalized = text.replace(/,/g, '');
              const parsed = Number(normalized);
              if (!Number.isFinite(parsed) || parsed <= 0) {
                return null;
              }
              return parsed;
            };
            const parsePositiveInteger = (value) => {
              const parsed = parsePositiveNumber(value);
              if (parsed == null) {
                return null;
              }
              const integerValue = Math.floor(parsed);
              return integerValue > 0 ? integerValue : null;
            };

            const getPatchSnapshot = () => {
              const sdk = window.SellPublishSdk;
              const state = sdk && sdk.engine && sdk.engine.getJsonState ? sdk.engine.getJsonState() : {};
              const components = (state && state.components) || {};
              const primaryValue = (((components.primaryPicture || {}).props || {}).value) || {};
              const rawImageList = Array.isArray(primaryValue.imageList) ? primaryValue.imageList : [];
              const primaryUrls = rawImageList
                .map((item) => normalizeIbankUrl(item && item.url))
                .filter(Boolean);
              const fallbackPrimaryUrls = Array.isArray(window.__codexDraftPatchConfig.primaryPictureUrls)
                ? window.__codexDraftPatchConfig.primaryPictureUrls
                    .map((item) => normalizeIbankUrl(item))
                    .filter(Boolean)
                : [];
              const deliveryServiceLabelIdMap =
                window.__codexDraftPatchConfig.deliveryServiceLabelIdMap &&
                typeof window.__codexDraftPatchConfig.deliveryServiceLabelIdMap === 'object'
                  ? window.__codexDraftPatchConfig.deliveryServiceLabelIdMap
                  : {};
              const normalizeDeliveryLabel = (value) => String(value || '').replace(/\\s+/g, '').trim();
              const parseServiceId = (value) => {
                const text = String(value == null ? '' : value).trim();
                if (!text) return null;
                const asNumber = Number(text);
                if (!Number.isFinite(asNumber)) {
                  return null;
                }
                const normalized = String(Math.trunc(asNumber));
                if (normalized !== text) {
                  return null;
                }
                return Math.trunc(asNumber);
              };
              const normalizeDeliveryServiceId = (rawLabel) => {
                const label = String(rawLabel || '').trim();
                if (!label) return null;
                const compactLabel = normalizeDeliveryLabel(label);
                const direct = Object.prototype.hasOwnProperty.call(deliveryServiceLabelIdMap, label)
                  ? deliveryServiceLabelIdMap[label]
                  : undefined;
                if (direct != null) {
                  return parseServiceId(direct);
                }
                const key = Object.keys(deliveryServiceLabelIdMap).find(
                  (item) => normalizeDeliveryLabel(item) === compactLabel
                );
                if (!key) {
                  return null;
                }
                return parseServiceId(deliveryServiceLabelIdMap[key]);
              };
              const collectDeliveryServiceState = () => {
                const configuredIds = normalizeServiceIds(window.__codexDraftPatchConfig.deliveryServiceIds || []);
                const customExtraProps = ((components.customExtraService || {}).props) || {};
                const customExtraValue = (customExtraProps.value || {});
                const stateCustomServices = normalizeServiceIds(
                  Array.isArray(customExtraValue.customServices) ? customExtraValue.customServices : []
                );
                const stateMap = customExtraValue && typeof customExtraValue.viewModelMap === 'object'
                  ? customExtraValue.viewModelMap
                  : {};
                const mapDeliveryIds = normalizeServiceIds(
                  Object.keys(stateMap)
                    .filter((item) => normalizeDeliveryLabel(item).includes('配送'))
                    .map((item) => stateMap[item])
                );
                const checkedLabels = Array.from(
                  document.querySelectorAll('#guid-customExtraService .special-service-wrapper .service-item .ant-checkbox-input[type="checkbox"]:checked')
                )
                  .map((inputNode) => {
                    const labelNode = inputNode.closest('label');
                    const textNode = labelNode ? labelNode.querySelector('span:last-child') : null;
                    return String((textNode && (textNode.innerText || textNode.textContent)) || '').replace(/\\s+/g, ' ').trim();
                  })
                  .filter(Boolean);
                const labelMappedIds = normalizeServiceIds(
                  checkedLabels
                    .map((label) => normalizeDeliveryServiceId(label))
                    .filter((item) => item != null)
                );
                const merged = normalizeServiceIds(
                  configuredIds.concat(stateCustomServices, mapDeliveryIds, labelMappedIds)
                );
                return {
                  deliveryServiceIds: merged,
                  deliveryServiceLabels: checkedLabels,
                };
              };
              const deliveryServiceState = collectDeliveryServiceState();

              const buyerProps = ((components.buyerProtection || {}).props) || {};
              const supplyTypeProps = ((components.supplyType || {}).props) || {};
              const titleProps = ((components.title || {}).props) || {};
              const titleValue = String(titleProps.value || '').trim();
              const fallbackTitleValue = String(window.__codexDraftPatchConfig.title || '').trim();
              const draftTitle = titleValue || fallbackTitleValue;
              const totalSalesProps = ((components.totalSales || {}).props) || {};
              const stateQuantity = parsePositiveInteger(totalSalesProps.value);
              const draftQuantity = stateQuantity || parsePositiveInteger(window.__codexDraftPatchConfig.quantity);
              const statePriceRange = (((components.priceRange || {}).props || {}).value) || [];
              const statePrice =
                Array.isArray(statePriceRange) && statePriceRange[0]
                  ? parsePositiveNumber(statePriceRange[0].pricerange_price)
                  : null;
              const draftPrice = statePrice || parsePositiveNumber(window.__codexDraftPatchConfig.price);
              const sendAddressProps = ((components.cbuSendAddress || {}).props) || {};
              const catPropProps = ((components.catProp || {}).props) || {};
              const catPropValueRaw = catPropProps.value && typeof catPropProps.value === 'object'
                ? catPropProps.value
                : {};
              const runtimeCatPropValue = JSON.parse(JSON.stringify(catPropValueRaw || {}));
              const configuredCatPropValueRaw =
                window.__codexDraftPatchConfig.catPropValue &&
                typeof window.__codexDraftPatchConfig.catPropValue === 'object'
                  ? window.__codexDraftPatchConfig.catPropValue
                  : {};
              const configuredCatPropValue = JSON.parse(JSON.stringify(configuredCatPropValueRaw || {}));
              const mergedCatPropValue = { ...(runtimeCatPropValue || {}) };
              Object.entries(configuredCatPropValue || {}).forEach(([key, rawValue]) => {
                if (!key) {
                  return;
                }
                if (rawValue == null) {
                  return;
                }
                if (typeof rawValue === 'string' && !String(rawValue || '').trim()) {
                  return;
                }
                mergedCatPropValue[key] = rawValue;
              });
              const parseSendAddressId = (value) => {
                if (value == null) {
                  return null;
                }
                if (typeof value === 'object') {
                  const obj = value || {};
                  return (
                    parsePositiveInteger(obj.value) ||
                    parsePositiveInteger(obj.id) ||
                    parsePositiveInteger(obj.key)
                  );
                }
                return parsePositiveInteger(value);
              };
              const stateSendAddressId = parseSendAddressId(sendAddressProps.value);
              const configuredSendAddressId = parseSendAddressId(window.__codexDraftPatchConfig.sendAddressId);
              const dataSourceSendAddressId = Array.isArray(sendAddressProps.dataSource)
                ? (sendAddressProps.dataSource || [])
                    .map((item) => parseSendAddressId(item))
                    .find((item) => item != null) || null
                : null;
              const sendAddressId = stateSendAddressId || configuredSendAddressId || dataSourceSendAddressId;
              const serviceName = String(window.__codexDraftPatchConfig.buyerProtectionServiceName || '').trim();
              const serviceCode = String(window.__codexDraftPatchConfig.buyerProtectionServiceCode || '').trim();
              const requestedStepTemplate = Array.isArray(window.__codexDraftPatchConfig.buyerProtectionStepTemplate)
                ? window.__codexDraftPatchConfig.buyerProtectionStepTemplate
                : [];
              const includeBuyerProtectionSpsCode = Boolean(window.__codexDraftPatchConfig.includeBuyerProtectionSpsCode);
              const buyerGroups = ((buyerProps.channelRenderMap || {}).dsc) || [];
              const buyerJgdzGroups = ((buyerProps.channelRenderMap || {}).jgdz) || [];
              const shipmentGroup = buyerGroups.find((item) => String(item.groupId || '') === '1') || buyerGroups[0] || {};
              const availableBuyerServices = Array.isArray(shipmentGroup.ptsOfferTagModels)
                ? shipmentGroup.ptsOfferTagModels
                : [];
              const matchedBuyerService =
                (serviceCode
                  ? availableBuyerServices.find(
                      (item) => String((item && item.serviceCode) || '').trim() === serviceCode
                    )
                  : null) ||
                (serviceName
                  ? (
                      availableBuyerServices.find(
                        (item) => String((item && item.serviceName) || '').trim() === serviceName
                      ) ||
                      availableBuyerServices.find(
                        (item) => {
                          const candidate = String((item && item.serviceName) || '').trim();
                          return candidate.includes(serviceName) || serviceName.includes(candidate);
                        }
                      )
                    )
                  : null) ||
                null;
              const selectedSceneStepModels =
                ((((shipmentGroup || {}).scenePtsOfferStepModels || {}).xhdz) || [])
                  .filter((item) => item && item.selected);
              const cloneSteps = (steps) =>
                (Array.isArray(steps) ? steps : [])
                  .map((step) => {
                    if (!step || typeof step !== 'object') {
                      return null;
                    }
                    return JSON.parse(JSON.stringify(step));
                  })
                  .filter(Boolean);
              const buildExplicitBuyerProtectionSteps = () =>
                requestedStepTemplate
                  .map((item) => {
                    const from = Number(item && item.from);
                    const endRaw = item && item.end;
                    const end = endRaw == null || endRaw === '' ? null : Number(endRaw);
                    const stepServiceName = String((item && item.serviceName) || '').trim();
                    const stepServiceCode = String((item && item.serviceCode) || '').trim();
                    if (!Number.isFinite(from) || from <= 0 || (!stepServiceName && !stepServiceCode)) {
                      return null;
                    }
                    const matched =
                      (stepServiceCode
                        ? availableBuyerServices.find(
                            (serviceItem) => String((serviceItem && serviceItem.serviceCode) || '').trim() === stepServiceCode
                          )
                        : null) ||
                      (stepServiceName
                        ? (
                            availableBuyerServices.find(
                              (serviceItem) => String((serviceItem && serviceItem.serviceName) || '').trim() === stepServiceName
                            ) ||
                            availableBuyerServices.find((serviceItem) => {
                              const candidate = String((serviceItem && serviceItem.serviceName) || '').trim();
                              return candidate.includes(stepServiceName) || stepServiceName.includes(candidate);
                            })
                          )
                        : null) ||
                      null;
                    if (!matched && !stepServiceCode) {
                      return null;
                    }
                    const nextStep = {
                      from,
                      value: String((matched && matched.serviceCode) || stepServiceCode || ''),
                      serviceName: String((matched && matched.serviceName) || stepServiceName || stepServiceCode || ''),
                    };
                    if (Number.isFinite(end) && end >= from) {
                      nextStep.end = end;
                    }
                    return nextStep.value && nextStep.serviceName ? nextStep : null;
                  })
                  .filter(Boolean);
              const explicitBuyerProtectionSteps = buildExplicitBuyerProtectionSteps();
              const shouldPreferExplicitBuyerProtectionSteps = Boolean(buyerProps.processOffer);
              const resolveBuyerProtectionSteps = () => {
                if (shouldPreferExplicitBuyerProtectionSteps && explicitBuyerProtectionSteps.length > 0) {
                  return explicitBuyerProtectionSteps;
                }
                const sceneSteps = selectedSceneStepModels
                  .map((item) => {
                    const selectedModel = item && item.selectedPtsOfferTagModel;
                    const serviceCode = String((selectedModel && selectedModel.serviceCode) || '').trim();
                    const stepServiceName = String((selectedModel && selectedModel.serviceName) || '').trim();
                    const from = Number(item && item.selectedStartQuantity);
                    const endRaw = item && item.selectedEndQuantity;
                    const end = endRaw == null || endRaw === '' ? null : Number(endRaw);
                    if (!serviceCode || !stepServiceName || !Number.isFinite(from) || from <= 0) {
                      return null;
                    }
                    const nextStep = {
                      from,
                      value: serviceCode,
                      serviceName: stepServiceName,
                    };
                    if (Number.isFinite(end) && end >= from) {
                      nextStep.end = end;
                    }
                    return nextStep;
                  })
                  .filter(Boolean);
                if (sceneSteps.length > 0) {
                  return sceneSteps;
                }

                if (matchedBuyerService && (serviceName || serviceCode)) {
                  return [
                    {
                      from: 1,
                      value: String(matchedBuyerService.serviceCode || ''),
                      serviceName: String(matchedBuyerService.serviceName || ''),
                    },
                  ];
                }
                return [];
              };
              let buyerProtectionSteps = resolveBuyerProtectionSteps();
              if (!buyerProps.processOffer && buyerProtectionSteps.length > 0) {
                const firstStep = buyerProtectionSteps[0] || {};
                buyerProtectionSteps = [
                  {
                    from: 1,
                    value: String(firstStep.value || '').trim(),
                    serviceName: String(firstStep.serviceName || '').trim(),
                  },
                ].filter((item) => item.value && item.serviceName);
              }
              const currentSupplyTypes = Array.isArray(supplyTypeProps.value)
                ? supplyTypeProps.value
                    .map((item) => Number(item))
                    .filter((item) => Number.isFinite(item) && item > 0)
                : [];
              const requiresProcessSupplyType =
                buyerProtectionSteps.length > 1 ||
                buyerProtectionSteps.some((item) => Number(item && item.from) > 1);
              const supplyTypeValues = Array.from(
                new Set(
                  (requiresProcessSupplyType ? currentSupplyTypes.concat([1, 2]) : currentSupplyTypes)
                    .filter((item) => Number.isFinite(item) && item > 0)
                )
              ).sort((left, right) => left - right);
              const buildBuyerProtectionGroups = () => {
                const selectedServices = (buyerProps.value || {}).selectedServices || {};
                const currentDscGroups = Array.isArray(selectedServices.dsc) ? selectedServices.dsc : [];
                const nextGroups = buyerGroups.map((groupItem) => {
                  const logicGroupId = String((groupItem && groupItem.groupId) || '');
                  const currentGroup =
                    currentDscGroups.find((item) => String((item || {}).logicGroupId || '') === logicGroupId) ||
                    {};
                  const tagMode = String((groupItem && groupItem.tagMode) || '').trim();
                  const nextGroup = {
                    ...(currentGroup || {}),
                    logicGroupId,
                    offerMode:
                      logicGroupId === '1'
                        ? 'step'
                        : (String((currentGroup || {}).offerMode || '').trim() || (tagMode === 'multi' ? 'multi' : 'single')),
                  };
                  if (logicGroupId === '1') {
                    nextGroup.offerRapidProcess = false;
                    nextGroup.steps = buyerProtectionSteps.map((stepItem, index) => {
                      const existingStep = (Array.isArray(currentGroup.steps) ? currentGroup.steps[index] : null) || {};
                      const nextStep = {
                        ...(existingStep || {}),
                        from: Number(stepItem.from),
                        value: String(stepItem.value || ''),
                      };
                      if (stepItem.end != null && Number(stepItem.end) >= Number(stepItem.from)) {
                        nextStep.end = Number(stepItem.end);
                      } else {
                        delete nextStep.end;
                      }
                      return nextStep;
                    });
                    return nextGroup;
                  }

                  const existingSteps = cloneSteps(currentGroup.steps);
                  if (existingSteps.length > 0) {
                    nextGroup.steps = existingSteps;
                    return nextGroup;
                  }

                  const selectedModels = Array.isArray(groupItem && groupItem.ptsOfferTagModels)
                    ? groupItem.ptsOfferTagModels.filter((item) => item && item.selected)
                    : [];
                  const shouldForceDefaultService = ['4', '6'].includes(logicGroupId);
                  const fallbackModels = Array.isArray(groupItem && groupItem.ptsOfferTagModels)
                    ? groupItem.ptsOfferTagModels
                    : [];
                  const effectiveModels =
                    selectedModels.length > 0
                      ? selectedModels
                      : (shouldForceDefaultService ? fallbackModels.slice(0, 1) : []);
                  nextGroup.steps = effectiveModels
                    .map((item) => {
                      const value = String((item && item.serviceCode) || '').trim();
                      return value ? { value } : null;
                    })
                    .filter(Boolean);
                  return nextGroup;
                });
                if (nextGroups.length > 0) {
                  return nextGroups;
                }
                return currentDscGroups;
              };
              const buildBuyerProtectionJgdzGroups = () => {
                const selectedServices = (buyerProps.value || {}).selectedServices || {};
                const currentJgdzGroups = Array.isArray(selectedServices.jgdz) ? selectedServices.jgdz : [];
                const nextGroups = buyerJgdzGroups.map((groupItem) => {
                  const logicGroupId = String((groupItem && groupItem.groupId) || '');
                  const currentGroup =
                    currentJgdzGroups.find((item) => String((item || {}).logicGroupId || '') === logicGroupId) ||
                    {};
                  const tagMode = String((groupItem && groupItem.tagMode) || '').trim();
                  const nextGroup = {
                    ...(currentGroup || {}),
                    logicGroupId,
                    offerMode:
                      String((currentGroup || {}).offerMode || '').trim() || (tagMode === 'multi' ? 'multi' : 'single'),
                  };
                  if (buyerProps.processOffer && logicGroupId === '1' && buyerProtectionSteps.length > 0) {
                    nextGroup.offerMode = 'step';
                    nextGroup.steps = buyerProtectionSteps.map((stepItem, index) => {
                      const existingStep = (Array.isArray(currentGroup.steps) ? currentGroup.steps[index] : null) || {};
                      const nextStep = {
                        ...(existingStep || {}),
                        from: Number(stepItem.from),
                        value: String(stepItem.value || ''),
                      };
                      if (stepItem.end != null && Number(stepItem.end) >= Number(stepItem.from)) {
                        nextStep.end = Number(stepItem.end);
                      } else {
                        delete nextStep.end;
                      }
                      return nextStep;
                    });
                    return nextGroup;
                  }
                  const existingSteps = cloneSteps(currentGroup.steps);
                  if (existingSteps.length > 0) {
                    nextGroup.steps = existingSteps;
                    return nextGroup;
                  }

                  const selectedModels = Array.isArray(groupItem && groupItem.ptsOfferTagModels)
                    ? groupItem.ptsOfferTagModels.filter((item) => item && item.selected)
                    : [];
                  const shouldForceDefaultService = ['4', '6'].includes(logicGroupId);
                  const fallbackModels = Array.isArray(groupItem && groupItem.ptsOfferTagModels)
                    ? groupItem.ptsOfferTagModels
                    : [];
                  const effectiveModels =
                    selectedModels.length > 0
                      ? selectedModels
                      : (shouldForceDefaultService ? fallbackModels.slice(0, 1) : []);
                  nextGroup.steps = effectiveModels
                    .map((item) => {
                      const value = String((item && item.serviceCode) || '').trim();
                      return value ? { value } : null;
                    })
                    .filter(Boolean);
                  return nextGroup;
                });
                const meaningfulNextGroups = nextGroups.filter(
                  (groupItem) => Array.isArray(groupItem && groupItem.steps) && groupItem.steps.length > 0
                );
                if (meaningfulNextGroups.length > 0) {
                  return meaningfulNextGroups;
                }
                const fallbackGroups = Array.isArray(currentJgdzGroups) ? currentJgdzGroups : [];
                return fallbackGroups.filter(
                  (groupItem) => Array.isArray(groupItem && groupItem.steps) && groupItem.steps.length > 0
                );
              };
              const buildBuyerProtectionSpsCode = (dscGroups, jgdzGroups) => {
                const values = [];
                const pushValue = (rawValue) => {
                  const value = String(rawValue || '').trim();
                  if (value && !values.includes(value)) {
                    values.push(value);
                  }
                };
                (Array.isArray(dscGroups) ? dscGroups : []).forEach((groupItem) => {
                  const logicGroupId = String((groupItem && groupItem.logicGroupId) || (groupItem && groupItem.groupId) || '').trim();
                  const steps = Array.isArray(groupItem && groupItem.steps) ? groupItem.steps : [];
                  if (logicGroupId === '1') {
                    pushValue(((steps[0] || {}).value));
                    return;
                  }
                  steps.forEach((stepItem) => pushValue(stepItem && stepItem.value));
                });
                (Array.isArray(jgdzGroups) ? jgdzGroups : []).forEach((groupItem) => {
                  const steps = Array.isArray(groupItem && groupItem.steps) ? groupItem.steps : [];
                  steps.forEach((stepItem) => pushValue(stepItem && stepItem.value));
                });
                return values;
              };
              const buyerProtectionGroups = buildBuyerProtectionGroups();
              const buyerProtectionJgdzGroups = buildBuyerProtectionJgdzGroups();

              return {
                primaryUrls: primaryUrls.length > 0 ? primaryUrls : fallbackPrimaryUrls,
                runtimeCatPropValue: mergedCatPropValue,
                buyerProtectionServiceName: serviceName,
                buyerProtectionServiceCode: matchedBuyerService ? String(matchedBuyerService.serviceCode || '') : '',
                buyerProtectionSteps,
                buyerProtectionGroups,
                buyerProtectionJgdzGroups,
                buyerProtectionSpsCode: includeBuyerProtectionSpsCode
                  ? buildBuyerProtectionSpsCode(buyerProtectionGroups, buyerProtectionJgdzGroups)
                  : [],
                includeBuyerProtectionSpsCode,
                detailHtml: String(window.__codexDraftPatchConfig.detailHtml || '').trim(),
                supplyTypeValues,
                draftTitle,
                draftPrice,
                draftQuantity,
                sendAddressId,
                deliveryServiceIds: deliveryServiceState.deliveryServiceIds,
                deliveryServiceLabels: deliveryServiceState.deliveryServiceLabels,
                availableBuyerServices: availableBuyerServices.map((item) => ({
                  serviceName: String(item.serviceName || ''),
                  serviceCode: String(item.serviceCode || ''),
                })),
              };
            };

            const patchDraftObject = (root, patchSnapshot) => {
              if (!root || typeof root !== 'object') {
                return;
              }
              const visited = new WeakSet();
              const parsePositiveNumber = (value) => {
                const text = String(value == null ? '' : value).trim();
                if (!text) return null;
                const normalized = text.replace(/,/g, '');
                const parsed = Number(normalized);
                if (!Number.isFinite(parsed) || parsed <= 0) {
                  return null;
                }
                return parsed;
              };
              const parsePositiveInteger = (value) => {
                const parsed = parsePositiveNumber(value);
                if (parsed == null) {
                  return null;
                }
                const integerValue = Math.floor(parsed);
                return integerValue > 0 ? integerValue : null;
              };
              const patchRangeList = (rangeList) => {
                if (!Array.isArray(rangeList)) {
                  return rangeList;
                }
                const draftPrice = parsePositiveNumber(patchSnapshot.draftPrice);
                if (draftPrice == null) {
                  return rangeList;
                }
                const sourceRow = rangeList[0] && typeof rangeList[0] === 'object' ? rangeList[0] : {};
                const nextRow = { ...(sourceRow || {}) };
                if (parsePositiveNumber(nextRow.pricerange_price) == null) {
                  nextRow.pricerange_price = draftPrice;
                }
                if (parsePositiveInteger(nextRow.pricerange_beginAmount) == null) {
                  nextRow.pricerange_beginAmount = 1;
                }
                return [nextRow];
              };
              const patchSkuRows = (skuRows) => {
                if (!Array.isArray(skuRows)) {
                  return skuRows;
                }
                const draftPrice = parsePositiveNumber(patchSnapshot.draftPrice);
                const draftQuantity = parsePositiveInteger(patchSnapshot.draftQuantity);
                if (draftPrice == null && draftQuantity == null) {
                  return skuRows;
                }
                return skuRows.map((row) => {
                  if (!row || typeof row !== 'object') {
                    return row;
                  }
                  const nextRow = { ...row };
                  if (draftPrice != null && parsePositiveNumber(nextRow.sku_price) == null) {
                    nextRow.sku_price = draftPrice;
                  }
                  if (draftQuantity != null && parsePositiveInteger(nextRow.sku_amountOnSale) == null) {
                    nextRow.sku_amountOnSale = draftQuantity;
                  }
                  return nextRow;
                });
              };
              const patchFormValues = (formValues) => {
                if (!formValues || typeof formValues !== 'object') {
                  return;
                }
                const runtimeCatPropValue =
                  patchSnapshot.runtimeCatPropValue && typeof patchSnapshot.runtimeCatPropValue === 'object'
                    ? patchSnapshot.runtimeCatPropValue
                    : null;
                if (runtimeCatPropValue) {
                  const currentCatProp = formValues.catProp && typeof formValues.catProp === 'object'
                    ? formValues.catProp
                    : {};
                  const nextCatProp = { ...(currentCatProp || {}) };
                  Object.entries(runtimeCatPropValue).forEach(([key, rawValue]) => {
                    if (!key) {
                      return;
                    }
                    if (rawValue == null) {
                      return;
                    }
                    if (typeof rawValue === 'string' && !String(rawValue || '').trim()) {
                      return;
                    }
                    nextCatProp[key] = rawValue;
                  });
                  formValues.catProp = nextCatProp;
                }
                const titleValue = String(formValues.title || '').trim();
                const draftTitle = String(patchSnapshot.draftTitle || '').trim();
                if (!titleValue && draftTitle) {
                  formValues.title = draftTitle;
                }
                if (Array.isArray(formValues.priceRange)) {
                  formValues.priceRange = patchRangeList(formValues.priceRange);
                }
                if (Array.isArray(formValues.skuTable)) {
                  formValues.skuTable = patchSkuRows(formValues.skuTable);
                }
                const sendAddressId = parsePositiveInteger(patchSnapshot.sendAddressId);
                if (sendAddressId != null) {
                  const currentSendAddress = formValues.cbuSendAddress;
                  let currentSendAddressId = null;
                  if (currentSendAddress && typeof currentSendAddress === 'object') {
                    currentSendAddressId = parsePositiveInteger(currentSendAddress.value);
                  } else {
                    currentSendAddressId = parsePositiveInteger(currentSendAddress);
                  }
                  if (currentSendAddressId == null) {
                    formValues.cbuSendAddress = { value: sendAddressId };
                  }
                }
                const draftQuantity = parsePositiveInteger(patchSnapshot.draftQuantity);
                if (draftQuantity != null) {
                  if (formValues.totalSales && typeof formValues.totalSales === 'object') {
                    if (parsePositiveInteger(formValues.totalSales.value) == null) {
                      formValues.totalSales = {
                        ...(formValues.totalSales || {}),
                        value: draftQuantity,
                      };
                    }
                  } else if (parsePositiveInteger(formValues.totalSales) == null) {
                    formValues.totalSales = draftQuantity;
                  }
                }
              };
              const visit = (node) => {
                if (!node || typeof node !== 'object') {
                  return;
                }
                if (visited.has(node)) {
                  return;
                }
                visited.add(node);
                if (node.formValues && typeof node.formValues === 'object') {
                  patchFormValues(node.formValues);
                }

                const nodeId = String(node.id || node.tag || node.name || node.UUID || '').trim();
                if (nodeId === 'title') {
                  const draftTitle = String(patchSnapshot.draftTitle || '').trim();
                  if (draftTitle) {
                    if (node.fields && typeof node.fields.value === 'string' && !String(node.fields.value || '').trim()) {
                      node.fields.value = draftTitle;
                    }
                    if (typeof node.value === 'string' && !String(node.value || '').trim()) {
                      node.value = draftTitle;
                    }
                  }
                } else if (nodeId === 'priceRange') {
                  if (Array.isArray(node.value)) {
                    node.value = patchRangeList(node.value);
                  }
                  if (node.fields && Array.isArray(node.fields.value)) {
                    node.fields.value = patchRangeList(node.fields.value);
                  }
                } else if (nodeId === 'skuTable') {
                  if (Array.isArray(node.value)) {
                    node.value = patchSkuRows(node.value);
                  }
                  if (node.fields && Array.isArray(node.fields.value)) {
                    node.fields.value = patchSkuRows(node.fields.value);
                  }
                } else if (nodeId === 'totalSales') {
                  const draftQuantity = parsePositiveInteger(patchSnapshot.draftQuantity);
                  if (draftQuantity != null) {
                    if (node.fields && node.fields.value && typeof node.fields.value === 'object') {
                      if (parsePositiveInteger(node.fields.value.value) == null) {
                        node.fields.value = {
                          ...(node.fields.value || {}),
                          value: draftQuantity,
                        };
                      }
                    } else if (node.fields && parsePositiveInteger(node.fields.value) == null) {
                      node.fields.value = draftQuantity;
                    }
                    if (node.value && typeof node.value === 'object') {
                      if (parsePositiveInteger(node.value.value) == null) {
                        node.value = {
                          ...(node.value || {}),
                          value: draftQuantity,
                        };
                      }
                    } else if (parsePositiveInteger(node.value) == null) {
                      node.value = draftQuantity;
                    }
                  }
                } else if (nodeId === 'cbuSendAddress') {
                  const sendAddressId = parsePositiveInteger(patchSnapshot.sendAddressId);
                  if (sendAddressId != null) {
                    if (node.fields && node.fields.value && typeof node.fields.value === 'object') {
                      if (parsePositiveInteger(node.fields.value.value) == null) {
                        node.fields.value = {
                          ...(node.fields.value || {}),
                          value: sendAddressId,
                        };
                      }
                    } else if (node.fields && parsePositiveInteger(node.fields.value) == null) {
                      node.fields.value = { value: sendAddressId };
                    }
                    if (node.value && typeof node.value === 'object') {
                      if (parsePositiveInteger(node.value.value) == null) {
                        node.value = {
                          ...(node.value || {}),
                          value: sendAddressId,
                        };
                      }
                    } else if (parsePositiveInteger(node.value) == null) {
                      node.value = { value: sendAddressId };
                    }
                  }
                }

                const primaryTargets = [];
                if (node.fields && node.fields.value && Array.isArray(node.fields.value.imageList)) {
                  primaryTargets.push(node.fields.value);
                }
                if (node.value && Array.isArray(node.value.imageList)) {
                  primaryTargets.push(node.value);
                }
                if (Array.isArray(node.imageList)) {
                  primaryTargets.push(node);
                }
                if (patchSnapshot.primaryUrls.length > 0) {
                  primaryTargets.forEach((target) => {
                    const existingImageList = Array.isArray(target.imageList) ? target.imageList : [];
                    target.imageList = existingImageList.map((item, index) => {
                      if (index >= patchSnapshot.primaryUrls.length) {
                        return item;
                      }
                      return {
                        ...(item || {}),
                        url: patchSnapshot.primaryUrls[index],
                      };
                    });
                    if (target.imageList.length === 0) {
                      target.imageList = patchSnapshot.primaryUrls.map((url) => ({ url }));
                    }
                  });
                }

                if (Array.isArray(patchSnapshot.supplyTypeValues) && patchSnapshot.supplyTypeValues.length > 0) {
                  if (String((node && node.id) || '').trim() === 'supplyType' && Array.isArray(node.value)) {
                    node.value = patchSnapshot.supplyTypeValues.slice();
                  }
                  if (node.fields && Array.isArray(node.fields.value)) {
                    node.fields.value = patchSnapshot.supplyTypeValues.slice();
                  }
                  if (Array.isArray(node.supplyType)) {
                    node.supplyType = patchSnapshot.supplyTypeValues.slice();
                  }
                  if (node.renderData && typeof node.renderData === 'object') {
                    node.renderData.cbuSupplyType = patchSnapshot.supplyTypeValues.slice();
                  }
                  if (Array.isArray(node.cbuSupplyType)) {
                    node.cbuSupplyType = patchSnapshot.supplyTypeValues.slice();
                  }
                }

                if (Array.isArray(patchSnapshot.deliveryServiceIds) && patchSnapshot.deliveryServiceIds.length > 0) {
                  const looksLikeCustomExtraNode = () => {
                    const marker = String(node.id || node.tag || node.name || node.UUID || '').trim().toLowerCase();
                    if (marker.includes('customextraservice')) {
                      return true;
                    }
                    const mapNode =
                      (node.value && node.value.viewModelMap && typeof node.value.viewModelMap === 'object' && node.value.viewModelMap) ||
                      (node.fields && node.fields.value && node.fields.value.viewModelMap && typeof node.fields.value.viewModelMap === 'object' && node.fields.value.viewModelMap) ||
                      (node.viewModelMap && typeof node.viewModelMap === 'object' && node.viewModelMap) ||
                      null;
                    if (!mapNode) {
                      return false;
                    }
                    return Object.keys(mapNode).some((key) => String(key || '').replace(/\\s+/g, '').includes('配送'));
                  };
                  if (looksLikeCustomExtraNode()) {
                    const deliveryServiceIds = normalizeServiceIds(patchSnapshot.deliveryServiceIds);
                    const patchCustomExtraValue = (target) => {
                      if (!target || typeof target !== 'object') {
                        return;
                      }
                      target.customServices = deliveryServiceIds.slice();
                      const nextMap = target.viewModelMap && typeof target.viewModelMap === 'object'
                        ? { ...target.viewModelMap }
                        : {};
                      const deliveryKey =
                        Object.keys(nextMap).find((key) => String(key || '').replace(/\\s+/g, '').includes('配送')) ||
                        '配送服务';
                      nextMap[deliveryKey] = deliveryServiceIds[0];
                      target.viewModelMap = nextMap;
                    };
                    if (node.fields && node.fields.value && typeof node.fields.value === 'object') {
                      patchCustomExtraValue(node.fields.value);
                    }
                    if (node.value && typeof node.value === 'object') {
                      patchCustomExtraValue(node.value);
                    }
                    patchCustomExtraValue(node);
                  }
                }

                const buyerTargets = [];
                if (node.fields && node.fields.value && node.fields.value.selectedServices) {
                  buyerTargets.push(node.fields.value);
                }
                if (node.value && node.value.selectedServices) {
                  buyerTargets.push(node.value);
                }
                if (node.selectedServices) {
                  buyerTargets.push(node);
                }
                if (Array.isArray(patchSnapshot.buyerProtectionSteps) && patchSnapshot.buyerProtectionSteps.length > 0) {
                  buyerTargets.forEach((target) => {
                    const nextGroups = Array.isArray(patchSnapshot.buyerProtectionGroups)
                      ? patchSnapshot.buyerProtectionGroups
                      : [];
                    const nextJgdzGroups = Array.isArray(patchSnapshot.buyerProtectionJgdzGroups)
                      ? patchSnapshot.buyerProtectionJgdzGroups
                      : [];
                    const nextSelectedServices = {
                      ...(target.selectedServices || {}),
                      dsc: nextGroups.length > 0 ? nextGroups : Array.isArray((target.selectedServices || {}).dsc)
                        ? target.selectedServices.dsc
                        : [],
                    };
                    if (nextJgdzGroups.length > 0) {
                      nextSelectedServices.jgdz = nextJgdzGroups;
                    } else if (Array.isArray((target.selectedServices || {}).jgdz) && target.selectedServices.jgdz.length > 0) {
                      nextSelectedServices.jgdz = target.selectedServices.jgdz;
                    } else {
                      delete nextSelectedServices.jgdz;
                    }
                    target.selectedServices = nextSelectedServices;
                    target.suggestBuyerProtectionDeliveryTime =
                      patchSnapshot.buyerProtectionSteps.length === 1
                        ? String((patchSnapshot.buyerProtectionSteps[0] || {}).serviceName || '')
                        : '';
                    if (patchSnapshot.includeBuyerProtectionSpsCode) {
                      target.spsCode = Array.isArray(patchSnapshot.buyerProtectionSpsCode)
                        ? patchSnapshot.buyerProtectionSpsCode
                            .map((item) => String(item || '').trim())
                            .filter(Boolean)
                        : Array.isArray(target.spsCode)
                          ? target.spsCode
                          : [];
                    } else if (Object.prototype.hasOwnProperty.call(target, 'spsCode')) {
                      delete target.spsCode;
                    }
                  });
                }

                const descriptionTargets = [];
                if (node.fields && node.fields.value && Array.isArray(node.fields.value.detailList)) {
                  descriptionTargets.push(node.fields.value);
                }
                if (node.value && Array.isArray(node.value.detailList)) {
                  descriptionTargets.push(node.value);
                }
                if (Array.isArray(node.detailList)) {
                  descriptionTargets.push(node);
                }
                if (patchSnapshot.detailHtml) {
                  descriptionTargets.forEach((target) => {
                    const detailList = Array.isArray(target.detailList) ? target.detailList : [];
                    if (!detailList[0]) {
                      return;
                    }
                    const currentContent = String(detailList[0].content || '').trim();
                    if (!currentContent) {
                      detailList[0].content = patchSnapshot.detailHtml;
                      return;
                    }
                    if (!currentContent.includes('<img')) {
                      detailList[0].content = patchSnapshot.detailHtml + currentContent;
                    }
                  });
                }

                Object.keys(node).forEach((key) => visit(node[key]));
              };
              visit(root);
            };

            const rewriteRequestBody = (body) => {
              const patchSnapshot = getPatchSnapshot();
              const applyPatch = Boolean(window.__codexDraftPatchConfig.applyPatch);
              const patchMode = String(window.__codexDraftPatchConfig.patchMode || '').trim() || 'full';
              const meta = {
                patchSnapshot,
                bodyType: typeof body,
                rewritten: false,
                patchApplied: applyPatch,
                patchMode,
              };
              if (body == null) {
                return { body, meta };
              }
              if (!applyPatch) {
                return { body, meta };
              }

              if (typeof body === 'string') {
                try {
                  const parsedJson = JSON.parse(body);
                  patchDraftObject(parsedJson, patchSnapshot);
                  meta.rewritten = true;
                  return { body: JSON.stringify(parsedJson), meta };
                } catch (error) {
                  // Fall through to URLSearchParams parsing.
                }

                try {
                  const params = new URLSearchParams(body);
                  if (Array.from(params.keys()).length === 0) {
                    return { body, meta };
                  }
                  let changed = false;
                  for (const [key, rawValue] of Array.from(params.entries())) {
                    const trimmedValue = String(rawValue || '').trim();
                    if (!trimmedValue.startsWith('{') && !trimmedValue.startsWith('[')) {
                      continue;
                    }
                    try {
                      const parsedValue = JSON.parse(trimmedValue);
                      patchDraftObject(parsedValue, patchSnapshot);
                      params.set(key, JSON.stringify(parsedValue));
                      changed = true;
                    } catch (error) {
                      continue;
                    }
                  }
                  meta.rewritten = changed;
                  return { body: params.toString(), meta };
                } catch (error) {
                  return { body, meta };
                }
              }

              if (typeof FormData !== 'undefined' && body instanceof FormData) {
                const cloned = new FormData();
                let changed = false;
                for (const [key, rawValue] of body.entries()) {
                  if (typeof rawValue !== 'string') {
                    cloned.append(key, rawValue);
                    continue;
                  }
                  const trimmedValue = String(rawValue || '').trim();
                  if (!trimmedValue.startsWith('{') && !trimmedValue.startsWith('[')) {
                    cloned.append(key, rawValue);
                    continue;
                  }
                  try {
                    const parsedValue = JSON.parse(trimmedValue);
                    patchDraftObject(parsedValue, patchSnapshot);
                    cloned.append(key, JSON.stringify(parsedValue));
                    changed = true;
                  } catch (error) {
                    cloned.append(key, rawValue);
                  }
                }
                meta.rewritten = changed;
                return { body: changed ? cloned : body, meta };
              }

              return { body, meta };
            };

            const installXHRPatch = () => {
              const originalOpen = XMLHttpRequest.prototype.open;
              const originalSend = XMLHttpRequest.prototype.send;
              XMLHttpRequest.prototype.open = function(method, url) {
                this.__codexDraftMeta = { method, url };
                return originalOpen.apply(this, arguments);
              };
              XMLHttpRequest.prototype.send = function(body) {
                const meta = this.__codexDraftMeta || {};
                const url = String(meta.url || '');
                if (!isDraftSubmitUrl(url)) {
                  return originalSend.call(this, body);
                }

                const record = {
                  transport: 'xhr',
                  method: String(meta.method || ''),
                  url,
                  originalBodyPreview: previewValue(body),
                };
                const rewritten = rewriteRequestBody(body);
                record.patch = rewritten.meta;
                record.patchedBodyPreview = previewValue(rewritten.body);
                this.addEventListener('loadend', function() {
                  record.status = this.status;
                  record.responseText = String(this.responseText || '');
                  try {
                    record.responseJson = JSON.parse(record.responseText);
                  } catch (error) {
                    record.responseJson = null;
                  }
                });
                window.__codexDraftSubmitRecords.push(record);
                return originalSend.call(this, rewritten.body);
              };
            };

            const installFetchPatch = () => {
              if (typeof window.fetch !== 'function') {
                return;
              }
              const originalFetch = window.fetch.bind(window);
              window.fetch = function() {
                const args = Array.from(arguments);
                const input = args[0];
                const url = typeof input === 'string' ? input : String((input && input.url) || '');
                if (!isDraftSubmitUrl(url)) {
                  return originalFetch.apply(window, args);
                }

                const requestInit = args[1] || {};
                const record = {
                  transport: 'fetch',
                  method: String(requestInit.method || 'GET'),
                  url,
                  originalBodyPreview: previewValue(requestInit.body),
                };
                const rewritten = rewriteRequestBody(requestInit.body);
                record.patch = rewritten.meta;
                record.patchedBodyPreview = previewValue(rewritten.body);
                args[1] = {
                  ...requestInit,
                  body: rewritten.body,
                };
                window.__codexDraftSubmitRecords.push(record);
                return originalFetch.apply(window, args).then(async (response) => {
                  try {
                    const clone = response.clone();
                    record.status = clone.status;
                    record.responseText = await clone.text();
                    try {
                      record.responseJson = JSON.parse(record.responseText);
                    } catch (error) {
                      record.responseJson = null;
                    }
                  } catch (error) {
                    record.responseText = String(error || '');
                    record.responseJson = null;
                  }
                  return response;
                });
              };
            };

            installXHRPatch();
            installFetchPatch();
            """,
            config_payload,
        )

    def _assert_draft_request_trace(
        self,
        publish_config: dict[str, Any],
        context: dict[str, Any],
    ) -> bool:
        if not self.driver:
            raise RuntimeError("Browser has not been opened.")

        patch_config = publish_config.get("draft_request_patch", {})
        if not patch_config or not patch_config.get("enabled", False):
            return True

        timeout_seconds = max(0.0, float(patch_config.get("timeout_seconds", 4) or 4))
        deadline = time.time() + timeout_seconds
        latest_record: dict[str, Any] | None = None
        while time.time() <= deadline:
            records = self.driver.execute_script(
                "return (window.__codexDraftSubmitRecords || []).map((item) => ({...item}));"
            )
            if records:
                latest_record = dict(records[-1] or {})
                if (
                    latest_record.get("responseText")
                    or latest_record.get("responseJson") is not None
                    or int(latest_record.get("status", 0) or 0) > 0
                ):
                    break
            time.sleep(0.2)

        if latest_record is None:
            context["draft_submit_trace_present"] = False
            return False

        context["draft_submit_trace_present"] = True
        context["draft_submit_trace"] = latest_record

        raw_status = latest_record.get("status", 0)
        try:
            status = int(raw_status or 0)
        except (TypeError, ValueError):
            status = 0
        context["draft_submit_response_status"] = status
        response_json = latest_record.get("responseJson")
        response_text = str(latest_record.get("responseText", "")).strip()
        has_response_payload = bool(response_text) or response_json is not None or status > 0
        if not has_response_payload:
            context["draft_submit_trace_complete"] = False
            return False
        context["draft_submit_trace_complete"] = True

        if status >= 400:
            raise PublishSubmitError(f"draft_submit request failed with HTTP status {status}.")

        if isinstance(response_json, dict) and response_json.get("success") is False:
            message = ""
            if isinstance(response_json.get("data"), dict):
                message = str(response_json["data"].get("message", "")).strip()
            message = message or str(response_json.get("message", "")).strip() or str(
                latest_record.get("responseText", "")
            ).strip()
            context["draft_submit_backend_message"] = message
            raise PublishSubmitError(f"draft_submit backend rejected request: {message}")

        failure_keywords = [
            str(item).strip()
            for item in patch_config.get("response_failure_keywords", [])
            if str(item).strip()
        ]
        if self._contains_any_keyword(response_text, failure_keywords):
            context["draft_submit_backend_message"] = response_text
            raise PublishSubmitError(f"draft_submit backend returned error response: {response_text}")
        return True

    def _install_submit_request_trace(self, publish_config: dict[str, Any]) -> None:
        if not self.driver:
            raise RuntimeError("Browser has not been opened.")
        patch_config = publish_config.get("submit_request_trace", {})
        if patch_config and not patch_config.get("enabled", True):
            return
        capture_body_chars = max(256, int((patch_config or {}).get("capture_body_chars", 3000) or 3000))
        self.driver.execute_script(
            """
            const captureChars = arguments[0];
            window.__codexPublishSubmitRecords = [];
            if (window.__codexPublishSubmitTraceInstalled) {
              return;
            }
            window.__codexPublishSubmitTraceInstalled = true;

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
                return /(^|\\/)(draftSubmit|submit)\\.htm(?:$|[?#])/i.test(text) && !/arms-retcode/i.test(text);
              }
            };

            const previewBody = (value) => {
              if (value == null) {
                return '';
              }
              if (typeof value === 'string') {
                return value.slice(0, captureChars);
              }
              if (typeof FormData !== 'undefined' && value instanceof FormData) {
                return Array.from(value.entries())
                  .map(([key, bodyValue]) => `${key}=${typeof bodyValue === 'string' ? bodyValue : '[binary]'}`)
                  .join('&')
                  .slice(0, captureChars);
              }
              try {
                return JSON.stringify(value).slice(0, captureChars);
              } catch (error) {
                return String(value).slice(0, captureChars);
              }
            };

            const updateResponse = (record, status, responseText) => {
              record.status = Number(status || 0);
              record.responseText = String(responseText || '').slice(0, 12000);
              try {
                record.responseJson = JSON.parse(record.responseText);
              } catch (error) {
                record.responseJson = null;
              }
            };

            const originalOpen = XMLHttpRequest.prototype.open;
            const originalSend = XMLHttpRequest.prototype.send;
            XMLHttpRequest.prototype.open = function(method, url) {
              this.__codexPublishSubmitMeta = { method, url };
              return originalOpen.apply(this, arguments);
            };
            XMLHttpRequest.prototype.send = function(body) {
              const meta = this.__codexPublishSubmitMeta || {};
              const requestUrl = normalizeUrl(meta.url || '');
              if (!isSubmitUrl(requestUrl)) {
                return originalSend.call(this, body);
              }
              const record = {
                transport: 'xhr',
                method: String(meta.method || ''),
                url: requestUrl,
                requestBodyPreview: previewBody(body),
              };
              this.addEventListener('loadend', function() {
                updateResponse(record, this.status, this.responseText || '');
              });
              window.__codexPublishSubmitRecords.push(record);
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
                  requestBodyPreview: previewBody(init.body),
                };
                window.__codexPublishSubmitRecords.push(record);
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
        )

    def _assert_submit_request_trace(self, publish_config: dict[str, Any], context: dict[str, Any]) -> bool:
        if not self.driver:
            raise RuntimeError("Browser has not been opened.")
        patch_config = publish_config.get("submit_request_trace", {})
        if patch_config and not patch_config.get("enabled", True):
            return True

        timeout_seconds = max(0.0, float((patch_config or {}).get("timeout_seconds", 5) or 5))
        deadline = time.time() + timeout_seconds
        latest_record: dict[str, Any] | None = None
        while time.time() <= deadline:
            records = self.driver.execute_script(
                "return (window.__codexPublishSubmitRecords || []).map((item) => ({...item}));"
            )
            if records:
                latest_record = dict(records[-1] or {})
                if (
                    latest_record.get("responseText")
                    or latest_record.get("responseJson") is not None
                    or int(latest_record.get("status", 0) or 0) > 0
                ):
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

        if isinstance(response_json, dict) and response_json.get("success") is False:
            message = ""
            if isinstance(response_json.get("data"), dict):
                message = str(response_json["data"].get("message", "")).strip()
            message = message or str(response_json.get("message", "")).strip()
            raise PublishSubmitError(f"submit backend rejected request: {message or response_json}")

        failure_keywords = [
            str(item).strip()
            for item in (patch_config or {}).get("response_failure_keywords", [])
            if str(item).strip()
        ]
        if self._contains_any_keyword(response_text, failure_keywords):
            raise PublishSubmitError(f"submit backend returned error response: {response_text}")
        return True

    def _verify_submit_result(self, publish_config: dict[str, Any], context: dict[str, Any]) -> None:
        verification = publish_config.get("submit_verification", {})
        if not verification or not verification.get("enabled", False):
            context["post_submit_verified"] = "skipped"
            return
        if not self.driver:
            raise RuntimeError("Browser has not been opened.")

        self._pause(float(verification.get("settle_seconds", 1.2)))
        current_url = str(self.driver.current_url or "")
        page_text = self._extract_error_text({}) if verification.get("check_page_text", True) else ""
        success_url_keywords = [
            str(item).strip()
            for item in verification.get("success_url_keywords", [])
            if str(item).strip()
        ]
        success_text_keywords = [
            str(item).strip()
            for item in verification.get("success_text_keywords", [])
            if str(item).strip()
        ]
        forbidden_url_keywords = [
            str(item).strip()
            for item in verification.get("forbidden_url_keywords", [])
            if str(item).strip()
        ]
        url_success = self._contains_any_keyword(current_url, success_url_keywords) if success_url_keywords else False
        text_success = self._contains_any_keyword(page_text, success_text_keywords) if success_text_keywords else False
        forbidden_hit = self._contains_any_keyword(current_url, forbidden_url_keywords) if forbidden_url_keywords else False

        if verification.get("require_leave_publish_page", False) and "/popular/publish.htm" in current_url:
            forbidden_hit = True

        if forbidden_hit and not (url_success or text_success):
            context["post_submit_verified"] = "false"
            raise PublishSubmitError("submit verification failed: still on publish page or entered forbidden URL state.")

        if verification.get("require_success_signal", False) and not (url_success or text_success):
            context["post_submit_verified"] = "false"
            raise PublishSubmitError("submit verification failed: success signal not detected.")

        context["post_submit_verified"] = "true"

    def _resolve_context_preferred_value(
        self,
        *,
        context: dict[str, Any],
        source: str,
        default_value: str,
    ) -> str:
        if source:
            value = str(context.get(source, "")).strip()
            if value:
                return value
        return str(default_value).strip()

    def _build_buyer_protection_step_template(
        self,
        step_config: Any,
        context: dict[str, Any],
    ) -> list[dict[str, Any]]:
        if not isinstance(step_config, list):
            return []

        result: list[dict[str, Any]] = []
        for item in step_config:
            if not isinstance(item, dict):
                continue
            try:
                from_value = int(item.get("from"))
            except (TypeError, ValueError):
                continue
            if from_value <= 0:
                continue

            service_code = self._resolve_context_preferred_value(
                context=context,
                source=str(item.get("service_code_source", "")).strip(),
                default_value=str(item.get("service_code", item.get("serviceCode", ""))).strip(),
            )
            service_name = self._resolve_context_preferred_value(
                context=context,
                source=str(item.get("service_source", "")).strip(),
                default_value=str(item.get("service_name", "")).strip(),
            )
            if not service_name and not service_code:
                continue

            normalized_step: dict[str, Any] = {
                "from": from_value,
            }
            if service_name:
                normalized_step["serviceName"] = service_name
            if service_code:
                normalized_step["serviceCode"] = service_code
            try:
                end_value = int(item.get("end"))
            except (TypeError, ValueError):
                end_value = 0
            if end_value >= from_value:
                normalized_step["end"] = end_value
            result.append(normalized_step)

        result.sort(key=lambda item: int(item.get("from", 0) or 0))
        return result

    def _resolve_delivery_service_ids(
        self,
        *,
        patch_config: dict[str, Any],
        context: dict[str, Any],
    ) -> list[int]:
        values: list[Any] = []
        delivery_state = context.get("draft_delivery_service_state")
        if isinstance(delivery_state, dict):
            values.extend(list(delivery_state.get("selectedServiceIds", []) or []))

            label_map = patch_config.get("delivery_service_label_id_map", {})
            if isinstance(label_map, dict):
                for label in list(delivery_state.get("selectedLabels", []) or []):
                    mapped = label_map.get(str(label).strip())
                    if mapped is not None:
                        values.append(mapped)

        values.extend(list(context.get("delivery_service_ids", []) or []))
        values.extend(list(patch_config.get("delivery_service_ids", []) or []))
        values.extend(list(patch_config.get("delivery_service_default_ids", []) or []))

        normalized: list[int] = []
        seen: set[str] = set()
        for raw in values:
            text = str(raw or "").strip()
            if not text:
                continue
            if text.lower() in {"on", "off", "true", "false"}:
                continue
            try:
                normalized_value = int(text)
            except (TypeError, ValueError):
                continue
            if normalized_value <= 0:
                continue
            key = str(normalized_value)
            if key in seen:
                continue
            seen.add(key)
            normalized.append(normalized_value)
        return normalized

    def _build_draft_page_state_patch_payload(
        self,
        publish_config: dict[str, Any],
        context: dict[str, Any],
    ) -> dict[str, Any]:
        patch_config = publish_config.get("draft_page_state_patch", {})
        if not patch_config or not patch_config.get("enabled", False):
            return {}

        cat_prop_patches: list[dict[str, str]] = []
        seen_cat_prop_labels: set[str] = set()

        def add_cat_prop_patch(label: str, value: str) -> None:
            normalized_label = str(label).strip()
            if not normalized_label:
                return
            if normalized_label in seen_cat_prop_labels:
                return
            seen_cat_prop_labels.add(normalized_label)
            cat_prop_patches.append(
                {
                    "label": normalized_label,
                    "value": str(value or "").strip(),
                }
            )

        for item in patch_config.get("cat_props", []):
            if not isinstance(item, dict):
                continue
            label = str(item.get("label", "")).strip()
            if not label:
                continue
            value = self._resolve_context_preferred_value(
                context=context,
                source=str(item.get("source", "")).strip(),
                default_value=str(item.get("default_value", "")).strip(),
            )
            add_cat_prop_patch(label, value)

        required_field_labels = [
            self._normalize_required_label(item)
            for item in list(context.get("draft_required_field_labels", []) or [])
            if str(item).strip()
        ]
        required_field_labels = [item for item in required_field_labels if item]
        if required_field_labels:
            cat_prop_hint_values = self._collect_category_prop_hints(publish_config, context)
            for required_label in required_field_labels:
                if required_label in {"发货时间", "配送服务", "图文详情"}:
                    continue
                resolved_value = str(cat_prop_hint_values.get(required_label, "")).strip()
                if not resolved_value:
                    for hint_label, hint_value in cat_prop_hint_values.items():
                        normalized_hint_label = str(hint_label).strip()
                        if not normalized_hint_label:
                            continue
                        if (
                            normalized_hint_label == required_label
                            or normalized_hint_label in required_label
                            or required_label in normalized_hint_label
                        ):
                            resolved_value = str(hint_value or "").strip()
                            if resolved_value:
                                break
                add_cat_prop_patch(required_label, resolved_value)

        buyer_protection_value = self._resolve_context_preferred_value(
            context=context,
            source=str(patch_config.get("buyer_protection_source", "")).strip(),
            default_value=str(patch_config.get("buyer_protection_default_value", "")).strip(),
        )
        buyer_protection_code = self._resolve_context_preferred_value(
            context=context,
            source=str(patch_config.get("buyer_protection_service_code_source", "")).strip(),
            default_value=str(patch_config.get("buyer_protection_service_code", "")).strip(),
        )
        if not buyer_protection_code:
            buyer_protection_code = str(context.get("buyer_protection_ship_time_code", "")).strip()
        runtime_step_template = context.get("buyer_protection_step_template_runtime")
        step_template_config = (
            runtime_step_template
            if isinstance(runtime_step_template, list) and runtime_step_template
            else patch_config.get("buyer_protection_step_template", [])
        )
        buyer_protection_steps = self._build_buyer_protection_step_template(
            step_template_config,
            context,
        )
        delivery_service_ids = self._resolve_delivery_service_ids(
            patch_config=patch_config,
            context=context,
        )
        if not buyer_protection_code and buyer_protection_steps:
            buyer_protection_code = str((buyer_protection_steps[0] or {}).get("serviceCode", "")).strip()
        buyer_protection_name = buyer_protection_value or (
            str((buyer_protection_steps[0] or {}).get("serviceName", "")).strip() if buyer_protection_steps else ""
        )
        return {
            "catPropPatches": cat_prop_patches,
            "buyerProtectionServiceName": buyer_protection_name,
            "buyerProtectionServiceCode": buyer_protection_code,
            "buyerProtectionStepTemplate": buyer_protection_steps,
            "deliveryServiceIds": delivery_service_ids,
            "includeBuyerProtectionSpsCode": bool(patch_config.get("buyer_protection_include_sps_code", False)),
        }

    def _collect_category_prop_hints(
        self,
        publish_config: dict[str, Any],
        context: dict[str, Any],
    ) -> dict[str, str]:
        hints: dict[str, str] = {}

        for step in list(publish_config.get("steps", []) or []):
            if not isinstance(step, dict):
                continue
            if str(step.get("action", "")).strip() != "category_prop_defaults":
                continue
            for rule in self._resolve_profile_rules(step, context):
                label = str(rule.get("label", "")).strip()
                if not label:
                    continue
                value = self._resolve_profile_rule_value(rule, context)
                if value:
                    hints[label] = value

        patch_config = publish_config.get("draft_page_state_patch", {})
        if isinstance(patch_config, dict):
            for item in list(patch_config.get("cat_props", []) or []):
                if not isinstance(item, dict):
                    continue
                label = str(item.get("label", "")).strip()
                if not label:
                    continue
                value = self._resolve_context_preferred_value(
                    context=context,
                    source=str(item.get("source", "")).strip(),
                    default_value=str(item.get("default_value", "")).strip(),
                )
                if value and label not in hints:
                    hints[label] = value

        fallback_field_map = {
            "品牌": "brand",
            "材质": "material",
            "台面材质": "material",
            "风格": "style",
            "型号": "outer_sku",
        }
        for label, source in fallback_field_map.items():
            if label in hints:
                continue
            value = str(context.get(source, "")).strip()
            if value:
                hints[label] = value

        return hints

    def _normalize_required_label(self, raw_label: str) -> str:
        label = str(raw_label or "").strip()
        if not label:
            return ""
        label = label.replace("为必填项", "").replace("不能为空", "").strip()
        label = re.sub(r"^\u7b2c\s*[0-9\uff10-\uff19]+\s*\u884c(?:\u7684)?", "", label).strip()
        for prefix in ("买家保障", "特色服务", "商品详情", "物流服务", "服务保障"):
            if label.startswith(prefix):
                label = label[len(prefix) :].strip()
        label = re.sub(r"^\u7b2c\s*[0-9\uff10-\uff19]+\s*\u884c(?:\u7684)?", "", label).strip()
        if " " in label:
            parts = [item.strip() for item in label.split(" ") if item.strip()]
            if len(parts) > 1:
                label = parts[-1]
        return label.strip("：:，,。.；; ")

    def _extract_required_labels_from_assist_messages(self, messages: list[str]) -> list[str]:
        labels: list[str] = []
        seen: set[str] = set()
        patterns = [
            re.compile(r"(?:请填写|请完善|请选择)\s*([^，。；;：:]+)"),
            re.compile(r"([^，。；;：:]+?)为必填项"),
        ]
        for raw_message in messages:
            message = str(raw_message or "").strip()
            if not message:
                continue
            raw_labels: list[str] = []
            for pattern in patterns:
                raw_labels.extend(match.strip() for match in pattern.findall(message) if str(match).strip())
            if not raw_labels and ("必填" in message or "请填写" in message or "请完善" in message):
                raw_labels.append(message)
            for raw_label in raw_labels:
                normalized = self._normalize_required_label(raw_label)
                if not normalized:
                    continue
                if len(normalized) > 40:
                    continue
                if normalized in seen:
                    continue
                seen.add(normalized)
                labels.append(normalized)
        return labels

    def _ensure_required_cat_props_before_draft_save(
        self,
        publish_config: dict[str, Any],
        context: dict[str, Any],
    ) -> None:
        if not self.driver:
            raise RuntimeError("Browser has not been opened.")
        preferred_values = self._collect_category_prop_hints(publish_config, context)
        target_labels = [
            self._normalize_required_label(item)
            for item in list(context.get("draft_required_field_labels", []) or [])
            if str(item).strip()
        ]
        target_labels = [item for item in target_labels if item]
        state = self.driver.execute_script(
            """
            const preferredMapRaw = arguments[0] || {};
            const targetLabelsRaw = Array.isArray(arguments[1]) ? arguments[1] : [];

            const norm = (value) => String(value || '').replace(/\\s+/g, ' ').trim();
            const clone = (value) => JSON.parse(JSON.stringify(value || {}));
            const normalizeMap = () => {
              const entries = [];
              Object.entries(preferredMapRaw || {}).forEach(([key, value]) => {
                const normalizedKey = norm(key);
                const normalizedValue = String(value || '').trim();
                if (!normalizedKey || !normalizedValue) {
                  return;
                }
                entries.push([normalizedKey, normalizedValue]);
              });
              return entries;
            };
            const preferredEntries = normalizeMap();
            const targetLabels = targetLabelsRaw.map((item) => norm(item)).filter(Boolean);

            const sdk = window.SellPublishSdk;
            const engine = sdk && sdk.engine;
            const core = engine && engine._engine && engine._engine._core;
            if (!engine || !engine.getJsonState || !core || !core.changeElementValue) {
              return {
                ok: false,
                reason: '1688 runtime core is unavailable',
              };
            }

            const state = engine.getJsonState() || {};
            const components = state.components || {};
            const catPropProps = ((components.catProp || {}).props) || {};
            const dataSource = Array.isArray(catPropProps.dataSource) ? catPropProps.dataSource : [];
            const nextValue = clone(catPropProps.value || {});
            const result = {
              ok: true,
              targetLabels,
              appliedLabels: [],
              unresolvedLabels: [],
            };

            const hasValue = (value) => {
              if (Array.isArray(value)) {
                return value.length > 0;
              }
              if (value && typeof value === 'object') {
                return Object.values(value).some((item) => {
                  if (Array.isArray(item)) {
                    return item.length > 0;
                  }
                  return String(item == null ? '' : item).trim() !== '';
                });
              }
              return String(value == null ? '' : value).trim() !== '';
            };

            const shouldHandleLabel = (label, required) => {
              const normalized = norm(label);
              if (!normalized) {
                return false;
              }
              if (targetLabels.length > 0) {
                return targetLabels.some(
                  (item) =>
                    item === normalized ||
                    item.includes(normalized) ||
                    normalized.includes(item)
                );
              }
              return Boolean(required);
            };

            const resolvePreferredValue = (label) => {
              const normalized = norm(label);
              if (!normalized) {
                return '';
              }
              const exact = preferredEntries.find(([key]) => key === normalized);
              if (exact) {
                return exact[1];
              }
              const fuzzy = preferredEntries.find(
                ([key]) => key.includes(normalized) || normalized.includes(key)
              );
              return fuzzy ? fuzzy[1] : '';
            };

            let changed = false;
            dataSource.forEach((item) => {
              const label = norm(item && item.label);
              const propKey = String((item && item.name) || '').trim();
              if (!label || !propKey) {
                return;
              }
              if (!shouldHandleLabel(label, item && item.required)) {
                return;
              }
              if (hasValue(nextValue[propKey])) {
                return;
              }

              const preferredValue = resolvePreferredValue(label);
              const options = Array.isArray(item && item.dataSource) ? item.dataSource : [];
              const uiType = String((item && item.uiType) || '').trim().toLowerCase();
              const isMulti = uiType === 'checkbox' || uiType === 'multiselect';

              const matchedOption =
                (preferredValue
                  ? (
                      options.find((option) => norm(option && option.text) === norm(preferredValue)) ||
                      options.find((option) => norm(option && option.text).includes(norm(preferredValue))) ||
                      options.find((option) => norm(preferredValue).includes(norm(option && option.text)))
                    )
                  : null) ||
                options[0] ||
                null;

              if (matchedOption) {
                const normalizedOption = {
                  value: matchedOption.value,
                  text: matchedOption.text,
                };
                nextValue[propKey] = isMulti ? [normalizedOption] : normalizedOption;
                result.appliedLabels.push(label);
                changed = true;
                return;
              }

              if (preferredValue) {
                const normalizedCustom = {
                  value: preferredValue,
                  text: preferredValue,
                  custom: true,
                };
                nextValue[propKey] = isMulti ? [normalizedCustom] : normalizedCustom;
                result.appliedLabels.push(label);
                changed = true;
                return;
              }

              result.unresolvedLabels.push(label);
            });

            if (changed) {
              core.changeElementValue('catProp', nextValue, { isDepth: false });
            }
            return result;
            """,
            preferred_values,
            target_labels,
        )
        context["draft_required_cat_props_state"] = state if isinstance(state, dict) else {"ok": False}

    def _normalize_1688_ibank_image_url(self, raw_value: str) -> str:
        value = str(raw_value or "").strip()
        if not value:
            return ""
        if value.startswith("img/ibank/"):
            return value
        match = re.search(r"/(img/ibank/[^?#]+)", value, re.IGNORECASE)
        if match:
            return match.group(1)
        return value

    def _remove_elements(self, selector: dict[str, str]) -> None:
        if not self.driver:
            raise RuntimeError("Browser has not been opened.")
        if not self._selector_is_configured(selector):
            return
        self.driver.execute_script(
            """
            const selector = arguments[0];
            document.querySelectorAll(selector).forEach((node) => node.remove());
            """,
            selector.get("value", ""),
        )

    def _wait_for_dialog(self, selector: dict[str, str]) -> WebElement:
        try:
            return self._wait_for_element(selector)
        except TimeoutException:
            # Some 1688 upload tiles only respond to native mouse events, so keep the
            # original timeout behavior for callers after a single retry window.
            return self._wait_for_element(selector)

    def _trigger_picker_opener(self, element: WebElement) -> None:
        if not self.driver:
            raise RuntimeError("Browser has not been opened.")
        self.driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", element)
        self.driver.execute_script(
            """
            const target = arguments[0];
            const rect = target.getBoundingClientRect();
            const hitNode = document.elementFromPoint(
              rect.left + rect.width / 2,
              rect.top + rect.height / 2,
            );
            (hitNode || target).click();
            """,
            element,
        )
        self._pause(0.5)
        if self.driver.find_elements(By.CSS_SELECTOR, "div.ibank-picker-dialog"):
            return
        self.driver.execute_script(
            """
            const target = arguments[0];
            const rect = target.getBoundingClientRect();
            const node = document.elementFromPoint(
              rect.left + rect.width / 2,
              rect.top + rect.height / 2,
            ) || target;
            ['mouseover', 'mousedown', 'mouseup', 'click'].forEach((eventName) => {
              node.dispatchEvent(
                new MouseEvent(eventName, {
                  bubbles: true,
                  cancelable: true,
                  view: window,
                  clientX: rect.left + Math.min(10, rect.width / 2),
                  clientY: rect.top + Math.min(10, rect.height / 2),
                })
              );
            });
            """,
            element,
        )
        self._pause(0.5)

    def _check_publish_error_state(
        self,
        detection_config: dict[str, Any],
        *,
        context: dict[str, Any] | None = None,
        stage_name: str,
        exception_cls: type[Exception] = ValueError,
    ) -> None:
        detection_config = self._resolve_error_detection(detection_config, context or {})
        if not detection_config or not detection_config.get("enabled", True):
            return
        if not self.driver:
            raise RuntimeError("Browser has not been opened.")

        timeout_seconds = float(detection_config.get("timeout_seconds", 0))
        deadline = time.time() + max(timeout_seconds, 0)
        keywords = [str(item).strip() for item in detection_config.get("keywords", []) if str(item).strip()]
        validation_keywords = [
            str(item).strip() for item in detection_config.get("validation_keywords", []) if str(item).strip()
        ]
        error_text = ""

        while True:
            error_text = self._extract_error_text(detection_config)
            if self._contains_any_keyword(error_text, validation_keywords):
                self._close_error_dialog(detection_config)
                self._annotate_page_error_context(
                    context,
                    stage_name=stage_name,
                    error_text=error_text,
                    error_category="business_validation",
                )
                raise PublishValidationError(f"{stage_name} blocked by page validation: {error_text}")

            if self._contains_any_keyword(error_text, keywords):
                self._close_error_dialog(detection_config)
                self._annotate_page_error_context(
                    context,
                    stage_name=stage_name,
                    error_text=error_text,
                    error_category="page_error",
                )
                raise exception_cls(f"{stage_name} blocked by page error: {error_text}")

            if time.time() >= deadline:
                break
            time.sleep(0.2)

    def _annotate_page_error_context(
        self,
        context: dict[str, Any] | None,
        *,
        stage_name: str,
        error_text: str,
        error_category: str,
    ) -> None:
        if context is None:
            return
        context["page_error_stage"] = stage_name
        context["page_error_text"] = error_text
        context["page_error_category"] = error_category

    def _extract_error_text(self, detection_config: dict[str, Any]) -> str:
        if not self.driver:
            raise RuntimeError("Browser has not been opened.")

        message_selector = detection_config.get("message_selector", {})
        if self._selector_is_configured(message_selector):
            try:
                elements = self.driver.find_elements(
                    BY_MAPPING.get(message_selector.get("by", "css").strip().lower(), By.CSS_SELECTOR),
                    message_selector.get("value", "").strip(),
                )
                visible_texts = [
                    " ".join((element.text or "").split())
                    for element in elements
                    if " ".join((element.text or "").split())
                ]
                return " | ".join(visible_texts)
            except Exception:
                return ""

        try:
            body_text = self.driver.find_element(By.TAG_NAME, "body").text
        except Exception:
            return ""
        return " ".join(body_text.split())

    def _contains_any_keyword(self, body_text: str, keywords: list[str]) -> bool:
        if not body_text:
            return False
        if not keywords:
            return False
        return any(keyword in body_text for keyword in keywords)

    def _close_error_dialog(self, detection_config: dict[str, Any]) -> None:
        close_selector = detection_config.get("close_selector", {})
        if not self._selector_is_configured(close_selector):
            return
        try:
            self._wait_for_element(close_selector, clickable=True).click()
        except Exception:
            return

    def _wait_for_element(
        self,
        selector: dict[str, str],
        *,
        clickable: bool = False,
    ) -> WebElement:
        if not self.driver:
            raise RuntimeError("Browser has not been opened.")

        by_key = selector.get("by", "css").strip().lower()
        locator = (BY_MAPPING.get(by_key, By.CSS_SELECTOR), selector.get("value", "").strip())
        wait = WebDriverWait(self.driver, self.browser_config.get("explicit_wait_seconds", 20))

        try:
            if clickable:
                return wait.until(EC.element_to_be_clickable(locator))
            return wait.until(EC.presence_of_element_located(locator))
        except TimeoutException as exc:
            raise TimeoutException(f"Timed out waiting for selector: {selector}") from exc

    def _resolve_selector(
        self,
        selector: dict[str, Any] | None,
        context: dict[str, Any],
    ) -> dict[str, str]:
        if not selector:
            return {}
        return {
            "by": str(selector.get("by", "css")).strip() or "css",
            "value": self._render_template(str(selector.get("value", "")).strip(), context),
        }

    def _resolve_error_detection(
        self,
        detection_config: dict[str, Any] | None,
        context: dict[str, Any],
    ) -> dict[str, Any]:
        if not detection_config:
            return {}
        resolved = dict(detection_config)
        for key in ("message_selector", "close_selector"):
            resolved[key] = self._resolve_selector(detection_config.get(key, {}), context)
        return resolved

    def _render_template(self, template: str, context: dict[str, Any]) -> str:
        if not template or "{" not in template:
            return template

        def replace(match: re.Match[str]) -> str:
            key = match.group(1)
            value = context.get(key, "")
            return str(value).strip()

        return re.sub(r"\{([a-zA-Z0-9_]+)\}", replace, template)

    def _split_category_path(self, raw_value: str) -> list[str]:
        if not raw_value:
            return []
        return [
            item.strip()
            for item in re.split(r"\s*>\s*", raw_value)
            if item.strip()
        ]

    def _selector_is_configured(self, selector: dict[str, str] | None) -> bool:
        if not selector:
            return False
        return bool(str(selector.get("value", "")).strip())

    def _click_with_javascript(self, selector: dict[str, str]) -> None:
        element = self._wait_for_element(selector, clickable=False)
        if not self.driver:
            raise RuntimeError("Browser has not been opened.")
        self.driver.execute_script("arguments[0].click();", element)

    def _dispatch_click_with_events(self, selector: dict[str, str]) -> None:
        if not self.driver:
            raise RuntimeError("Browser has not been opened.")
        element = self._wait_for_element(selector, clickable=False)
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

    def _ensure_draft_send_address_selected(self, context: dict[str, Any]) -> None:
        if not self.driver:
            raise RuntimeError("Browser has not been opened.")
        result: dict[str, Any] | None = None
        for _ in range(3):
            state = self.driver.execute_script(
                """
                const root = document.querySelector('#guid-cbuSendAddress');
                if (!root) {
                  return { exists: false, selected: false, clicked: false };
                }
                function isVisible(node) {
                  if (!node) return false;
                  const style = window.getComputedStyle(node);
                  const rect = node.getBoundingClientRect();
                  return style.display !== 'none' && style.visibility !== 'hidden' && rect.width > 0 && rect.height > 0;
                }
                function norm(value) {
                  return String(value || '').replace(/\\s+/g, ' ').trim();
                }
                const sdk = window.SellPublishSdk;
                const getSelectedValueFromState = () => {
                  const runtimeState = sdk && sdk.engine && sdk.engine.getJsonState ? sdk.engine.getJsonState() : {};
                  const runtimeProps = (((runtimeState || {}).components || {}).cbuSendAddress || {}).props || {};
                  return String((runtimeProps.value == null ? '' : runtimeProps.value)).trim();
                };
                const selectedNode = Array.from(root.querySelectorAll('.ant-select-selection-item')).find(isVisible) || null;
                const selectedText = norm(selectedNode ? selectedNode.innerText || selectedNode.textContent || '' : '');
                const selectedValue = getSelectedValueFromState();
                if (selectedText && !selectedText.includes('请选择')) {
                  return { exists: true, selected: true, clicked: false, selectedText, selectedValue };
                }
                const selectorNode =
                  Array.from(root.querySelectorAll('.ant-select-selector')).find(isVisible) ||
                  Array.from(root.querySelectorAll('.ant-select')).find(isVisible) ||
                  null;
                if (!selectorNode || !isVisible(selectorNode)) {
                  return { exists: true, selected: false, clicked: false, reason: 'selector_not_visible' };
                }
                selectorNode.scrollIntoView({block: 'center', inline: 'nearest'});
                selectorNode.click();
                const options = Array.from(
                  document.querySelectorAll(
                    '.ant-select-dropdown:not(.ant-select-dropdown-hidden) .ant-select-item-option'
                  )
                ).filter((node) => isVisible(node));
                const target = options.find((node) => {
                  const disabled = node.classList.contains('ant-select-item-option-disabled');
                  if (disabled) {
                    return false;
                  }
                  const text = norm(node.innerText || node.textContent || '');
                  return Boolean(text);
                }) || null;
                if (target) {
                  target.click();
                }
                const afterNode = Array.from(root.querySelectorAll('.ant-select-selection-item')).find(isVisible) || null;
                const afterText = norm(afterNode ? afterNode.innerText || afterNode.textContent || '' : '');
                const afterValue = getSelectedValueFromState();
                return {
                  exists: true,
                  selected: Boolean(afterText && !afterText.includes('请选择')),
                  clicked: Boolean(target),
                  selectedText: afterText,
                  selectedValue: afterValue,
                };
                """
            )
            result = state if isinstance(state, dict) else {"exists": False, "selected": False}
            if bool(result.get("selected")):
                break
            self._pause(0.6)
        context["draft_send_address_state"] = result if isinstance(result, dict) else {"exists": False}
        if isinstance(result, dict):
            selected_value = str(result.get("selectedValue", "")).strip()
            if selected_value:
                context["send_address_id"] = selected_value

    def _ensure_draft_required_delivery_service(self, context: dict[str, Any]) -> None:
        if not self.driver:
            raise RuntimeError("Browser has not been opened.")
        result: dict[str, Any] | None = None
        for _ in range(3):
            state = self.driver.execute_script(
                """
                const root = document.querySelector('#guid-customExtraService');
                if (!root) {
                  return {
                    exists: false,
                    selected: false,
                    clicked: false,
                    selectedLabels: [],
                    selectedServiceIds: [],
                  };
                }
                function isVisible(node) {
                  if (!node) return false;
                  const style = window.getComputedStyle(node);
                  const rect = node.getBoundingClientRect();
                  return style.display !== 'none' && style.visibility !== 'hidden' && rect.width > 0 && rect.height > 0;
                }
                function parseServiceId(value) {
                  const text = String(value == null ? '' : value).trim();
                  if (!text) {
                    return null;
                  }
                  if (['on', 'off', 'true', 'false'].includes(text.toLowerCase())) {
                    return null;
                  }
                  const asNumber = Number(text);
                  if (!Number.isFinite(asNumber)) {
                    return null;
                  }
                  const normalized = String(Math.trunc(asNumber));
                  if (normalized !== text) {
                    return null;
                  }
                  return Math.trunc(asNumber);
                }
                function uniqueIds(values) {
                  const result = [];
                  const seen = new Set();
                  (Array.isArray(values) ? values : []).forEach((item) => {
                    if (item == null) return;
                    const key = String(item);
                    if (seen.has(key)) return;
                    seen.add(key);
                    result.push(item);
                  });
                  return result;
                }
                function selectedLabels(inputs) {
                  return (Array.isArray(inputs) ? inputs : [])
                    .filter((item) => item && item.checked)
                    .map((item) => {
                      const label = item.closest('label');
                      if (!label) return '';
                      const textNode = label.querySelector('span:last-child');
                      return String((textNode && (textNode.innerText || textNode.textContent)) || '')
                        .replace(/\\s+/g, ' ')
                        .trim();
                    })
                    .filter(Boolean);
                }
                function selectedServiceIds(inputs) {
                  const raw = (Array.isArray(inputs) ? inputs : [])
                    .filter((item) => item && item.checked)
                    .map((item) => {
                      const direct = parseServiceId(item.value);
                      if (direct != null) return direct;
                      const label = item.closest('label');
                      if (!label) return null;
                      const attrs = ['data-value', 'data-id', 'data-service-id', 'data-service'];
                      for (const attr of attrs) {
                        const parsed = parseServiceId(label.getAttribute(attr));
                        if (parsed != null) return parsed;
                      }
                      return null;
                    })
                    .filter((item) => item != null);
                  return uniqueIds(raw);
                }
                function collectInputs() {
                  return Array.from(root.querySelectorAll('input.ant-checkbox-input[type=\"checkbox\"]'))
                    .filter((item) => isVisible(item));
                }
                const beforeInputs = collectInputs();
                const alreadySelected = beforeInputs.some((item) => item.checked);
                if (alreadySelected) {
                  return {
                    exists: true,
                    selected: true,
                    clicked: false,
                    optionCount: beforeInputs.length,
                    selectedLabels: selectedLabels(beforeInputs),
                    selectedServiceIds: selectedServiceIds(beforeInputs),
                  };
                }
                const first = beforeInputs.find((item) => !item.disabled);
                if (!first) {
                  return {
                    exists: true,
                    selected: false,
                    clicked: false,
                    optionCount: beforeInputs.length,
                    reason: 'no_enabled_checkbox',
                    selectedLabels: [],
                    selectedServiceIds: [],
                  };
                }

                const label = first.closest('label') || first.parentElement || first;
                label.scrollIntoView({block: 'center', inline: 'nearest'});
                if (typeof label.click === 'function') {
                  label.click();
                }
                if (!first.checked && typeof first.click === 'function') {
                  first.click();
                }
                first.dispatchEvent(new Event('input', {bubbles: true}));
                first.dispatchEvent(new Event('change', {bubbles: true}));

                if (!first.checked) {
                  const editButton = root.querySelector('.service-edit-btn');
                  if (editButton && isVisible(editButton) && typeof editButton.click === 'function') {
                    editButton.click();
                  }
                  if (!first.checked && typeof label.click === 'function') {
                    label.click();
                  }
                }

                const afterInputs = collectInputs();
                const selectedAfter = afterInputs.some((item) => item.checked);
                return {
                  exists: true,
                  selected: selectedAfter,
                  clicked: true,
                  optionCount: afterInputs.length,
                  selectedCount: afterInputs.filter((item) => item.checked).length,
                  selectedLabels: selectedLabels(afterInputs),
                  selectedServiceIds: selectedServiceIds(afterInputs),
                };
                """
            )
            result = state if isinstance(state, dict) else {"exists": False, "selected": False}
            if bool(result.get("selected")):
                break
            self._pause(0.6)
        context["draft_delivery_service_state"] = result if isinstance(result, dict) else {"exists": False}

    def _ensure_draft_logistics_dimensions_before_save(
        self,
        publish_config: dict[str, Any],
        context: dict[str, Any],
    ) -> None:
        steps = list(publish_config.get("steps", []) or [])
        target_step: dict[str, Any] | None = None
        for step in steps:
            if not isinstance(step, dict):
                continue
            action = str(step.get("action", "")).strip()
            name = str(step.get("name", "")).strip()
            if action == "logistics_dimensions" or name == "logistics_dimensions":
                target_step = step
                break
        if not target_step:
            return
        filled = self._fill_logistics_dimensions(target_step, context)
        context["draft_logistics_pre_save_filled"] = filled

    def _resolve_buyer_protection_ship_time_for_draft(
        self,
        publish_config: dict[str, Any],
        context: dict[str, Any],
    ) -> str:
        for config_key in ("draft_page_state_patch", "draft_request_patch", "draft_verification"):
            config = publish_config.get(config_key, {})
            if not isinstance(config, dict):
                continue
            value = self._resolve_context_preferred_value(
                context=context,
                source=str(config.get("buyer_protection_source", "")).strip(),
                default_value=str(config.get("buyer_protection_default_value", "")).strip(),
            )
            if value:
                return value
        return ""

    def _resolve_fallback_buyer_protection_service(self) -> dict[str, str]:
        if not self.driver:
            raise RuntimeError("Browser has not been opened.")
        payload = self.driver.execute_script(
            """
            const sdk = window.SellPublishSdk;
            const state = sdk && sdk.engine && sdk.engine.getJsonState ? sdk.engine.getJsonState() : {};
            const props = (((state || {}).components || {}).buyerProtection || {}).props || {};
            const groups = ((props.channelRenderMap || {}).dsc) || [];
            const shipmentGroup = groups.find((item) => String((item || {}).groupId || '') === '1') || groups[0] || {};
            const services = Array.isArray(shipmentGroup.ptsOfferTagModels) ? shipmentGroup.ptsOfferTagModels : [];
            const preferred =
              services.find((item) => item && item.selected) ||
              services[0] ||
              null;
            if (!preferred) {
              return { service_name: '', service_code: '' };
            }
            return {
              service_name: String(preferred.serviceName || '').trim(),
              service_code: String(preferred.serviceCode || '').trim(),
            };
            """
        )
        if isinstance(payload, dict):
            return {
                "service_name": str(payload.get("service_name", "")).strip(),
                "service_code": str(payload.get("service_code", "")).strip(),
            }
        return {"service_name": "", "service_code": ""}

    def _resolve_buyer_protection_service_code_by_name(self, service_name: str) -> str:
        if not self.driver:
            raise RuntimeError("Browser has not been opened.")
        normalized_name = str(service_name or "").strip()
        if not normalized_name:
            return ""
        payload = self.driver.execute_script(
            """
            const expected = String(arguments[0] || '').replace(/\\s+/g, ' ').trim();
            if (!expected) {
              return '';
            }
            const norm = (value) => String(value || '').replace(/\\s+/g, ' ').trim();
            const sdk = window.SellPublishSdk;
            const state = sdk && sdk.engine && sdk.engine.getJsonState ? sdk.engine.getJsonState() : {};
            const props = (((state || {}).components || {}).buyerProtection || {}).props || {};
            const groups = ((props.channelRenderMap || {}).dsc) || [];
            const shipmentGroup = groups.find((item) => String((item || {}).groupId || '') === '1') || groups[0] || {};
            const services = Array.isArray(shipmentGroup.ptsOfferTagModels) ? shipmentGroup.ptsOfferTagModels : [];
            const matched =
              services.find((item) => norm(item && item.serviceName) === expected) ||
              services.find((item) => {
                const text = norm(item && item.serviceName);
                return text && (text.includes(expected) || expected.includes(text));
              }) ||
              null;
            return matched ? String((matched && matched.serviceCode) || '').trim() : '';
            """,
            normalized_name,
        )
        return str(payload or "").strip()

    def _ensure_buyer_protection_ship_time_before_draft_save(
        self,
        publish_config: dict[str, Any],
        context: dict[str, Any],
    ) -> None:
        expected_value = self._resolve_buyer_protection_ship_time_for_draft(publish_config, context)
        if not expected_value:
            return
        attempt_count = max(1, int(publish_config.get("draft_buyer_protection_ui_retry_count", 2) or 2))
        force_reselect = bool(publish_config.get("draft_buyer_protection_force_reselect", True))
        selected_text = self._select_buyer_protection_ship_time_from_row(
            expected_value,
            select_first_option_if_unmatched=True,
            force_reselect=force_reselect,
            attempt_count=max(2, attempt_count),
            open_wait_seconds=float(publish_config.get("draft_buyer_protection_open_wait_seconds", 0.45)),
            select_wait_seconds=float(publish_config.get("draft_buyer_protection_select_wait_seconds", 0.55)),
        )
        context["draft_buyer_protection_pre_save_expected"] = expected_value
        context["draft_buyer_protection_pre_save_selected_text"] = selected_text
        context["draft_buyer_protection_pre_save_selected"] = bool(selected_text)
        effective_name = selected_text or expected_value
        if effective_name:
            effective_code = self._resolve_buyer_protection_service_code_by_name(effective_name)
            if effective_code:
                context["buyer_protection_ship_time"] = effective_name
                context["buyer_protection_ship_time_code"] = effective_code
                context["buyer_protection_step_template_runtime"] = [
                    {
                        "from": 1,
                        "service_name": effective_name,
                        "service_code": effective_code,
                    }
                ]

    def _save_draft_once(self, publish_config: dict[str, Any], context: dict[str, Any]) -> None:
        draft_selector = publish_config.get("draft_selector", {})
        if not self._selector_is_configured(draft_selector):
            raise ValueError("Auto save draft is enabled but draft_selector is not configured.")
        self._install_draft_request_patch(publish_config, context)
        self._click_with_javascript(draft_selector)
        self._handle_optional_draft_confirmation(publish_config, context=context)
        trace_detected = self._assert_draft_request_trace(publish_config, context)
        if not trace_detected:
            context["draft_submit_retry_mode"] = "dispatch_event_click"
            self._dispatch_click_with_events(draft_selector)
            self._pause(1.0)
            self._handle_optional_draft_confirmation(publish_config, context=context)
            trace_detected = self._assert_draft_request_trace(publish_config, context)
        if not trace_detected:
            raise PublishSubmitError(
                "Draft button was clicked but no draftSubmit request was captured. "
                "The page likely blocked draft save due to hidden validation or disabled state."
            )
        self._check_publish_error_state(
            publish_config.get("draft_error_detection", publish_config.get("submit_error_detection", {})),
            context=context,
            stage_name="post_draft",
            exception_cls=PublishSubmitError,
        )

    def _should_retry_draft_verify(self, error: PublishValidationError, context: dict[str, Any]) -> bool:
        message = str(error or "")
        message_lower = message.lower()
        if "buyer protection ship time" in message_lower:
            return True
        if "buyer protection" in message_lower and "schedule" in message_lower:
            return True

        combined_assist = " | ".join(
            [
                str(item).strip()
                for item in list(context.get("draft_assist_messages", []) or [])
                if str(item).strip()
            ]
        )
        if "\u53d1\u8d27\u65f6\u95f4" in combined_assist and "\u5fc5\u586b" in combined_assist:
            return True
        if "\u5fc5\u586b\u9879" in combined_assist:
            return True
        if "\u8bf7\u586b\u5199" in combined_assist or "\u8bf7\u5b8c\u5584" in combined_assist:
            return True
        if "\u914d\u9001\u670d\u52a1" in combined_assist:
            return True
        if not list(context.get("draft_buyer_protection_schedule", []) or []) and not str(
            context.get("draft_buyer_protection_value", "")
        ).strip():
            if "\u53d1\u8d27\u65f6\u95f4" in combined_assist or "buyer protection" in message_lower:
                return True
        return False

    def _should_retry_draft_submit(self, error: PublishSubmitError) -> bool:
        message = str(error or "").lower()
        retryable_keywords = [
            "系统错误",
            "请稍后尝试",
            "server error",
            "temporarily unavailable",
            "network error",
            "timeout",
            "保存失败",
        ]
        normalized_message = str(error or "")
        for keyword in retryable_keywords:
            if keyword.lower() in message or keyword in normalized_message:
                return True
        return False

    def _is_draft_submit_backend_reject_error(self, error: PublishSubmitError) -> bool:
        message = str(error or "").strip().lower()
        return (
            "draft_submit backend rejected request" in message
            or "draft_submit backend returned error response" in message
        )

    def _resolve_draft_request_patch_modes(self, publish_config: dict[str, Any]) -> list[str]:
        patch_config = publish_config.get("draft_request_patch", {})
        if not patch_config or not patch_config.get("enabled", False):
            return []
        raw_modes = list(publish_config.get("draft_request_patch_retry_modes", []) or [])
        if not raw_modes:
            raw_modes = ["full", "capture_only"]
        normalized_modes: list[str] = []
        for item in raw_modes:
            text = str(item or "").strip().lower()
            if text not in {"full", "capture_only"}:
                continue
            if text not in normalized_modes:
                normalized_modes.append(text)
        if not normalized_modes:
            normalized_modes = ["full"]
        return normalized_modes

    def _resolve_initial_draft_request_patch_mode(self, publish_config: dict[str, Any]) -> str:
        patch_modes = self._resolve_draft_request_patch_modes(publish_config)
        return patch_modes[0] if patch_modes else ""

    def _resolve_next_draft_request_patch_mode(
        self,
        publish_config: dict[str, Any],
        context: dict[str, Any],
    ) -> str:
        patch_modes = self._resolve_draft_request_patch_modes(publish_config)
        if not patch_modes:
            return ""
        current_mode = str(context.get("draft_request_patch_mode", "")).strip().lower()
        if current_mode not in patch_modes:
            return patch_modes[0]
        current_index = patch_modes.index(current_mode)
        if current_index >= len(patch_modes) - 1:
            return ""
        return patch_modes[current_index + 1]

    def _handle_optional_draft_confirmation(
        self,
        publish_config: dict[str, Any],
        *,
        context: dict[str, Any] | None = None,
    ) -> None:
        if not self.driver:
            raise RuntimeError("Browser has not been opened.")

        confirm_selector = publish_config.get(
            "draft_confirm_selector",
            {"by": "xpath", "value": "//button[normalize-space(.)='直接保存' or .//span[normalize-space(.)='直接保存']]"},
        )
        modal_selector = publish_config.get(
            "draft_confirm_modal_selector",
            {"by": "css", "value": ".ant-modal-wrap, .ant-modal-root, .next-dialog"},
        )
        wait_seconds = float(publish_config.get("draft_confirm_wait_seconds", 5))

        if not self._selector_is_configured(confirm_selector):
            return

        end_time = time.time() + max(wait_seconds, 0)
        while time.time() <= end_time:
            if self._has_visible_modal(modal_selector):
                modal_text = self._extract_visible_text(modal_selector)
                blocker_keywords = [
                    str(item).strip()
                    for item in publish_config.get("draft_confirm_blocker_keywords", [])
                    if str(item).strip()
                ]
                if self._contains_any_keyword(modal_text, blocker_keywords):
                    self._annotate_page_error_context(
                        context,
                        stage_name="draft_confirmation",
                        error_text=modal_text,
                        error_category="business_validation",
                    )
                    raise PublishValidationError(f"draft_confirmation blocked by modal: {modal_text}")
                try:
                    self._click_with_javascript(confirm_selector)
                    self._pause(float(publish_config.get("draft_confirm_settle_seconds", 0.6)))
                except TimeoutException:
                    pass
                return
            time.sleep(0.2)

    def _verify_saved_draft(self, publish_config: dict[str, Any], context: dict[str, Any]) -> None:
        verification = publish_config.get("draft_verification", {})
        if not verification or not verification.get("enabled", True):
            return
        if not self.driver:
            raise RuntimeError("Browser has not been opened.")

        self._pause(float(verification.get("settle_seconds", 1.2)))
        refreshed = False
        if verification.get("refresh_after_save", True):
            refresh_wait_seconds = float(verification.get("refresh_wait_seconds", 4))
            try:
                self.driver.refresh()
                self._pause(refresh_wait_seconds)
                refreshed = True
            except WebDriverException as exc:
                current_url = str(self.driver.current_url or "").strip()
                try:
                    if current_url:
                        self.driver.get(current_url)
                        self._pause(refresh_wait_seconds)
                        refreshed = True
                except WebDriverException:
                    refreshed = False
                if not refreshed:
                    context["draft_refresh_warning"] = str(exc)
                    print(f"[WARN] Skip draft refresh due webdriver error: {exc}")
                    self._pause(min(refresh_wait_seconds, 1.5))
        context["draft_verify_refreshed"] = refreshed

        assist_messages = self._collect_assist_messages()
        context["draft_assist_messages"] = assist_messages
        context["draft_required_field_labels"] = self._extract_required_labels_from_assist_messages(assist_messages)
        context["draft_main_image_present"] = self._draft_main_image_present()
        context["draft_description_present"] = self._draft_description_present()
        context["draft_spec_values"] = self._collect_spec_values()
        context["draft_send_address_value"] = self._draft_selected_send_address()
        context["draft_logistics_dimensions"] = self._draft_logistics_dimension_values()
        context["draft_buyer_protection_value"] = self._draft_selected_buyer_protection()
        context["draft_buyer_protection_schedule"] = self._draft_selected_buyer_protection_schedule()
        trace_patch_snapshot = (
            ((context.get("draft_submit_trace") or {}).get("patch") or {}).get("patchSnapshot") or {}
        )

        forbidden_keywords = [
            str(item).strip()
            for item in verification.get("forbidden_assist_keywords", [])
            if str(item).strip()
        ]
        strict_buyer_protection_persist = bool(verification.get("strict_buyer_protection_persist", False))
        combined_assist = " | ".join(assist_messages)
        buyer_protection_required_warning = (
            "\u53d1\u8d27\u65f6\u95f4" in combined_assist and "\u5fc5\u586b" in combined_assist
        )
        buyer_protection_grace_refresh_count = max(
            0,
            int(verification.get("buyer_protection_warning_grace_refresh_count", 1) or 0),
        )
        buyer_protection_grace_wait_seconds = float(
            verification.get("buyer_protection_warning_grace_wait_seconds", 1.2)
        )
        buyer_protection_grace_refresh_wait_seconds = float(
            verification.get("buyer_protection_warning_grace_refresh_wait_seconds", 2.0)
        )
        if refreshed and buyer_protection_required_warning and buyer_protection_grace_refresh_count > 0:
            grace_recheck_count = 0
            for _ in range(buyer_protection_grace_refresh_count):
                grace_recheck_count += 1
                self._pause(max(0.1, buyer_protection_grace_wait_seconds))
                try:
                    self.driver.refresh()
                    self._pause(max(0.2, buyer_protection_grace_refresh_wait_seconds))
                except WebDriverException:
                    break
                assist_messages = self._collect_assist_messages()
                context["draft_assist_messages"] = assist_messages
                context["draft_required_field_labels"] = self._extract_required_labels_from_assist_messages(
                    assist_messages
                )
                context["draft_main_image_present"] = self._draft_main_image_present()
                context["draft_description_present"] = self._draft_description_present()
                context["draft_spec_values"] = self._collect_spec_values()
                context["draft_send_address_value"] = self._draft_selected_send_address()
                context["draft_logistics_dimensions"] = self._draft_logistics_dimension_values()
                context["draft_buyer_protection_value"] = self._draft_selected_buyer_protection()
                context["draft_buyer_protection_schedule"] = self._draft_selected_buyer_protection_schedule()
                combined_assist = " | ".join(assist_messages)
                buyer_protection_required_warning = (
                    "\u53d1\u8d27\u65f6\u95f4" in combined_assist and "\u5fc5\u586b" in combined_assist
                )
                if not buyer_protection_required_warning:
                    context["draft_buyer_protection_grace_recovered"] = True
                    break
            context["draft_buyer_protection_grace_recheck_count"] = grace_recheck_count
        if refreshed and self._contains_any_keyword(combined_assist, forbidden_keywords):
            self._annotate_page_error_context(
                context,
                stage_name="draft_verify",
                error_text=combined_assist,
                error_category="business_validation",
            )
            raise PublishValidationError(f"draft_verify blocked by assist warnings: {combined_assist}")

        if verification.get("require_main_image", True) and not context["draft_main_image_present"]:
            raise PublishValidationError("draft_verify blocked: main image did not persist after save.")

        if verification.get("require_description", True) and not context["draft_description_present"]:
            raise PublishValidationError("draft_verify blocked: description content did not persist after save.")

        if verification.get("require_specs", True):
            required_spec_labels = [
                str(item).strip()
                for item in verification.get("required_spec_labels", [])
                if str(item).strip()
            ]
            for label in required_spec_labels:
                if not str(context["draft_spec_values"].get(label, "")).strip():
                    raise PublishValidationError(f"draft_verify blocked: spec '{label}' is empty after save.")

        if verification.get("require_send_address", False):
            actual_send_address = str(context.get("draft_send_address_value", "")).strip()
            if not actual_send_address:
                trace_send_address = str(trace_patch_snapshot.get("sendAddressId", "")).strip()
                if trace_send_address:
                    actual_send_address = trace_send_address
                    context["draft_send_address_value"] = trace_send_address
                    context["draft_send_address_source"] = "draft_submit_trace"
            if not actual_send_address:
                raise PublishValidationError("draft_verify blocked: send address did not persist after save.")

        if verification.get("require_logistics_dimensions", False):
            dimension_map = dict(context.get("draft_logistics_dimensions", {}) or {})
            required_fields = [
                (
                    str(item.get("name", "")).strip().lower(),
                    self._resolve_context_preferred_value(
                        context=context,
                        source=str(item.get("source", "")).strip(),
                        default_value=str(item.get("default_value", "")).strip(),
                    ),
                )
                for item in list(verification.get("required_logistics_fields", []) or [])
                if isinstance(item, dict)
            ]
            if not required_fields:
                required_fields = [
                    ("length", str(context.get("length_cm", "")).strip()),
                    ("width", str(context.get("width_cm", "")).strip()),
                    ("height", str(context.get("height_cm", "")).strip()),
                    ("weight", str(context.get("weight_g", "")).strip()),
                ]
            missing_fields = [
                name
                for name, expected in required_fields
                if expected and not str(dimension_map.get(name, "")).strip()
            ]
            if missing_fields:
                raise PublishValidationError(
                    "draft_verify blocked: logistics dimensions missing after save -> "
                    + ",".join(missing_fields)
                )

        if verification.get("require_buyer_protection", False):
            desired_steps = [
                {
                    key: value
                    for key, value in {
                        "from": int(item.get("from", 0) or 0),
                        "end": int(item.get("end", 0) or 0) if item.get("end") is not None else None,
                        "serviceName": str(item.get("serviceName", "")).strip(),
                        "serviceCode": str(item.get("value", "")).strip(),
                    }.items()
                    if value not in ("", None, 0)
                }
                for item in list(((context.get("draft_page_state_patch") or {}).get("buyerProtectionDesiredSteps", [])) or [])
                if isinstance(item, dict)
                and int(item.get("from", 0) or 0) > 0
                and (str(item.get("serviceName", "")).strip() or str(item.get("value", "")).strip())
            ]
            expected_schedule = [
                {
                    key: value
                    for key, value in step.items()
                    if key in {"from", "end", "serviceName"} and value not in ("", None, 0)
                }
                for step in desired_steps
                if isinstance(step, dict)
            ]
            if not expected_schedule:
                expected_schedule = self._build_buyer_protection_step_template(
                    verification.get("buyer_protection_step_template", []),
                    context,
                )
            expected_code_schedule = [
                {
                    key: value
                    for key, value in {
                        "from": int(item.get("from", 0) or 0),
                        "end": int(item.get("end", 0) or 0) if item.get("end") is not None else None,
                        "serviceCode": str(item.get("serviceCode", "")).strip(),
                    }.items()
                    if value not in ("", None, 0)
                }
                for item in desired_steps
                if isinstance(item, dict) and str(item.get("serviceCode", "")).strip()
            ]
            trace_steps = [
                {
                    key: value
                    for key, value in {
                        "from": int(item.get("from", 0) or 0),
                        "end": int(item.get("end", 0) or 0) if item.get("end") is not None else None,
                        "serviceName": str(item.get("serviceName", "")).strip(),
                        "serviceCode": str(
                            item.get("value", "") if item.get("value") is not None else item.get("serviceCode", "")
                        ).strip(),
                    }.items()
                    if value not in ("", None, 0)
                }
                for item in list((trace_patch_snapshot.get("buyerProtectionSteps", [])) or [])
                if isinstance(item, dict) and int(item.get("from", 0) or 0) > 0
            ]
            context["draft_buyer_protection_schedule_trace"] = trace_steps
            actual_schedule = list(context.get("draft_buyer_protection_schedule", []) or [])
            if expected_schedule:
                simplified_actual = [
                    {
                        key: value
                        for key, value in {
                            "from": int(item.get("from", 0) or 0),
                            "end": int(item.get("end", 0) or 0) if item.get("end") is not None else None,
                            "serviceName": str(item.get("serviceName", "")).strip(),
                        }.items()
                        if value not in ("", None, 0)
                    }
                    for item in actual_schedule
                    if int(item.get("from", 0) or 0) > 0 and str(item.get("serviceName", "")).strip()
                ]
                if simplified_actual != expected_schedule and trace_steps:
                    trace_by_name = [
                        {
                            key: value
                            for key, value in item.items()
                            if key in {"from", "end", "serviceName"} and value not in ("", None, 0)
                        }
                        for item in trace_steps
                        if isinstance(item, dict) and str(item.get("serviceName", "")).strip()
                    ]
                    trace_by_code = [
                        {
                            key: value
                            for key, value in item.items()
                            if key in {"from", "end", "serviceCode"} and value not in ("", None, 0)
                        }
                        for item in trace_steps
                        if isinstance(item, dict) and str(item.get("serviceCode", "")).strip()
                    ]
                    if (trace_by_name and trace_by_name == expected_schedule) or (
                        expected_code_schedule and trace_by_code and trace_by_code == expected_code_schedule
                    ):
                        simplified_actual = expected_schedule
                        context["draft_buyer_protection_schedule_source"] = "draft_submit_trace"
                if simplified_actual != expected_schedule:
                    if strict_buyer_protection_persist or (refreshed and buyer_protection_required_warning):
                        raise PublishValidationError(
                            "draft_verify blocked: buyer protection ship time schedule did not persist after save."
                        )
                    context["draft_buyer_protection_schedule_unverified"] = True
            expected_value = self._resolve_context_preferred_value(
                context=context,
                source=str(verification.get("buyer_protection_source", "")).strip(),
                default_value=str(verification.get("buyer_protection_default_value", "")).strip(),
            )
            actual_value = str(context.get("draft_buyer_protection_value", "")).strip()
            if not actual_value:
                trace_value = str(trace_patch_snapshot.get("buyerProtectionServiceName", "")).strip()
                if trace_value:
                    actual_value = trace_value
                    context["draft_buyer_protection_value_source"] = "draft_submit_trace"
            if expected_value and actual_value != expected_value:
                if strict_buyer_protection_persist or (refreshed and buyer_protection_required_warning):
                    raise PublishValidationError(
                        "draft_verify blocked: buyer protection ship time did not persist after save."
                    )
                context["draft_buyer_protection_value_unverified"] = True

    def _collect_assist_messages(self) -> list[str]:
        if not self.driver:
            raise RuntimeError("Browser has not been opened.")
        messages = self.driver.execute_script(
            """
            const nodes = Array.from(
              document.querySelectorAll('#guid-assistBoard .info-list li div, #guid-assistBoard .info-list li')
            );
            return nodes
              .map((node) => (node.innerText || '').replace(/\\s+/g, ' ').trim())
              .filter(Boolean);
            """
        )
        return [str(item).strip() for item in (messages or []) if str(item).strip()]

    def _draft_main_image_present(self) -> bool:
        if not self.driver:
            raise RuntimeError("Browser has not been opened.")
        payload = self.driver.execute_script(
            """
            const slot = document.querySelector('#guid-primaryPicture .picture-sort-item');
            const wrapper = slot ? slot.querySelector('.module-picture-cover-wrapper') : null;
            const img = slot ? slot.querySelector('img') : null;
            const domPresent = Boolean(
              wrapper &&
              !(wrapper.className || '').includes('cover-empty') &&
              img &&
              img.getAttribute('src')
            );

            const sdk = window.SellPublishSdk;
            const state = sdk && sdk.engine && sdk.engine.getJsonState ? sdk.engine.getJsonState() : {};
            const components = (state && state.components) || {};
            const primaryPicture = (components && components.primaryPicture) || {};
            const props = (primaryPicture && primaryPicture.props) || {};
            const primaryValue =
              props && typeof props.value === 'object' && props.value
                ? props.value
                : {};
            const imageList = Array.isArray(primaryValue.imageList)
              ? primaryValue.imageList
              : (Array.isArray(props.imageList) ? props.imageList : []);
            const statePresent = imageList.some((item) => {
              if (!item || typeof item !== 'object') return false;
              const url =
                item.url ||
                item.imageUrl ||
                item.imgUrl ||
                item.fileUrl ||
                item.downloadUrl ||
                '';
              return String(url || '').trim().length > 0;
            });
            return {
              domPresent,
              statePresent,
            };
            """
        )
        if isinstance(payload, dict):
            return bool(payload.get("domPresent") or payload.get("statePresent"))
        return bool(payload)

    def _draft_description_present(self) -> bool:
        if not self.driver:
            raise RuntimeError("Browser has not been opened.")
        return bool(
            self.driver.execute_script(
                """
                const textarea = document.querySelector('#tinyMCE-0');
                const rawValue = textarea ? (textarea.value || '') : '';
                if (rawValue.replace(/\\s+/g, '').length > 0) {
                  return true;
                }
                const editor = window.tinyMCE && window.tinyMCE.get && window.tinyMCE.get('tinyMCE-0');
                const html = editor ? (editor.getContent() || '') : '';
                const text = html.replace(/<[^>]+>/g, ' ').replace(/\\s+/g, ' ').trim();
                return html.includes('<img') || text.length > 0;
                """
            )
        )

    def _collect_spec_values(self) -> dict[str, str]:
        if not self.driver:
            raise RuntimeError("Browser has not been opened.")
        payload = self.driver.execute_script(
            """
            const sections = Array.from(document.querySelectorAll('#guid-saleProp .module-spec-decorator'));
            const result = {};
            sections.forEach((section) => {
              const label = (section.querySelector('.nak-label')?.innerText || '').replace(/\\s+/g, ' ').trim();
              const values = Array.from(section.querySelectorAll('input'))
                .map((node) => (node.value || '').replace(/\\s+/g, ' ').trim())
                .filter(Boolean);
              if (label) {
                result[label.replace(/^\\*/, '').trim()] = values.join('|');
              }
            });
            return result;
            """
        )
        return {str(key): str(value) for key, value in dict(payload or {}).items()}

    def _draft_selected_send_address(self) -> str:
        if not self.driver:
            raise RuntimeError("Browser has not been opened.")
        payload = self.driver.execute_script(
            """
            const sdk = window.SellPublishSdk;
            const state = sdk && sdk.engine && sdk.engine.getJsonState ? sdk.engine.getJsonState() : {};
            const props = (((state || {}).components || {}).cbuSendAddress || {}).props || {};
            const rawValue = props.value;
            if (rawValue != null && typeof rawValue === 'object') {
              const candidate =
                rawValue.value != null
                  ? rawValue.value
                  : (
                      rawValue.id != null
                        ? rawValue.id
                        : (
                            rawValue.addressId != null
                              ? rawValue.addressId
                              : rawValue.sendAddressId
                          )
                    );
              const normalized = String(candidate == null ? '' : candidate).trim();
              if (normalized) {
                return normalized;
              }
              const textValue = String(
                rawValue.label == null
                  ? (rawValue.text == null ? (rawValue.name == null ? '' : rawValue.name) : rawValue.text)
                  : rawValue.label
              ).trim();
              if (textValue) {
                return textValue;
              }
            }
            const value = String((rawValue == null ? '' : rawValue)).trim();
            if (value && value !== '[object Object]') {
              return value;
            }
            const root = document.querySelector('#guid-cbuSendAddress');
            if (!root) {
              return '';
            }
            const selected = root.querySelector('.ant-select-selection-item');
            return selected ? String(selected.innerText || selected.textContent || '').replace(/\\s+/g, ' ').trim() : '';
            """
        )
        return str(payload or "").strip()

    def _draft_logistics_dimension_values(self) -> dict[str, str]:
        if not self.driver:
            raise RuntimeError("Browser has not been opened.")
        payload = self.driver.execute_script(
            """
            const result = {
              length: '',
              width: '',
              height: '',
              weight: '',
            };
            const norm = (value) => String(value || '').replace(/\\s+/g, '').trim();
            const trimText = (value) => String(value || '').trim();
            const firstNonEmpty = (...values) => {
              for (const value of values) {
                const text = trimText(value);
                if (text) {
                  return text;
                }
              }
              return '';
            };
            const readFromRuntimeState = () => {
              const sdk = window.SellPublishSdk;
              const state = sdk && sdk.engine && sdk.engine.getJsonState ? sdk.engine.getJsonState() : {};
              const props = (((state || {}).components || {}).officialLogistics || {}).props || {};
              const value = props.value && typeof props.value === 'object' ? props.value : {};
              const skuInfoCandidates = [];
              if (Array.isArray(value.skuInfo)) {
                skuInfoCandidates.push(...value.skuInfo);
              }
              if (Array.isArray((value.offerInfo || {}).skuInfo)) {
                skuInfoCandidates.push(...((value.offerInfo || {}).skuInfo || []));
              }
              if (Array.isArray(props.skuInfo)) {
                skuInfoCandidates.push(...props.skuInfo);
              }
              const firstRow = skuInfoCandidates.find((item) => item && typeof item === 'object') || null;
              if (!firstRow) {
                return null;
              }
              const nestedDimension =
                (firstRow.dimension && typeof firstRow.dimension === 'object' && firstRow.dimension) ||
                (firstRow.skuDimension && typeof firstRow.skuDimension === 'object' && firstRow.skuDimension) ||
                {};
              const stateResult = {
                length: firstNonEmpty(firstRow.length, firstRow.lengthCm, nestedDimension.length, nestedDimension.lengthCm),
                width: firstNonEmpty(firstRow.width, firstRow.widthCm, nestedDimension.width, nestedDimension.widthCm),
                height: firstNonEmpty(firstRow.height, firstRow.heightCm, nestedDimension.height, nestedDimension.heightCm),
                weight: firstNonEmpty(firstRow.weight, firstRow.weightG, nestedDimension.weight, nestedDimension.weightG),
              };
              if (Object.values(stateResult).some((item) => trimText(item))) {
                return stateResult;
              }
              return null;
            };
            const stateResult = readFromRuntimeState();
            if (stateResult) {
              return stateResult;
            }
            const root = document.querySelector('#guid-officialLogistics') || document;
            const table = Array.from(root.querySelectorAll('table'))
              .find((tableNode) => {
                const headerText = norm(tableNode.innerText || '');
                return (
                  headerText.includes('长(cm)') &&
                  headerText.includes('宽(cm)') &&
                  headerText.includes('高(cm)') &&
                  headerText.includes('重量(g)')
                );
              });
            if (!table) {
              return result;
            }
            const headerNodes = Array.from(
              table.querySelectorAll('thead [data-next-table-col], .next-table-header [data-next-table-col], thead th')
            );
            const colTextByKey = {};
            headerNodes.forEach((node, index) => {
              const colKey = String(node.getAttribute('data-next-table-col') || '').trim() || String(index + 1);
              const text = norm(node.innerText || node.textContent || '');
              if (text) {
                colTextByKey[colKey] = text;
              }
            });
            const findColumnKey = (keywords, fallbackIndex) => {
              const entries = Object.entries(colTextByKey);
              for (const [key, text] of entries) {
                if ((Array.isArray(keywords) ? keywords : []).every((token) => text.includes(norm(token)))) {
                  return key;
                }
              }
              return String(fallbackIndex);
            };
            const colMap = {
              length: findColumnKey(['长'], 3),
              width: findColumnKey(['宽'], 4),
              height: findColumnKey(['高'], 5),
              weight: findColumnKey(['重量'], 7),
            };
            const bodyRow =
              table.querySelector('tbody tr') ||
              table.querySelector('.next-table-body tr');
            if (!bodyRow) {
              return result;
            }
            const readByCol = (colKey) => {
              const key = String(colKey || '').trim();
              if (!key) return '';
              let cell = bodyRow.querySelector(`td[data-next-table-col="${key}"]`);
              if (!cell && /^\\d+$/.test(key)) {
                cell = bodyRow.querySelector(`td:nth-child(${key})`);
              }
              if (!cell) return '';
              const input = cell.querySelector('input');
              if (input) {
                return String(input.value || '').trim();
              }
              const text = String(cell.innerText || cell.textContent || '').replace(/\\s+/g, ' ').trim();
              if (!text) return '';
              const matched = text.match(/-?\\d+(?:\\.\\d+)?/);
              return matched ? matched[0] : text;
            };
            result.length = readByCol(colMap.length);
            result.width = readByCol(colMap.width);
            result.height = readByCol(colMap.height);
            result.weight = readByCol(colMap.weight);
            return result;
            """
        )
        return {
            "length": str((payload or {}).get("length", "")).strip(),
            "width": str((payload or {}).get("width", "")).strip(),
            "height": str((payload or {}).get("height", "")).strip(),
            "weight": str((payload or {}).get("weight", "")).strip(),
        }

    def _draft_selected_buyer_protection(self) -> str:
        if not self.driver:
            raise RuntimeError("Browser has not been opened.")
        payload = self.driver.execute_script(
            """
            const sdk = window.SellPublishSdk;
            const state = sdk && sdk.engine && sdk.engine.getJsonState ? sdk.engine.getJsonState() : {};
            const props = (((state || {}).components || {}).buyerProtection || {}).props || {};
            const selectedGroups = (((props.value || {}).selectedServices || {}).dsc) || [];
            const shipmentGroup = selectedGroups.find((item) => String(item.logicGroupId || '') === '1') || selectedGroups[0];
            if (!shipmentGroup || !Array.isArray(shipmentGroup.steps) || shipmentGroup.steps.length === 0) {
              return '';
            }
            const selectedCode = String((shipmentGroup.steps[0] || {}).value || '').trim();
            if (!selectedCode) {
              return '';
            }
            const renderGroups = ((props.channelRenderMap || {}).dsc) || [];
            const shipmentRenderGroup = renderGroups.find((item) => String(item.groupId || '') === '1') || renderGroups[0] || {};
            const services = Array.isArray(shipmentRenderGroup.ptsOfferTagModels)
              ? shipmentRenderGroup.ptsOfferTagModels
              : [];
            const matched = services.find((item) => String(item.serviceCode || '').trim() === selectedCode);
            return matched ? String(matched.serviceName || '').trim() : '';
            """
        )
        return str(payload or "").strip()

    def _draft_selected_buyer_protection_schedule(self) -> list[dict[str, Any]]:
        if not self.driver:
            raise RuntimeError("Browser has not been opened.")
        payload = self.driver.execute_script(
            """
            const sdk = window.SellPublishSdk;
            const state = sdk && sdk.engine && sdk.engine.getJsonState ? sdk.engine.getJsonState() : {};
            const props = (((state || {}).components || {}).buyerProtection || {}).props || {};
            const selectedGroups = (((props.value || {}).selectedServices || {}).dsc) || [];
            const shipmentGroup = selectedGroups.find((item) => String(item.logicGroupId || '') === '1') || selectedGroups[0];
            if (!shipmentGroup || !Array.isArray(shipmentGroup.steps) || shipmentGroup.steps.length === 0) {
              return [];
            }
            const renderGroups = ((props.channelRenderMap || {}).dsc) || [];
            const shipmentRenderGroup = renderGroups.find((item) => String(item.groupId || '') === '1') || renderGroups[0] || {};
            const services = Array.isArray(shipmentRenderGroup.ptsOfferTagModels)
              ? shipmentRenderGroup.ptsOfferTagModels
              : [];
            return shipmentGroup.steps
              .map((step) => {
                const code = String((step && step.value) || '').trim();
                const from = Number(step && step.from);
                const endRaw = step && step.end;
                const end = endRaw == null || endRaw === '' ? null : Number(endRaw);
                if (!code || !Number.isFinite(from) || from <= 0) {
                  return null;
                }
                const matched = services.find((item) => String(item.serviceCode || '').trim() === code) || null;
                const result = {
                  from,
                  serviceCode: code,
                  serviceName: matched ? String(matched.serviceName || '').trim() : '',
                };
                if (Number.isFinite(end) && end >= from) {
                  result.end = end;
                }
                return result;
              })
              .filter(Boolean);
            """
        )
        if not isinstance(payload, list):
            return []
        result: list[dict[str, Any]] = []
        for item in payload:
            if not isinstance(item, dict):
                continue
            try:
                from_value = int(item.get("from"))
            except (TypeError, ValueError):
                continue
            if from_value <= 0:
                continue
            step: dict[str, Any] = {
                "from": from_value,
                "serviceCode": str(item.get("serviceCode", "")).strip(),
                "serviceName": str(item.get("serviceName", "")).strip(),
            }
            try:
                end_value = int(item.get("end"))
            except (TypeError, ValueError):
                end_value = 0
            if end_value >= from_value:
                step["end"] = end_value
            result.append(step)
        return result

    def _extract_visible_text(self, selector: dict[str, str]) -> str:
        if not self.driver or not self._selector_is_configured(selector):
            return ""
        by_key = selector.get("by", "css").strip().lower()
        elements = self.driver.find_elements(
            BY_MAPPING.get(by_key, By.CSS_SELECTOR),
            selector.get("value", "").strip(),
        )
        texts: list[str] = []
        for element in elements:
            try:
                if element.is_displayed():
                    text = " ".join((element.text or "").split())
                    if text:
                        texts.append(text)
            except Exception:
                continue
        return " | ".join(texts)

    def _has_visible_modal(self, selector: dict[str, str]) -> bool:
        if not self.driver or not self._selector_is_configured(selector):
            return False

        by_key = selector.get("by", "css").strip().lower()
        elements = self.driver.find_elements(
            BY_MAPPING.get(by_key, By.CSS_SELECTOR),
            selector.get("value", "").strip(),
        )
        for element in elements:
            try:
                if element.is_displayed():
                    return True
            except Exception:
                continue
        return False

    def _pause(self, seconds: float) -> None:
        if seconds > 0:
            time.sleep(seconds)

    def _wait_for_manual_confirmation(self, message: str) -> None:
        prompt = str(message or "").strip() or "Manual check required. Press Enter to continue..."
        if self.browser_config.get("auto_continue_manual_steps", False):
            print(f"[INFO] Auto-continue manual step: {prompt}")
            return
        input(prompt)

    def _record_page_metadata(self, context: dict[str, Any]) -> None:
        if not self.driver:
            return
        context["current_url"] = self.driver.current_url
        context["page_title"] = self.driver.title

    def _capture_screenshot(self, product_title: str) -> None:
        if not self.driver or not self.browser_config.get("screenshot_on_error", True):
            return

        screenshot_dir = self.project_root / self.browser_config.get(
            "screenshot_dir", "logs/screenshots"
        )
        screenshot_dir.mkdir(parents=True, exist_ok=True)

        safe_name = "".join(
            character if character.isalnum() else "_"
            for character in product_title
        ).strip("_") or "product"
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        target = screenshot_dir / f"{safe_name}_{timestamp}.png"
        self.driver.save_screenshot(str(target))
        self.last_screenshot_path = str(target)
        print(f"[INFO] Saved screenshot: {target}")
        self._capture_html_snapshot(safe_name, timestamp)

    def _capture_html_snapshot(self, safe_name: str, timestamp: str) -> None:
        if not self.driver:
            return

        snapshot_dir = self.project_root / self.browser_config.get(
            "html_snapshot_dir", "logs/html_snapshots"
        )
        snapshot_dir.mkdir(parents=True, exist_ok=True)
        target = snapshot_dir / f"{safe_name}_{timestamp}.html"
        target.write_text(self.driver.page_source, encoding="utf-8")
        self.last_html_snapshot_path = str(target)
        print(f"[INFO] Saved html snapshot: {target}")
