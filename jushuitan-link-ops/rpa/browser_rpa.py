from __future__ import annotations

import re
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from selenium import webdriver
from selenium.common.exceptions import TimeoutException
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.remote.webelement import WebElement
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait
from webdriver_manager.chrome import ChromeDriverManager

from .exceptions import (
    BatchDialogOpenError,
    OldCodeInputError,
    PageChangedError,
    PlatformSelectionError,
    PostCheckFailedError,
    RowEditError,
    SearchResultEmptyError,
    SearchResultMismatchError,
    ShopSelectionError,
    SubmitConfirmError,
)


@dataclass
class TaskRuntimeResult:
    matched_count_before: int
    remaining_count_after: int
    status: str
    current_url: str
    page_title: str
    success_message: str = ""
    screenshot_path: str = ""
    html_snapshot_path: str = ""


class BrowserRPA:
    def __init__(self, system_config: dict[str, Any], operation_config: dict[str, Any], project_root: Path) -> None:
        self.system_config = system_config
        self.operation_config = operation_config
        self.project_root = project_root
        self.driver: webdriver.Chrome | None = None
        browser_config = system_config.get("browser", {})
        self.headless = bool(browser_config.get("headless", False))
        self.wait_seconds = int(browser_config.get("wait_seconds", 20))
        self.implicit_wait_seconds = int(browser_config.get("implicit_wait_seconds", 2))
        self.allow_manual_fallback = bool(operation_config.get("allow_manual_fallback", True))
        self.last_screenshot_path = ""
        self.last_html_snapshot_path = ""
        self.last_result_context: dict[str, Any] = {}

    def open(self) -> None:
        options = Options()
        if self.headless:
            options.add_argument("--headless=new")
        options.add_argument("--disable-gpu")
        options.add_argument("--start-maximized")
        self.driver = webdriver.Chrome(
            service=Service(ChromeDriverManager().install()),
            options=options,
        )
        self.driver.implicitly_wait(self.implicit_wait_seconds)

    def close(self) -> None:
        if self.driver:
            self.driver.quit()
            self.driver = None

    def login(self, *, skip_login: bool = False) -> None:
        if not self.driver:
            raise RuntimeError("Browser is not open")

        self.driver.get(self.system_config["login_url"])
        if skip_login:
            return

        username = str(self.system_config.get("username", "")).strip()
        password = str(self.system_config.get("password", "")).strip()
        selectors = self.system_config.get("login_selectors", {})

        if username and password and selectors.get("username_input", {}).get("value"):
            self._input(selectors["username_input"], username)
            self._input(selectors["password_input"], password)
            if selectors.get("submit_button", {}).get("value"):
                self._click(selectors["submit_button"])
            time.sleep(2)
            return

        input("请在浏览器中完成聚水潭登录，完成后按回车继续...")

    def open_manage_page(self) -> None:
        if not self.driver:
            raise RuntimeError("Browser is not open")

        manage_page_url = str(self.system_config.get("manage_page_url", "")).strip()
        if manage_page_url:
            self.driver.get(manage_page_url)
            time.sleep(2)
            return

        input("请手动打开 聚水潭 > 店铺商品管理 > 批量修改线上商品编码 页面，完成后按回车继续...")

    def process_task(self, task) -> TaskRuntimeResult:
        try:
            matched_count_before = self._prepare_search_and_collect(task)
            self._run_batch_update(task)
            remaining_count_after = self._post_check(task)
            status = "success" if remaining_count_after == 0 else "partial_success"
            result = TaskRuntimeResult(
                matched_count_before=matched_count_before,
                remaining_count_after=remaining_count_after,
                status=status,
                current_url=self.driver.current_url if self.driver else "",
                page_title=self.driver.title if self.driver else "",
                success_message=self._extract_text(self.operation_config.get("selectors", {}).get("success_message", {})),
            )
            self.last_result_context = result.__dict__.copy()
            return result
        except Exception:
            self.capture_runtime_artifacts(task.task_id)
            raise

    def capture_runtime_artifacts(self, task_id: str) -> tuple[str, str]:
        if not self.driver:
            return "", ""

        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        safe_task_id = re.sub(r"[^A-Za-z0-9_.-]+", "_", task_id)
        target_dir = self.project_root / "logs" / "artifacts" / safe_task_id
        target_dir.mkdir(parents=True, exist_ok=True)

        screenshot_path = target_dir / f"{stamp}.png"
        html_snapshot_path = target_dir / f"{stamp}.html"
        self.driver.save_screenshot(str(screenshot_path))
        html_snapshot_path.write_text(self.driver.page_source, encoding="utf-8")

        self.last_screenshot_path = str(screenshot_path)
        self.last_html_snapshot_path = str(html_snapshot_path)
        self.last_result_context = {
            **self.last_result_context,
            "current_url": self.driver.current_url,
            "page_title": self.driver.title,
            "screenshot_path": self.last_screenshot_path,
            "html_snapshot_path": self.last_html_snapshot_path,
        }
        return self.last_screenshot_path, self.last_html_snapshot_path

    def _prepare_search_and_collect(self, task) -> int:
        selectors = self.operation_config.get("selectors", {})
        self._ensure_page_ready(selectors)
        self._select_dimension_if_needed(selectors)
        self._set_platform(task.platform, selectors)
        self._set_shop(task.shop_name, selectors)
        self._query_old_code(task.old_online_sku_code, selectors)
        count = self._count_rows(selectors)
        if count <= 0:
            raise SearchResultEmptyError(
                f"未查询到旧编码结果: {task.old_online_sku_code}",
                step_name="search_old_code",
            )
        self._validate_rows(task, selectors)
        return count

    def _run_batch_update(self, task) -> None:
        selectors = self.operation_config.get("selectors", {})
        self._select_rows(selectors)
        self._open_batch_dialog(selectors)
        self._fill_new_code(task.new_online_sku_code, selectors)
        self._submit_batch(selectors)

    def _post_check(self, task) -> int:
        selectors = self.operation_config.get("selectors", {})
        time.sleep(float(self.operation_config.get("post_submit_wait_seconds", 2)))
        self._query_old_code(task.old_online_sku_code, selectors)
        remaining = self._count_rows(selectors)
        if remaining < 0:
            raise PostCheckFailedError("回查旧编码失败", step_name="post_check")
        return remaining

    def _ensure_page_ready(self, selectors: dict[str, Any]) -> None:
        ready_selector = selectors.get("page_ready_marker", {})
        if ready_selector.get("value"):
            self._wait(ready_selector)
        elif not self.allow_manual_fallback:
            raise PageChangedError("page_ready_marker 未配置，且未允许人工兜底", step_name="page_ready")

    def _select_dimension_if_needed(self, selectors: dict[str, Any]) -> None:
        dropdown = selectors.get("search_dimension_dropdown", {})
        option_selector = selectors.get("search_dimension_sku_option", {})
        if dropdown.get("value"):
            self._click(dropdown)
            time.sleep(0.3)
        if option_selector.get("value"):
            self._click(option_selector)
            return
        if dropdown.get("value") and self.allow_manual_fallback:
            input("请手动选择查询维度为“按SKU”，完成后按回车继续...")

    def _set_platform(self, platform: str, selectors: dict[str, Any]) -> None:
        dropdown = selectors.get("platform_dropdown", {})
        option_template = selectors.get("platform_option_template", {})
        search_input = selectors.get("platform_search_input", {})
        if dropdown.get("value"):
            self._click(dropdown)
            time.sleep(0.3)
        if search_input.get("value"):
            self._input(search_input, platform)
        if option_template.get("value"):
            self._click(self._render_selector(option_template, {"platform": platform}))
            return
        if self.allow_manual_fallback:
            input(f"请手动选择平台：{platform}，完成后按回车继续...")
            return
        raise PlatformSelectionError(f"平台选择器未配置: {platform}", step_name="select_platform")

    def _set_shop(self, shop_name: str, selectors: dict[str, Any]) -> None:
        dropdown = selectors.get("shop_dropdown", {})
        option_template = selectors.get("shop_option_template", {})
        search_input = selectors.get("shop_search_input", {})
        if dropdown.get("value"):
            self._click(dropdown)
            time.sleep(0.3)
        if search_input.get("value"):
            self._input(search_input, shop_name)
        if option_template.get("value"):
            self._click(self._render_selector(option_template, {"shop_name": shop_name}))
            return
        if self.allow_manual_fallback:
            input(f"请手动选择店铺：{shop_name}，完成后按回车继续...")
            return
        raise ShopSelectionError(f"店铺选择器未配置: {shop_name}", step_name="select_shop")

    def _query_old_code(self, old_code: str, selectors: dict[str, Any]) -> None:
        input_selector = selectors.get("old_code_input", {})
        search_button = selectors.get("search_button", {})

        if input_selector.get("value"):
            self._input(input_selector, old_code)
        elif self.allow_manual_fallback:
            input(f"请手动输入旧线上商品编码：{old_code}，完成后按回车继续...")
        else:
            raise OldCodeInputError("old_code_input 未配置", step_name="search_old_code")

        if search_button.get("value"):
            self._click(search_button)
        elif self.allow_manual_fallback:
            input("请手动点击查询，完成后按回车继续...")
        else:
            raise OldCodeInputError("search_button 未配置", step_name="search_old_code")
        time.sleep(float(self.operation_config.get("search_wait_seconds", 2)))

    def _count_rows(self, selectors: dict[str, Any]) -> int:
        count_selector = selectors.get("result_count_label", {})
        if count_selector.get("value"):
            text = self._extract_text(count_selector)
            digits = re.findall(r"\d+", text)
            if digits:
                return int(digits[-1])
        rows_selector = selectors.get("result_rows", {})
        if rows_selector.get("value"):
            return len(self._find_elements(rows_selector))
        if self.allow_manual_fallback:
            raw_value = input("未配置结果计数选择器，请输入当前查询结果数量：").strip()
            if raw_value.isdigit():
                return int(raw_value)
        return -1

    def _validate_rows(self, task, selectors: dict[str, Any]) -> None:
        rows_selector = selectors.get("result_rows", {})
        if not rows_selector.get("value"):
            if self.allow_manual_fallback:
                input(
                    f"请人工确认查询结果平台={task.platform}、店铺={task.shop_name}、旧编码={task.old_online_sku_code} 均正确，完成后按回车继续..."
                )
                return
            raise SearchResultMismatchError("result_rows 未配置", step_name="validate_rows")

        rows = self._find_elements(rows_selector)
        if not rows:
            raise SearchResultEmptyError("结果行为空", step_name="validate_rows")

        for row in rows:
            text = " ".join(row.text.split())
            if task.platform not in text or task.shop_name not in text or task.old_online_sku_code not in text:
                raise SearchResultMismatchError("结果行内容与任务不匹配", step_name="validate_rows")

    def _select_rows(self, selectors: dict[str, Any]) -> None:
        select_all_selector = selectors.get("select_all_checkbox", {})
        if select_all_selector.get("value"):
            self._click(select_all_selector)
            return
        if self.allow_manual_fallback:
            input("请手动勾选要修改的记录，完成后按回车继续...")
            return
        raise RowEditError("select_all_checkbox 未配置", step_name="select_rows")

    def _open_batch_dialog(self, selectors: dict[str, Any]) -> None:
        button_selector = selectors.get("batch_update_button", {})
        dialog_selector = selectors.get("batch_dialog", {})
        if button_selector.get("value"):
            self._click(button_selector)
        elif self.allow_manual_fallback:
            input("请手动点击“批量修改线上商品编码”，完成后按回车继续...")
        else:
            raise BatchDialogOpenError("batch_update_button 未配置", step_name="open_batch_dialog")

        if dialog_selector.get("value"):
            try:
                self._wait(dialog_selector)
            except TimeoutException as exc:
                raise BatchDialogOpenError("批量弹窗未出现", step_name="open_batch_dialog") from exc

    def _fill_new_code(self, new_code: str, selectors: dict[str, Any]) -> None:
        input_selector = selectors.get("new_code_input", {})
        batch_fill_button = selectors.get("batch_fill_button", {})
        if input_selector.get("value"):
            self._input(input_selector, new_code)
        elif self.allow_manual_fallback:
            input(f"请手动输入新线上商品编码：{new_code}，完成后按回车继续...")
        else:
            raise RowEditError("new_code_input 未配置", step_name="fill_new_code")

        if batch_fill_button.get("value"):
            self._click(batch_fill_button)
            return
        if self.allow_manual_fallback:
            input("请手动执行“批量填充”，完成后按回车继续...")
            return
        raise RowEditError("batch_fill_button 未配置", step_name="fill_new_code")

    def _submit_batch(self, selectors: dict[str, Any]) -> None:
        submit_selector = selectors.get("submit_button", {})
        error_selector = selectors.get("error_message", {})
        if submit_selector.get("value"):
            self._click(submit_selector)
        elif self.allow_manual_fallback:
            input("请手动点击确定提交，完成后按回车继续...")
        else:
            raise SubmitConfirmError("submit_button 未配置", step_name="submit")

        time.sleep(float(self.operation_config.get("submit_wait_seconds", 2)))
        error_text = self._extract_text(error_selector)
        if error_text and any(keyword in error_text for keyword in self.operation_config.get("error_keywords", [])):
            raise SubmitConfirmError(error_text, step_name="submit")

    def _wait(self, selector: dict[str, str], *, clickable: bool = False) -> WebElement:
        if not self.driver:
            raise RuntimeError("Browser is not open")
        locator = (selector_to_by(selector), selector["value"])
        wait = WebDriverWait(self.driver, self.wait_seconds)
        if clickable:
            return wait.until(EC.element_to_be_clickable(locator))
        return wait.until(EC.presence_of_element_located(locator))

    def _find_elements(self, selector: dict[str, str]) -> list[WebElement]:
        if not self.driver:
            raise RuntimeError("Browser is not open")
        return self.driver.find_elements(selector_to_by(selector), selector["value"])

    def _click(self, selector: dict[str, str]) -> None:
        self._wait(selector, clickable=True).click()

    def _input(self, selector: dict[str, str], value: str) -> None:
        element = self._wait(selector, clickable=True)
        element.send_keys(Keys.CONTROL, "a")
        element.send_keys(Keys.DELETE)
        element.send_keys(value)

    def _extract_text(self, selector: dict[str, str]) -> str:
        if not selector or not str(selector.get("value", "")).strip():
            return ""
        try:
            return " ".join(self._wait(selector).text.split())
        except Exception:
            return ""

    def _render_selector(self, selector: dict[str, str], context: dict[str, str]) -> dict[str, str]:
        value = str(selector.get("value", ""))
        for key, raw_value in context.items():
            value = value.replace("{" + key + "}", raw_value)
        return {"by": selector.get("by", "css"), "value": value}


def selector_to_by(selector: dict[str, str]) -> By:
    mapping = {
        "css": By.CSS_SELECTOR,
        "xpath": By.XPATH,
        "id": By.ID,
        "name": By.NAME,
    }
    return mapping.get(str(selector.get("by", "css")).strip().lower(), By.CSS_SELECTOR)
