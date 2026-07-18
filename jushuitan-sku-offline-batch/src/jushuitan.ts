import fs from "node:fs/promises";
import path from "node:path";
import { chromium, Frame, Locator, Page } from "playwright";
import { appConfig } from "./config";
import { platformLabels, selectors } from "./selectors";
import {
  PlatformKey,
  PlatformRunResult,
  RemainingRow,
  SourceRow,
  UpdatePopupRecord,
} from "./types";
import { chunkArray, ensureDir, resolveFirstVisibleLocator } from "./utils";
import { openStorePickerWithFallback, StoreSelectionError } from "./store-picker";

export type Target = Page | Frame | Locator;

function allTargets(page: Page): Target[] {
  return [page, ...page.frames()];
}

async function isVisible(target: Target, selector: string, timeoutMs = 1500): Promise<boolean> {
  try {
    return await target.locator(selector).first().isVisible({ timeout: timeoutMs });
  } catch {
    return false;
  }
}

async function hasVisible(target: Target, candidates: readonly string[], timeoutMs = 1500): Promise<boolean> {
  for (const candidate of candidates) {
    if (await isVisible(target, candidate, timeoutMs)) {
      return true;
    }
  }
  return false;
}

async function clickByCandidates(target: Target, candidates: readonly string[]): Promise<void> {
  const locator = await resolveFirstVisibleLocator(target, candidates);
  await locator.click({ force: true });
}

async function fillByCandidates(
  target: Target,
  candidates: readonly string[],
  value: string,
  shouldClear = true,
): Promise<void> {
  for (const candidate of candidates) {
    try {
      const locator = await resolveFirstVisibleLocator(target, [candidate], 5000);
      await locator.scrollIntoViewIfNeeded().catch(() => undefined);
      const tagName = await locator.evaluate((element) => element.tagName.toLowerCase()).catch(() => "");

      if (tagName === "input" || tagName === "textarea") {
        if (shouldClear) {
          await locator.fill("");
        }
        await locator.fill(value);
        return;
      }

      await locator.click({ force: true });
      if ("keyboard" in target) {
        if (shouldClear) {
          await target.keyboard.press("Control+A").catch(() => undefined);
          await target.keyboard.press("Backspace").catch(() => undefined);
        }
        await target.keyboard.type(value);
        return;
      }
    } catch {
      continue;
    }
  }

  throw new Error(`未找到可输入元素，候选选择器: ${candidates.join(" | ")}`);
}

async function waitForAnyLocatorText(target: Target, candidates: readonly string[]): Promise<string> {
  for (const candidate of candidates) {
    try {
      const locator = target.locator(candidate).first();
      await locator.waitFor({ state: "visible", timeout: appConfig.searchWaitMs });
      const text = (await locator.innerText()).trim();
      if (text) {
        return text;
      }
    } catch {
      continue;
    }
  }
  return "";
}

async function saveDebugScreenshot(page: Page, runId: string, name: string): Promise<void> {
  const filePath = path.join(appConfig.artifactsDir, runId, `${name}.png`);
  await ensureDir(path.dirname(filePath));
  await page.screenshot({ path: filePath, fullPage: true });
}

async function saveDebugHtml(page: Page, runId: string, name: string): Promise<void> {
  const filePath = path.join(appConfig.artifactsDir, runId, `${name}.html`);
  await ensureDir(path.dirname(filePath));
  await fs.writeFile(filePath, await page.content(), "utf8");
}

async function saveTargetHtml(target: Target, runId: string, name: string): Promise<void> {
  const filePath = path.join(appConfig.artifactsDir, runId, `${name}.html`);
  await ensureDir(path.dirname(filePath));
  const html = await target.locator("body").evaluate((element) => (element as HTMLElement).outerHTML).catch(() => "");
  await fs.writeFile(filePath, html, "utf8");
}

async function saveTableDiagnostics(page: Page, runId: string, name: string): Promise<void> {
  const filePath = path.join(appConfig.artifactsDir, runId, `${name}.json`);
  await ensureDir(path.dirname(filePath));
  const payload = await page.evaluate(() => {
    const rows = Array.from(document.querySelectorAll(".ant-table-tbody tr")).slice(0, 10);
    return {
      title: document.title,
      url: location.href,
      bodyText: document.body.innerText.slice(0, 5000),
      headerHtml:
        (
          document.querySelector(
            ".ant-table-thead .ant-checkbox-input, .ant-table-thead .ant-checkbox-inner, .ant-table-thead .ant-checkbox, .ant-table-header thead",
          ) as HTMLElement | null
        )?.outerHTML?.slice(0, 2000) ?? "",
      rowCount: rows.length,
      rows: rows.map((row) => ({
        text: row.textContent?.slice(0, 500) ?? "",
        html: (row as HTMLElement).outerHTML.slice(0, 2000),
      })),
    };
  });
  await fs.writeFile(filePath, JSON.stringify(payload, null, 2), "utf8");
}

async function saveTargetTableDiagnostics(target: Target, runId: string, name: string): Promise<void> {
  const filePath = path.join(appConfig.artifactsDir, runId, `${name}.json`);
  await ensureDir(path.dirname(filePath));
  const payload = await target
    .locator("body")
    .evaluate((body) => {
      const rows = Array.from(body.querySelectorAll(".ant-table-tbody tr")).slice(0, 10);
      const fallbackRows = rows.length > 0 ? rows : Array.from(body.querySelectorAll("table tbody tr")).slice(0, 10);
      return {
        bodyText: (body as HTMLElement).innerText.slice(0, 5000),
        headerHtml:
          (
            body.querySelector(
              ".ant-table-thead .ant-checkbox-input, .ant-table-thead .ant-checkbox-inner, .ant-table-thead .ant-checkbox, .ant-table-header thead, .art-table-header thead, table thead",
            ) as HTMLElement | null
          )?.outerHTML?.slice(0, 2000) ?? "",
        rowCount: fallbackRows.length,
        rows: fallbackRows.map((row) => ({
          text: row.textContent?.slice(0, 500) ?? "",
          html: (row as HTMLElement).outerHTML.slice(0, 2000),
        })),
      };
    })
    .catch(() => ({ bodyText: "", headerHtml: "", rowCount: 0, rows: [] }));
  await fs.writeFile(filePath, JSON.stringify(payload, null, 2), "utf8");
}

async function waitForVisibleModal(page: Page, candidates: readonly string[], timeoutMs = 10000): Promise<Locator> {
  const startedAt = Date.now();

  for (const candidate of candidates) {
    try {
      const locator = page.locator(candidate).last();
      await locator.waitFor({
        state: "visible",
        timeout: Math.max(500, timeoutMs - (Date.now() - startedAt)),
      });
      return locator;
    } catch {
      continue;
    }
  }

  throw new Error(`未找到可见弹窗，候选选择器: ${candidates.join(" | ")}`);
}

async function waitForVisibleTargetLocator(
  page: Page,
  candidates: readonly string[],
  timeoutMs = 10000,
): Promise<Locator> {
  const startedAt = Date.now();

  while (Date.now() - startedAt < timeoutMs) {
    for (const target of allTargets(page)) {
      for (const candidate of candidates) {
        try {
          const locator = target.locator(candidate).last();
          if (await locator.isVisible({ timeout: 500 }).catch(() => false)) {
            return locator;
          }
        } catch {
          continue;
        }
      }
    }
    await page.waitForTimeout(200);
  }

  throw new Error(`未找到可见弹窗，候选选择器: ${candidates.join(" | ")}`);
}

