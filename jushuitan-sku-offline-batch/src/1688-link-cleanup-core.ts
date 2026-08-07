import crypto from "node:crypto";
import fs from "node:fs/promises";
import path from "node:path";
import { z } from "zod";
import { ensureDir } from "./utils";

const cleanupTaskSchema = z.object({
  task_id: z.string().trim().optional(),
  source: z.string().trim().default("1688_sku_offline"),
  source_status: z.enum(["pending_1688", "success", "already_offline"]),
  store_name: z.string().trim().min(1),
  platform: z.string().trim().min(1),
  product_id: z.string().trim().min(1),
  online_sku: z.string().trim().min(1),
  platform_store_item_code: z.string().trim().min(1),
  handling: z.string().trim().min(1),
  source_file: z.string().trim().optional().default(""),
  source_row_number: z.coerce.number().int().positive().optional(),
});

export type CleanupMode = "preview" | "probe" | "execute";

export interface CleanupTask extends z.infer<typeof cleanupTaskSchema> {
  task_id: string;
}

export type CleanupStatus =
  | "preview"
  | "found"
  | "success"
  | "already_cleared"
  | "failed";

export interface CleanupResult {
  task_id: string;
  status: CleanupStatus;
  store_name: string;
  product_id: string;
  online_sku: string;
  platform_store_item_code: string;
  category?: string;
  message?: string;
  evidence_path?: string;
  row_text?: string;
  recorded_at: string;
}

export interface RowEvidence {
  index: number;
  text: string;
  columns: {
    store_name: string;
    product_id: string;
    online_sku: string;
    platform_store_item_code: string;
  };
}

const OPERATIONS_CATEGORY_LABELS: Record<string, string> = {
  ledger_idempotency: "历史已完成",
  verified_target_absent: "目标链接已不存在",
  login_required: "登录失效",
  risk_control: "验证码或风控",
  store_picker_unavailable: "店铺选择器不可用",
  store_mismatch: "店铺不匹配",
  store_stopped: "店铺已安全停止",
  session_stopped: "会话已安全停止",
  task_not_found: "聚水潭无匹配",
  ambiguous_match: "匹配到多行",
  row_selection_failed: "勾选失败",
  action_unavailable: "清除入口不可用",
  confirmation_failed: "确认失败",
  verification_failed: "清除后复查仍存在",
  browser_error: "页面执行异常",
};

function normalizedKeyPart(value: string): string {
  return value.replace(/\s+/g, "").trim().toLowerCase();
}

function normalizedHeader(value: string): string {
  return value.replace(/[\s：:()（）/\\_-]+/g, "").trim().toLowerCase();
}

const rowIdentityHeaderAliases = {
  store_name: [
    "店铺名称",
    "店铺",
    "平台店铺",
    "平台/店铺",
    "平台/店铺名称",
    "平台店铺名称",
  ],
  product_id: ["商品ID", "商品 ID", "平台商品ID", "平台商品 ID", "商品信息"],
  online_sku: ["线上商品编码", "线上编码", "线上SKU", "线上 SKU"],
  platform_store_item_code: [
    "平台店铺商品编码",
    "平台商品编码",
    "平台店铺商品ID",
    "店铺商品编码",
    "店铺商品ID",
    "店铺商品 ID",
  ],
} as const;

function extractStoreName(value: string): string {
  const lines = value
    .split(/\r?\n/)
    .map((line) => line.replace(/\s+/g, " ").trim())
    .filter(Boolean);
  const exactAlibabaStore = lines.find((line) => /^阿里巴巴[-—–]/.test(line));
  return exactAlibabaStore ?? (lines.length === 1 ? lines[0] : "");
}

function extractProductId(value: string): string {
  const labeled = value.match(/(?:商品|平台商品)\s*ID\s*[：:]\s*([A-Za-z0-9][A-Za-z0-9_-]*)/i);
  if (labeled?.[1]) {
    return labeled[1].trim();
  }
  const direct = value.replace(/\s+/g, " ").trim();
  return /^[A-Za-z0-9][A-Za-z0-9_-]*$/.test(direct) ? direct : "";
}

function extractIdentityColumnValue(
  field: keyof typeof rowIdentityHeaderAliases,
  value: string,
): string {
  switch (field) {
    case "store_name":
      return extractStoreName(value);
    case "product_id":
      return extractProductId(value);
    case "online_sku":
    case "platform_store_item_code":
      return value.replace(/\s+/g, " ").trim();
    default:
      return value.trim();
  }
}

