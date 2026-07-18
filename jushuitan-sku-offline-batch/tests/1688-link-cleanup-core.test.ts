import assert from "node:assert/strict";
import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import test from "node:test";
import {
  appendLedgerResult,
  assertTasksAllowedForMode,
  buildOperationsMessages,
  buildTaskId,
  findMatchingRows,
  loadCleanupTasks,
  loadSuccessfulLedgerTaskIds,
  parseCleanupTask,
} from "../src/1688-link-cleanup-core";

const rawTask = {
  source_status: "success" as const,
  store_name: "阿里巴巴-常州工莱家具",
  platform: "Alibaba",
  product_id: "1005537490740",
  online_sku: "CY001301N35",
  platform_store_item_code: "6166627859436",
  handling: "全渠道下架",
};

test("task identity includes the platform store item code", () => {
  const first = buildTaskId(rawTask);
  const second = buildTaskId({ ...rawTask, platform_store_item_code: "other-code" });
  assert.notEqual(first, second);
});

test("parser rejects a non-Alibaba task", () => {
  assert.throws(() => parseCleanupTask({ ...rawTask, platform: "Taobao" }), /Only Alibaba/);
});

test("execute rejects a task that has not succeeded on 1688", () => {
  const task = parseCleanupTask({ ...rawTask, source_status: "pending_1688" });
  assert.throws(() => assertTasksAllowedForMode("execute", [task]), /already verified/);
  assert.doesNotThrow(() => assertTasksAllowedForMode("probe", [task]));
});

test("row matching requires store, product, SKU and platform code", () => {
  const task = parseCleanupTask(rawTask);
  const rows = findMatchingRows(task, [
    {
      index: 0,
      text: "阿里巴巴-常州工莱家具 商品ID: 1005537490740 6166627859436 CY001301N35",
    },
    {
      index: 1,
      text: "阿里巴巴-常州工莱家具 商品ID: 1005537490740 other-code CY001301N35",
    },
  ]);
  assert.deepEqual(rows.map((row) => row.index), [0]);
});

test("JSONL loading dedupes only identical four-field task identities", async () => {
  const dir = await fs.mkdtemp(path.join(os.tmpdir(), "jst-cleanup-"));
  const file = path.join(dir, "tasks.jsonl");
  await fs.writeFile(
    file,
    [rawTask, rawTask, { ...rawTask, platform_store_item_code: "another-code" }]
      .map((item) => JSON.stringify(item))
      .join("\n"),
    "utf8",
  );
  const tasks = await loadCleanupTasks(file);
  assert.equal(tasks.length, 2);
});

test("ledger recognizes only verified success records", async () => {
  const dir = await fs.mkdtemp(path.join(os.tmpdir(), "jst-ledger-"));
  const ledger = path.join(dir, "ledger.jsonl");
  const task = parseCleanupTask(rawTask);
  await appendLedgerResult(ledger, {
    task_id: task.task_id,
    status: "failed",
    store_name: task.store_name,
    product_id: task.product_id,
    online_sku: task.online_sku,
    platform_store_item_code: task.platform_store_item_code,
    recorded_at: new Date().toISOString(),
  });
  assert.equal((await loadSuccessfulLedgerTaskIds(ledger)).size, 0);
  await appendLedgerResult(ledger, {
    task_id: task.task_id,
    status: "success",
    store_name: task.store_name,
    product_id: task.product_id,
    online_sku: task.online_sku,
    platform_store_item_code: task.platform_store_item_code,
    recorded_at: new Date().toISOString(),
  });
  assert.deepEqual([...await loadSuccessfulLedgerTaskIds(ledger)], [task.task_id]);
});

test("operations messages contain per-store counts and readable failure details", () => {
  const task = parseCleanupTask(rawTask);
  const messages = buildOperationsMessages("run-001", [
    {
      task_id: task.task_id,
      status: "success",
      store_name: task.store_name,
      product_id: task.product_id,
      online_sku: task.online_sku,
      platform_store_item_code: task.platform_store_item_code,
      recorded_at: new Date().toISOString(),
    },
    {
      task_id: `${task.task_id}-failed`,
      status: "failed",
      store_name: task.store_name,
      product_id: "2002",
      online_sku: "SKU-B",
      platform_store_item_code: "CODE-B",
      category: "task_not_found",
      evidence_path: "D:/private/technical.json",
      recorded_at: new Date().toISOString(),
    },
  ]);

  assert.match(messages[0], /常州工莱家具：成功1，已完成0，异常1/);
  assert.match(messages[1], /ID 2002 \| SKU SKU-B \| 聚水潭无匹配/);
  assert.doesNotMatch(messages.join("\n"), /private|technical\.json/);
});