export async function dismissVisibleModals(page: Page): Promise<void> {
  const modalLocator = page.locator(".ant-modal-wrap, [role='dialog']");
  const modalCount = await modalLocator.count();

  for (let index = 0; index < modalCount; index += 1) {
    const modal = modalLocator.nth(index);
    const visible = await modal.isVisible().catch(() => false);
    if (!visible) {
      continue;
    }

    const closeButton = modal
      .locator(
        [
          ".ant-modal-close",
          ".ant-modal-close-x",
          'button:has-text("知道了")',
          'button:has-text("取消")',
          'button:has-text("关闭")',
          'button:has-text("同意")',
          'button:has-text("确定")',
        ].join(","),
      )
      .or(modal.getByRole("button", { name: /确\s*定|知\s*道\s*了|关\s*闭/ }))
      .or(modal.locator(".ant-modal-footer button.ant-btn-primary"))
      .first();

    if ((await closeButton.count()) > 0) {
      await closeButton.click({ force: true }).catch(() => undefined);
      await page.waitForTimeout(500);
    }
  }
}

async function closeGuideIfPresent(page: Page): Promise<void> {
  if (await hasVisible(page, selectors.productPage.guideClose, 800)) {
    await clickByCandidates(page, selectors.productPage.guideClose).catch(() => undefined);
    await page.waitForTimeout(300);
  }
}

export async function dismissQuickSaveModal(page: Page): Promise<void> {
  const modal = page.locator(selectors.productPage.quickSaveModal.join(",")).first();
  if (!(await modal.count())) {
    return;
  }

  const visible = await modal.isVisible().catch(() => false);
  if (!visible) {
    return;
  }

  const closeButton = modal.locator('.ant-modal-close, button:has-text("取消")').first();
  if ((await closeButton.count()) > 0) {
    await closeButton.click({ force: true }).catch(() => undefined);
    await page.waitForTimeout(500);
  }
}

async function findTargetBySelector(page: Page, selector: string, visibleOnly = false): Promise<Target | null> {
  for (const target of allTargets(page)) {
    try {
      const locator = target.locator(selector).first();
      const count = await locator.count();
      if (count === 0) {
        continue;
      }
      if (!visibleOnly) {
        return target;
      }
      if (await locator.isVisible({ timeout: 1000 }).catch(() => false)) {
        return target;
      }
    } catch {
      continue;
    }
  }

  return null;
}

async function getQueryTarget(page: Page): Promise<Target> {
  return (await findTargetBySelector(page, "#shopGoodsImportQuery", true)) ?? page;
}

async function findProductTarget(page: Page): Promise<Target | null> {
  for (const target of allTargets(page)) {
    if (
      (await hasVisible(target, selectors.productPage.queryForm, 800)) ||
      (await hasVisible(target, selectors.productPage.batchUpdateButton, 800)) ||
      (await hasVisible(target, selectors.productPage.skuTab, 800))
    ) {
      return target;
    }
  }
  return null;
}

async function waitForLoggedIn(page: Page): Promise<boolean> {
  const startedAt = Date.now();

  while (Date.now() - startedAt < appConfig.loginWaitMs) {
    await dismissVisibleModals(page);

    if (!(await hasVisible(page, selectors.login.username, 800))) {
      return true;
    }

    if (await findProductTarget(page)) {
      return true;
    }

    await page.waitForTimeout(1000);
  }

  return false;
}

async function ensureLoginAgreementChecked(page: Page): Promise<void> {
  const checkboxInput = page.locator('input[type="checkbox"]').first();
  if ((await checkboxInput.count().catch(() => 0)) > 0) {
    if (!(await checkboxInput.isChecked().catch(() => false))) {
      await checkboxInput.check({ force: true }).catch(() => undefined);
    }
    if (!(await checkboxInput.isChecked().catch(() => false))) {
      const wrapper = checkboxInput.locator("xpath=ancestor::label[1]");
      await wrapper.click({ force: true }).catch(() => undefined);
    }
    if (await checkboxInput.isChecked().catch(() => false)) {
      return;
    }
  }

  if (await hasVisible(page, selectors.login.agreementCheckbox)) {
    await clickByCandidates(page, selectors.login.agreementCheckbox);
    if ((await checkboxInput.count().catch(() => 0)) === 0 || (await checkboxInput.isChecked().catch(() => false))) {
      return;
    }
  }

  throw new Error("Login agreement checkbox could not be selected");
}

async function dismissLoginAgreement(page: Page): Promise<void> {
  if (await hasVisible(page, selectors.login.agreementModalConfirm)) {
    await clickByCandidates(page, selectors.login.agreementModalConfirm);
    await page.waitForTimeout(800);
  }
}

export async function login(page: Page, options: { allowManualWait?: boolean } = {}): Promise<void> {
  await page.goto(appConfig.loginUrl, { waitUntil: "domcontentloaded", timeout: 120000 });
  await dismissLoginAgreement(page);
  await fillByCandidates(page, selectors.login.username, appConfig.username);
  await fillByCandidates(page, selectors.login.password, appConfig.password);
  await ensureLoginAgreementChecked(page);
  await dismissLoginAgreement(page);
  await clickByCandidates(page, selectors.login.submit);
  await dismissVisibleModals(page);

  if (await waitForLoggedIn(page)) {
    return;
  }

  if (options.allowManualWait === false) {
    throw new Error(`Login did not complete without manual intervention. Current URL: ${page.url()}`);
  }

  await page.waitForTimeout(appConfig.manualLoginTimeoutMs);
  await dismissVisibleModals(page);

  if (await waitForLoggedIn(page)) {
    return;
  }

  throw new Error(`Login did not complete. Current URL: ${page.url()}`);
}

async function waitForQueryReady(page: Page): Promise<Target> {
  const startedAt = Date.now();

  while (Date.now() - startedAt < 30000) {
    const target = await getQueryTarget(page);
    if (
      (await hasVisible(target, selectors.productPage.multiSkuInput, 500)) &&
      (await hasVisible(target, selectors.productPage.platformTrigger, 500))
    ) {
      return target;
    }
    await page.waitForTimeout(500);
  }

  throw new Error("未找到店铺商品管理查询表单");
}

export async function ensureProductPage(page: Page): Promise<Target> {
  await dismissVisibleModals(page);
  await closeGuideIfPresent(page);

  const existing = await findProductTarget(page);
  if (existing) {
    const target = await getQueryTarget(page);
    if (await hasVisible(target, selectors.productPage.skuTab)) {
      await clickByCandidates(target, selectors.productPage.skuTab);
      await page.waitForTimeout(800);
    }
    return waitForQueryReady(page);
  }

  for (let attempt = 0; attempt < 3; attempt += 1) {
    try {
      await page.goto(appConfig.productUrl, { waitUntil: "domcontentloaded" });
      await page.waitForLoadState("domcontentloaded", { timeout: 120000 }).catch(() => undefined);
      await page.waitForTimeout(2000);
      await dismissVisibleModals(page);
      await closeGuideIfPresent(page);

      const target = await getQueryTarget(page);
      if (await hasVisible(target, selectors.productPage.multiSkuInput, 1000)) {
        if (await hasVisible(target, selectors.productPage.skuTab)) {
          await clickByCandidates(target, selectors.productPage.skuTab);
          await page.waitForTimeout(800);
        }
        return await waitForQueryReady(page);
      }

      if (await hasVisible(page, selectors.productPage.entryLinks, 1000)) {
        await clickByCandidates(page, selectors.productPage.entryLinks);
        await page.waitForTimeout(2000);
        await dismissVisibleModals(page);
        await closeGuideIfPresent(page);
        const clickedTarget = await getQueryTarget(page);
        if (await hasVisible(clickedTarget, selectors.productPage.skuTab)) {
          await clickByCandidates(clickedTarget, selectors.productPage.skuTab);
          await page.waitForTimeout(800);
        }
        return await waitForQueryReady(page);
      }
    } catch {
      // Retry product URL.
    }
  }

  throw new Error("未能进入店铺商品管理页面");
}

