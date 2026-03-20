import "dotenv/config";
import fs from "node:fs/promises";
import path from "node:path";
import { chromium, Frame, Locator, Page } from "playwright";
import { appConfig } from "./config";
import { selectors } from "./selectors";

type Target = Page | Frame | Locator;

function allTargets(page: Page): Target[] {
  return [page, ...page.frames()];
}

async function firstVisible(target: Target, candidates: readonly string[], timeoutMs = 5000): Promise<Locator> {
  const startedAt = Date.now();
  while (Date.now() - startedAt < timeoutMs) {
    for (const candidate of candidates) {
      const locator = target.locator(candidate).first();
      if (await locator.isVisible({ timeout: 300 }).catch(() => false)) {
        return locator;
      }
    }
    if ("waitForTimeout" in target) {
      await target.waitForTimeout(200);
    }
  }
  throw new Error(`No visible locator: ${candidates.join(" | ")}`);
}

async function dismissCommon(page: Page): Promise<void> {
  const buttons = ['button:has-text("知道了")', ".ant-modal-close", 'button:has-text("确定")'];
  for (const target of allTargets(page)) {
    for (const button of buttons) {
      const locator = target.locator(button);
      const count = await locator.count().catch(() => 0);
      for (let index = 0; index < Math.min(count, 6); index += 1) {
        await locator.nth(index).click({ force: true }).catch(() => undefined);
      }
    }
  }
}

async function findQueryTarget(page: Page): Promise<Target> {
  for (const target of allTargets(page)) {
    const count = await target.locator("#shopGoodsImportQuery").count().catch(() => 0);
    if (count > 0) {
      return target;
    }
  }
  return page;
}

async function getStoreDialog(page: Page): Promise<Locator> {
  return page
    .locator("[role='dialog'], .ant-modal, .ant-modal-content")
    .filter({ hasText: /请选择平台.?店铺/ })
    .first();
}

async function saveArtifacts(page: Page, name: string, payload?: string) {
  const base = path.resolve("artifacts", "debug-table");
  await fs.mkdir(base, { recursive: true });
  await page.screenshot({ path: path.join(base, `${name}.png`), fullPage: true });
  if (payload !== undefined) {
    await fs.writeFile(path.join(base, `${name}.html`), payload, "utf8");
  }
}

async function run(): Promise<void> {
  const browser = await chromium.launch({
    headless: false,
    channel: appConfig.browserChannel,
    executablePath: appConfig.browserExecutablePath,
    slowMo: 120,
  });
  const context = await browser.newContext({ storageState: appConfig.storageStatePath });
  const page = await context.newPage();

  await page.goto(appConfig.productUrl, { waitUntil: "domcontentloaded", timeout: 120000 });
  await page.waitForTimeout(6000);
  await dismissCommon(page);

  const queryTarget = await findQueryTarget(page);
  const skuTab = queryTarget.locator("text=按SKU").first();
  if (await skuTab.isVisible().catch(() => false)) {
    await skuTab.click({ force: true }).catch(() => undefined);
    await page.waitForTimeout(1000);
  }

  const itemInput = await firstVisible(queryTarget, selectors.productPage.multiSkuInput, 5000);
  await itemInput.fill("135150,135580,135620,135650,13");

  const platformTrigger = await firstVisible(queryTarget, selectors.productPage.platformTrigger, 5000);
  await platformTrigger.click({ force: true }).catch(() => undefined);
  await page.waitForTimeout(1000);

  const dialog = await getStoreDialog(page);
  await dialog.waitFor({ state: "visible", timeout: 10000 });
  const storeInput = dialog.locator('input[placeholder*="平台或店铺名称"]').first();
  await storeInput.fill("淘宝");
  await dialog.locator('button:has-text("搜索")').first().click({ force: true });
  await page.waitForTimeout(1800);
  await dialog.getByText("全选", { exact: true }).click({ force: true }).catch(() => undefined);
  await page.waitForTimeout(500);
  await dialog.locator(".ant-modal-footer .ant-btn-primary").last().click({ force: true });
  await page.waitForTimeout(1000);

  await queryTarget.locator('button:has-text("搜索")').first().click({ force: true });
  await page.waitForTimeout(10000);

  const tableHtml = await page.evaluate(() => {
    const table = document.querySelector(".ant-table-wrapper, .ant-table");
    return table ? table.outerHTML : document.body.outerHTML;
  });
  await saveArtifacts(page, "after-search", tableHtml);

  const inspect = await page.evaluate(() => {
    const header = document.querySelector(".ant-table-thead .ant-checkbox-input, .ant-table-thead .ant-checkbox-inner, .ant-table-thead .ant-checkbox");
    const rows = Array.from(document.querySelectorAll(".ant-table-tbody tr")).slice(0, 5);
    const rowData = rows.map((row) => {
      const cb = row.querySelector(".ant-checkbox-input, .ant-checkbox-inner, .ant-checkbox");
      const firstCell = row.querySelector("td");
      const cellRect = firstCell?.getBoundingClientRect();
      return {
        text: row.textContent?.slice(0, 200) ?? "",
        hasCheckbox: Boolean(cb),
        checkboxHtml: cb ? (cb as HTMLElement).outerHTML.slice(0, 300) : "",
        firstCellRect: cellRect
          ? { x: cellRect.x, y: cellRect.y, w: cellRect.width, h: cellRect.height }
          : null,
      };
    });
    const headerRect = header?.getBoundingClientRect();
    return {
      headerHtml: header ? (header as HTMLElement).outerHTML.slice(0, 300) : "",
      headerRect: headerRect ? { x: headerRect.x, y: headerRect.y, w: headerRect.width, h: headerRect.height } : null,
      rowData,
      bodyText: document.body.innerText.slice(0, 3000),
    };
  });
  await fs.writeFile(path.resolve("artifacts", "debug-table", "inspect.json"), JSON.stringify(inspect, null, 2), "utf8");

  if (inspect.headerRect) {
    await page.mouse.click(Math.round(inspect.headerRect.x + inspect.headerRect.w / 2), Math.round(inspect.headerRect.y + inspect.headerRect.h / 2));
    await page.waitForTimeout(1200);
    await saveArtifacts(page, "after-header-click");
  }

  const warningVisible = await page.getByText("请选择商品", { exact: true }).isVisible().catch(() => false);
  console.log(JSON.stringify({ warningVisible, inspect }, null, 2));

  await page.waitForTimeout(10000);
  await context.close();
  await browser.close();
}

run().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
