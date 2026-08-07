import fs from "node:fs/promises";
import path from "node:path";
import { chromium, Locator, Page } from "playwright";
import { appConfig } from "./config";
import {
  appendLedgerResult,
  buildProductGroupKey,
  buildStructuredRowEvidence,
  CleanupMode,
  CleanupResult,
  CleanupTask,
  findIdentitySiblingRows,
  findMatchingRows,
  orderCleanupTasksForExecution,
  RowEvidence,
} from "./1688-link-cleanup-core";
import {
  dismissQuickSaveModal,
  dismissVisibleGuides,
  dismissVisibleModals,
  collectStorePickerDiagnostics,
  ensureProductPage,
  login,
  selectExactStore,
  Target,
} from "./jushuitan";
import { normalizeStoreSelectionError } from "./store-picker";
import { selectors } from "./selectors";
import { ensureDir, resolveFirstVisibleLocator, sanitizeFileName } from "./utils";

class CleanupBrowserError extends Error {
  constructor(
    readonly category: string,
    message: string,
    readonly stopScope: "task" | "store" | "all" = "task",
    readonly details: Record<string, unknown> = {},
  ) {
    super(message);
    this.name = "CleanupBrowserError";
  }
}

interface BrowserRunOptions {
  mode: Exclude<CleanupMode, "preview">;
  runId: string;
  ledgerPath: string;
  artifactsDir: string;
}

interface ProductPageSession {
  target?: Target;
}

interface EmptyQueryEvidence {
  productValue: string;
  onlineSkuValue: string;
  explicitEmpty: boolean;
}

const productIdSelectors = [
  '#shopGoodsImportQuery_shopStyleCode input[placeholder*="多个商品ID"]',
  'input[placeholder*="多个商品ID，以逗号分隔"]',
] as const;

const onlineSkuSelectors = [
  '#shopGoodsImportQuery_itemCode input[placeholder*="多个线上商品编码"]',
  'input[placeholder*="多个线上商品编码，以逗号分隔"]',
] as const;

const resultRowSelector = [
  ".art-table-body tbody tr.art-table-row",
  ".art-table-body tbody tr:has(td:first-child input.ant-checkbox-input)",
  ".ant-table-tbody > tr:has(td:first-child input.ant-checkbox-input)",
  "tbody tr:has(td:first-child input[type='checkbox'])",
  ".art-table-body [role='row']:has([role='gridcell'])",
  ".ant-table-tbody [role='row']:has([role='gridcell'])",
].join(",");

function now(): string {
  return new Date().toISOString();
}

async function anyVisible(target: Target, candidates: readonly string[], timeoutMs = 800): Promise<boolean> {
  for (const candidate of candidates) {
    if (await target.locator(candidate).first().isVisible({ timeout: timeoutMs }).catch(() => false)) {
      return true;
    }
  }
  return false;
}

async function assertNoRiskControl(page: Page): Promise<void> {
  const bodyText = await page.locator("body").innerText().catch(() => "");
  if (/向右滑动验证|滑块验证|验证码|安全验证|账号存在风险|操作过于频繁/.test(bodyText)) {
    throw new CleanupBrowserError("risk_control", "Jushuitan risk control or captcha is present", "all");
  }
}

function assertLoginCredentialsAvailable(): void {
  if (!appConfig.username || !appConfig.password) {
    throw new CleanupBrowserError(
      "login_required",
      "Jushuitan stored login session is unavailable and login credentials are not configured",
      "all",
    );
  }
}

async function saveGlobalFailureEvidence(
  page: Page,
  options: BrowserRunOptions,
): Promise<string> {
  const baseDir = path.join(options.artifactsDir, options.runId);
  await ensureDir(baseDir);
  const screenshotPath = path.join(baseDir, "session-failed.png");
  const htmlPath = path.join(baseDir, "session-failed.html");
  await page.screenshot({ path: screenshotPath, fullPage: true }).catch(() => undefined);
  await fs.writeFile(htmlPath, await page.content().catch(() => ""), "utf8");
  return screenshotPath;
}

async function fillExact(target: Target, candidates: readonly string[], value: string): Promise<void> {
  const input = await resolveFirstVisibleLocator(target, candidates, 8000);
  await input.fill("");
  await input.fill(value);
  if ((await input.inputValue().catch(() => "")) !== value) {
    throw new CleanupBrowserError("query_identity_mismatch", `Query input rejected exact value: ${value}`);
  }
}