async function clearPlatformSelection(target: Target): Promise<void> {
  const candidates = [
    target
      .locator(
        "xpath=//*[@id='shopGoodsImportQuery_shopIds']/ancestor::*[contains(@class,'ant-input-affix-wrapper')][1]//*[contains(@class,'anticon-close-circle') or contains(@class,'ant-select-clear')]",
      )
      .first(),
    target.locator('#shopGoodsImport_shopIds').locator(".ant-select-clear, .anticon-close-circle").first(),
  ];

  for (const clearIcon of candidates) {
    if ((await clearIcon.count()) > 0 && (await clearIcon.isVisible().catch(() => false))) {
      await clearIcon.click({ force: true }).catch(() => undefined);
      return;
    }
  }
}

async function clickPlatformRowCheckbox(modal: Locator, platformLabel: string): Promise<boolean> {
  const rowGroups = [
    modal.locator("tbody tr"),
    modal.locator(".ant-table-tbody > tr"),
    modal.locator(".ant-tree-treenode"),
    modal.locator('[role="row"]'),
  ];

  for (const rows of rowGroups) {
    const count = await rows.count().catch(() => 0);
    if (count === 0) {
      continue;
    }

    for (let index = 0; index < count; index += 1) {
      const row = rows.nth(index);
      const rowText = (await row.innerText().catch(() => "")).replace(/\s+/g, "");
      if (!rowText.includes(platformLabel)) {
        continue;
      }

      const checkbox = row.locator(selectors.productPage.platformModalRowCheckboxInputs.join(",")).first();
      if ((await checkbox.count()) > 0) {
        const checked = await checkbox.isChecked().catch(() => false);
        if (!checked) {
          await checkbox.check({ force: true }).catch(() => undefined);
          if (!(await checkbox.isChecked().catch(() => false))) {
            await row.click({ force: true }).catch(() => undefined);
          }
        }
        return true;
      }

      const checkboxLabel = row.locator("label.ant-checkbox-wrapper, .ant-checkbox-wrapper").first();
      if ((await checkboxLabel.count()) > 0) {
        await checkboxLabel.click({ force: true }).catch(() => undefined);
        return true;
      }

      await row.click({ force: true }).catch(() => undefined);
      return true;
    }
  }

  return false;
}

async function clickPlatformFooterSelectAll(modal: Locator): Promise<boolean> {
  const footerInput = modal
    .locator(selectors.productPage.platformModalFooterSelectAllInput.join(","))
    .first();
  const footerLabel = modal
    .locator(selectors.productPage.platformModalFooterSelectAll.join(","))
    .first();

  if ((await footerInput.count()) > 0) {
    const checkedBefore = await footerInput.isChecked().catch(() => false);
    if (!checkedBefore) {
      if ((await footerLabel.count()) > 0) {
        await footerLabel.click({ force: true }).catch(() => undefined);
      } else {
        await footerInput.check({ force: true }).catch(() => undefined);
      }
    }
    return await footerInput.isChecked().catch(() => false);
  }

  if ((await footerLabel.count()) > 0) {
    await footerLabel.click({ force: true }).catch(() => undefined);
    return true;
  }

  return false;
}

function parseSelectionCounts(footerText: string): { platformCount: number; shopCount: number } {
  const numbers = [...footerText.matchAll(/\d+/g)].map((match) => Number(match[0]));
  return {
    platformCount: numbers[0] ?? 0,
    shopCount: numbers[1] ?? 0,
  };
}

async function ensurePlatformSelectedInTrigger(queryTarget: Target): Promise<boolean> {
  const trigger = queryTarget.locator(selectors.productPage.platformTrigger.join(",")).first();
  const value = (await trigger.inputValue().catch(() => "")).trim();
  if (value) {
    return true;
  }

  const wrapperText = await trigger
    .locator("xpath=ancestor::span[contains(@class,'ant-input-affix-wrapper')]")
    .innerText()
    .catch(() => "");

  return !wrapperText.includes("请选择平台/店铺");
}

async function getStoreDialog(page: Page, timeoutMs = 10000): Promise<Locator | null> {
  const startedAt = Date.now();

  while (Date.now() - startedAt < timeoutMs) {
    for (const target of allTargets(page)) {
      const dialogs = [
        target.locator('[role="dialog"]').filter({ hasText: /请选择平台.?店铺/ }),
        target.locator('.ant-modal').filter({ hasText: /请选择平台.?店铺/ }),
        target.locator('.ant-modal-content').filter({ hasText: /请选择平台.?店铺/ }),
        target.locator('[role="dialog"]').filter({
          has: target.locator('input[placeholder*="平台或店铺名称"], input[placeholder*="回车搜索"]'),
        }),
        target.locator('.ant-modal, .ant-modal-wrap, .ant-modal-content').filter({
          has: target.locator('input[placeholder*="平台或店铺名称"], input[placeholder*="回车搜索"]'),
        }),
        target.locator('.ant-drawer, .ant-drawer-content').filter({
          has: target.locator('input[placeholder*="平台或店铺名称"], input[placeholder*="回车搜索"]'),
        }),
        target.locator('.ant-modal-content').filter({
          has: target.locator('input[placeholder*="平台或店铺名称"]'),
        }),
      ];

      for (const dialog of dialogs) {
        const count = await dialog.count().catch(() => 0);
        for (let index = 0; index < Math.min(count, 6); index += 1) {
          const item = dialog.nth(index);
          if (await item.isVisible().catch(() => false)) {
            return item;
          }
        }
      }
    }

    await page.waitForTimeout(250);
  }

  return null;
}

async function clickVisibleStoreTrigger(queryTarget: Target, candidate: string): Promise<boolean> {
  const locators = queryTarget.locator(candidate);
  const count = await locators.count().catch(() => 0);

  for (let index = 0; index < Math.min(count, 6); index += 1) {
    const locator = locators.nth(index);
    if (!(await locator.isVisible({ timeout: 800 }).catch(() => false))) {
      continue;
    }

    await locator.scrollIntoViewIfNeeded().catch(() => undefined);
    const clicked = await locator
      .click({ timeout: 2500 })
      .then(() => true)
      .catch(async () =>
        locator
          .click({ timeout: 2500, force: true })
          .then(() => true)
          .catch(() => false),
      );
    if (clicked) {
      return true;
    }
  }

  return false;
}

export async function collectStorePickerDiagnostics(page: Page, queryTarget: Target): Promise<Record<string, unknown>> {
  const triggerCandidates = [];
  for (const selector of selectors.productPage.platformTrigger) {
    const locators = queryTarget.locator(selector);
    const count = await locators.count().catch(() => 0);
    const elements = [];
    for (let index = 0; index < Math.min(count, 6); index += 1) {
      const locator = locators.nth(index);
      const visible = await locator.isVisible().catch(() => false);
      const metadata = await locator
        .evaluate((element) => {
          const htmlElement = element as HTMLInputElement;
          return {
            tag: element.tagName.toLowerCase(),
            id: element.id,
            class_name: element.getAttribute("class") ?? "",
            placeholder: element.getAttribute("placeholder") ?? "",
            aria_expanded: element.getAttribute("aria-expanded") ?? "",
            disabled: Boolean(htmlElement.disabled),
            readonly: Boolean(htmlElement.readOnly),
          };
        })
        .catch(() => null);
      elements.push({ index, visible, metadata });
    }
    triggerCandidates.push({ selector, count, elements });
  }

  const targets = [];
  for (const [index, target] of allTargets(page).entries()) {
    const visibleDialogs = [];
    const dialogs = target.locator("[role='dialog'], .ant-modal-wrap, .ant-modal, .ant-drawer");
    const dialogCount = await dialogs.count().catch(() => 0);
    for (let dialogIndex = 0; dialogIndex < Math.min(dialogCount, 10); dialogIndex += 1) {
      const dialog = dialogs.nth(dialogIndex);
      if (!(await dialog.isVisible().catch(() => false))) {
        continue;
      }
      visibleDialogs.push({
        index: dialogIndex,
        text: (await dialog.innerText().catch(() => "")).replace(/\s+/g, " ").trim().slice(0, 1200),
      });
    }

    const visibleOverlays = [];
    for (const selector of [
      ".ant-modal-mask",
      ".ant-drawer-mask",
      ".ant-popover",
      ".ant-tooltip",
      ".ant-tour",
      "[class*='guide']",
    ]) {
      const overlays = target.locator(selector);
      const overlayCount = await overlays.count().catch(() => 0);
      let visibleCount = 0;
      for (let overlayIndex = 0; overlayIndex < Math.min(overlayCount, 10); overlayIndex += 1) {
        if (await overlays.nth(overlayIndex).isVisible().catch(() => false)) {
          visibleCount += 1;
        }
      }
      if (overlayCount > 0) {
        visibleOverlays.push({ selector, count: overlayCount, visible_count: visibleCount });
      }
    }

    targets.push({
      index,
      url: "url" in target && typeof target.url === "function" ? target.url() : page.url(),
      visible_dialogs: visibleDialogs,
      overlays: visibleOverlays,
    });
  }

  return {
    page_url: page.url(),
    page_title: await page.title().catch(() => ""),
    login_input_visible: await hasVisible(page, selectors.login.username, 300),
    trigger_candidates: triggerCandidates,
    targets,
  };
}

