import assert from "node:assert/strict";
import test from "node:test";

process.env.JST_LOGIN_URL ||= "https://example.test/login";
process.env.JST_PRODUCT_URL ||= "https://example.test/product";
process.env.EXCEL_PATH ||= "test.xlsx";

async function loadActivationHelper() {
  return (await import("../src/1688-link-sync-browser")).activateWithFallback;
}

test("tab activation falls back after an ineffective click", async () => {
  const activateWithFallback = await loadActivationHelper();
  let active = false;
  const attempts: string[] = [];

  const activated = await activateWithFallback(
    async () => active,
    [
      async () => {
        attempts.push("playwright-click");
      },
      async () => {
        attempts.push("native-click");
        active = true;
      },
      async () => {
        attempts.push("unexpected");
      },
    ],
    {pollAttempts: 1, pollDelayMs: 0},
  );

  assert.equal(activated, true);
  assert.deepEqual(attempts, ["playwright-click", "native-click"]);
});

test("tab activation returns immediately when the target is already active", async () => {
  const activateWithFallback = await loadActivationHelper();
  let clicked = false;
  const activated = await activateWithFallback(
    async () => true,
    [async () => {
      clicked = true;
    }],
    {pollAttempts: 1, pollDelayMs: 0},
  );

  assert.equal(activated, true);
  assert.equal(clicked, false);
});