async function readExactInputValue(target: Target, candidates: readonly string[]): Promise<string> {
  const input = await resolveFirstVisibleLocator(target, candidates, 8000);
  return (await input.inputValue().catch(() => "")).trim();
}

async function hasExplicitNoSearchResults(target: Target): Promise<boolean> {
  if (
    await anyVisible(
      target,
      [".ant-empty", ".art-empty-wrapper", ".empty-tips", "text=暂无数据", "text=暂无店铺商品"],
      800,
    )
  ) {
    return true;
  }
  const visibleText = await target.locator("body").innerText().catch(() => "");
  return /共\s*0\s*条/.test(visibleText);
}

export function isVerifiedEmptyCleanupQuery(
  evidence: EmptyQueryEvidence,
  task: Pick<CleanupTask, "product_id" | "online_sku">,
): boolean {
  return (
    evidence.explicitEmpty &&
    evidence.productValue === task.product_id.trim() &&
    evidence.onlineSkuValue === task.online_sku.trim()
  );
}

async function readHeadersForRow(target: Target, row: Locator): Promise<string[]> {
  const tableRoot = row.locator(
    "xpath=ancestor::*[self::table or contains(concat(' ', normalize-space(@class), ' '), ' art-table ') or contains(concat(' ', normalize-space(@class), ' '), ' ant-table-wrapper ')][1]",
  ).first();
  const scopedHeaderRows = tableRoot.locator(
    ".art-table-header thead tr, .ant-table-header thead tr, thead tr, [role='row']:has([role='columnheader'])",
  );
  const fallbackHeaderRows = target.locator(
    ".art-table-header thead tr, .ant-table-header thead tr, table thead tr, [role='row']:has([role='columnheader'])",
  );
  const headerRows = (await scopedHeaderRows.count().catch(() => 0)) > 0
    ? scopedHeaderRows
    : fallbackHeaderRows;
  let visibleHeaders: string[] = [];
  const headerRowCount = await headerRows.count().catch(() => 0);
  for (let index = 0; index < headerRowCount; index += 1) {
    const headerRow = headerRows.nth(index);
    if (!(await headerRow.isVisible().catch(() => false))) {
      continue;
    }
    const headers = await headerRow.locator(":scope > th, :scope > [role='columnheader']").allInnerTexts()
      .catch(() => []);
    if (headers.length > visibleHeaders.length) {
      visibleHeaders = headers;
    }
  }
  return visibleHeaders;
}

async function collectRows(target: Target): Promise<{ locator: Locator; rows: RowEvidence[] }> {
  const locator = target.locator(resultRowSelector);
  const count = await locator.count().catch(() => 0);
  const rows: RowEvidence[] = [];
  for (let index = 0; index < count; index += 1) {
    const row = locator.nth(index);
    if (!(await row.isVisible().catch(() => false))) {
      continue;
    }
    if ((await row.locator("td[colspan]").count().catch(() => 0)) > 0) {
      continue;
    }
    const text = (await row.innerText().catch(() => "")).trim();
    if (text) {
      const headers = await readHeadersForRow(target, row);
      const cells = await row.locator(":scope > td, :scope > [role='gridcell']").allInnerTexts()
        .catch(() => []);
      rows.push(buildStructuredRowEvidence(index, text, headers, cells));
    }
  }
  return { locator, rows };
}

async function saveTaskEvidence(
  page: Page,
  target: Target,
  options: BrowserRunOptions,
  task: CleanupTask,
  stage: string,
  error?: unknown,
  evidenceDetails: Record<string, unknown> = {},
): Promise<string> {
  const baseName = sanitizeFileName(`${task.task_id.slice(0, 12)}-${stage}`);
  const baseDir = path.join(options.artifactsDir, options.runId);
  await ensureDir(baseDir);
  const screenshotPath = path.join(baseDir, `${baseName}.png`);
  const evidencePath = path.join(baseDir, `${baseName}.json`);
  const { rows } = await collectRows(target);
  const pickerDiagnostics = stage === "failed" ? await collectStorePickerDiagnostics(page, target).catch(() => null) : null;
  await page.screenshot({ path: screenshotPath, fullPage: true }).catch(() => undefined);
  await fs.writeFile(
    evidencePath,
    JSON.stringify(
      {
        task_id: task.task_id,
        store_name: task.store_name,
        jushuitan_store_name: task.jushuitan_store_name,
        product_id: task.product_id,
        online_sku: task.online_sku,
        platform_store_item_code: task.platform_store_item_code,
        stage,
        url: page.url(),
        rows,
        error_details: error instanceof CleanupBrowserError ? error.details : undefined,
        evidence_details: evidenceDetails,
        store_picker_diagnostics: pickerDiagnostics,
        captured_at: now(),
      },
      null,
      2,
    ),
    "utf8",
  );
  return evidencePath;
}