async function getStoreSearchInputInPopup(page: Page, timeoutMs = 10000): Promise<{ dialog: Locator; input: Locator } | null> {
  const startedAt = Date.now();

  while (Date.now() - startedAt < timeoutMs) {
    const dialog = await getStoreDialog(page, 1200);
    if (!dialog) {
      await page.waitForTimeout(250);
      continue;
    }

    const candidates = [
      dialog.locator('input[placeholder="请输入平台或店铺名称，回车搜索"]'),
      dialog.locator('input[placeholder*="平台或店铺名称"]'),
      dialog.locator('input[placeholder*="回车搜索"]'),
    ];

    for (const candidate of candidates) {
      const count = await candidate.count().catch(() => 0);
      for (let index = 0; index < Math.min(count, 6); index += 1) {
        const input = candidate.nth(index);
        if (await input.isVisible().catch(() => false)) {
          return { dialog, input };
        }
      }
    }

    await page.waitForTimeout(250);
  }

  return null;
}

async function waitStoreDialogSearchLoaded(dialog: Locator, timeoutMs = 12000): Promise<boolean> {
  const startedAt = Date.now();
  let noSpinnerSince = 0;

  while (Date.now() - startedAt < timeoutMs) {
    const spinner = dialog.locator(".ant-spin-spinning, [class*='loading']");
    const spinnerCount = await spinner.count().catch(() => 0);
    let hasVisibleSpinner = false;

    for (let index = 0; index < Math.min(spinnerCount, 8); index += 1) {
      if (await spinner.nth(index).isVisible().catch(() => false)) {
        hasVisibleSpinner = true;
        break;
      }
    }

    if (!hasVisibleSpinner) {
      if (!noSpinnerSince) {
        noSpinnerSince = Date.now();
      }

      const rowHints = [dialog.getByText("抖音"), dialog.locator(".ant-checkbox")];
      for (const hint of rowHints) {
        const count = await hint.count().catch(() => 0);
        for (let index = 0; index < Math.min(count, 10); index += 1) {
          if (await hint.nth(index).isVisible().catch(() => false)) {
            return true;
          }
        }
      }

      if (Date.now() - noSpinnerSince >= 1200) {
        return true;
      }
    } else {
      noSpinnerSince = 0;
    }

    await dialog.page().waitForTimeout(220);
  }

  return false;
}

async function clickSelectAllInStoreDialog(dialog: Locator, timeoutMs = 12000): Promise<boolean> {
  const startedAt = Date.now();

  while (Date.now() - startedAt < timeoutMs) {
    const checked = dialog.locator("input[type='checkbox']:checked, .ant-checkbox-checked");
    const checkedCount = await checked.count().catch(() => 0);
    if (checkedCount > 1) {
      return true;
    }

    const allCandidates = [
      dialog.locator("label").filter({ hasText: /^全选$/ }),
      dialog.locator("span").filter({ hasText: /^全选$/ }),
      dialog.getByText("全选", { exact: true }),
      dialog.locator("text=/^\\s*全选\\s*$/"),
    ];

    for (const candidate of allCandidates) {
      const count = await candidate.count().catch(() => 0);
      for (let index = 0; index < Math.min(count, 8); index += 1) {
        const node = candidate.nth(index);
        if (!(await node.isVisible().catch(() => false))) {
          continue;
        }

        const row = node.locator(
          'xpath=ancestor-or-self::*[.//input[@type="checkbox"] or .//*[contains(@class,"ant-checkbox")]][1]',
        );
        const rowNode = (await row.count().catch(() => 0)) > 0 ? row.first() : node;
        const clickTargets = [
          rowNode.locator(".ant-checkbox-inner"),
          rowNode.locator(".ant-checkbox"),
          rowNode.locator("input[type='checkbox']"),
        ];

        for (const target of clickTargets) {
          const targetCount = await target.count().catch(() => 0);
          for (let targetIndex = 0; targetIndex < Math.min(targetCount, 3); targetIndex += 1) {
            const item = target.nth(targetIndex);
            if (!(await item.isVisible().catch(() => false))) {
              continue;
            }

            await item.click({ timeout: 2500 }).catch(async () => {
              await item.click({ timeout: 2500, force: true }).catch(() => undefined);
            });
            await dialog.page().waitForTimeout(400);

            const rechecked = await rowNode
              .locator("input[type='checkbox']:checked, .ant-checkbox-checked")
              .count()
              .catch(() => 0);
            if (rechecked > 0) {
              return true;
            }
          }
        }
      }
    }

    await dialog.page().waitForTimeout(220);
  }

  return false;
}

async function clickConfirmInStorePopup(page: Page, timeoutMs = 10000): Promise<boolean> {
  const startedAt = Date.now();
  let sawDialog = false;

  while (Date.now() - startedAt < timeoutMs) {
    const dialog = await getStoreDialog(page, 1200);
    if (!dialog) {
      if (sawDialog) {
        return true;
      }
      await page.waitForTimeout(250);
      continue;
    }

    sawDialog = true;
    const candidates = [
      dialog.locator(".ant-modal-footer .ant-btn-primary"),
      dialog.locator(".ant-modal-footer button:last-child"),
      dialog.getByRole("button", { name: /确定/ }),
      dialog.locator("button").filter({ hasText: /确定/ }),
      dialog.getByText(/确定/),
    ];

    for (const candidate of candidates) {
      const count = await candidate.count().catch(() => 0);
      for (let index = 0; index < Math.min(count, 8); index += 1) {
        const button = candidate.nth(index);
        if (!(await button.isVisible().catch(() => false))) {
          continue;
        }

        await button.scrollIntoViewIfNeeded().catch(() => undefined);
        await button.click({ timeout: 3000 }).catch(async () => {
          await button.click({ timeout: 3000, force: true }).catch(() => undefined);
        });
        await page.waitForTimeout(400);

        const stillOpen = await getStoreDialog(page, 500);
        if (!stillOpen) {
          return true;
        }
      }
    }

    await page.waitForTimeout(250);
  }

  return false;
}

function compactText(value: string): string {
  return value.replace(/\s+/g, "").trim();
}

