import assert from "node:assert/strict";
import test from "node:test";

process.env.JST_LOGIN_URL ||= "https://example.test/login";
process.env.JST_PRODUCT_URL ||= "https://example.test/product";
process.env.EXCEL_PATH ||= "test.xlsx";

async function loadSelectionHelper() {
  return (await import("../src/1688-link-cleanup-browser")).activateRowSelectionWithFallback;
}

async function loadEmptyQueryHelper() {
  return (await import("../src/1688-link-cleanup-browser")).isVerifiedEmptyCleanupQuery;
}

test("row selection stops after the first action accepted by the custom checkbox", async () => {
  const activateRowSelectionWithFallback = await loadSelectionHelper();
  let selected = false;
  const attempts: string[] = [];

  const result = await activateRowSelectionWithFallback(
    async () => selected,
    [
      async () => attempts.push("input-check"),
      async () => {
        attempts.push("wrapper-click");
        selected = true;
      },
      async () => attempts.push("unexpected"),
    ],
  );

  assert.equal(result, true);
  assert.deepEqual(attempts, ["input-check", "wrapper-click"]);
});

test("row selection remains fail closed when no action is accepted", async () => {
  const activateRowSelectionWithFallback = await loadSelectionHelper();
  let waits = 0;

  const result = await activateRowSelectionWithFallback(
    async () => false,
    [
      async () => { throw new Error("unsupported"); },
      async () => undefined,
    ],
    async () => { waits += 1; },
  );

  assert.equal(result, false);
  assert.equal(waits, 2);
});

test("explicit zero rows are accepted only after exact filter values are read back", async () => {
  const isVerifiedEmptyCleanupQuery = await loadEmptyQueryHelper();
  const task = { product_id: "1013994516595", online_sku: "CJ003171N68V01" };

  assert.equal(isVerifiedEmptyCleanupQuery({
    productValue: task.product_id,
    onlineSkuValue: task.online_sku,
    explicitEmpty: true,
  }, task), true);
  assert.equal(isVerifiedEmptyCleanupQuery({
    productValue: task.product_id,
    onlineSkuValue: "",
    explicitEmpty: true,
  }, task), false);
  assert.equal(isVerifiedEmptyCleanupQuery({
    productValue: task.product_id,
    onlineSkuValue: task.online_sku,
    explicitEmpty: false,
  }, task), false);
});