async function queryTaskRows(
  page: Page,
  task: CleanupTask,
  options: BrowserRunOptions,
  selectStore: boolean,
  existingTarget?: Target,
): Promise<{
  target: Target;
  rows: RowEvidence[];
  locator: Locator;
  emptyQueryEvidence: EmptyQueryEvidence;
}> {
  await assertNoRiskControl(page);
  const target = existingTarget ?? await ensureProductPage(page);
  await fillExact(target, productIdSelectors, task.product_id);
  // Keep the product page and store selection, but query each SKU exactly so large
  // products do not hide the target row on later pagination pages.
  await fillExact(target, onlineSkuSelectors, task.online_sku);
  if (selectStore) {
    await page.waitForTimeout(600);
    await dismissVisibleModals(page);
    try {
      await selectExactStore(page, target, task.jushuitan_store_name);
    } catch (error) {
      const normalized = normalizeStoreSelectionError(error);
      const selectionDiagnostics = await collectStorePickerDiagnostics(page, target).catch(() => null);
      throw new CleanupBrowserError(normalized.category, normalized.message, "store", {
        ...normalized.details,
        selection_diagnostics: selectionDiagnostics,
      });
    }
  }

  const searchButton = await resolveFirstVisibleLocator(target, selectors.productPage.queryButton, 8000);
  await searchButton.click({ force: true });
  await page.waitForTimeout(appConfig.searchWaitMs);
  await dismissQuickSaveModal(page);
  await dismissVisibleGuides(page);
  await assertNoRiskControl(page);
  const collected = await collectRows(target);
  const emptyQueryEvidence = {
    productValue: await readExactInputValue(target, productIdSelectors),
    onlineSkuValue: await readExactInputValue(target, onlineSkuSelectors),
    explicitEmpty: collected.rows.length === 0 && await hasExplicitNoSearchResults(target),
  };
  await saveTaskEvidence(
    page,
    target,
    options,
    task,
    selectStore ? "query" : "verify",
    undefined,
    { empty_query: emptyQueryEvidence },
  );
  return { target, ...collected, emptyQueryEvidence };
}

async function rowSelectionAccepted(row: Locator, input: Locator): Promise<boolean> {
  return (
    (await input.isChecked().catch(() => false)) ||
    (await input.getAttribute("aria-checked").catch(() => "")) === "true" ||
    (await row.getAttribute("aria-selected").catch(() => "")) === "true" ||
    /(?:^|\s)(?:art|ant)-table-row-selected(?:\s|$)/.test(
      await row.getAttribute("class").catch(() => "") ?? "",
    ) ||
    (await row.locator(
      ".ant-checkbox-checked, [role='checkbox'][aria-checked='true'], [data-checked='true']",
    ).count().catch(() => 0)) > 0
  );
}

export async function activateRowSelectionWithFallback(
  isSelected: () => Promise<boolean>,
  actions: Array<() => Promise<void>>,
  waitAfterAction: () => Promise<void> = async () => undefined,
): Promise<boolean> {
  if (await isSelected()) {
    return true;
  }
  for (const action of actions) {
    try {
      await action();
    } catch {
      // Custom Ant/Art table checkboxes differ across deployed page versions.
    }
    await waitAfterAction();
    if (await isSelected()) {
      return true;
    }
  }
  return false;
}

