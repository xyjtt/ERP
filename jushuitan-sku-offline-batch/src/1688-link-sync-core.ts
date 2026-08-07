import crypto from "node:crypto";
import fs from "node:fs/promises";
import path from "node:path";
import { z } from "zod";
import { ensureDir } from "./utils";

const syncTaskSchema = z.object({
  task_id: z.string().trim().optional(),
  source: z.literal("1688_sku_replace").default("1688_sku_replace"),
  source_status: z.enum(["pending_1688", "success", "already_replaced"]),
  store_name: z.string().trim().min(1),
  jushuitan_store_name: z.string().trim().optional().default(""),
  platform: z.string().trim().min(1),
  product_id: z.string().trim().min(1),
  online_sku: z.string().trim().min(1),
  replacement_sku: z.string().trim().min(1),
  platform_store_item_code: z.string().trim().optional().default(""),
  handling: z.literal("全渠道替换"),
  action: z.literal("sync_by_link"),
  source_file: z.string().trim().optional().default(""),
  source_row_number: z.coerce.number().int().positive().optional(),
});

export type SyncMode = "preview" | "execute";

export interface SyncTask extends z.infer<typeof syncTaskSchema> {
  task_id: string;
  jushuitan_store_name: string;
}

export type SyncStatus = "preview" | "success" | "already_synced" | "failed";

export interface SyncResult {
  task_id: string;
  status: SyncStatus;
  store_name: string;
  jushuitan_store_name?: string;
  product_id: string;
  online_sku: string;
  replacement_sku: string;
  category?: string;
  message?: string;
  evidence_path?: string;
  recorded_at: string;
}

function normalized(value: string): string {
  return value.replace(/\s+/g, "").trim().toLowerCase();
}

export function buildSyncTaskId(task: {
  store_name: string;
  product_id: string;
  online_sku: string;
  replacement_sku: string;
}): string {
  const identity = [task.store_name, task.product_id, task.online_sku, task.replacement_sku]
    .map(normalized)
    .join("|");
  return crypto.createHash("sha256").update(identity, "utf8").digest("hex");
}

export function parseSyncTask(raw: unknown): SyncTask {
  const parsed = syncTaskSchema.parse(raw);
  if (parsed.platform.toLowerCase() !== "alibaba") {
    throw new Error(`Only Alibaba sync tasks are allowed: ${parsed.platform}`);
  }
  const jushuitanStoreName = parsed.jushuitan_store_name || parsed.store_name;
  if (!jushuitanStoreName.startsWith("阿里巴巴-")) {
    throw new Error(`Jushuitan store must be an exact Alibaba store name: ${jushuitanStoreName}`);
  }
  const generatedTaskId = buildSyncTaskId(parsed);
  if (parsed.task_id && parsed.task_id !== generatedTaskId) {
    throw new Error(`Task identity mismatch for ${parsed.product_id}/${parsed.online_sku}`);
  }
  return {...parsed, jushuitan_store_name: jushuitanStoreName, task_id: generatedTaskId};
}

export async function loadSyncTasks(filePath: string): Promise<SyncTask[]> {
  const content = await fs.readFile(filePath, "utf8");
  const extension = path.extname(filePath).toLowerCase();
  let rawItems: unknown[];
  if (extension === ".jsonl") {
    rawItems = content
      .split(/\r?\n/)
      .map((line) => line.trim())
      .filter(Boolean)
      .map((line) => JSON.parse(line) as unknown);
  } else if (extension === ".json") {
    const payload = JSON.parse(content) as unknown;
    rawItems = Array.isArray(payload)
      ? payload
      : Array.isArray((payload as {tasks?: unknown[]})?.tasks)
        ? (payload as {tasks: unknown[]}).tasks
        : [payload];
  } else {
    throw new Error(`Unsupported sync input format: ${extension}`);
  }
  const deduped = new Map<string, SyncTask>();
  for (const raw of rawItems) {
    const task = parseSyncTask(raw);
    deduped.set(task.task_id, task);
  }
  return [...deduped.values()];
}