async function clickExactStoreRowCheckbox(modal: Locator, storeName: string): Promise<boolean> {
  const normalizedStore = compactText(storeName);
  const storeSuffix = compactText(storeName.replace(/^阿里巴巴[-—–]?/, ""));
  const rows = modal.locator(
    "tbody tr, .ant-table-tbody > tr, .ant-tree-treenode, [role='row'], .ant-list-item, label.ant-checkbox-wrapper",
  );
  const matches: Array<{ row: Locator; text: string }> = [];
  const count = await rows.count().catch(() => 0);

  for (let index = 0; index < count; index += 1) {
    const row = rows.nth(index);
    const text = compactText(await row.innerText().catch(() => ""));
    if (!text || (!text.includes(normalizedStore) && !text.includes(storeSuffix))) {
      continue;
    }
    const checkboxCount = await row
      .locator("input[type='checkbox'], label.ant-checkbox-wrapper, .ant-checkbox-wrapper")
      .count()
      .catch(() => 0);
    if (checkboxCount > 0) {
      matches.push({ row, text });
    }
  }

  if (matches.length === 0) {
    return false;
  }

  matches.sort((left, right) => left.text.length - right.text.length);
  const row = matches[0].row;
  const input = row.locator("input[type='checkbox']").first();
  if ((await input.count().catch(() => 0)) > 0) {
    if (!(await input.isChecked().catch(() => false))) {
      await input.check({ force: true }).catch(() => undefined);
    }
    if (await input.isChecked().catch(() => false)) {
      return true;
    }
  }

  const label = row.locator("label.ant-checkbox-wrapper, .ant-checkbox-wrapper").first();
  if ((await label.count().catch(() => 0)) > 0) {
    await label.click({ force: true }).catch(() => undefined);
    return (
      (await input.isChecked().catch(() => false)) ||
      (await row.locator(".ant-checkbox-checked").count().catch(() => 0)) > 0
    );
  }

  return false;
}

async function clearStoreDialogSelection(modal: Locator): Promise<void> {
  for (let attempt = 0; attempt < 5; attempt += 1) {
    const footerText = await modal
      .locator(selectors.productPage.platformModalFooter.join(","))
      .innerText()
      .catch(() => "");
    const counts = parseSelectionCounts(footerText);
    if (counts.platformCount === 0 && counts.shopCount === 0) {
      return;
    }

    const checkedInputs = modal.locator("label.ant-checkbox-wrapper input[type='checkbox']:checked");
    const checkedCount = await checkedInputs.count().catch(() => 0);
    for (let index = checkedCount - 1; index >= 0; index -= 1) {
      const input = checkedInputs.nth(index);
      await input.uncheck({ force: true }).catch(async () => {
        await input.locator("xpath=ancestor::label[1]").click({ force: true }).catch(() => undefined);
      });
      await modal.page().waitForTimeout(150);
    }
    await modal.page().waitForTimeout(350);
  }

  const footerText = await modal
    .locator(selectors.productPage.platformModalFooter.join(","))
    .innerText()
    .catch(() => "");
  throw new Error(`Could not clear existing Jushuitan store selection. Footer: ${footerText}`);
}

export async function selectExactStore(
  page: Page,
  queryTarget: Target,
  storeName: string,
): Promise<void> {
  await clearPlatformSelection(queryTarget);
  const opened = await openStorePickerWithFallback({
    candidates: selectors.productPage.platformTrigger,
    getOpenPicker: () => getStoreDialog(page, 1800),
    clickCandidate: async (candidate) => {
      const clicked = await clickVisibleStoreTrigger(queryTarget, candidate);
      if (clicked) {
        await page.waitForTimeout(350);
      }
      return clicked;
    },
  });
  if (!opened) {
    throw new StoreSelectionError(
      "store_picker_unavailable",
      `Store picker dialog did not open for ${storeName}`,
      { attempted_selectors: [...selectors.productPage.platformTrigger] },
    );
  }

  const storeContext = await getStoreSearchInputInPopup(page, 5000);
  if (!storeContext) {
    throw new StoreSelectionError(
      "store_picker_unavailable",
      `Store picker opened without a usable search input for ${storeName}`,
      { attempted_selectors: opened.attemptedSelectors },
    );
  }

  const { dialog, input } = storeContext;
  await clearStoreDialogSelection(dialog);
  const suffix = storeName.replace(/^阿里巴巴[-—–]?/, "").trim();
  const searchTerms = [...new Set([storeName.trim(), suffix].filter(Boolean))];
  let selected = false;

  for (const term of searchTerms) {
    const resetButton = dialog.locator(selectors.productPage.platformModalResetButton.join(",")).first();
    if ((await resetButton.count().catch(() => 0)) > 0) {
      await resetButton.click({ force: true }).catch(() => undefined);
      await page.waitForTimeout(300);
    }
    await input.fill("").catch(() => undefined);
    await input.fill(term).catch(async () => input.type(term, { delay: 40 }));
    const searchButton = dialog.locator(selectors.productPage.platformModalSearchButton.join(",")).first();
    if ((await searchButton.count().catch(() => 0)) > 0) {
      await searchButton.click({ force: true }).catch(() => undefined);
    } else {
      await input.press("Enter").catch(() => undefined);
    }
    await waitStoreDialogSearchLoaded(dialog, 12000);
    selected = await clickExactStoreRowCheckbox(dialog, storeName);
    if (selected) {
      break;
    }
  }

  if (!selected) {
    throw new StoreSelectionError("store_mismatch", `Exact Jushuitan store was not found or selected: ${storeName}`);
  }

  const footerText = await dialog
    .locator(selectors.productPage.platformModalFooter.join(","))
    .innerText()
    .catch(() => "");
  const counts = parseSelectionCounts(footerText);
  if (counts.platformCount !== 1 || counts.shopCount !== 1) {
    throw new StoreSelectionError(
      "store_mismatch",
      `Exact store selection verification failed for ${storeName}. Footer: ${footerText}`,
    );
  }

  if (!(await clickConfirmInStorePopup(page, 10000))) {
    throw new StoreSelectionError("store_picker_unavailable", `Store picker confirmation failed for ${storeName}`);
  }

  const trigger = await resolveFirstVisibleLocator(queryTarget, selectors.productPage.platformTrigger, 5000);
  const triggerValue = compactText(await trigger.inputValue().catch(() => ""));
  if (!triggerValue.includes("已选1个平台1个店铺")) {
    throw new StoreSelectionError(
      "store_mismatch",
      `Jushuitan store filter is not exact after confirmation: ${triggerValue}`,
    );
  }
}

