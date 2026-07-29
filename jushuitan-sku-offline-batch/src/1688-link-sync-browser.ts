import fs from "node:fs/promises";
import path from "node:path";
import {chromium, Locator, Page} from "playwright";
import {appConfig} from "./config";
import {
  appendSyncLedger,
  groupSyncTasksByStore,
  StoreSyncGroup,
  SyncResult,
  SyncTask,
} from "./1688-link-sync-core";
import {
  dismissQuickSaveModal,
  dismissVisibleGuides,
  dismissVisibleModals,
  ensureProductPage,
  login,
  Target,
} from "./jushuitan";
import {selectors} from "./selectors";
import {ensureDir, sanitizeFileName} from "./utils";

class LinkSyncBrowserError extends Error {
  constructor(readonly category: string, message: string) {
    super(message);
    this.name = "LinkSyncBrowserError";
  }
}

interface LinkSyncRunOptions {
  runId: string;
  ledgerPath: string;
  artifactsDir: string;
}

const manualSyncButtons = [
  'button:has-text("手动同步商品")',
  'button:has-text("手工同步商品")',
  'text=手动同步商品',
] as const;
const syncModals = [
  '.ant-modal:has-text("手工同步商品")',
  '.ant-modal:has-text("手动同步商品")',
] as const;

function now(): string {
  return new Date().toISOString();
}

async function firstVisible(target: Target, candidates: readonly string[], timeoutMs = 8000): Promise<Locator> {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() <= deadline) {
    for (const candidate of candidates) {
      const locator = target.locator(candidate).first();
      if (await locator.isVisible({timeout: 300}).catch(() => false)) return locator;
    }
    await new Promise((resolve) => setTimeout(resolve, 150));
  }
  throw new LinkSyncBrowserError("action_unavailable", `No visible locator matched: ${candidates.join(" | ")}`);
}

export async function activateWithFallback(
  isActive: () => Promise<boolean>,
  actions: Array<() => Promise<void>>,
  options: {pollAttempts?: number; pollDelayMs?: number} = {},
): Promise<boolean> {
  if (await isActive()) return true;
  const pollAttempts = options.pollAttempts ?? 10;
  const pollDelayMs = options.pollDelayMs ?? 150;
  for (const action of actions) {
    try {
      await action();
    } catch {
      // Try the next activation strategy.
    }
    for (let attempt = 0; attempt < pollAttempts; attempt += 1) {
      if (await isActive()) return true;
      if (pollDelayMs > 0) {
        await new Promise((resolve) => setTimeout(resolve, pollDelayMs));
      }
    }
  }
  return false;
}

async function assertSessionSafe(page: Page): Promise<void> {
  const text = await page.locator("body").innerText().catch(() => "");
  if (/向右滑动验证|滑块验证|验证码|安全验证|账号存在风险|操作过于频繁/.test(text)) {
    throw new LinkSyncBrowserError("risk_control", "Jushuitan risk control or captcha is present");
  }
  if (/login/i.test(page.url()) || await page.locator(selectors.login.username.join(",")).first().isVisible({timeout: 500}).catch(() => false)) {
    throw new LinkSyncBrowserError("login_required", "Jushuitan login session is invalid");
  }
}

async function openSyncModal(page: Page, target: Target): Promise<Locator> {
  await dismissQuickSaveModal(page);
  await dismissVisibleGuides(page);
  await dismissVisibleModals(page);
  const button = await firstVisible(target, manualSyncButtons);
  await button.click({force: true});
  const modal = await firstVisible(target, syncModals, 10000);
  const linkTab = await firstVisible(modal, [
    '[role="tab"]:has-text("按链接同步")',
    '.ant-tabs-tab:has-text("按链接同步")',
    '.ant-tabs-tab-btn:has-text("按链接同步")',
    'text=按链接同步',
  ]);
  const tabContainer = linkTab.locator(
    'xpath=ancestor::*[contains(concat(" ", normalize-space(@class), " "), " ant-tabs-tab ")][1]',
  );
  const hasTabContainer = (await tabContainer.count()) > 0;
  const isLinkTabActive = async () => {
    for (const selector of [
      '[role="tab"][aria-selected="true"]:has-text("按链接同步")',
      '.ant-tabs-tab-active [role="tab"]:has-text("按链接同步")',
      '.ant-tabs-tab-active:has-text("按链接同步")',
    ]) {
      if (await modal.locator(selector).first().isVisible({timeout: 200}).catch(() => false)) {
        return true;
      }
    }
    return false;
  };
  await linkTab.scrollIntoViewIfNeeded().catch(() => undefined);
  const actions: Array<() => Promise<void>> = [
    async () => linkTab.click({timeout: 3000}),
    async () => linkTab.evaluate((element) => (element as HTMLElement).click()),
  ];
  if (hasTabContainer) {
    actions.push(async () => tabContainer.click({force: true, timeout: 3000}));
  }
  actions.push(async () => {
    await linkTab.focus();
    await linkTab.press("Enter");
  });
  if (!await activateWithFallback(isLinkTabActive, actions)) {
    throw new LinkSyncBrowserError(
      "action_unavailable",
      "The manual-sync dialog opened, but the 按链接同步 tab could not be activated",
    );
  }
  return modal;
}

