import fs from "node:fs/promises";
import path from "node:path";
import { Frame, Locator, Page } from "playwright";

export async function ensureDir(dirPath: string): Promise<void> {
  await fs.mkdir(dirPath, { recursive: true });
}

export async function resolveFirstVisibleLocator(
  target: Page | Frame | Locator,
  candidates: readonly string[],
  timeoutMs = 5000,
): Promise<Locator> {
  const startedAt = Date.now();

  for (const candidate of candidates) {
    try {
      const locator = target.locator(candidate).first();
      await locator.waitFor({
        state: "visible",
        timeout: Math.max(500, timeoutMs - (Date.now() - startedAt)),
      });
      return locator;
    } catch {
      continue;
    }
  }

  throw new Error(`未找到可见元素，候选选择器: ${candidates.join(" | ")}`);
}

export function chunkArray<T>(items: T[], chunkSize: number): T[][] {
  const chunks: T[][] = [];

  for (let index = 0; index < items.length; index += chunkSize) {
    chunks.push(items.slice(index, index + chunkSize));
  }

  return chunks;
}

export function sanitizeFileName(input: string): string {
  return input.replace(/[\\/:*?"<>|]/g, "_");
}

export async function writeTextFile(filePath: string, content: string): Promise<void> {
  await ensureDir(path.dirname(filePath));
  await fs.writeFile(filePath, content, "utf8");
}
