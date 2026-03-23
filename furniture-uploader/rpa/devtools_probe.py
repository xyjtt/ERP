from __future__ import annotations

import argparse
import base64
import itertools
import json
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any

import websocket


PROBE_SCRIPT = r"""
(() => {
  const nodes = Array.from(document.querySelectorAll('input, textarea, select, button, a, img, [role="button"], [contenteditable="true"], iframe'));
  const visible = nodes.filter((node) => {
    const style = window.getComputedStyle(node);
    const rect = node.getBoundingClientRect();
    return style.visibility !== 'hidden' && style.display !== 'none' && rect.width > 0 && rect.height > 0;
  });

  function shortText(value) {
    return (value || '').replace(/\s+/g, ' ').trim().slice(0, 200);
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
    while (current && depth < 5) {
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
    while (current && current.nodeType === Node.ELEMENT_NODE && depth < 6) {
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
})()
"""


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Capture selector probe payload through DevTools remote debugging.")
    parser.add_argument("--debugger-address", required=True, help="DevTools address such as 127.0.0.1:9222")
    parser.add_argument(
        "--url-contains",
        default="",
        help="Optional substring used to choose the target page from open tabs.",
    )
    parser.add_argument(
        "--output",
        default="logs/selector_probe/devtools_probe.json",
        help="JSON output path.",
    )
    parser.add_argument(
        "--screenshot",
        default="",
        help="Optional screenshot output path. Defaults next to the JSON output.",
    )
    parser.add_argument(
        "--html",
        default="",
        help="Optional HTML snapshot path. Defaults next to the JSON output.",
    )
    return parser


def main() -> None:
    args = build_argument_parser().parse_args()
    tab = choose_target_tab(fetch_tabs(args.debugger_address), url_contains=args.url_contains)
    payload, screenshot_bytes, html = capture_tab_payload(tab["webSocketDebuggerUrl"])

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(
            {
                "captured_at": datetime.now().isoformat(timespec="seconds"),
                "current_url": tab["url"],
                "page_title": tab.get("title", ""),
                "element_count": len(payload),
                "elements": payload,
            },
            ensure_ascii=False,
            indent=2,
        ) + "\n",
        encoding="utf-8",
    )

    screenshot_path = Path(args.screenshot) if args.screenshot else output_path.with_suffix(".png")
    screenshot_path.parent.mkdir(parents=True, exist_ok=True)
    screenshot_path.write_bytes(screenshot_bytes)

    html_path = Path(args.html) if args.html else output_path.with_suffix(".html")
    html_path.parent.mkdir(parents=True, exist_ok=True)
    html_path.write_text(html, encoding="utf-8")

    print(f"Saved probe json: {output_path}")
    print(f"Saved screenshot: {screenshot_path}")
    print(f"Saved html snapshot: {html_path}")


def fetch_tabs(debugger_address: str) -> list[dict[str, Any]]:
    with urllib.request.urlopen(f"http://{debugger_address}/json/list") as response:
        return json.loads(response.read().decode("utf-8"))


def choose_target_tab(tabs: list[dict[str, Any]], *, url_contains: str) -> dict[str, Any]:
    page_tabs = [tab for tab in tabs if tab.get("type") == "page"]
    if not page_tabs:
        raise RuntimeError("No page tabs found in the remote debugging session.")

    if url_contains:
        for tab in page_tabs:
            if url_contains in tab.get("url", ""):
                return tab
        raise RuntimeError(f"No page tab matched url substring: {url_contains}")

    return page_tabs[0]


def capture_tab_payload(websocket_url: str) -> tuple[list[dict[str, Any]], bytes, str]:
    ws = websocket.create_connection(websocket_url, timeout=20, suppress_origin=True)
    message_id = itertools.count(1)

    def send(method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        payload: dict[str, Any] = {"id": next(message_id), "method": method}
        if params is not None:
            payload["params"] = params
        ws.send(json.dumps(payload))
        while True:
            message = json.loads(ws.recv())
            if message.get("id") == payload["id"]:
                if "error" in message:
                    raise RuntimeError(f"CDP {method} failed: {message['error']}")
                return message.get("result", {})

    try:
        send("Page.enable")
        probe_result = send("Runtime.evaluate", {"expression": PROBE_SCRIPT, "returnByValue": True})
        html_result = send(
            "Runtime.evaluate",
            {"expression": "document.documentElement.outerHTML", "returnByValue": True},
        )
        screenshot_result = send("Page.captureScreenshot", {"format": "png", "fromSurface": True})
        return (
            probe_result["result"]["value"],
            base64.b64decode(screenshot_result["data"]),
            html_result["result"]["value"],
        )
    finally:
        ws.close()


if __name__ == "__main__":
    main()
