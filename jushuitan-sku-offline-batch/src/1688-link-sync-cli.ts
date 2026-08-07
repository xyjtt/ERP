import fs from "node:fs/promises";
import path from "node:path";
import {format} from "date-fns";
import {
  assertSyncTasksAllowed,
  buildSyncNotification,
  loadSuccessfulSyncTaskIds,
  loadSyncTasks,
  summarizeSyncResults,
  SyncMode,
  SyncResult,
  SyncTask,
  writeSyncResults,
} from "./1688-link-sync-core";
import {sendDingTalkText} from "./dingtalk";
import {ensureDir} from "./utils";

interface CliOptions {
  file: string;
  mode: SyncMode;
  runId: string;
  yes: boolean;
  noNotify: boolean;
  limit: number;
  ledgerPath: string;
  resultsDir: string;
  artifactsDir: string;
}

function valueAfter(args: string[], name: string): string {
  const index = args.indexOf(name);
  return index >= 0 && index + 1 < args.length ? args[index + 1] : "";
}

export function parseLinkSyncCliOptions(args: string[]): CliOptions {
  const mode = (valueAfter(args, "--mode") || "preview") as SyncMode;
  if (!( ["preview", "execute"] as string[]).includes(mode)) throw new Error(`Unsupported mode: ${mode}`);
  const file = valueAfter(args, "--file");
  if (!file) throw new Error("--file is required");
  const limitText = valueAfter(args, "--limit");
  const limit = limitText ? Number(limitText) : 0;
  if (!Number.isInteger(limit) || limit < 0) throw new Error("--limit must be a non-negative integer");
  const runId = valueAfter(args, "--run-id");
  if (runId && !/^[A-Za-z0-9_.-]{1,64}$/.test(runId)) throw new Error("Invalid --run-id");
  return {
    file: path.resolve(file),
    mode,
    runId,
    yes: args.includes("--yes"),
    noNotify: args.includes("--no-notify"),
    limit,
    ledgerPath: path.resolve(valueAfter(args, "--ledger") || "storage/1688-link-sync-ledger.jsonl"),
    resultsDir: path.resolve(valueAfter(args, "--results-dir") || "results/1688-link-sync"),
    artifactsDir: path.resolve(valueAfter(args, "--artifacts-dir") || "artifacts/1688-link-sync"),
  };
}

function previewResult(task: SyncTask): SyncResult {
  return {
    task_id: task.task_id,
    status: "preview",
    store_name: task.store_name,
    jushuitan_store_name: task.jushuitan_store_name,
    product_id: task.product_id,
    online_sku: task.online_sku,
    replacement_sku: task.replacement_sku,
    message: "Validated only; no browser or online operation was performed",
    recorded_at: new Date().toISOString(),
  };
}

function knownResult(task: SyncTask): SyncResult {
  return {
    task_id: task.task_id,
    status: "already_synced",
    store_name: task.store_name,
    jushuitan_store_name: task.jushuitan_store_name,
    product_id: task.product_id,
    online_sku: task.online_sku,
    replacement_sku: task.replacement_sku,
    category: "ledger_idempotency",
    message: "A prior verified Jushuitan link-sync success exists in the ledger",
    recorded_at: new Date().toISOString(),
  };
}

async function main(): Promise<void> {
  const options = parseLinkSyncCliOptions(process.argv.slice(2));
  if (options.mode === "execute" && !options.yes) throw new Error("execute mode requires --yes");
  const runId = options.runId || format(new Date(), "yyyyMMdd-HHmmss");
  const loaded = await loadSyncTasks(options.file);
  const tasks = options.limit > 0 ? loaded.slice(0, options.limit) : loaded;
  assertSyncTasksAllowed(options.mode, tasks);
  const successfulIds = await loadSuccessfulSyncTaskIds(options.ledgerPath);
  const known = tasks.filter((task) => successfulIds.has(task.task_id)).map(knownResult);
  const pending = tasks.filter((task) => !successfulIds.has(task.task_id));
  let results: SyncResult[];
  if (options.mode === "preview") {
    results = [...known, ...pending.map(previewResult)];
  } else if (!pending.length) {
    results = known;
  } else {
    const {runBrowserLinkSync} = await import("./1688-link-sync-browser");
    results = [...known, ...await runBrowserLinkSync(pending, {
      runId,
      ledgerPath: options.ledgerPath,
      artifactsDir: options.artifactsDir,
    })];
  }

  const resultPath = path.join(options.resultsDir, `${runId}.jsonl`);
  const summaryPath = path.join(options.resultsDir, `${runId}.summary.json`);
  await writeSyncResults(resultPath, results);
  const summary = {
    ...summarizeSyncResults(options.mode, results),
    run_id: runId,
    input_file: options.file,
    result_path: resultPath,
    summary_path: summaryPath,
    ledger_path: options.ledgerPath,
    notification_sent: null as boolean | null,
    finished_at: new Date().toISOString(),
  };
  if (!options.noNotify && options.mode === "execute") {
    summary.notification_sent = await sendDingTalkText(buildSyncNotification(runId, results));
  }
  await ensureDir(path.dirname(summaryPath));
  await fs.writeFile(summaryPath, `${JSON.stringify(summary, null, 2)}\n`, "utf8");
  console.log(JSON.stringify(summary, null, 2));
  if (summary.failed > 0) process.exitCode = 2;
}

main().catch((error) => {
  console.error(`1688 link sync failed: ${error instanceof Error ? error.message : String(error)}`);
  process.exitCode = 1;
});
