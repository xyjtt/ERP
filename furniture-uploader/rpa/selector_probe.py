from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from webdriver_manager.chrome import ChromeDriverManager


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Capture visible page elements for selector discovery.")
    parser.add_argument("--url", default="", help="Optional URL to open before manual navigation.")
    parser.add_argument(
        "--output",
        default="logs/selector_probe/selector_probe.json",
        help="JSON output path.",
    )
    parser.add_argument(
        "--screenshot",
        default="",
        help="Optional screenshot path. Defaults next to output file.",
    )
    parser.add_argument(
        "--html",
        default="",
        help="Optional HTML snapshot path. Defaults next to output file.",
    )
    parser.add_argument(
        "--headless",
        action="store_true",
        help="Run browser in headless mode.",
    )
    return parser


def main() -> None:
    args = build_argument_parser().parse_args()
    driver = open_browser(headless=args.headless)
    try:
        if args.url:
            driver.get(args.url)
        input("完成登录并打开目标页面后，按回车开始抓取 selector 探针结果...")
        payload = build_probe_payload(driver)
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

        screenshot_path = Path(args.screenshot) if args.screenshot else output_path.with_suffix(".png")
        screenshot_path.parent.mkdir(parents=True, exist_ok=True)
        driver.save_screenshot(str(screenshot_path))

        html_path = Path(args.html) if args.html else output_path.with_suffix(".html")
        html_path.parent.mkdir(parents=True, exist_ok=True)
        html_path.write_text(driver.page_source, encoding="utf-8")

        print(f"Saved probe json: {output_path}")
        print(f"Saved screenshot: {screenshot_path}")
        print(f"Saved html snapshot: {html_path}")
    finally:
        driver.quit()


def open_browser(*, headless: bool) -> webdriver.Chrome:
    options = Options()
    if headless:
        options.add_argument("--headless=new")
    return webdriver.Chrome(
        service=Service(ChromeDriverManager().install()),
        options=options,
    )


def build_probe_payload(driver: webdriver.Chrome) -> dict[str, object]:
    script = """
    const nodes = Array.from(document.querySelectorAll('input, textarea, select, button, a, img, [role="button"], [contenteditable="true"]'));
    const visible = nodes.filter((node) => {
      const style = window.getComputedStyle(node);
      const rect = node.getBoundingClientRect();
      return style.visibility !== 'hidden' && style.display !== 'none' && rect.width > 0 && rect.height > 0;
    });

    function shortText(value) {
      return (value || '').replace(/\\s+/g, ' ').trim().slice(0, 120);
    }

    function buildSelector(node) {
      if (node.id) return `#${CSS.escape(node.id)}`;
      if (node.getAttribute('name')) return `${node.tagName.toLowerCase()}[name="${node.getAttribute('name')}"]`;
      if (node.getAttribute('placeholder')) return `${node.tagName.toLowerCase()}[placeholder="${node.getAttribute('placeholder')}"]`;
      const classes = Array.from(node.classList || []).slice(0, 3).filter(Boolean);
      if (classes.length) return `${node.tagName.toLowerCase()}.${classes.map((item) => CSS.escape(item)).join('.')}`;
      return node.tagName.toLowerCase();
    }

    return visible.map((node, index) => ({
      index: index + 1,
      tag: node.tagName.toLowerCase(),
      type: node.getAttribute('type') || '',
      id: node.id || '',
      name: node.getAttribute('name') || '',
      text: shortText(node.innerText || node.textContent || ''),
      value: shortText(node.value || ''),
      placeholder: node.getAttribute('placeholder') || '',
      role: node.getAttribute('role') || '',
      classes: Array.from(node.classList || []).slice(0, 8),
      selector_hint: buildSelector(node),
      attributes: {
        title: node.getAttribute('title') || '',
        href: node.getAttribute('href') || '',
        src: node.getAttribute('src') || '',
        'data-testid': node.getAttribute('data-testid') || '',
        'data-name': node.getAttribute('data-name') || '',
        'aria-label': node.getAttribute('aria-label') || '',
      }
    }));
    """
    elements = driver.execute_script(script)
    return {
        "captured_at": datetime.now().isoformat(timespec="seconds"),
        "current_url": driver.current_url,
        "page_title": driver.title,
        "element_count": len(elements),
        "elements": elements,
    }


if __name__ == "__main__":
    main()