async function selectExactResultRow(page: Page, target: Target, task: CleanupTask): Promise<void> {
  for (let attempt = 0; attempt < 2; attempt += 1) {
    await dismissVisibleGuides(page);
    const collected = await collectRows(target);
    const matches = findMatchingRows(task, collected.rows);
    if (matches.length !== 1) {
      throw new CleanupBrowserError(
        matches.length === 0 ? "task_not_found" : "ambiguous_match",
        `Exact row changed before selection; expected one match but found ${matches.length}`,
      );
    }

    const row = collected.locator.nth(matches[0].index);
    const inputs = row.locator(
      "td:first-child input.ant-checkbox-input, td:first-child input[type='checkbox'], input.ant-checkbox-input[type='checkbox'], [role='checkbox']",
    );
    const inputCount = await inputs.count().catch(() => 0);
    if (inputCount === 0) {
      throw new CleanupBrowserError("row_selection_failed", "The exact result row has no selectable checkbox");
    }

    for (let index = 0; index < Math.min(inputCount, 4); index += 1) {
      const input = inputs.nth(index);
      if (await rowSelectionAccepted(row, input)) {
        return;
      }
      const wrappers = [
        input.locator("xpath=ancestor::label[contains(@class,'ant-checkbox-wrapper')][1]"),
        input.locator("xpath=ancestor::*[contains(@class,'ant-checkbox')][1]"),
        row.locator("td:first-child label.ant-checkbox-wrapper").first(),
        row.locator("td:first-child .ant-checkbox-inner").first(),
      ];
      const selected = await activateRowSelectionWithFallback(
        () => rowSelectionAccepted(row, input),
        [
          () => input.check({ force: true }),
          () => input.click({ force: true }),
          () => input.evaluate((element) => (element as HTMLElement).click()),
          ...wrappers.map((wrapper) => () => wrapper.click({ force: true })),
          () => input.evaluate((element) => {
            if (element instanceof HTMLInputElement) {
              if (!element.checked) {
                element.click();
              }
              element.dispatchEvent(new Event("input", { bubbles: true }));
              element.dispatchEvent(new Event("change", { bubbles: true }));
              return;
            }
            (element as HTMLElement).click();
          }),
        ],
        () => page.waitForTimeout(300),
      );
      if (selected) {
        return;
      }
    }
  }

  throw new CleanupBrowserError("row_selection_failed", "Jushuitan did not accept the exact row selection");
}

async function clickClearLinkAction(target: Target, page: Page): Promise<void> {
  const groupedActionButton = target.getByRole("button", { name: /更新.?清除链接/ }).first();
  if (await groupedActionButton.isVisible({ timeout: 2000 }).catch(() => false)) {
    const triggerCandidates = [
      groupedActionButton.locator(
        "xpath=following-sibling::button[contains(@class,'ant-dropdown-trigger')][1]",
      ),
      groupedActionButton.locator(
        "xpath=parent::*//button[contains(@class,'ant-dropdown-trigger')][1]",
      ),
      groupedActionButton.locator(
        "xpath=ancestor::*[contains(@class,'dropdown')][1]//button[contains(@class,'ant-dropdown-trigger')][1]",
      ),
    ];
    let trigger: Locator | null = null;
    for (const candidate of triggerCandidates) {
      if (await candidate.isVisible({ timeout: 2000 }).catch(() => false)) {
        trigger = candidate;
        break;
      }
    }
    if (!trigger) {
      throw new CleanupBrowserError("action_unavailable", "Clear-link dropdown trigger is unavailable");
    }
    await trigger.click({ force: true, timeout: 3000 });
    await page.waitForTimeout(300);
    const menuItem = target
      .locator(".ant-dropdown:not(.ant-dropdown-hidden), [role='menu']")
      .getByText("清除链接", { exact: true })
      .last();
    if (!(await menuItem.isVisible({ timeout: 3000 }).catch(() => false))) {
      throw new CleanupBrowserError("action_unavailable", "Clear-link menu item did not appear");
    }
    await menuItem.click({ force: true });
    return;
  }

  const directButton = target.getByRole("button", { name: "清除链接", exact: true }).first();
  if (await directButton.isVisible({ timeout: 2000 }).catch(() => false)) {
    await directButton.click({ force: true });
    return;
  }

  throw new CleanupBrowserError("action_unavailable", "Clear-link action is unavailable on the product page");
}

