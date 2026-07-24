"""Print safe DOM diagnostics for the currently open 1688 image picker."""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

from selenium.webdriver.common.by import By
from selenium.webdriver.edge.options import Options
from selenium.webdriver.edge.service import Service
from selenium.webdriver.edge.webdriver import WebDriver


PROJECT_ROOT = Path(__file__).resolve().parents[1]
RPA_ROOT = PROJECT_ROOT / "rpa"
if str(RPA_ROOT) not in sys.path:
    sys.path.insert(0, str(RPA_ROOT))

from config_loader import load_json_with_local_override
from webdriver_factory import resolve_edge_driver_path


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    operator_config = load_json_with_local_override(PROJECT_ROOT / "config" / "operator_config.json")
    browser_config = operator_config.get("browser", {})
    debugger_address = str(browser_config.get("debugger_address") or "127.0.0.1:9222")
    driver_path = resolve_edge_driver_path(debugger_address=debugger_address)
    options = Options()
    options.add_experimental_option("debuggerAddress", debugger_address)
    driver: WebDriver | None = None
    try:
        driver = WebDriver(service=Service(driver_path), options=options)
        for handle in driver.window_handles:
            driver.switch_to.window(handle)
            if "offer-new.1688.com/popular/publish.htm" in str(driver.current_url or ""):
                break
        visible_picker_exists = any(
            frame.is_displayed()
            for frame in driver.find_elements(By.CSS_SELECTOR, "iframe.picker-frame")
        )
        if "--open" in sys.argv and not visible_picker_exists:
            opener = driver.find_element(
                By.XPATH,
                "(//div[@id='guid-primaryPicture']//div[contains(@class,'picture-sort-list')]"
                "//div[contains(@class,'module-picture-cover-wrapper') and contains(@class,'cover-empty')])[1]",
            )
            driver.execute_script("arguments[0].click();", opener)
            time.sleep(2)
        frames = [
            frame
            for frame in driver.find_elements(By.CSS_SELECTOR, "iframe.picker-frame")
            if frame.is_displayed()
        ]
        if not frames:
            opener_xpath = (
                "(//div[@id='guid-primaryPicture']//div[contains(@class,'picture-sort-item')]"
                "//div[contains(@class,'module-picture-cover-wrapper')])[1]"
            )
            openers = driver.find_elements(By.XPATH, opener_xpath)
            screenshot_path = PROJECT_ROOT / "logs" / "production" / "picker-open-failure.png"
            driver.save_screenshot(str(screenshot_path))
            print(
                json.dumps(
                    {
                        "status": "no_picker_frame",
                        "url": driver.current_url,
                        "opener_count": len(openers),
                        "openers": [
                            {
                                "displayed": item.is_displayed(),
                                "enabled": item.is_enabled(),
                                "class": str(item.get_attribute("class") or ""),
                                "html": str(item.get_attribute("outerHTML") or "")[:1000],
                            }
                            for item in openers
                        ],
                        "dialog_count": len(
                            driver.find_elements(By.CSS_SELECTOR, "div.ibank-picker-dialog, div.ui-dialog")
                        ),
                        "add_image_targets": driver.execute_script(
                            """
                            return Array.from(document.querySelectorAll('#guid-primaryPicture *'))
                              .filter((node) => String(node.innerText || node.textContent || '').trim() === '添加图片')
                              .slice(0, 20)
                              .map((node) => ({
                                tag: node.tagName,
                                className: String(node.className || ''),
                                html: String(node.outerHTML || '').slice(0, 1500),
                                parentHtml: String((node.parentElement && node.parentElement.outerHTML) || '').slice(0, 2000),
                              }));
                            """
                        ),
                        "body_text": str(driver.find_element(By.TAG_NAME, "body").text or "")[:3000],
                        "screenshot": str(screenshot_path),
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return 1
        parent_dialogs = driver.execute_script(
            """
            return Array.from(document.querySelectorAll('div.ibank-picker-dialog, div.ui-dialog')).map((dialog) => ({
              className: String(dialog.className || ''),
              html: String(dialog.outerHTML || '').slice(0, 6000),
              closeCandidates: Array.from(dialog.querySelectorAll('button, a, [aria-label], [class*=close]'))
                .slice(0, 50)
                .map((node) => ({
                  tag: node.tagName,
                  className: String(node.className || ''),
                  ariaLabel: String(node.getAttribute('aria-label') || ''),
                  title: String(node.getAttribute('title') || ''),
                  text: String(node.innerText || node.textContent || '').trim().slice(0, 100),
                })),
            }));
            """
        )
        driver.switch_to.frame(frames[0])
        payload = driver.execute_script(
            """
            const visible = (node) => {
              if (!node) return false;
              const style = window.getComputedStyle(node);
              return style.display !== 'none' && style.visibility !== 'hidden' && node.getClientRects().length > 0;
            };
            const describe = (selector) => Array.from(document.querySelectorAll(selector)).map((node) => ({
              tag: node.tagName,
              className: String(node.className || ''),
              text: String(node.innerText || node.textContent || '').trim().slice(0, 200),
              value: String(node.value || '').slice(0, 200),
              title: String(node.getAttribute('title') || '').slice(0, 200),
              visible: visible(node),
              display: String(window.getComputedStyle(node).display || ''),
              visibility: String(window.getComputedStyle(node).visibility || ''),
              width: Number(node.getBoundingClientRect().width || 0),
              height: Number(node.getBoundingClientRect().height || 0),
              disabled: Boolean(node.disabled),
            }));
            return {
              url: window.location.href,
              bodyText: String((document.body && document.body.innerText) || '').slice(0, 5000),
              rawBodyText: String((document.body && document.body.textContent) || '').slice(0, 8000),
              tabItems: describe('li'),
              tabContents: describe('.tabs-content'),
              albumCreate: describe('.album-create'),
              nameInputs: describe("input.create-field[name='name']"),
              privateControls: describe('#album-manager-pri'),
              submitButtons: describe('a.button.insert'),
              parentDialogs: arguments[0],
              selects: Array.from(document.querySelectorAll('select')).map((select) => ({
                visible: visible(select),
                options: Array.from(select.options).slice(0, 100).map((option) => ({
                  text: String(option.text || '').trim(),
                  title: String(option.getAttribute('title') || '').trim(),
                  value: String(option.value || '').trim(),
                  selected: Boolean(option.selected),
                  disabled: Boolean(option.disabled),
                })),
              })),
            };
            """,
            parent_dialogs,
        )
        if "--summary" in sys.argv:
            visible_selects = [item for item in payload.get("selects", []) if item.get("visible")]
            options = visible_selects[0].get("options", []) if visible_selects else []
            payload = {
                "url": payload.get("url"),
                "selected": [item for item in options if item.get("selected")],
                "available": [
                    item
                    for item in options
                    if not item.get("disabled") and "已满" not in str(item.get("text") or "")
                ],
                "album_create_visible": any(
                    item.get("visible") for item in payload.get("albumCreate", [])
                ),
                "name_input_values": [
                    item.get("value")
                    for item in payload.get("nameInputs", [])
                    if item.get("visible")
                ],
                "body_text": str(payload.get("bodyText") or "")[-1200:],
                "raw_body_text": str(payload.get("rawBodyText") or "")[:2000],
                "tab_items": payload.get("tabItems", []),
                "tab_contents": payload.get("tabContents", []),
            }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0
    finally:
        if driver is not None:
            driver.service.stop()


if __name__ == "__main__":
    raise SystemExit(main())
