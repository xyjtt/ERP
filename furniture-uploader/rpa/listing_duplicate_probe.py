"""Read-only live duplicate checks for guarded 1688 listings."""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from typing import Any, Iterable

from selenium.common.exceptions import TimeoutException
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys

from browser_rpa import BY_MAPPING


NO_DATA_MARKERS = (
    "暂无数据",
    "没有找到商品",
    "未找到相关商品",
    "没有符合条件的商品",
)


def normalize_search_text(value: Any) -> str:
    return re.sub(r"\s+", "", str(value or "")).lower()


def parse_draft_count(text: Any) -> int | None:
    body = str(text or "")
    patterns = (
        r"草稿箱\s*[（(]?\s*(\d+)\s*[）)]?",
        r"草稿\s*[（(]\s*(\d+)\s*[）)]",
        r"草稿\s+(\d+)(?:\s|$)",
    )
    for pattern in patterns:
        match = re.search(pattern, body)
        if match:
            return int(match.group(1))
    return None


def parse_pagination_total(text: Any) -> int | None:
    body = str(text or "")
    patterns = (
        r"共\s*(\d+)\s*条",
        r"共\s*(\d+)\s*个商品",
        r"总计\s*(\d+)\s*条",
    )
    for pattern in patterns:
        match = re.search(pattern, body)
        if match:
            return int(match.group(1))
    return None


def summarize_search_result(
    query: str,
    row_texts: Iterable[str],
    body_text: str,
) -> dict[str, Any]:
    normalized_query = normalize_search_text(query)
    visible_rows = [str(text or "").strip() for text in row_texts if str(text or "").strip()]
    matching_rows = [
        text for text in visible_rows if normalized_query in normalize_search_text(text)
    ]
    no_data_marker = next(
        (marker for marker in NO_DATA_MARKERS if marker in str(body_text or "")),
        "",
    )
    if matching_rows:
        status = "found"
    elif no_data_marker:
        status = "clear"
    else:
        status = "inconclusive"
    return {
        "query": str(query or "").strip(),
        "status": status,
        "result_count": len(visible_rows),
        "exact_text_match_count": len(matching_rows),
        "no_data_marker": no_data_marker,
    }


def evaluate_duplicate_gate(
    *,
    sku_search: dict[str, Any],
    spu_search: dict[str, Any],
    draft_count: int | None,
    draft_limit: int,
) -> dict[str, Any]:
    sku_status = str(sku_search.get("status") or "inconclusive")
    spu_status = str(spu_search.get("status") or "inconclusive")
    draft_gate = (
        "passed"
        if draft_count is not None and 0 <= draft_count < int(draft_limit)
        else "blocked"
    )
    novelty_type = ""
    if sku_status == "clear" and spu_status == "clear":
        novelty_type = "new_spu"
    elif sku_status == "clear" and spu_status == "found":
        novelty_type = "new_sku"
    status = (
        "clear"
        if novelty_type and draft_gate == "passed"
        else "blocked"
    )
    return {
        "status": status,
        "novelty_type": novelty_type,
        "sku_result_count": int(sku_search.get("result_count") or 0),
        "spu_result_count": int(spu_search.get("result_count") or 0),
        "draft_count": draft_count,
        "draft_limit": int(draft_limit),
        "draft_remaining": (
            max(0, int(draft_limit) - int(draft_count))
            if draft_count is not None
            else None
        ),
        "draft_capacity_gate": draft_gate,
    }


@dataclass(frozen=True)
class ListingCandidate:
    sku: str
    spu: str