export function buildStructuredRowEvidence(
  index: number,
  text: string,
  headers: string[],
  cells: string[],
): RowEvidence {
  const normalizedHeaders = headers.map(normalizedHeader);
  const valueFor = (
    field: keyof typeof rowIdentityHeaderAliases,
    aliases: readonly string[],
  ): string => {
    const accepted = new Set(aliases.map(normalizedHeader));
    const columnIndex = normalizedHeaders.findIndex((header) => accepted.has(header));
    return columnIndex >= 0
      ? extractIdentityColumnValue(field, cells[columnIndex] ?? "")
      : "";
  };
  return {
    index,
    text,
    columns: {
      store_name: valueFor("store_name", rowIdentityHeaderAliases.store_name),
      product_id: valueFor("product_id", rowIdentityHeaderAliases.product_id),
      online_sku: valueFor("online_sku", rowIdentityHeaderAliases.online_sku),
      platform_store_item_code: valueFor(
        "platform_store_item_code",
        rowIdentityHeaderAliases.platform_store_item_code,
      ),
    },
  };
}

function rowMatchesTaskIdentity(
  task: CleanupTask,
  row: RowEvidence,
  includePlatformCode: boolean,
): boolean {
  const expectedStore = normalizedKeyPart(task.store_name);
  const expectedStoreSuffix = normalizedKeyPart(task.store_name.replace(/^阿里巴巴[-—–]?/, ""));
  const observedStore = normalizedKeyPart(row.columns.store_name);
  const baseMatches = (
    Boolean(observedStore) &&
    (observedStore === expectedStore || observedStore === expectedStoreSuffix) &&
    normalizedKeyPart(row.columns.product_id) === normalizedKeyPart(task.product_id) &&
    normalizedKeyPart(row.columns.online_sku) === normalizedKeyPart(task.online_sku)
  );
  if (!baseMatches) {
    return false;
  }
  const observedPlatformCode = normalizedKeyPart(row.columns.platform_store_item_code);
  if (!observedPlatformCode) {
    return false;
  }
  const expectedPlatformCode = normalizedKeyPart(task.platform_store_item_code);
  return includePlatformCode
    ? observedPlatformCode === expectedPlatformCode
    : observedPlatformCode !== expectedPlatformCode;
}

export function buildTaskId(task: {
  store_name: string;
  product_id: string;
  online_sku: string;
  platform_store_item_code: string;
}): string {
  const key = [
    task.store_name,
    task.product_id,
    task.online_sku,
    task.platform_store_item_code,
  ]
    .map(normalizedKeyPart)
    .join("|");
  return crypto.createHash("sha256").update(key, "utf8").digest("hex");
}

export function buildProductGroupKey(task: {
  store_name: string;
  product_id: string;
}): string {
  return [task.store_name, task.product_id].map(normalizedKeyPart).join("\u0000");
}

export function orderCleanupTasksForExecution(tasks: CleanupTask[]): CleanupTask[] {
  return [...tasks].sort((left, right) =>
    buildProductGroupKey(left).localeCompare(buildProductGroupKey(right)),
  );
}

export function parseCleanupTask(raw: unknown): CleanupTask {
  const parsed = cleanupTaskSchema.parse(raw);
  if (parsed.platform.toLowerCase() !== "alibaba") {
    throw new Error(`Only Alibaba cleanup tasks are allowed: ${parsed.platform}`);
  }
  if (parsed.handling !== "全渠道下架") {
    throw new Error(`Unsupported handling value: ${parsed.handling}`);
  }
  if (!parsed.store_name.startsWith("阿里巴巴-")) {
    throw new Error(`Jushuitan store must be an exact Alibaba store name: ${parsed.store_name}`);
  }

  const generatedTaskId = buildTaskId(parsed);
  if (parsed.task_id && parsed.task_id !== generatedTaskId) {
    throw new Error(`Task identity mismatch for ${parsed.product_id}/${parsed.online_sku}`);
  }
  return { ...parsed, task_id: generatedTaskId };
}

export async function loadCleanupTasks(filePath: string): Promise<CleanupTask[]> {
  const content = await fs.readFile(filePath, "utf8");
  const extension = path.extname(filePath).toLowerCase();
  let rawItems: unknown[];

  if (extension === ".jsonl") {
    rawItems = content
      .split(/\r?\n/)
      .map((line) => line.trim())
      .filter(Boolean)
      .map((line, index) => {
        try {
          return JSON.parse(line) as unknown;
        } catch (error) {
          throw new Error(`Invalid JSONL at line ${index + 1}: ${String(error)}`);
        }
      });
  } else if (extension === ".json") {
    const payload = JSON.parse(content) as unknown;
    rawItems = Array.isArray(payload)
      ? payload
      : Array.isArray((payload as { tasks?: unknown[] })?.tasks)
        ? (payload as { tasks: unknown[] }).tasks
        : [payload];
  } else {
    throw new Error(`Unsupported cleanup input format: ${extension}`);
  }

  const deduped = new Map<string, CleanupTask>();
  for (const rawItem of rawItems) {
    const task = parseCleanupTask(rawItem);
    deduped.set(task.task_id, task);
  }
  return [...deduped.values()];
}

