from __future__ import annotations

import time
from datetime import datetime
from pathlib import Path
from typing import Any

from selenium import webdriver
from selenium.common.exceptions import TimeoutException
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.remote.webelement import WebElement
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.select import Select
from selenium.webdriver.support.ui import WebDriverWait
from webdriver_manager.chrome import ChromeDriverManager

from exceptions import MatchCandidateInvalidError, ProductMatchNotFoundError, PublishSubmitError, PublishValidationError
from parser import ProductRecord, strip_emoji


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
        self.driver: webdriver.Chrome | None = None
        self.last_screenshot_path = ""
        self.last_html_snapshot_path = ""
        self.last_result_context: dict[str, Any] = {}

    def open(self) -> None:
        options = Options()
        if self.browser_config.get("headless"):
            options.add_argument("--headless=new")

        self.driver = webdriver.Chrome(
            service=Service(ChromeDriverManager().install()),
            options=options,
        )
        self.driver.implicitly_wait(self.browser_config.get("implicit_wait_seconds", 10))

    def close(self) -> None:
        if self.driver:
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
            input(prompt)
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
            input(
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
                stage_name="pre_submit",
                exception_cls=PublishValidationError,
            )

            if publish_config.get("pause_before_submit", False):
                input("请确认页面填写无误，准备继续提交流程时按回车...")

            submit_selector = publish_config.get("submit_selector", {})
            if publish_config.get("auto_submit"):
                if not self._selector_is_configured(submit_selector):
                    raise ValueError("Auto submit is enabled but submit_selector is not configured.")
                self._wait_for_element(submit_selector, clickable=True).click()
                self._check_publish_error_state(
                    publish_config.get("submit_error_detection", {}),
                    stage_name="post_submit",
                    exception_cls=PublishSubmitError,
                )
                print(f"[INFO] Submitted product: {product.title}")
            else:
                print(f"[INFO] Auto submit disabled for {product.title}.")
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
            action = step.get("action", "").strip()
            if not action:
                continue

            if action == "manual":
                input(step.get("message", "Manual check required. Press Enter to continue..."))
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
                    stage_name=step.get("name", "assert_no_errors"),
                    exception_cls=PublishValidationError,
                )
                continue

            if action == "extract":
                self._extract_value(step, context)
                continue

            if action == "match_candidates":
                self._match_candidates(step, context)
                self._pause(self.browser_config.get("action_wait_seconds", 0.5))
                continue

            selector = step.get("selector", {})
            required = bool(step.get("required", False))
            if not self._selector_is_configured(selector):
                if required:
                    raise ValueError(f"Required selector not configured for step '{step.get('name')}'")
                print(f"[WARN] Skip step '{step.get('name')}' because selector is empty.")
                continue

            if action in {"input", "textarea"}:
                value = self._resolve_value(step, context)
                if not value:
                    if required:
                        raise ValueError(f"Required value missing for step '{step.get('name')}'")
                    continue
                element = self._wait_for_element(selector, clickable=True)
                if step.get("clear", True):
                    element.clear()
                element.send_keys(str(value))
            elif action == "select":
                value = self._resolve_value(step, context)
                if not value:
                    if required:
                        raise ValueError(f"Required select value missing for step '{step.get('name')}'")
                    continue
                element = self._wait_for_element(selector, clickable=True)
                self._select_option(element, step, value)
            elif action == "file":
                values = self._resolve_file_values(step, context)
                if not values:
                    if required:
                        raise ValueError(f"Required file value missing for step '{step.get('name')}'")
                    continue
                element = self._wait_for_element(selector)
                payload = "\n".join(values) if step.get("multiple") else values[0]
                element.send_keys(payload)
            elif action == "click":
                self._wait_for_element(selector, clickable=True).click()
                if step.get("check_errors_after"):
                    self._check_publish_error_state(
                        step.get("error_detection", {}),
                        stage_name=step.get("name", "click"),
                        exception_cls=PublishValidationError,
                    )
            else:
                raise ValueError(f"Unsupported action type: {action}")

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
        context["resolved_category_name"] = category_entry.get("platform_categories", {}).get(
            platform_key, ""
        )
        context["detail_images_list"] = detail_images
        return context

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

    def _extract_value(self, step: dict[str, Any], context: dict[str, Any]) -> None:
        if not self.driver:
            raise RuntimeError("Browser has not been opened.")

        selector = step.get("selector", {})
        target_key = step.get("target", step.get("name", "extracted_value"))
        if not self._selector_is_configured(selector):
            if step.get("required", False):
                raise ValueError(f"Required selector not configured for extractor '{target_key}'")
            return

        element = self._wait_for_element(selector)
        attribute = step.get("attribute", "").strip()
        if attribute:
            context[target_key] = (element.get_attribute(attribute) or "").strip()
        else:
            context[target_key] = " ".join(element.text.split())

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

    def _check_publish_error_state(
        self,
        detection_config: dict[str, Any],
        *,
        stage_name: str,
        exception_cls: type[Exception] = ValueError,
    ) -> None:
        if not detection_config or not detection_config.get("enabled", True):
            return
        if not self.driver:
            raise RuntimeError("Browser has not been opened.")

        timeout_seconds = float(detection_config.get("timeout_seconds", 0))
        deadline = time.time() + max(timeout_seconds, 0)
        keywords = [str(item).strip() for item in detection_config.get("keywords", []) if str(item).strip()]
        error_text = ""

        while True:
            error_text = self._extract_error_text(detection_config)
            if self._contains_any_keyword(error_text, keywords):
                self._close_error_dialog(detection_config)
                raise exception_cls(f"{stage_name} blocked by page error: {error_text}")

            if time.time() >= deadline:
                break
            time.sleep(0.2)

    def _extract_error_text(self, detection_config: dict[str, Any]) -> str:
        if not self.driver:
            raise RuntimeError("Browser has not been opened.")

        message_selector = detection_config.get("message_selector", {})
        if self._selector_is_configured(message_selector):
            try:
                element = self.driver.find_element(
                    BY_MAPPING.get(message_selector.get("by", "css").strip().lower(), By.CSS_SELECTOR),
                    message_selector.get("value", "").strip(),
                )
                return " ".join(element.text.split())
            except Exception:
                pass

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

    def _selector_is_configured(self, selector: dict[str, str] | None) -> bool:
        if not selector:
            return False
        return bool(str(selector.get("value", "")).strip())

    def _pause(self, seconds: float) -> None:
        if seconds > 0:
            time.sleep(seconds)

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