async function selectStoreInSyncModal(modal: Locator, storeName: string): Promise<void> {
  const searchInput = await firstVisible(modal, [
    'input[placeholder*="请输入店铺名称"]',
    'input[placeholder*="店铺名称"]',
  ]);
  const searchTerm = storeName.replace(/^阿里巴巴[-—–]?/, "");
  await searchInput.fill(searchTerm);
  const searchButton = await firstVisible(
    modal,
    ['button:has-text("搜索")', '[role="button"]:has-text("搜索")', 'text=搜索'],
    3000,
  ).catch(() => null);
  if (searchButton) {
    await searchButton.click({force: true});
  } else {
    await searchInput.press("Enter");
  }

  const exactCandidates = [
    `label.ant-radio-wrapper:has-text("${storeName}")`,
    `.ant-radio-group label:has-text("${storeName}")`,
    `text=${storeName}`,
  ];
  let storeChoice: Locator;
  try {
    storeChoice = await firstVisible(modal, exactCandidates, 8000);
  } catch {
    const suffixCandidates = [
      `label.ant-radio-wrapper:has-text("${searchTerm}")`,
      `.ant-radio-group label:has-text("${searchTerm}")`,
    ];
    storeChoice = await firstVisible(modal, suffixCandidates, 3000);
  }
  const rowText = (await storeChoice.innerText().catch(() => "")).replace(/\s+/g, "").trim();
  if (!rowText.includes(storeName.replace(/\s+/g, "")) && !rowText.includes(searchTerm.replace(/\s+/g, ""))) {
    throw new LinkSyncBrowserError("store_mismatch", `Sync dialog did not expose exact store ${storeName}`);
  }
  await storeChoice.click({force: true});
  const selectedLabels = await modal
    .locator('label.ant-radio-wrapper-checked, label:has(input[type="radio"]:checked)')
    .allInnerTexts()
    .catch(() => []);
  const normalizedExpected = storeName.replace(/\s+/g, "");
  const normalizedSuffix = searchTerm.replace(/\s+/g, "");
  if (!selectedLabels.some((text) => {
    const normalizedText = text.replace(/\s+/g, "");
    return normalizedText.includes(normalizedExpected) || normalizedText.includes(normalizedSuffix);
  })) {
    throw new LinkSyncBrowserError("store_mismatch", `Store radio was not selected: ${storeName}`);
  }
}

async function submitStoreSync(page: Page, target: Target, group: StoreSyncGroup): Promise<string> {
  const modal = await openSyncModal(page, target);
  await selectStoreInSyncModal(modal, group.store_name);
  const textarea = await firstVisible(modal, [
    'textarea[placeholder*="商品链接或商品ID"]',
    'textarea[placeholder*="商品链接"]',
    'textarea',
  ]);
  const payload = group.product_ids.join("\n");
  await textarea.fill(payload);
  if ((await textarea.inputValue()).trim() !== payload) {
    throw new LinkSyncBrowserError("input_rejected", "Jushuitan rejected the exact product ID list");
  }
  const submit = await firstVisible(modal, ['button:has-text("立即下载")', 'text=立即下载']);
  await submit.click({force: true});

  const deadline = Date.now() + 15000;
  while (Date.now() <= deadline) {
    const modalVisible = await modal.isVisible().catch(() => false);
    const message = await target.locator('.ant-message-notice-content, [role="alert"]').allInnerTexts().catch(() => []);
    const messageText = message.join(" | ").trim();
    if (/失败|错误|不能为空|请选择/.test(messageText)) {
      throw new LinkSyncBrowserError("sync_rejected", messageText);
    }
    if (!modalVisible || /成功|已提交|正在下载|同步任务/.test(messageText)) {
      return messageText || "Jushuitan accepted the manual sync request";
    }
    await page.waitForTimeout(300);
  }
  throw new LinkSyncBrowserError("verification_failed", "Manual sync click produced no accepted state");
}