export function findMatchingRows(task: CleanupTask, rows: RowEvidence[]): RowEvidence[] {
  return rows.filter((row) => rowMatchesTaskIdentity(task, row, true));
}

export function findIdentitySiblingRows(task: CleanupTask, rows: RowEvidence[]): RowEvidence[] {
  return rows.filter((row) => rowMatchesTaskIdentity(task, row, false));
}

export function assertTasksAllowedForMode(mode: CleanupMode, tasks: CleanupTask[]): void {
  if (
    mode === "execute" &&
    tasks.some((task) => !["success", "already_offline"].includes(task.source_status))
  ) {
    throw new Error("execute mode only accepts tasks already verified by the 1688 offline step");
  }
}

export async function loadSuccessfulLedgerTaskIds(ledgerPath: string): Promise<Set<string>> {
  try {
    const content = await fs.readFile(ledgerPath, "utf8");
    const ids = new Set<string>();
    for (const line of content.split(/\r?\n/)) {
      const text = line.trim();
      if (!text) {
        continue;
      }
      try {
        const payload = JSON.parse(text) as Partial<CleanupResult>;
        if (["success", "already_cleared"].includes(String(payload.status)) && payload.task_id) {
          ids.add(payload.task_id);
        }
      } catch {
        // Ignore malformed historical lines; the current run still validates every new record.
      }
    }
    return ids;
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code === "ENOENT") {
      return new Set<string>();
    }
    throw error;
  }
}

export async function appendLedgerResult(ledgerPath: string, result: CleanupResult): Promise<void> {
  await ensureDir(path.dirname(ledgerPath));
  await fs.appendFile(ledgerPath, `${JSON.stringify(result)}\n`, "utf8");
}

export async function writeResultsJsonl(resultPath: string, results: CleanupResult[]): Promise<void> {
  await ensureDir(path.dirname(resultPath));
  const content = results.map((result) => JSON.stringify(result)).join("\n");
  await fs.writeFile(resultPath, content ? `${content}\n` : "", "utf8");
}

export function summarizeResults(mode: CleanupMode, results: CleanupResult[]) {
  const counts: Record<string, number> = {};
  for (const result of results) {
    counts[result.status] = (counts[result.status] ?? 0) + 1;
  }
  return {
    mode,
    total: results.length,
    preview: counts.preview ?? 0,
    found: counts.found ?? 0,
    success: counts.success ?? 0,
    already_cleared: counts.already_cleared ?? 0,
    failed: counts.failed ?? 0,
  };
}

export function buildOperationsMessages(runId: string, results: CleanupResult[]): string[] {
  const summary = summarizeResults("execute", results);
  const storeCounts = new Map<string, { success: number; already: number; failed: number }>();
  for (const result of results) {
    const current = storeCounts.get(result.store_name) ?? { success: 0, already: 0, failed: 0 };
    if (result.status === "success") {
      current.success += 1;
    } else if (result.status === "already_cleared") {
      current.already += 1;
    } else if (result.status === "failed") {
      current.failed += 1;
    }
    storeCounts.set(result.store_name, current);
  }

  const summaryLines = [
    "【1688 聚水潭清除链接】",
    `批次：${runId}`,
    `总数：${summary.total}`,
    `清除成功：${summary.success}`,
    `历史已完成：${summary.already_cleared}`,
    `异常：${summary.failed}`,
    "分店结果：",
    ...[...storeCounts.entries()].map(([storeName, counts]) => {
      const displayName = storeName.replace(/^阿里巴巴-/, "");
      return `${displayName}：成功${counts.success}，已完成${counts.already}，异常${counts.failed}`;
    }),
  ];

  const messages = [summaryLines.join("\n")];
  const failures = results.filter((result) => result.status === "failed");
  const maxFailureLines = 60;
  const includedFailures = failures.slice(0, maxFailureLines);
  const chunkSize = 15;
  const chunkCount = Math.ceil(includedFailures.length / chunkSize);
  for (let offset = 0; offset < includedFailures.length; offset += chunkSize) {
    const chunk = includedFailures.slice(offset, offset + chunkSize);
    const chunkNumber = Math.floor(offset / chunkSize) + 1;
    const lines = [
      `【1688 聚水潭异常明细 ${chunkNumber}/${chunkCount}】`,
      ...chunk.map((result) => {
        const displayName = result.store_name.replace(/^阿里巴巴-/, "");
        const reason = OPERATIONS_CATEGORY_LABELS[result.category ?? ""] ?? "执行异常";
        return `${displayName} | ID ${result.product_id} | SKU ${result.online_sku} | ${reason}`;
      }),
    ];
    if (offset + chunkSize >= includedFailures.length && failures.length > maxFailureLines) {
      lines.push(`另有 ${failures.length - maxFailureLines} 条异常，请联系技术人员导出完整明细。`);
    }
    messages.push(lines.join("\n"));
  }
  return messages;
}