class LiveListingDuplicateProbe:
    def __init__(
        self,
        browser: Any,
        system_config: dict[str, Any],
        *,
        expected_shop: str,
        expected_shop_aliases: Iterable[str] = (),
        draft_limit: int = 20,
    ) -> None:
        self.browser = browser
        self.system_config = system_config
        self.expected_shop = str(expected_shop or "").strip()
        self.expected_shop_aliases = {
            normalize_search_text(value)
            for value in (self.expected_shop, *expected_shop_aliases)
            if str(value or "").strip()
        }
        self.draft_limit = int(draft_limit)

    def run(self, candidates: Iterable[ListingCandidate]) -> dict[str, Any]:
        driver = self.browser.driver
        if driver is None:
            raise RuntimeError("Browser has not been opened.")
        management_url = str(self.system_config.get("management_url") or "").strip()
        if "tab=all" not in management_url.lower():
            raise ValueError("management_url must explicitly select tab=all")

        self.browser._navigate_with_timeout_recovery(management_url)
        self.browser._pause(self.browser.browser_config.get("page_load_wait_seconds", 2))
        self.browser._prune_duplicate_automation_tabs()
        top_level_url = str(driver.current_url or "")
        tab_all = "tab=all" in top_level_url.lower()
        identity = self._shop_identity()
        top_level_body = self._body_text()
        top_level_draft_hints = self._draft_hints()
        selectors = dict(((self.system_config.get("workflow") or {}).get("selectors") or {}))
        self._switch_management_frame(selectors)
        self.browser._wait_for_management_search_ready({})

        frame_draft_hints = self._draft_hints()
        results = []
        for candidate in candidates:
            sku_result = self._search(selectors, candidate.sku)
            spu_result = self._search(selectors, candidate.spu)
            results.append(
                {
                    "sku": candidate.sku,
                    "spu": candidate.spu,
                    "sku_search": sku_result,
                    "spu_search": spu_result,
                }
            )

        draft_evidence = self._inspect_draft_count(selectors)
        draft_count = draft_evidence["draft_count"]
        if draft_count is None:
            draft_count = parse_draft_count(
                "\n".join(
                    [
                        top_level_body,
                        *(item["text"] for item in top_level_draft_hints),
                        *(item["text"] for item in frame_draft_hints),
                    ]
                )
            )
        for item in results:
            item["duplicate_check"] = evaluate_duplicate_gate(
                sku_search=item["sku_search"],
                spu_search=item["spu_search"],
                draft_count=draft_count,
                draft_limit=self.draft_limit,
            )

        passed = bool(tab_all and identity["matched"] and results) and all(
            item["duplicate_check"]["status"] == "clear" for item in results
        )
        return {
            "status": "passed" if passed else "blocked",
            "read_only": True,
            "expected_shop": self.expected_shop,
            "shop_identity": identity,
            "management_url": top_level_url,
            "tab_all": tab_all,
            "tab_count": len(driver.window_handles),
            "draft_count": draft_count,
            "draft_limit": self.draft_limit,
            "draft_count_evidence": draft_evidence,
            "draft_hints": {
                "top_level": top_level_draft_hints,
                "management_frame": frame_draft_hints,
            },
            "candidates": results,
        }

    def _shop_identity(self) -> dict[str, Any]:
        driver = self.browser.driver
        assert driver is not None
        body_text = self._body_text()
        visible_names: list[str] = []
        selector = (
            ((self.system_config.get("workflow") or {}).get("selectors") or {}).get(
                "current_store_name", {}
            )
        )
        resolved = self.browser._resolve_selector(selector, {})
        if self.browser._selector_is_configured(resolved):
            by = BY_MAPPING.get(
                str(resolved.get("by") or "css").strip().lower(),
                By.CSS_SELECTOR,
            )
            for element in driver.find_elements(by, str(resolved.get("value") or "")):
                text = str(element.text or "").strip()
                if text:
                    visible_names.append(text)
        haystack = normalize_search_text(" ".join([*visible_names, body_text]))
        matched_alias = next(
            (alias for alias in self.expected_shop_aliases if alias and alias in haystack),
            "",
        )
        return {
            "matched": bool(matched_alias),
            "visible_names": visible_names,
            "matched_alias": matched_alias,
        }

    def _switch_management_frame(self, selectors: dict[str, Any]) -> None:
        driver = self.browser.driver
        assert driver is not None
        frame_selector = self.browser._resolve_selector(selectors.get("management_iframe", {}), {})
        if not self.browser._selector_is_configured(frame_selector):
            return
        try:
            frame = self.browser._wait_for_element(frame_selector)
        except TimeoutException:
            return
        driver.switch_to.frame(frame)

    def _search(self, selectors: dict[str, Any], query: str) -> dict[str, Any]:
        product_selector = self.browser._resolve_selector(selectors.get("product_id_input", {}), {})
        title_selector = self.browser._resolve_selector(selectors.get("title_sku_input", {}), {})
        search_selector = self.browser._resolve_selector(selectors.get("search_button", {}), {})
        if not all(
            self.browser._selector_is_configured(item)
            for item in (product_selector, title_selector, search_selector)
        ):
            raise ValueError("1688 management duplicate-search selectors are incomplete")
        product_input = self.browser._wait_for_element(product_selector, clickable=True)
        title_input = self.browser._wait_for_element(title_selector, clickable=True)
        self.browser._wait_for_element(search_selector, clickable=True)
        before_rows = tuple(self._visible_result_rows())
        self.browser._fill_management_search_field(product_input, "")
        self.browser._fill_management_search_field(title_input, query)
        input_value = str(title_input.get_attribute("value") or "").strip()
        title_input.send_keys(Keys.ENTER)
        self._wait_for_search_settle(query, before_rows=before_rows)
        row_texts = self._visible_result_rows()
        result = summarize_search_result(query, row_texts, self._body_text())
        result["input_value_verified"] = input_value == query
        result["rows_changed"] = tuple(row_texts) != before_rows
        if (
            result["status"] == "inconclusive"
            and result["input_value_verified"]
            and result["rows_changed"]
            and row_texts
        ):
            result["status"] = "found"
            result["match_method"] = "filtered_result_set"
        elif result["exact_text_match_count"]:
            result["match_method"] = "visible_exact_text"
        elif result["status"] == "clear":
            result["match_method"] = "platform_no_data"
        else:
            result["match_method"] = "inconclusive"
        return result

    def _wait_for_search_settle(
        self,
        query: str,
        *,
        before_rows: tuple[str, ...],
    ) -> None:
        started_at = time.monotonic()
        deadline = time.monotonic() + float(
            self.browser.browser_config.get("explicit_wait_seconds", 20)
        )
        previous: tuple[str, ...] | None = None
        stable_count = 0
        while time.monotonic() < deadline:
            rows = tuple(self._visible_result_rows())
            body = self._body_text()
            query_matched = any(
                normalize_search_text(query) in normalize_search_text(row)
                for row in rows
            )
            no_data = any(marker in body for marker in NO_DATA_MARKERS)
            changed = rows != before_rows
            explicit = query_matched or no_data or changed
            minimum_wait_elapsed = time.monotonic() - started_at >= 2.0
            if minimum_wait_elapsed and explicit and rows == previous:
                stable_count += 1
                if stable_count >= 2:
                    return
            else:
                stable_count = 0
            previous = rows
            time.sleep(0.5)

    def _visible_result_rows(self) -> list[str]:
        driver = self.browser.driver
        assert driver is not None
        rows = driver.find_elements(By.CSS_SELECTOR, "table tbody tr")
        result = []
        for row in rows:
            try:
                text = str(row.text or "").strip()
                classes = str(row.get_attribute("class") or "")
                if row.is_displayed() and text and "placeholder" not in classes:
                    result.append(text)
            except Exception:
                continue
        return result

    def _body_text(self) -> str:
        driver = self.browser.driver
        assert driver is not None
        return str(driver.find_element(By.TAG_NAME, "body").text or "")

    def _draft_hints(self) -> list[dict[str, str]]:
        driver = self.browser.driver
        assert driver is not None
        values = driver.execute_script(
            """
            const results = [];
            const seen = new Set();
            for (const node of document.querySelectorAll('a,button,[role="tab"],span,div')) {
              const text = String(node.innerText || node.textContent || '').trim();
              if (!text.includes('草稿') || text.length > 80 || seen.has(text)) continue;
              seen.add(text);
              results.push({
                text,
                href: node.href ? String(node.href) : '',
                tag: String(node.tagName || '').toLowerCase(),
              });
              if (results.length >= 20) break;
            }
            return results;
            """
        )
        return [
            {
                "text": str(item.get("text") or "").strip(),
                "href": str(item.get("href") or "").strip(),
                "tag": str(item.get("tag") or "").strip(),
            }
            for item in list(values or [])
            if isinstance(item, dict) and str(item.get("text") or "").strip()
        ]

    def _inspect_draft_count(self, selectors: dict[str, Any]) -> dict[str, Any]:
        driver = self.browser.driver
        assert driver is not None
        product_selector = self.browser._resolve_selector(selectors.get("product_id_input", {}), {})
        title_selector = self.browser._resolve_selector(selectors.get("title_sku_input", {}), {})
        product_input = self.browser._wait_for_element(product_selector, clickable=True)
        title_input = self.browser._wait_for_element(title_selector, clickable=True)
        self.browser._fill_management_search_field(product_input, "")
        self.browser._fill_management_search_field(title_input, "")
        title_input.send_keys(Keys.ENTER)
        time.sleep(2.0)

        draft_tabs = driver.find_elements(
            By.XPATH,
            "//*[normalize-space(text())='草稿箱']",
        )
        draft_tab = next((item for item in draft_tabs if item.is_displayed()), None)
        if draft_tab is None:
            return {
                "draft_count": None,
                "status": "inconclusive",
                "reason": "draft tab is not visible",
            }
        driver.execute_script("arguments[0].click();", draft_tab)
        time.sleep(2.0)

        deadline = time.monotonic() + float(
            self.browser.browser_config.get("explicit_wait_seconds", 20)
        )
        previous_rows: tuple[str, ...] | None = None
        stable_count = 0
        while time.monotonic() < deadline:
            rows = tuple(self._visible_result_rows())
            body = self._body_text()
            no_data_marker = next(
                (marker for marker in NO_DATA_MARKERS if marker in body),
                "",
            )
            pagination_texts = [
                str(element.text or "").strip()
                for element in driver.find_elements(
                    By.CSS_SELECTOR,
                    ".ant-pagination-total-text, .next-pagination-total, [class*='pagination']",
                )
                if str(element.text or "").strip()
            ]
            pagination_total = parse_pagination_total("\n".join(pagination_texts))
            active = bool(
                driver.execute_script(
                    """
                    const node = arguments[0];
                    for (const candidate of [node, node.parentElement, node.parentElement && node.parentElement.parentElement]) {
                      if (candidate && String(candidate.className || '').toLowerCase().includes('active')) return true;
                    }
                    return false;
                    """,
                    draft_tab,
                )
            )
            if rows == previous_rows:
                stable_count += 1
            else:
                stable_count = 0
            previous_rows = rows
            if active and stable_count >= 2:
                if pagination_total is not None:
                    draft_count = pagination_total
                    source = "pagination_total"
                elif no_data_marker and not rows:
                    draft_count = 0
                    source = "platform_no_data"
                elif len(rows) < 20:
                    draft_count = len(rows)
                    source = "visible_rows_below_page_size"
                else:
                    draft_count = None
                    source = "page_size_boundary"
                return {
                    "draft_count": draft_count,
                    "status": "passed" if draft_count is not None else "inconclusive",
                    "source": source,
                    "active": active,
                    "visible_row_count": len(rows),
                    "pagination_texts": pagination_texts,
                    "no_data_marker": no_data_marker,
                }
            time.sleep(0.5)
        return {
            "draft_count": None,
            "status": "inconclusive",
            "reason": "draft tab did not settle",
        }
