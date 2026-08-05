import assert from "node:assert/strict";
import { mkdtemp, mkdir, rm, writeFile } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import test from "node:test";
import { discoverTestFiles } from "../run-tests.mjs";

test("test discovery recursively includes only tests/**/*.test.ts", async () => {
  const root = await mkdtemp(path.join(os.tmpdir(), "jst-test-runner-"));
  try {
    await mkdir(path.join(root, "nested", "deeper"), { recursive: true });
    await Promise.all([
      writeFile(path.join(root, "root.test.ts"), ""),
      writeFile(path.join(root, "nested", "child.test.ts"), ""),
      writeFile(path.join(root, "nested", "deeper", "leaf.test.ts"), ""),
      writeFile(path.join(root, "nested", "ignored.ts"), ""),
      writeFile(path.join(root, "nested", "ignored.test.js"), ""),
    ]);

    const discovered = await discoverTestFiles(root);

    assert.deepEqual(
      discovered.map((file) => path.relative(root, file).split(path.sep).join("/")),
      ["nested/child.test.ts", "nested/deeper/leaf.test.ts", "root.test.ts"],
    );
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});