async function saveEvidence(
  page: Page,
  target: Target,
  options: LinkSyncRunOptions,
  group: StoreSyncGroup,
  stage: string,
  error?: unknown,
): Promise<string> {
  const dir = path.join(options.artifactsDir, options.runId);
  await ensureDir(dir);
  const base = sanitizeFileName(`${group.store_name}-b${group.batch_index}-${stage}`);
  const screenshotPath = path.join(dir, `${base}.png`);
  const htmlPath = path.join(dir, `${base}.html`);
  const targetHtmlPath = path.join(dir, `${base}-target.html`);
  const jsonPath = path.join(dir, `${base}.json`);
  await page.screenshot({path: screenshotPath, fullPage: true}).catch(() => undefined);
  await fs.writeFile(htmlPath, await page.content().catch(() => ""), "utf8");
  const targetHtml = await target.locator("body").evaluate((body) => body.outerHTML).catch(() => "");
  await fs.writeFile(targetHtmlPath, targetHtml, "utf8");
  await fs.writeFile(jsonPath, JSON.stringify({
    store_name: group.store_name,
    batch_index: group.batch_index,
    product_ids: group.product_ids,
    stage,
    url: page.url(),
    error: error instanceof Error ? error.message : error ? String(error) : "",
    captured_at: now(),
  }, null, 2), "utf8");
  return jsonPath;
}

function resultForTask(task: SyncTask, status: SyncResult["status"], options: {
  message: string;
  category?: string;
  evidencePath?: string;
}): SyncResult {
  return {
    task_id: task.task_id,
    status,
    store_name: task.store_name,
    product_id: task.product_id,
    online_sku: task.online_sku,
    replacement_sku: task.replacement_sku,
    category: options.category,
    message: options.message,
    evidence_path: options.evidencePath,
    recorded_at: now(),
  };
}

export async function runBrowserLinkSync(
  tasks: SyncTask[],
  options: LinkSyncRunOptions,
): Promise<SyncResult[]> {
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
  const context = await browser.newContext(storageStatePath ? {storageState: storageStatePath} : undefined);
  const page = await context.newPage();
  const results: SyncResult[] = [];
  const stoppedStores = new Set<string>();
  let stopAll = false;
  try {
    if (storageStatePath) {
      await page.goto(appConfig.productUrl, {waitUntil: "domcontentloaded", timeout: 120000});
      await page.waitForTimeout(2500);
      if (/login/i.test(page.url())) {
        if (!appConfig.username || !appConfig.password) throw new LinkSyncBrowserError("login_required", "Stored session expired and credentials are unavailable");
        await login(page, {allowManualWait: false});
      }
    } else {
      if (!appConfig.username || !appConfig.password) throw new LinkSyncBrowserError("login_required", "Stored session and credentials are unavailable");
      await login(page, {allowManualWait: false});
    }
    await assertSessionSafe(page);
    let productTarget = await ensureProductPage(page);
    await ensureDir(path.dirname(appConfig.storageStatePath));
    if (appConfig.saveStorageState) await context.storageState({path: appConfig.storageStatePath});

    for (const group of groupSyncTasksByStore(tasks)) {
      if (stopAll || stoppedStores.has(group.store_name)) {
        const category = stopAll ? "session_stopped" : "store_stopped";
        const message = stopAll
          ? "Jushuitan sync session was stopped by a safety error"
          : "Jushuitan store sync was stopped by a store identity error";
        results.push(...group.tasks.map((task) => resultForTask(task, "failed", {category, message})));
        continue;
      }
      try {
        await assertSessionSafe(page);
        const message = await submitStoreSync(page, productTarget, group);
        const evidencePath = await saveEvidence(page, productTarget, options, group, "success");
        const groupResults = group.tasks.map((task) => resultForTask(task, "success", {message, evidencePath}));
        results.push(...groupResults);
        await appendSyncLedger(options.ledgerPath, groupResults);
      } catch (error) {
        const evidencePath = await saveEvidence(page, productTarget, options, group, "failed", error).catch(() => "");
        const category = error instanceof LinkSyncBrowserError ? error.category : "browser_error";
        const message = error instanceof Error ? error.message : String(error);
        results.push(...group.tasks.map((task) => resultForTask(task, "failed", {category, message, evidencePath})));
        if (category === "store_mismatch") stoppedStores.add(group.store_name);
        if (["login_required", "risk_control"].includes(category)) stopAll = true;
        await page.reload({waitUntil: "domcontentloaded", timeout: 120000}).catch(() => undefined);
        productTarget = await ensureProductPage(page).catch(() => page);
      }
    }
  } catch (error) {
    const category = error instanceof LinkSyncBrowserError ? error.category : "browser_error";
    const message = error instanceof Error ? error.message : String(error);
    const pending = tasks.filter((task) => !results.some((result) => result.task_id === task.task_id));
    results.push(...pending.map((task) => resultForTask(task, "failed", {category, message})));
  } finally {
    await context.close().catch(() => undefined);
    await browser.close().catch(() => undefined);
  }
  return results;
}