async function selectPlatform(page: Page, queryTarget: Target, platform: PlatformKey): Promise<boolean> {
  await clearPlatformSelection(queryTarget);
  let storeDialog = await getStoreDialog(page, 800);
  if (!storeDialog) {
    for (const candidate of selectors.productPage.platformTrigger) {
      try {
        const locator = queryTarget.locator(candidate).first();
        if (!(await locator.isVisible({ timeout: 800 }).catch(() => false))) {
          continue;
        }
        await locator.click({ force: true }).catch(() => undefined);
        await page.waitForTimeout(500);
        storeDialog = await getStoreDialog(page, 1200);
        if (storeDialog) {
          break;
        }
      } catch {
        continue;
      }
    }
  }

  const platformLabel = platformLabels[platform];
  const storeContext = await getStoreSearchInputInPopup(page, 10000);
  if (!storeContext) {
    throw new Error(`Platform picker dialog did not open for ${platformLabel}`);
  }

  const { dialog: modal, input: searchInput } = storeContext;
  const resetButton = modal.locator(selectors.productPage.platformModalResetButton.join(",")).first();
  if ((await resetButton.count()) > 0 && (await resetButton.isVisible().catch(() => false))) {
    await resetButton.click({ force: true }).catch(() => undefined);
    await page.waitForTimeout(500);
  }

  await searchInput.click({ timeout: 2500 }).catch(() => undefined);
  await searchInput.fill("").catch(() => undefined);
  await searchInput.fill(platformLabel).catch(async () => {
    await searchInput.type(platformLabel, { delay: 50 }).catch(() => undefined);
  });

  const searchButton = modal.locator(selectors.productPage.platformModalSearchButton.join(",")).first();
  if ((await searchButton.count()) > 0) {
    await searchButton.click({ force: true }).catch(() => undefined);
  } else {
    await searchInput.press("Enter").catch(() => undefined);
  }

  await waitStoreDialogSearchLoaded(modal, 12000);

  const emptyStateVisible = await modal.getByText("暂无数据", { exact: true }).isVisible().catch(() => false);
  if (emptyStateVisible) {
    await modal.getByRole("button", { name: "取消" }).click({ force: true }).catch(() => undefined);
    await page.waitForTimeout(300);
    return false;
  }

  const rowSelected = await clickPlatformRowCheckbox(modal, platformLabel);
  const footerSelected = await clickSelectAllInStoreDialog(modal, 12000);
  if (!rowSelected && !footerSelected) {
    const modalHtml = await modal.evaluate((element) => element.outerHTML).catch(() => "");
    if (modalHtml) {
      const debugPath = path.join(appConfig.artifactsDir, `platform-modal-${platform}.html`);
      await ensureDir(path.dirname(debugPath));
      await fs.writeFile(debugPath, modalHtml, "utf8").catch(() => undefined);
    }
    throw new Error(`Platform ${platformLabel} was not selected in the shop picker`);
  }

  const footerText = await modal.locator(selectors.productPage.platformModalFooter.join(",")).innerText().catch(() => "");
  const { platformCount: selectedPlatformCount, shopCount: selectedShopCount } = parseSelectionCounts(footerText);

  if (selectedPlatformCount < 1 || selectedShopCount <= 1) {
    const modalHtml = await modal.evaluate((element) => element.outerHTML).catch(() => "");
    if (modalHtml) {
      const debugPath = path.join(appConfig.artifactsDir, `platform-modal-${platform}.html`);
      await ensureDir(path.dirname(debugPath));
      await fs.writeFile(debugPath, modalHtml, "utf8").catch(() => undefined);
    }
    throw new Error(`Platform ${platformLabel} selection verification failed. Footer: ${footerText}`);
  }

  const confirmed = await clickConfirmInStorePopup(page, 10000);
  if (!confirmed) {
    throw new Error(`Platform ${platformLabel} dialog confirm failed`);
  }

  if (!(await ensurePlatformSelectedInTrigger(queryTarget))) {
    throw new Error(`Platform ${platformLabel} did not appear in the query filter after confirmation`);
  }

  return true;
}

async function hasNoSearchResults(target: Target): Promise<boolean> {
  if (
    await hasVisible(
      target,
      ["text=暂无数据", "text=无数据", "text=暂无店铺商品", ".ant-empty", ".art-empty-wrapper", ".empty-tips"],
      800,
    )
  ) {
    return true;
  }

  try {
    const selectableRowCount = await target
      .locator(
        ".art-table-body tbody tr:has(td:first-child input.ant-checkbox-input), .ant-table-tbody > tr:has(td:first-child input.ant-checkbox-input), tbody tr:has(td:first-child input[type='checkbox'])",
      )
      .count()
      .catch(() => 0);
    const placeholderRowCount = await target
      .locator(".art-table-body tbody tr.no-hover td[colspan], .ant-table-tbody > tr td[colspan]")
      .count()
      .catch(() => 0);
    if (selectableRowCount === 0 && placeholderRowCount > 0) {
      return true;
    }

    const text = await target.locator("body").innerText();
    return (
      text.includes("共0条") ||
      text.includes("暂无数据") ||
      text.includes("无数据") ||
      text.includes("暂无店铺商品")
    );
  } catch {
    return false;
  }
}

async function setPageSizeTo500(page: Page): Promise<void> {
  const triggerCandidates = [
    page.getByText(/^\d+条\/页$/),
    page.locator(".ant-pagination-options .ant-select-selector"),
    page.locator(".ant-pagination-options .ant-select-selection-item"),
  ];

  for (const candidate of triggerCandidates) {
    const count = await candidate.count().catch(() => 0);
    for (let index = 0; index < Math.min(count, 6); index += 1) {
      const trigger = candidate.nth(index);
      if (!(await trigger.isVisible().catch(() => false))) {
        continue;
      }

      const currentText = ((await trigger.innerText().catch(() => "")) || "").replace(/\s+/g, "");
      if (currentText.includes("500条/页")) {
        return;
      }

      await trigger.click({ force: true }).catch(() => undefined);
      await page.waitForTimeout(500);

      const optionCandidates = [
        page.getByText(/^500条\/页$/, { exact: true }),
        page.locator('.ant-select-item-option[title="500条/页"]'),
        page.locator('.ant-select-item-option').filter({ hasText: /^500条\/页$/ }),
      ];

      for (const optionCandidate of optionCandidates) {
        const optionCount = await optionCandidate.count().catch(() => 0);
        for (let optionIndex = 0; optionIndex < Math.min(optionCount, 6); optionIndex += 1) {
          const option = optionCandidate.nth(optionIndex);
          if (!(await option.isVisible().catch(() => false))) {
            continue;
          }

          await option.click({ force: true }).catch(() => undefined);
          await page.waitForTimeout(1200);
          return;
        }
      }
    }
  }
}

async function getSelectableResultsTable(target: Target): Promise<Locator | null> {
  const rootSelectors = [".art-table", ".ant-table-wrapper", ".ant-table"];

  for (const selector of rootSelectors) {
    const roots = target.locator(selector);
    const count = await roots.count().catch(() => 0);

    for (let index = 0; index < Math.min(count, 6); index += 1) {
      const root = roots.nth(index);
      if (!(await root.isVisible().catch(() => false))) {
        continue;
      }

      const rowCount = await root
        .locator(
          ".art-table-body tbody tr:has(td:first-child input.ant-checkbox-input), .ant-table-tbody > tr:has(td:first-child input.ant-checkbox-input), tbody tr:has(td:first-child input[type='checkbox'])",
        )
        .count()
        .catch(() => 0);
      if (rowCount === 0) {
        continue;
      }

      const headerCheckboxCount = await root
        .locator(
          ".art-table-header thead th:first-child input.ant-checkbox-input, .ant-table-thead th:first-child input.ant-checkbox-input, thead th:first-child input[type='checkbox']",
        )
        .count()
        .catch(() => 0);
      if (headerCheckboxCount === 0) {
        continue;
      }

      return root;
    }
  }

  return null;
}

async function hasSelectionEvidence(tableRoot: Locator): Promise<boolean> {
  const headerChecked = await tableRoot
    .locator(
      ".art-table-header thead th:first-child .ant-checkbox-checked, .ant-table-thead th:first-child .ant-checkbox-checked, thead th:first-child .ant-checkbox-checked",
    )
    .count()
    .catch(() => 0);
  if (headerChecked > 0) {
    return true;
  }

  const checkedInputs = await tableRoot
    .locator(
      ".art-table-body tbody input.ant-checkbox-input:checked, .art-table-body tbody .ant-checkbox-checked, .ant-table-tbody input.ant-checkbox-input:checked, .ant-table-tbody .ant-checkbox-checked, tbody input[type='checkbox']:checked",
    )
    .count()
    .catch(() => 0);
  return checkedInputs > 0;
}

async function waitForSelectionEvidence(tableRoot: Locator, page: Page, timeoutMs = 1500): Promise<boolean> {
  const startedAt = Date.now();

  while (Date.now() - startedAt < timeoutMs) {
    if (await hasSelectionEvidence(tableRoot)) {
      return true;
    }
    await page.waitForTimeout(120);
  }

  return hasSelectionEvidence(tableRoot);
}

async function activateLocator(locator: Locator): Promise<void> {
  if ((await locator.count().catch(() => 0)) === 0) {
    return;
  }
  if (!(await locator.isVisible().catch(() => false))) {
    return;
  }

  await locator.scrollIntoViewIfNeeded().catch(() => undefined);
  await locator.click({ timeout: 2000 }).catch(async () => {
    await locator.click({ timeout: 2000, force: true }).catch(async () => {
      await locator
        .evaluate((element: Element) => {
          if (element instanceof HTMLElement) {
            element.click();
          }
        })
        .catch(() => undefined);
    });
  });
}