async function confirmClearLink(target: Target, page: Page): Promise<string> {
  await page.waitForTimeout(500);
  const dialogs = target.locator("[role='dialog'], .ant-modal, .ant-modal-content");
  const count = await dialogs.count().catch(() => 0);
  for (let index = count - 1; index >= 0; index -= 1) {
    const dialog = dialogs.nth(index);
    if (!(await dialog.isVisible().catch(() => false))) {
      continue;
    }
    const text = (await dialog.innerText().catch(() => "")).trim();
    if (!/清除链接|确认/.test(text)) {
      continue;
    }
    const confirm = dialog
      .getByRole("button", { name: /确\s*定|确\s*认|清\s*除/ })
      .or(dialog.locator(".ant-modal-footer button.ant-btn-primary, button.ant-btn-primary"))
      .last();
    if (!(await confirm.isVisible().catch(() => false))) {
      throw new CleanupBrowserError("confirmation_failed", "Clear-link confirmation button is unavailable");
    }
    await confirm.click({ force: true });
    await page.waitForTimeout(800);
    return text.slice(0, 500);
  }
  return "Clear-link action submitted without a confirmation dialog";
}

async function processTask(
  page: Page,
  task: CleanupTask,
  options: BrowserRunOptions,
  session: ProductPageSession,
  selectStore: boolean,
): Promise<CleanupResult> {
  const initial = await queryTaskRows(page, task, options, selectStore, session.target);
  session.target = initial.target;
  const matches = findMatchingRows(task, initial.rows);
  if (matches.length === 0) {
    const identitySiblings = findIdentitySiblingRows(task, initial.rows);
    const verifiedEmpty = isVerifiedEmptyCleanupQuery(initial.emptyQueryEvidence, task);
    if (options.mode === "execute" && (identitySiblings.length > 0 || verifiedEmpty)) {
      const evidencePath = await saveTaskEvidence(
        page,
        initial.target,
        options,
        task,
        "already-cleared",
        undefined,
        {
          identity_sibling_count: identitySiblings.length,
          empty_query: initial.emptyQueryEvidence,
        },
      );
      return {
        task_id: task.task_id,
        status: "already_cleared",
        store_name: task.store_name,
        jushuitan_store_name: task.jushuitan_store_name,
        product_id: task.product_id,
        online_sku: task.online_sku,
        platform_store_item_code: task.platform_store_item_code,
        category: "verified_target_absent",
        message: identitySiblings.length > 0
          ? `Target platform code is absent while ${identitySiblings.length} exact store/product/SKU sibling row(s) remain`
          : "Exact store/product/SKU query returned an explicit zero-row result",
        evidence_path: evidencePath,
        recorded_at: now(),
      };
    }
    throw new CleanupBrowserError(
      "task_not_found",
      `No exact row matched store/product/SKU/platform code for ${task.product_id}/${task.online_sku}`,
    );
  }
  if (matches.length !== 1) {
    throw new CleanupBrowserError("ambiguous_match", `Expected one exact row but found ${matches.length}`);
  }

  const evidencePath = await saveTaskEvidence(page, initial.target, options, task, "matched");
  if (options.mode === "probe") {
    return {
      task_id: task.task_id,
      status: "found",
      store_name: task.store_name,
      jushuitan_store_name: task.jushuitan_store_name,
      product_id: task.product_id,
      online_sku: task.online_sku,
      platform_store_item_code: task.platform_store_item_code,
      row_text: matches[0].text,
      evidence_path: evidencePath,
      recorded_at: now(),
    };
  }

  await selectExactResultRow(page, initial.target, task);
  await saveTaskEvidence(page, initial.target, options, task, "selected");
  await clickClearLinkAction(initial.target, page);
  const confirmationText = await confirmClearLink(initial.target, page);
  await page.waitForTimeout(appConfig.searchWaitMs);

  const verification = await queryTaskRows(page, task, options, false, session.target ?? initial.target);
  const remainingMatches = findMatchingRows(task, verification.rows);
  if (remainingMatches.length > 0) {
    throw new CleanupBrowserError(
      "verification_failed",
      `Exact row still exists after clear-link submission (${remainingMatches.length})`,
    );
  }

  return {
    task_id: task.task_id,
    status: "success",
    store_name: task.store_name,
    jushuitan_store_name: task.jushuitan_store_name,
    product_id: task.product_id,
    online_sku: task.online_sku,
    platform_store_item_code: task.platform_store_item_code,
    message: confirmationText,
    evidence_path: evidencePath,
    recorded_at: now(),
  };
}

function failureResult(task: CleanupTask, error: unknown, evidencePath = ""): CleanupResult {
  const browserError = error instanceof CleanupBrowserError ? error : null;
  return {
    task_id: task.task_id,
    status: "failed",
    store_name: task.store_name,
    jushuitan_store_name: task.jushuitan_store_name,
    product_id: task.product_id,
    online_sku: task.online_sku,
    platform_store_item_code: task.platform_store_item_code,
    category: browserError?.category ?? "browser_error",
    message: error instanceof Error ? error.message : String(error),
    evidence_path: evidencePath,
    recorded_at: now(),
  };
}

