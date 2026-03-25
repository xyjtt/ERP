from __future__ import annotations

import base64
import html
import mimetypes
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from selenium.common.exceptions import TimeoutException
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

        self.driver, self.attached_to_existing_browser = open_webdriver(
            headless=bool(self.browser_config.get("headless")),
            debugger_address=debugger_address,
            user_data_dir=user_data_dir,
            profile_directory=profile_directory,
            browser_binary_path=browser_binary_path,
            browser_type=browser_type,
        )
        self.driver.implicitly_wait(self.browser_config.get("implicit_wait_seconds", 10))

    def close(self) -> None:
        if self.driver:
            if self.attached_to_existing_browser and self.browser_config.get("keep_browser_open_on_close", True):
                self.driver = None
                return
            self.driver.quit()
            self.driver = None

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
            self._check_publish_error_state(
                publish_config.get("pre_submit_error_detection", {}),
                context=context,
                stage_name="pre_submit",
                exception_cls=PublishValidationError,
            )

            final_action_mode = self._resolve_publish_mode(publish_config)
            if publish_config.get("pause_before_submit", False):
                action_label = {
                    "draft": "保存草稿",
                    "submit": "提交发布",
                }.get(final_action_mode, "继续后续流程")
                self._wait_for_manual_confirmation(f"请确认页面填写无误，准备{action_label}时按回车...")

            if final_action_mode == "draft":
                draft_selector = publish_config.get("draft_selector", {})
                if not self._selector_is_configured(draft_selector):
                    raise ValueError("Auto save draft is enabled but draft_selector is not configured.")
                self._click_with_javascript(draft_selector)
                self._handle_optional_draft_confirmation(publish_config)
                self._check_publish_error_state(
                    publish_config.get("draft_error_detection", publish_config.get("submit_error_detection", {})),
                    context=context,
                    stage_name="post_draft",
                    exception_cls=PublishSubmitError,
                )
                print(f"[INFO] Saved draft for product: {product.title}")
            elif final_action_mode == "submit":
                submit_selector = publish_config.get("submit_selector", {})
                if not self._selector_is_configured(submit_selector):
                    raise ValueError("Auto submit is enabled but submit_selector is not configured.")
                self._wait_for_element(submit_selector, clickable=True).click()
                self._check_publish_error_state(
                    publish_config.get("submit_error_detection", {}),
                    context=context,
                    stage_name="post_submit",
                    exception_cls=PublishSubmitError,
                )
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
                self._select_category_path(step, selector, context)
                self._pause(self.browser_config.get("action_wait_seconds", 0.5))
                continue

            if action == "match_candidates":
                self._match_candidates(step, context)
                self._pause(self.browser_config.get("action_wait_seconds", 0.5))
                continue

            selector = self._resolve_selector(step.get("selector", {}), context)
            required = bool(step.get("required", False))
            if not self._selector_is_configured(selector):
                if required:
                    raise ValueError(f"Required selector not configured for step '{step.get('name')}'")
                print(f"[WARN] Skip step '{step.get('name')}' because selector is empty.")
                continue

            retry_count = max(0, int(step.get("retry_count", 0) or 0))
            retry_wait_seconds = float(step.get("retry_wait_seconds", 1.5))
            completed = False
            for attempt_index in range(retry_count + 1):
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
                    elif action == "tinymce":
                        value = self._resolve_value(step, context)
                        if not value:
                            if required:
                                raise ValueError(f"Required TinyMCE value missing for step '{step.get('name')}'")
                            completed = True
                            break
                        self._write_tinymce_content(step, selector, value)
                    elif action == "tinymce_images":
                        values = self._resolve_file_values(step, context)
                        if not values:
                            if required:
                                raise ValueError(f"Required TinyMCE image value missing for step '{step.get('name')}'")
                            completed = True
                            break
                        self._insert_tinymce_images(step, selector, values)
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
                    if attempt_index < retry_count:
                        print(
                            f"[WARN] Retry step '{step_name}' after timeout "
                            f"({attempt_index + 1}/{retry_count})."
                        )
                        self._pause(retry_wait_seconds)
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

            if not completed:
                continue

            self._pause(self.browser_config.get("action_wait_seconds", 0.5))

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
        return str(value).strip()

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
        if force_reselect or "select.htm" not in (self.driver.current_url or ""):
            self.driver.get(select_url)
            self._pause(float(step.get("category_page_wait_seconds", 2)))

        for index, level in enumerate(levels):
            self._click_category_option(level)
            if index < len(levels) - 1:
                self._wait_for_category_level(levels[index + 1], timeout_seconds=float(step.get("category_level_timeout_seconds", 10)))
            else:
                self._wait_for_category_confirmation(levels, timeout_seconds=float(step.get("category_confirm_wait_seconds", 10)))

        confirm_selector = self._resolve_selector(
            step.get("confirm_selector", {"by": "css", "value": "#submitButton"}),
            context,
        )
        confirm_button = self._wait_for_element(confirm_selector, clickable=True)
        self.driver.execute_script("arguments[0].click();", confirm_button)
        WebDriverWait(self.driver, float(step.get("category_return_timeout_seconds", 20))).until(
            lambda driver: "publish.htm" in (driver.current_url or "")
        )
        self._pause(float(step.get("after_category_return_wait_seconds", 2)))

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

    def _click_category_option(self, label: str) -> None:
        if not self.driver:
            raise RuntimeError("Browser has not been opened.")

        clicked = self.driver.execute_script(
            """
            const label = arguments[0];
            function norm(value) {
              return (value || '').replace(/\\s+/g, ' ').trim();
            }
            const options = Array.from(document.querySelectorAll('.next-cascader-menu-wrapper li[role="option"]'));
            const target = options.find((node) => norm(node.getAttribute('title') || node.innerText) === label);
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
                return Array.from(document.querySelectorAll('.next-cascader-menu-wrapper li[role="option"]'))
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
            return

        element.click()
        self._pause(0.1)
        if clear:
            element.send_keys(Keys.CONTROL, "a")
            element.send_keys(Keys.DELETE)
        if next_value:
            element.send_keys(next_value)
        self._pause(0.3)

        current_value = str(element.get_attribute("value") or "")
        if current_value != target_value:
            raise ValueError(
                f"Failed to set text field value. Expected '{target_value}', got '{current_value}'."
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

        input_element = self._wait_for_element(selector)
        self.driver.execute_script(
            """
            const input = arguments[0];
            input.scrollIntoView({block: 'center', inline: 'nearest'});
            const container =
              input.closest('.ant-select') ||
              input.closest('.next-select') ||
              input.closest('.next-select-inner') ||
              input.closest('.ant-select-selector') ||
              input;
            if (input.hasAttribute('readonly')) {
              input.removeAttribute('readonly');
            }
            container.click();
            input.focus();
            """,
            input_element,
        )
        self._pause(float(step.get("open_wait_seconds", 0.3)))

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
            input_element.send_keys(str(value))
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
                str(value),
            )
        self._pause(float(step.get("type_wait_seconds", 0.5)))

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
            str(value),
        )
        if not matched:
            input_element.send_keys(Keys.ENTER)
        self._pause(float(step.get("select_wait_seconds", 0.5)))

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

            insert_button = self._wait_for_element(insert_selector, clickable=True)
            self.driver.execute_script("arguments[0].click();", insert_button)
        finally:
            self.driver.switch_to.default_content()

        self._pause(float(step.get("after_insert_wait_seconds", 1.5)))
        self._remove_elements(cleanup_selector)
        self._pause(float(step.get("after_cleanup_wait_seconds", 0.3)))

    def _insert_tinymce_images(
        self,
        step: dict[str, Any],
        selector: dict[str, str],
        values: list[str],
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
            raise ValueError(f"TinyMCE editor '{editor_id}' is not available.")

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
                    (
                        str(
                            self._read_primary_picture_bridge_slot(
                                bridge_selector,
                                slot_index,
                            ).get("url", "")
                        ).strip()
                        or str(
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
            album_select = self.driver.find_element(
                BY_MAPPING.get(selector.get("by", "css").strip().lower(), By.CSS_SELECTOR),
                selector.get("value", "").strip(),
            )
        except Exception:
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
        select_control.select_by_value(target_option.get_attribute("value"))
        self._pause(0.3)

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

    def _handle_optional_draft_confirmation(self, publish_config: dict[str, Any]) -> None:
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
                try:
                    self._click_with_javascript(confirm_selector)
                    self._pause(float(publish_config.get("draft_confirm_settle_seconds", 0.6)))
                except TimeoutException:
                    pass
                return
            time.sleep(0.2)

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