async function activateCheckboxInCell(cell: Locator): Promise<void> {
  const checkboxInput = cell.locator("input.ant-checkbox-input, input[type='checkbox']").first();
  if ((await checkboxInput.count().catch(() => 0)) > 0) {
    const checked = await checkboxInput.isChecked().catch(() => false);
    if (!checked) {
      await checkboxInput.check({ force: true }).catch(() => undefined);
      if (await checkboxInput.isChecked().catch(() => false)) {
        return;
      }
    } else {
      return;
    }
  }

  const clickCandidates = [
    cell.locator("label.ant-checkbox-wrapper").first(),
    cell.locator(".ant-checkbox-inner").first(),
    cell.locator(".ant-checkbox").first(),
    checkboxInput,
    cell,
  ];

  for (const candidate of clickCandidates) {
    await activateLocator(candidate);
    const checked = await checkboxInput.isChecked().catch(() => false);
    const checkedClassCount = await cell.locator(".ant-checkbox-checked").count().catch(() => 0);
    if (checked || checkedClassCount > 0) {
      return;
    }
  }
}

async function clickTableSelectAll(target: Target, page: Page, timeoutMs = 12000): Promise<boolean> {
  const startedAt = Date.now();

  while (Date.now() - startedAt < timeoutMs) {
    const tableRoot = await getSelectableResultsTable(target);
    if (!tableRoot) {
      await page.waitForTimeout(250);
      continue;
    }

    if (await hasSelectionEvidence(tableRoot)) {
      return true;
    }

    const headerCell = tableRoot
      .locator(".art-table-header thead th:first-child, .ant-table-thead th:first-child, thead th:first-child")
      .first();
    if ((await headerCell.count().catch(() => 0)) > 0) {
      const headerCandidates = [
        headerCell.locator("label.ant-checkbox-wrapper").first(),
        headerCell.locator(".ant-checkbox-inner").first(),
        headerCell.locator(".ant-checkbox").first(),
        headerCell.locator("input.ant-checkbox-input, input[type='checkbox']").first(),
        headerCell,
      ];

      for (const candidate of headerCandidates) {
        await activateLocator(candidate);
        if (await waitForSelectionEvidence(tableRoot, page)) {
          return true;
        }
      }
    }

    await page.waitForTimeout(200);
  }

  return false;
}

async function hasSelectGoodsWarning(page: Page): Promise<boolean> {
  const warnings = [
    page.getByText("请选择商品", { exact: true }),
    page.locator(".ant-message-notice-content").filter({ hasText: "请选择商品" }),
    page.locator("[role='alert']").filter({ hasText: "请选择商品" }),
  ];

  for (const warning of warnings) {
    const count = await warning.count().catch(() => 0);
    for (let index = 0; index < Math.min(count, 4); index += 1) {
      if (await warning.nth(index).isVisible().catch(() => false)) {
        return true;
      }
    }
  }

  return false;
}

async function selectRowsByFirstColumn(target: Target, page: Page, maxRows = 20): Promise<boolean> {
  const tableRoot = await getSelectableResultsTable(target);
  if (!tableRoot) {
    return false;
  }

  if (await hasSelectionEvidence(tableRoot)) {
    return true;
  }

  const rows = tableRoot.locator(".art-table-body tbody tr.art-table-row, .art-table-body tbody tr, .ant-table-tbody > tr, tbody tr");
  const rowCount = await rows.count().catch(() => 0);

  for (let rowIndex = 0; rowIndex < Math.min(rowCount, maxRows); rowIndex += 1) {
    const row = rows.nth(rowIndex);
    if (!(await row.isVisible().catch(() => false))) {
      continue;
    }

    const firstCell = row.locator("td").first();
    if ((await firstCell.count().catch(() => 0)) === 0) {
      continue;
    }

    await activateCheckboxInCell(firstCell);
    if (await waitForSelectionEvidence(tableRoot, page, 1000)) {
      return true;
    }
  }

  return false;
}

async function getBatchUpdateDialog(page: Page, timeoutMs = 10000): Promise<Locator | null> {
  const startedAt = Date.now();

  while (Date.now() - startedAt < timeoutMs) {
    for (const target of allTargets(page)) {
      const dialogs = [
        target.locator("[role='dialog']"),
        target.locator(".ant-modal"),
        target.locator(".ant-modal-content"),
        target.locator(".ant-drawer, .ant-drawer-content"),
      ];

      for (const dialogGroup of dialogs) {
        const count = await dialogGroup.count().catch(() => 0);
        for (let index = 0; index < Math.min(count, 6); index += 1) {
          const dialog = dialogGroup.nth(index);
          if (!(await dialog.isVisible().catch(() => false))) {
            continue;
          }

          const text = ((await dialog.innerText().catch(() => "")) || "").trim();
          if (!text || text.length < 2) {
            continue;
          }
          if (!/批量|商品编码|填充|修改/.test(text)) {
            continue;
          }
          if (/请选择平台.?店铺/.test(text)) {
            continue;
          }
          if (/修改成功|查看日志|正在批量更新中|处理中/.test(text)) {
            continue;
          }
          return dialog;
        }
      }
    }

    await page.waitForTimeout(200);
  }

  return null;
}

async function openUpdateModal(queryTarget: Target, page: Page): Promise<Locator> {
  for (let attempt = 0; attempt < 2; attempt += 1) {
    const button = await waitForVisibleTargetLocator(page, selectors.productPage.batchUpdateButton, 5000);
    await button.scrollIntoViewIfNeeded().catch(() => undefined);
    await button.click({ force: true }).catch(() => undefined);
    await button.evaluate((element: Element) => {
      if (element instanceof HTMLElement) {
        element.click();
      }
    }).catch(() => undefined);
    await page.waitForTimeout(800);

    const dialog = await getBatchUpdateDialog(page, 3000);
    if (dialog) {
      return dialog;
    }

    if (await hasSelectGoodsWarning(page)) {
      await clickTableSelectAll(queryTarget, page, 3000);
      await selectRowsByFirstColumn(queryTarget, page, 10);
      await page.waitForTimeout(500);
      continue;
    }
  }

  throw new Error(`Batch update dialog did not appear after clicking batch update button`);
}