export function assertSyncTasksAllowed(mode: SyncMode, tasks: SyncTask[]): void {
  if (mode === "execute" && tasks.some((task) => !["success", "already_replaced"].includes(task.source_status))) {
    throw new Error("execute mode only accepts tasks verified by the 1688 replacement step");
  }
}

export interface StoreSyncGroup {
  store_name: string;
  jushuitan_store_name: string;
  batch_index: number;
  product_ids: string[];
  tasks: SyncTask[];
}

export function groupSyncTasksByStore(tasks: SyncTask[], maxProductIds = 50): StoreSyncGroup[] {
  if (!Number.isInteger(maxProductIds) || maxProductIds <= 0) {
    throw new Error("maxProductIds must be a positive integer");
  }
  const groups = new Map<string, SyncTask[]>();
  for (const task of tasks) {
    const groupKey = JSON.stringify([task.store_name, task.jushuitan_store_name]);
    const current = groups.get(groupKey) ?? [];
    current.push(task);
    groups.set(groupKey, current);
  }
  const batches: StoreSyncGroup[] = [];
  for (const storeTasks of groups.values()) {
    const store_name = storeTasks[0].store_name;
    const jushuitan_store_name = storeTasks[0].jushuitan_store_name;
    const productIds = [...new Set(storeTasks.map((task) => task.product_id))];
    for (let offset = 0; offset < productIds.length; offset += maxProductIds) {
      const batchProductIds = productIds.slice(offset, offset + maxProductIds);
      const included = new Set(batchProductIds);
      batches.push({
        store_name,
        jushuitan_store_name,
        batch_index: Math.floor(offset / maxProductIds) + 1,
        product_ids: batchProductIds,
        tasks: storeTasks.filter((task) => included.has(task.product_id)),
      });
    }
  }
  return batches;
}

export async function loadSuccessfulSyncTaskIds(ledgerPath: string): Promise<Set<string>> {
  try {
    const content = await fs.readFile(ledgerPath, "utf8");
    const ids = new Set<string>();
    for (const line of content.split(/\r?\n/)) {
      try {
        const payload = JSON.parse(line) as Partial<SyncResult>;
        if (payload.status === "success" && payload.task_id) ids.add(payload.task_id);
      } catch {
        // Ignore malformed historical records.
      }
    }
    return ids;
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code === "ENOENT") return new Set<string>();
    throw error;
  }
}

export async function appendSyncLedger(ledgerPath: string, results: SyncResult[]): Promise<void> {
  const successful = results.filter((result) => result.status === "success");
  if (!successful.length) return;
  await ensureDir(path.dirname(ledgerPath));
  await fs.appendFile(ledgerPath, successful.map((result) => JSON.stringify(result)).join("\n") + "\n", "utf8");
}

export async function writeSyncResults(resultPath: string, results: SyncResult[]): Promise<void> {
  await ensureDir(path.dirname(resultPath));
  const content = results.map((result) => JSON.stringify(result)).join("\n");
  await fs.writeFile(resultPath, content ? `${content}\n` : "", "utf8");
}

export function summarizeSyncResults(mode: SyncMode, results: SyncResult[]) {
  const counts: Record<string, number> = {};
  for (const result of results) counts[result.status] = (counts[result.status] ?? 0) + 1;
  return {
    mode,
    total: results.length,
    preview: counts.preview ?? 0,
    success: counts.success ?? 0,
    already_synced: counts.already_synced ?? 0,
    failed: counts.failed ?? 0,
  };
}

export function buildSyncNotification(runId: string, results: SyncResult[]): string {
  const summary = summarizeSyncResults("execute", results);
  const failures = results.filter((result) => result.status === "failed");
  const lines = [
    "【1688 SKU替换后聚水潭同步】",
    `批次：${runId}`,
    `总数：${summary.total}`,
    `同步成功：${summary.success}`,
    `历史已完成：${summary.already_synced}`,
    `异常：${summary.failed}`,
  ];
  if (failures.length) {
    lines.push("异常明细：");
    lines.push(...failures.slice(0, 20).map((item) =>
      `${item.store_name}/${item.product_id}/${item.replacement_sku}：${item.category || "browser_error"} ${item.message || ""}`,
    ));
  }
  return lines.join("\n");
}
