# 1688 Stop-Sale Execution-Machine Merge Review

## Scope

This review covers the execution-machine branch through `fda2760` and the
development baseline through `5b3bea8`. The execution-machine history is a
fast-forward descendant of the development baseline; no unrelated working-tree
files are part of this merge.

## Retained

- Audit heartbeat updates and transient database retries.
- Atomic heartbeat verification when SQL Server returns an unknown row count.
- Stop-sale stage completion before final per-SKU audit persistence.
- Dead WebDriver detection and retry termination.
- Jushuitan exact SKU queries while reusing the selected store and product page.
- The unfiltered 1688 management URL (`tab=all`, empty `q`, empty
  `filterOfferId`).
- CDP submit clicks with a WebDriver click fallback.

## Corrected During Merge

- The management URL is normalized in code and an already-open unfiltered
  management page is reused.
- The optional "all products" tab click uses valid Selenium XPath selectors.
- The unsupported Playwright-style CSS selector `button:has-text(...)` and its
  duplicate hard-coded navigation path were removed.
- CDP coordinate failures now actually continue to `element.click()` instead
  of returning as if the CDP click succeeded.
- All extra edit windows are closed before the next grouped product starts.

## Excluded

- The 2026-07-21 and 2026-07-23 temporary execution reports. Their totals were
  internally inconsistent and their recommendations to delete lock files or
  bypass DingTalk notification conflict with the production safety contract.
- Unrelated listing/publishing code, demo assets, image-picker probes, migration
  scripts, crawler worker changes, and local execution-machine files.
- Passwords, cookies, tokens, webhook values, and other runtime credentials.

## Production Safety Contract

- Execute mode keeps preflight, shared-lock, active-run, store, account, and
  product identity checks.
- Business and technical exceptions remain terminal per-item results and are
  included in DingTalk notification and audit output.
- The merge does not automatically delete locks, rewrite audit rows, bypass
  risk control, or disable notifications.

## Validation

- Furniture uploader: `254 passed`.
- Stop-sale focused Python suite: `162 passed`.
- Python bytecode compilation: passed.
- Jushuitan TypeScript check: passed.
- Jushuitan tests: `12 passed`.
- Jushuitan build: passed.
- Git whitespace validation: passed.
