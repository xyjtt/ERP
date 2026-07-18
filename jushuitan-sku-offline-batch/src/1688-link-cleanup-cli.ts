import fs from "node:fs/promises";
import path from "node:path";
import { format } from "date-fns";
import {
  assertTasksAllowedForMode,
  buildOperationsMessages,
  CleanupMode,
  CleanupResult,
  loadCleanupTasks,
  loadSuccessfulLedgerTaskIds,
  summarizeResults,
  writeResultsJsonl,
} from "./1688-link-cleanup-core";
import { sendDingTalkText } from "./dingtalk";
import { ensureDir } from "./utils";

interface CliOptions {
  file: string;
  mode: CleanupMode;
  yes: boolean;
  noNotify: boolean;
  limit: number;
  ledgerPath: string;
  resultsDir: string;
  artifactsDir: string;
}

function valueAfter(args: string[], name: string): string {
  const index = args.indexOf(name);
  if (index < 0 || index + 1 >= args.length) {
    return "";
  }
  return args[index + 1];
}

export function parseCliOptions(args: string[]): CliOptions {
  const mode = (valueAfter(args, "--mode") || "preview") as CleanupMode;
  if (!(["preview", "probe", "execute"] as string[]).includes(mode)) {
    throw new Error(`Unsupported mode: ${mode}`);
  }
  const file = valueAfter(args, "--file");
  if (!file) {
    throw new Error("--file is required");
  }
  const limitText = valueAfter(args, "--limit");
  const limit = limitText ? Number(limitText) : 0;
  if (!Number.isInteger(limit) || limit < 0) {
    throw new Error("--limit must be a non-negative integer");
  }

  return {
    file: path.resolve(file),
    mode,
    yes: args.includes("--yes"),
    noNotify: args.includes("--no-notify"),
    limit,
    ledgerPath: path.resolve(
      valueAfter(args, "--ledger") || "storage/1688-link-cleanup-ledger.jsonl",
    ),
    resultsDir: path.resolve(valueAfter(args, "--results-dir") || "results/1688-link-cleanup"),
    artifactsDir: path.resolve(
      valueAfter(args, "--artifacts-dir") || "artifacts/1688-link-cleanup",
    ),
  };
}

function resultForKnownTask(task: Awaited<ReturnType<typeof loadCleanupTasks>>[number]): CleanupResult {
  return {
    task_id: task.task_id,
    status: "already_cleared",
    store_name: task.store_name,
    product_id: task.product_id,
    online_sku: task.online_sku,
    platform_store_item_code: task.platform_store_item_code,
    category: "ledger_idempotency",
    message: "A prior verified clear-link success exists in the local ledger",
    recorded_at: new Date().toISOString(),
  };
}

function previewResult(task: Awaited<ReturnType<typeof loadCleanupTasks>>[number]): CleanupResult {
  return {
    task_id: task.task_id,
    status: "preview",
    store_name: task.store_name,
    product_id: task.product_id,
    online_sku: task.online_sku,
    platform_store_item_code: task.platform_store_item_code,
    message: "Validated only; no browser or online operation was performed",
    recorded_at: new Date().toISOString(),
  };
}

async function writeSummary(summaryPath: string, payload: object): Promise<void> {
  await ensureDir(path.dirname(summaryPath));
  await fs.writeFile(summaryPath, `${JSON.stringify(payload, null, 2)}\n`, "utf8");
}

async function main(): Promise<void> {
  const options = parseCliOptions(process.argv.slice(2));
  if (options.mode === "execute" && !options.yes) {
    throw new Error("execute mode requires --yes");
  }

  const runId = format(new Date(), "yyyyMMdd-HHmmss");
  const allTasks = await loadCleanupTasks(options.file);
  const tasks = options.limit > 0 ? allTasks.slice(0, options.limit) : allTasks;
  assertTasksAllowedForMode(options.mode, tasks);
  const successfulIds = await loadSuccessfulLedgerTaskIds(options.ledgerPath);
  const knownResults = tasks.filter((task) => successfulIds.has(task.task_id)).map(resultForKnownTask);
  const pendingTasks = tasks.filter((task) => !successfulIds.has(task.task_id));
  let results: CleanupResult[];

  if (options.mode === "preview") {
    results = [...knownResults, ...pendingTasks.map(previewResult)];
  } else if (pendingTasks.length === 0) {
    results = knownResults;
  } else {
    const { runBrowserCleanup } = await import("./1688-link-cleanup-browser");
    const browserResults = await runBrowserCleanup(pendingTasks, {
      mode: options.mode,
      runId,
      ledgerPath: options.ledgerPath,
      artifactsDir: options.artifactsDir,
    });
    results = [...knownResults, ...browserResults];
  }

  const resultPath = path.join(options.resultsDir, `${runId}.jsonl`);
  const summaryPath = path.join(options.resultsDir, `${runId}.summary.json`);
  await writeResultsJsonl(resultPath, results);
  const summary = {
    ...summarizeResults(options.mode, results),
    run_id: runId,
    input_file: options.file,
    result_path: resultPath,
    summary_path: summaryPath,
    ledger_path: options.ledgerPath,
    notification_sent: null as boolean | null,
    notification_message_count: 0,
    finished_at: new Date().toISOString(),
  };

  if (!options.noNotify && options.mode === "execute") {
    const messages = buildOperationsMessages(runId, results);
    const deliveryResults: boolean[] = [];
    for (const message of messages) {
      deliveryResults.push(await sendDingTalkText(message));
      if (messages.length > 1) {
        await new Promise((resolve) => setTimeout(resolve, 600));
      }
    }
    summary.notification_message_count = messages.length;
    summary.notification_sent = deliveryResults.every(Boolean);
  }

  await writeSummary(summaryPath, summary);

  console.log(JSON.stringify(summary, null, 2));
  if (summary.failed > 0) {
    process.exitCode = 2;
  }
}

main().catch((error) => {
  console.error(`1688 link cleanup failed: ${error instanceof Error ? error.message : String(error)}`);
  process.exitCode = 1;
});
