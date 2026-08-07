import assert from "node:assert/strict";
import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import test from "node:test";
import {
  appendLedgerResult,
  assertTasksAllowedForMode,
  buildStructuredRowEvidence,
  buildOperationsMessages,
  buildProductGroupKey,
  buildTaskId,
  findIdentitySiblingRows,
  findMatchingRows,
  loadCleanupTasks,
  loadSuccessfulLedgerTaskIds,
  orderCleanupTasksForExecution,
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

test("product grouping ignores SKU but keeps store and product boundaries", () => {
  assert.equal(
    buildProductGroupKey(rawTask),
    buildProductGroupKey({ ...rawTask, online_sku: "OTHER-SKU" }),
  );
  assert.notEqual(
    buildProductGroupKey(rawTask),
    buildProductGroupKey({ ...rawTask, product_id: "OTHER-PRODUCT" }),
  );
  assert.notEqual(
    buildProductGroupKey(rawTask),
    buildProductGroupKey({ ...rawTask, store_name: "阿里巴巴-其他店铺" }),
  );
});

test("execution ordering keeps store and product tasks contiguous", () => {
  const tasks = [
    parseCleanupTask({ ...rawTask, product_id: "PRODUCT-B", online_sku: "SKU-1" }),
    parseCleanupTask({ ...rawTask, product_id: "PRODUCT-A", online_sku: "SKU-2" }),
    parseCleanupTask({ ...rawTask, product_id: "PRODUCT-B", online_sku: "SKU-3" }),
  ];

  assert.deepEqual(
    orderCleanupTasksForExecution(tasks).map((task) => task.product_id),
    ["PRODUCT-A", "PRODUCT-B", "PRODUCT-B"],
  );
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
  const headers = ["店铺名称", "商品ID", "平台店铺商品编码", "线上商品编码"];
  const rows = findMatchingRows(task, [
    buildStructuredRowEvidence(0, "matching row", headers, [
      "阿里巴巴-常州工莱家具", "1005537490740", "6166627859436", "CY001301N35",
    ]),
    buildStructuredRowEvidence(1, "other code", headers, [
      "阿里巴巴-常州工莱家具", "1005537490740", "other-code", "CY001301N35",
    ]),
  ]);
  assert.deepEqual(rows.map((row) => row.index), [0]);
});

test("real Jushuitan row fixture survives composite cells and header reordering", async () => {
  const fixturePath = path.join(process.cwd(), "tests", "fixtures", "wolai-jushuitan-row.json");
  const fixture = JSON.parse(await fs.readFile(fixturePath, "utf8")) as {
    source_evidence: {
      sha256: string;
      fields: {
        store_name: string;
        product_id: string;
        platform_store_item_code: string;
        online_sku: string;
        status: string;
        inventory_sync: string;
      };
    };
    snapshots: Array<{ headers: string[]; cells: string[]; row_text: string }>;
  };
  const task = parseCleanupTask({
    ...rawTask,
    store_name: fixture.source_evidence.fields.store_name,
    product_id: fixture.source_evidence.fields.product_id,
    platform_store_item_code: fixture.source_evidence.fields.platform_store_item_code,
    online_sku: fixture.source_evidence.fields.online_sku,
  });

  assert.equal(
    fixture.source_evidence.sha256,
    "ED4C6DF9E04900C8754C3B8CD03C7BA9B5C111AE357D069822A20F8346F07AF4",
  );
  assert.equal(fixture.source_evidence.fields.status, "上架");
  assert.match(fixture.source_evidence.fields.inventory_sync, /已开启同步/);

  for (const snapshot of fixture.snapshots) {
    const row = buildStructuredRowEvidence(0, snapshot.row_text, snapshot.headers, snapshot.cells);
    assert.deepEqual(row.columns, {
      store_name: fixture.source_evidence.fields.store_name,
      product_id: fixture.source_evidence.fields.product_id,
      online_sku: fixture.source_evidence.fields.online_sku,
      platform_store_item_code: fixture.source_evidence.fields.platform_store_item_code,
    }, snapshot.headers.join(" / "));
    assert.deepEqual(findMatchingRows(task, [row]).map((item) => item.index), [0]);
    assert.match(row.text, /上架/);
    assert.match(row.text, /已开启同步/);
  }
});

test("identity siblings prove the query loaded while the target platform code is absent", () => {
  const task = parseCleanupTask(rawTask);
  const headers = ["店铺名称", "商品ID", "平台店铺商品编码", "线上商品编码"];
  const rows = findIdentitySiblingRows(task, [
    buildStructuredRowEvidence(0, "sibling row", headers, [
      "阿里巴巴-常州工莱家具", "1005537490740", "other-code", "CY001301N35",
    ]),
    buildStructuredRowEvidence(1, "matching row", headers, [
      "阿里巴巴-常州工莱家具", "1005537490740", "6166627859436", "CY001301N35",
    ]),
    buildStructuredRowEvidence(2, "other store", headers, [
      "阿里巴巴-其他店铺", "1005537490740", "another-code", "CY001301N35",
    ]),
  ]);
  assert.deepEqual(rows.map((row) => row.index), [0]);
});

test("exact column matching rejects ABC1 versus ABC10 prefix collisions", () => {
  const task = parseCleanupTask({
    ...rawTask,
    online_sku: "ABC1",
    platform_store_item_code: "CODE1",
  });
  const headers = ["店铺名称", "商品ID", "平台店铺商品编码", "线上商品编码"];
  const rows = [
    buildStructuredRowEvidence(0, "prefix collision", headers, [
      task.store_name, task.product_id, "CODE10", "ABC10",
    ]),
  ];
  assert.deepEqual(findMatchingRows(task, rows), []);
  assert.deepEqual(findIdentitySiblingRows(task, rows), []);
});

test("missing structured identity columns fail closed", () => {
  const task = parseCleanupTask(rawTask);
  const row = buildStructuredRowEvidence(
    0,
    `${task.store_name} ${task.product_id} ${task.online_sku} ${task.platform_store_item_code}`,
    ["未知列"],
    [`${task.store_name} ${task.product_id} ${task.online_sku} ${task.platform_store_item_code}`],
  );
  assert.deepEqual(findMatchingRows(task, [row]), []);
  assert.deepEqual(findIdentitySiblingRows(task, [row]), []);
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

test("ledger recognizes verified success and verified target absence records", async () => {
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

  const absentTask = parseCleanupTask({ ...rawTask, platform_store_item_code: "already-gone" });
  await appendLedgerResult(ledger, {
    task_id: absentTask.task_id,
    status: "already_cleared",
    store_name: absentTask.store_name,
    product_id: absentTask.product_id,
    online_sku: absentTask.online_sku,
    platform_store_item_code: absentTask.platform_store_item_code,
    category: "verified_target_absent",
    recorded_at: new Date().toISOString(),
  });
  assert.deepEqual(
    [...await loadSuccessfulLedgerTaskIds(ledger)].sort(),
    [task.task_id, absentTask.task_id].sort(),
  );
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
