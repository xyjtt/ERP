import assert from "node:assert/strict";
import test from "node:test";
import {
  assertSyncTasksAllowed,
  buildSyncTaskId,
  groupSyncTasksByStore,
  parseSyncTask,
  summarizeSyncResults,
} from "../src/1688-link-sync-core";

function rawTask(overrides: Record<string, unknown> = {}) {
  return {
    source: "1688_sku_replace",
    source_status: "success",
    store_name: "阿里巴巴-常州工莱家具",
    platform: "Alibaba",
    product_id: "732745838005",
    online_sku: "DSG005015N11V01",
    replacement_sku: "DSG005015N11V02",
    platform_store_item_code: "10001",
    handling: "全渠道替换",
    action: "sync_by_link",
    ...overrides,
  };
}

test("replacement sync task validates identity and operation", () => {
  const payload = rawTask();
  const taskId = buildSyncTaskId(payload as ReturnType<typeof rawTask> & {
    store_name: string;
    product_id: string;
    online_sku: string;
    replacement_sku: string;
  });
  const parsed = parseSyncTask({...payload, task_id: taskId});
  assert.equal(parsed.task_id, taskId);
  assert.equal(parsed.action, "sync_by_link");
});

test("replacement sync rejects offline handling", () => {
  assert.throws(
    () => parseSyncTask(rawTask({handling: "全渠道下架"})),
    /全渠道替换|Invalid input/,
  );
});

test("store grouping dedupes product IDs but keeps per-SKU tasks", () => {
  const first = parseSyncTask(rawTask());
  const second = parseSyncTask(rawTask({
    online_sku: "DSG005015N11V03",
    replacement_sku: "DSG005015N11V04",
  }));
  const groups = groupSyncTasksByStore([first, second]);
  assert.equal(groups.length, 1);
  assert.deepEqual(groups[0].product_ids, ["732745838005"]);
  assert.equal(groups[0].tasks.length, 2);
});

test("store grouping chunks large product ID lists", () => {
  const tasks = Array.from({length: 51}, (_, index) => parseSyncTask(rawTask({
    product_id: String(1000 + index),
    online_sku: `OLD-${index}`,
    replacement_sku: `NEW-${index}`,
  })));
  const groups = groupSyncTasksByStore(tasks, 50);
  assert.deepEqual(groups.map((group) => group.product_ids.length), [50, 1]);
  assert.deepEqual(groups.map((group) => group.batch_index), [1, 2]);
});

test("execute accepts only verified 1688 replacement statuses", () => {
  const valid = parseSyncTask(rawTask({source_status: "already_replaced"}));
  assert.doesNotThrow(() => assertSyncTasksAllowed("execute", [valid]));
  const pending = parseSyncTask(rawTask({source_status: "pending_1688"}));
  assert.throws(() => assertSyncTasksAllowed("execute", [pending]), /verified/);
});

test("sync summary keeps success and failure counts", () => {
  const summary = summarizeSyncResults("execute", [
    {task_id: "1", status: "success", store_name: "s", product_id: "p", online_sku: "o", replacement_sku: "n", recorded_at: "now"},
    {task_id: "2", status: "failed", store_name: "s", product_id: "p", online_sku: "x", replacement_sku: "y", recorded_at: "now"},
  ]);
  assert.equal(summary.success, 1);
  assert.equal(summary.failed, 1);
});