async function submitUpdateModal(page: Page): Promise<void> {
  const modal = await getBatchUpdateDialog(page, 5000);
  if (!modal) {
    throw new Error(`Batch update dialog is not visible`);
  }

  const fillButtonCandidates = [
    modal.getByRole("button", { name: "批量填充" }),
    modal.locator("button").filter({ hasText: "批量填充" }),
    modal.getByText("批量填充", { exact: true }),
    modal.locator("label.ant-radio-wrapper").filter({ hasText: "批量填充" }),
  ];

  for (const candidate of fillButtonCandidates) {
    const count = await candidate.count().catch(() => 0);
    for (let index = 0; index < Math.min(count, 6); index += 1) {
      const button = candidate.nth(index);
      if (!(await button.isVisible().catch(() => false))) {
        continue;
      }
      await button.click({ timeout: 2500 }).catch(async () => {
        await button.click({ timeout: 2500, force: true }).catch(() => undefined);
      });
      await page.waitForTimeout(350);
      index = 99;
      break;
    }
  }

  const inputCandidates = [
    modal.locator('input[placeholder*="请输入批量填充商品编码"]'),
    modal.locator('input[placeholder*="批量填充商品编码"]'),
    modal.locator("input#title_name8"),
    modal.locator("input.ant-input"),
  ];

  let filled = false;
  for (const candidate of inputCandidates) {
    const count = await candidate.count().catch(() => 0);
    for (let index = 0; index < Math.min(count, 8); index += 1) {
      const input = candidate.nth(index);
      if (!(await input.isVisible().catch(() => false))) {
        continue;
      }
      await input.click({ timeout: 2500 }).catch(() => undefined);
      await input.fill("").catch(() => undefined);
      await input.fill(appConfig.replacementOnlineSku).catch(async () => {
        await input.type(appConfig.replacementOnlineSku, { delay: 30 }).catch(() => undefined);
      });
      filled = true;
      break;
    }
    if (filled) {
      break;
    }
  }

  if (!filled) {
    throw new Error(`Could not find replacement code input inside batch update dialog`);
  }

  const confirmCandidates = [
    modal.locator(".ant-modal-footer .ant-btn-primary"),
    modal.getByRole("button", { name: "确定" }),
    modal.locator("button").filter({ hasText: "确定" }),
  ];

  for (const candidate of confirmCandidates) {
    const count = await candidate.count().catch(() => 0);
    for (let index = 0; index < Math.min(count, 8); index += 1) {
      const button = candidate.nth(index);
      if (!(await button.isVisible().catch(() => false))) {
        continue;
      }
      await button.click({ timeout: 3000 }).catch(async () => {
        await button.click({ timeout: 3000, force: true }).catch(() => undefined);
      });
      await page.waitForTimeout(500);
      const stillOpen = await getBatchUpdateDialog(page, 800);
      if (!stillOpen) {
        return;
      }
    }
  }

  throw new Error(`Could not confirm batch update dialog`);
}

async function collectRemainingRows(target: Target, platform: PlatformKey): Promise<RemainingRow[]> {
  for (const candidate of selectors.productPage.tableRows) {
    const rows = target.locator(candidate);
    const count = await rows.count().catch(() => 0);
    if (count === 0) {
      continue;
    }

    const result: RemainingRow[] = [];
    for (let index = 0; index < count; index += 1) {
      const row = rows.nth(index);
      const text = (await row.innerText().catch(() => "")).trim();
      const colSpan = await row.locator("td[colspan]").count().catch(() => 0);
      if (
        !text ||
        text.includes("暂无数据") ||
        text.includes("无数据") ||
        text.includes("暂无店铺商品") ||
        colSpan > 0
      ) {
        continue;
      }
      result.push({ platform, rawText: text });
    }
    return result;
  }

  return [];
}

async function runPlatformBatch(
  page: Page,
  runId: string,
  platform: PlatformKey,
  productCodes: string[],
): Promise<PlatformRunResult> {
  const updatePopups: UpdatePopupRecord[] = [];
  const batches = chunkArray(productCodes, appConfig.batchSize);
  let platformPrepared = false;

  for (let index = 0; index < batches.length; index += 1) {
    const batch = batches[index];
    const queryTarget = await ensureProductPage(page);

    await fillByCandidates(queryTarget, selectors.productPage.multiSkuInput, batch.join(","), true);
    const platformSelected =
      platformPrepared && (await ensurePlatformSelectedInTrigger(queryTarget))
        ? true
        : await selectPlatform(page, queryTarget, platform);
    if (!platformSelected) {
      updatePopups.push({
        platform,
        batchIndex: index + 1,
        productCodes: batch,
        message: "No shops configured for this platform under the current account",
        capturedAt: new Date().toISOString(),
      });
      continue;
    }
    platformPrepared = true;
    await clickByCandidates(queryTarget, selectors.productPage.queryButton);
    await page.waitForTimeout(appConfig.searchWaitMs);
    await dismissQuickSaveModal(page);

    if (await hasNoSearchResults(queryTarget)) {
      updatePopups.push({
        platform,
        batchIndex: index + 1,
        productCodes: batch,
        message: "No matched rows found for this batch",
        capturedAt: new Date().toISOString(),
      });
      await saveDebugScreenshot(page, runId, `${platform}-batch-${index + 1}-no-results`);
      continue;
    }

    await saveTargetHtml(queryTarget, runId, `${platform}-batch-${index + 1}-results`);
    await saveTargetTableDiagnostics(queryTarget, runId, `${platform}-batch-${index + 1}-results`);
    await setPageSizeTo500(page);
    await saveTargetHtml(queryTarget, runId, `${platform}-batch-${index + 1}-results-500`);
    await saveTargetTableDiagnostics(queryTarget, runId, `${platform}-batch-${index + 1}-results-500`);
    const selected = (await clickTableSelectAll(queryTarget, page)) || (await selectRowsByFirstColumn(queryTarget, page, 10));
    await saveTargetHtml(queryTarget, runId, `${platform}-batch-${index + 1}-after-select`);
    await saveTargetTableDiagnostics(queryTarget, runId, `${platform}-batch-${index + 1}-after-select`);
    if (!selected) {
      throw new Error(`未能选中查询结果中的商品，无法执行批量更新`);
    }

    await openUpdateModal(queryTarget, page);
    await submitUpdateModal(page);

    const popupText = (await waitForAnyLocatorText(page, selectors.productPage.popupMessage)) || "No popup message captured";
    updatePopups.push({
      platform,
      batchIndex: index + 1,
      productCodes: batch,
      message: popupText,
      capturedAt: new Date().toISOString(),
    });

    await saveDebugScreenshot(page, runId, `${platform}-batch-${index + 1}`);
  }

  const queryTarget = await ensureProductPage(page);
  await fillByCandidates(queryTarget, selectors.productPage.multiSkuInput, productCodes.join(","), true);
  const platformSelected =
    platformPrepared && (await ensurePlatformSelectedInTrigger(queryTarget))
      ? true
      : await selectPlatform(page, queryTarget, platform);
  if (!platformSelected) {
    return {
      platform,
      totalRequested: productCodes.length,
      updatePopups,
      remainingRows: [],
    };
  }
  await clickByCandidates(queryTarget, selectors.productPage.queryButton);
  await page.waitForTimeout(appConfig.searchWaitMs);
  await dismissQuickSaveModal(page);
  if (!(await hasNoSearchResults(queryTarget))) {
    await setPageSizeTo500(page);
  }

  return {
    platform,
    totalRequested: productCodes.length,
    updatePopups,
    remainingRows: await collectRemainingRows(queryTarget, platform),
  };
}

export async function runJushuitanFlow(runId: string, sourceRows: SourceRow[]) {
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

  await ensureDir(path.dirname(appConfig.storageStatePath));

  const context = await browser.newContext(
    storageStatePath
      ? {
          storageState: storageStatePath,
        }
      : undefined,
  );
  const page = await context.newPage();
  let keepOpenForInspection = false;

  try {
    if (storageStatePath) {
      await page.goto(appConfig.productUrl, { waitUntil: "domcontentloaded", timeout: 120000 });
      if (await hasVisible(page, selectors.login.username, 800)) {
        await login(page);
      }
    } else {
      await login(page);
    }

    if (appConfig.saveStorageState) {
      await context.storageState({ path: appConfig.storageStatePath });
    }

    const uniqueProductCodes = [...new Set(sourceRows.map((row) => row.productCode))];
    const productCodes = appConfig.maxProductCodes
      ? uniqueProductCodes.slice(0, appConfig.maxProductCodes)
      : uniqueProductCodes;
    const results: PlatformRunResult[] = [];

    for (const platform of appConfig.targetPlatforms) {
      const result = await runPlatformBatch(page, runId, platform, productCodes);
      results.push(result);
    }

    return results;
  } catch (error) {
    await saveDebugScreenshot(page, runId, "fatal");
    if (appConfig.keepBrowserOpenOnError) {
      keepOpenForInspection = true;
      console.error("Browser left open for inspection. Use F12 on the failed page.");
      await new Promise(() => undefined);
    }
    throw error;
  } finally {
    if (!keepOpenForInspection) {
      await context.close();
      await browser.close();
    }
  }
}