export async function runBrowserCleanup(
  tasks: CleanupTask[],
  options: BrowserRunOptions,
): Promise<CleanupResult[]> {
  let storageStatePath: string | undefined;
  try {
    await fs.access(appConfig.storageStatePath);
    storageStatePath = appConfig.storageStatePath;
  } catch {
    storageStatePath = undefined;
  }

  const browser = await chromium.launch({
    headless: appConfig.headless,
    channel: appConfig.browserChannel,
    executablePath: appConfig.browserExecutablePath,
    slowMo: appConfig.slowMo,
  });
  const context = await browser.newContext(storageStatePath ? { storageState: storageStatePath } : undefined);
  const page = await context.newPage();
  const results: CleanupResult[] = [];
  const stoppedStores = new Set<string>();
  let activeProduct: { key: string; session: ProductPageSession } | null = null;
  let selectedStoreName = "";
  let stopAll = false;

  try {
    if (storageStatePath) {
      await page.goto(appConfig.productUrl, { waitUntil: "domcontentloaded", timeout: 120000 });
      await page.waitForTimeout(2500);
      await assertNoRiskControl(page);
      if (/login/i.test(page.url()) || (await anyVisible(page, selectors.login.username, 2000))) {
        assertLoginCredentialsAvailable();
        await login(page, { allowManualWait: false });
      }
    } else {
      assertLoginCredentialsAvailable();
      await login(page, { allowManualWait: false });
    }
    await assertNoRiskControl(page);
    try {
      await ensureProductPage(page);
    } catch (error) {
      await assertNoRiskControl(page);
      if (/login/i.test(page.url()) || (await anyVisible(page, selectors.login.username, 1000))) {
        throw new CleanupBrowserError("login_required", "Jushuitan login session is invalid", "all");
      }
      throw error;
    }
    await ensureDir(path.dirname(appConfig.storageStatePath));
    if (appConfig.saveStorageState) {
      await context.storageState({ path: appConfig.storageStatePath });
    }

    for (const task of orderCleanupTasksForExecution(tasks)) {
      if (stopAll || stoppedStores.has(task.store_name)) {
        results.push(
          failureResult(
            task,
            new CleanupBrowserError(
              stopAll ? "session_stopped" : "store_stopped",
              stopAll ? "Jushuitan session was stopped by a safety error" : "Store was stopped by a safety error",
            ),
          ),
        );
        continue;
      }

      try {
        const sessionKey = buildProductGroupKey(task);
        const session: ProductPageSession =
          activeProduct?.key === sessionKey ? activeProduct.session : {};
        const result = await processTask(
          page,
          task,
          options,
          session,
          selectedStoreName !== task.store_name,
        );
        activeProduct = { key: sessionKey, session };
        selectedStoreName = task.store_name;
        results.push(result);
        if (result.status === "success" || result.status === "already_cleared") {
          await appendLedgerResult(options.ledgerPath, result);
        }
      } catch (error) {
        activeProduct = null;
        const preservePickerState =
          error instanceof CleanupBrowserError &&
          (error.category === "store_picker_unavailable" || error.category === "store_mismatch");
        const evidenceTarget = preservePickerState
          ? page
          : ((await ensureProductPage(page).catch(() => page)) as Target);
        const evidencePath = await saveTaskEvidence(
          page,
          evidenceTarget,
          options,
          task,
          "failed",
          error,
        ).catch(() => "");
        results.push(failureResult(task, error, evidencePath));
        if (error instanceof CleanupBrowserError && error.stopScope === "store") {
          stoppedStores.add(task.store_name);
          selectedStoreName = "";
        }
        if (error instanceof CleanupBrowserError && error.stopScope === "all") {
          stopAll = true;
          selectedStoreName = "";
        }
      }
    }
  } catch (error) {
    const globalEvidencePath = await saveGlobalFailureEvidence(page, options).catch(() => "");
    for (const task of tasks) {
      if (!results.some((result) => result.task_id === task.task_id)) {
        results.push(failureResult(task, error, globalEvidencePath));
      }
    }
  } finally {
    await context.close().catch(() => undefined);
    await browser.close().catch(() => undefined);
  }

  return results;
}
