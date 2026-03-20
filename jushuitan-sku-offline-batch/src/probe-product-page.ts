import fs from "node:fs/promises";
import path from "node:path";
import { chromium } from "playwright";
import { appConfig } from "./config";
import { selectors } from "./selectors";

async function hasAnyVisible(page: import("playwright").Page, candidates: readonly string[]) {
  for (const candidate of candidates) {
    try {
      if (await page.locator(candidate).first().isVisible({ timeout: 1000 })) {
        return true;
      }
    } catch {
      continue;
    }
  }
  return false;
}

async function clickFirst(page: import("playwright").Page, candidates: readonly string[]) {
  for (const candidate of candidates) {
    const locator = page.locator(candidate).first();
    try {
      await locator.waitFor({ state: "visible", timeout: 3000 });
      await locator.click({ force: true });
      return;
    } catch {
      continue;
    }
  }
  throw new Error(`No visible element for: ${candidates.join(" | ")}`);
}

async function dismissVisibleModals(page: import("playwright").Page) {
  const buttons = ['button:has-text("知道了")', '.ant-modal-close', 'button:has-text("确定")'];
  for (const sel of buttons) {
    const locator = page.locator(sel);
    const count = await locator.count();
    for (let index = 0; index < count; index += 1) {
      await locator.nth(index).click({ force: true }).catch(() => undefined);
      await page.waitForTimeout(300);
    }
  }
}

async function main() {
  const browser = await chromium.launch({
    channel: appConfig.browserChannel,
    executablePath: appConfig.browserExecutablePath,
    headless: false,
    slowMo: 200,
  });
  const context = await browser.newContext({ storageState: appConfig.storageStatePath });
  const page = await context.newPage();

  await page.goto(appConfig.productUrl, { waitUntil: "domcontentloaded" });
  await page.waitForTimeout(5000);
  await dismissVisibleModals(page);

  if (!(await hasAnyVisible(page, selectors.productPage.batchUpdateButton))) {
    await clickFirst(page, selectors.productPage.entryLinks).catch(() => undefined);
    await page.waitForTimeout(3000);
    await dismissVisibleModals(page);
  }

  if (await hasAnyVisible(page, selectors.productPage.skuTab)) {
    await clickFirst(page, selectors.productPage.skuTab);
    await page.waitForTimeout(2000);
  }

  await fs.mkdir(path.resolve("artifacts"), { recursive: true });
  await page.screenshot({ path: path.resolve("artifacts/probe-product-page.png"), fullPage: true });
  await fs.writeFile(path.resolve("artifacts/probe-product-page.html"), await page.content(), "utf8");
  console.log(`url=${page.url()}`);
  console.log(`saved=${path.resolve("artifacts/probe-product-page.html")}`);

  await page.waitForTimeout(10000);
  await context.close();
  await browser.close();
}

main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
