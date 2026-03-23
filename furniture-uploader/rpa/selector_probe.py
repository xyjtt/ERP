from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path
from typing import Any

from webdriver_factory import open_webdriver


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Capture visible page elements for selector discovery.")
    parser.add_argument("--url", default="", help="Optional URL to open before manual navigation.")
    parser.add_argument(
        "--debugger-address",
        default="",
        help="Attach to an existing Chrome or Edge instance via remote debugging, for example 127.0.0.1:9222.",
    )
    parser.add_argument(
        "--browser",
        default="chrome",
        choices=["chrome", "edge"],
        help="Browser engine to launch or attach to.",
    )
    parser.add_argument(
        "--user-data-dir",
        default="",
        help="Optional browser user data directory for reusing a local browser profile.",
    )
    parser.add_argument(
        "--profile-directory",
        default="",
        help="Optional browser profile directory name, for example Default.",
    )
    parser.add_argument(
        "--chrome-binary",
        default="",
        help="Optional browser binary path. Works for both Chrome and Edge for backward compatibility.",
    )
    parser.add_argument(
        "--browser-binary",
        default="",
        help="Optional browser binary path.",
    )
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
    driver, attached_to_existing_browser = open_browser(
        headless=args.headless,
        debugger_address=args.debugger_address,
        user_data_dir=args.user_data_dir,
        profile_directory=args.profile_directory,
        browser_binary=args.browser_binary or args.chrome_binary,
        browser_type=args.browser,
    )
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
        if not attached_to_existing_browser:
            driver.quit()


def open_browser(
    *,
    headless: bool,
    debugger_address: str,
    user_data_dir: str,
    profile_directory: str,
    browser_binary: str,
    browser_type: str,
) -> tuple[Any, bool]:
    return open_webdriver(
        headless=headless,
        debugger_address=debugger_address,
        user_data_dir=user_data_dir,
        profile_directory=profile_directory,
        browser_binary_path=browser_binary,
        browser_type=browser_type,
    )


def build_probe_payload(driver: Any) -> dict[str, object]:
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

    function findLabelText(node) {
      const id = node.id || '';
      if (id) {
        const explicit = document.querySelector(`label[for="${CSS.escape(id)}"]`);
        if (explicit) {
          return shortText(explicit.innerText || explicit.textContent || '');
        }
      }
      const wrappedLabel = node.closest('label');
      if (wrappedLabel) {
        return shortText(wrappedLabel.innerText || wrappedLabel.textContent || '');
      }
      return '';
    }

    function findParentText(node) {
      let current = node.parentElement;
      let depth = 0;
      while (current && depth < 4) {
        const text = shortText(current.innerText || current.textContent || '');
        if (text) {
          return text;
        }
        current = current.parentElement;
        depth += 1;
      }
      return '';
    }

    function buildDomPath(node) {
      const parts = [];
      let current = node;
      let depth = 0;
      while (current && current.nodeType === Node.ELEMENT_NODE && depth < 5) {
        let part = current.tagName.toLowerCase();
        if (current.id) {
          part += `#${current.id}`;
          parts.unshift(part);
          break;
        }
        const classes = Array.from(current.classList || []).slice(0, 2).filter(Boolean);
        if (classes.length) {
          part += `.${classes.join('.')}`;
        }
        parts.unshift(part);
        current = current.parentElement;
        depth += 1;
      }
      return parts.join(' > ');
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
      label_text: findLabelText(node),
      parent_text: findParentText(node),
      role: node.getAttribute('role') || '',
      classes: Array.from(node.classList || []).slice(0, 8),
      selector_hint: buildSelector(node),
      dom_path_hint: buildDomPath(node),
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
