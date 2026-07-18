import assert from "node:assert/strict";
import test from "node:test";
import {
  normalizeStoreSelectionError,
  openStorePickerWithFallback,
  StoreSelectionError,
} from "../src/store-picker";

test("store picker fallback stops after the first candidate that opens the picker", async () => {
  const attempts: string[] = [];
  let pickerOpen = false;

  const result = await openStorePickerWithFallback({
    candidates: ["input", "wrapper", "form-item"],
    getOpenPicker: async () => (pickerOpen ? "dialog" : null),
    clickCandidate: async (candidate) => {
      attempts.push(candidate);
      if (candidate === "wrapper") {
        pickerOpen = true;
      }
      return true;
    },
  });

  assert.deepEqual(attempts, ["input", "wrapper"]);
  assert.deepEqual(result, {
    picker: "dialog",
    attemptedSelectors: ["input", "wrapper"],
  });
});

test("store picker fallback reuses an already-open picker", async () => {
  let clicked = false;
  const result = await openStorePickerWithFallback({
    candidates: ["input"],
    getOpenPicker: async () => "existing-dialog",
    clickCandidate: async () => {
      clicked = true;
      return true;
    },
  });

  assert.equal(clicked, false);
  assert.deepEqual(result, { picker: "existing-dialog", attemptedSelectors: [] });
});

test("store picker errors preserve unavailable and mismatch categories", () => {
  const unavailable = normalizeStoreSelectionError(
    new StoreSelectionError("store_picker_unavailable", "picker did not open", {
      attempted_selectors: ["input"],
    }),
  );
  const mismatch = normalizeStoreSelectionError(
    new Error("Exact Jushuitan store was not found or selected: 阿里巴巴-常州工莱家具"),
  );

  assert.equal(unavailable.category, "store_picker_unavailable");
  assert.deepEqual(unavailable.details.attempted_selectors, ["input"]);
  assert.equal(mismatch.category, "store_mismatch");
});
