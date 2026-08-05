import { spawnSync } from "node:child_process";
import { readdir } from "node:fs/promises";
import { createRequire } from "node:module";
import path from "node:path";
import { fileURLToPath } from "node:url";

const require = createRequire(import.meta.url);
const projectRoot = path.dirname(fileURLToPath(import.meta.url));

function comparePaths(left, right) {
  return left < right ? -1 : left > right ? 1 : 0;
}

export async function discoverTestFiles(testRoot) {
  const entries = await readdir(testRoot, { withFileTypes: true });
  const files = [];

  for (const entry of entries) {
    const entryPath = path.join(testRoot, entry.name);
    if (entry.isDirectory()) {
      files.push(...await discoverTestFiles(entryPath));
    } else if (entry.isFile() && entry.name.endsWith(".test.ts")) {
      files.push(entryPath);
    }
  }

  return files.sort(comparePaths);
}

export async function runTests() {
  const testFiles = await discoverTestFiles(path.join(projectRoot, "tests"));
  if (testFiles.length === 0) {
    throw new Error("No tests/**/*.test.ts files were found");
  }

  const tsxCli = require.resolve("tsx/cli");
  const result = spawnSync(process.execPath, [tsxCli, "--test", ...testFiles], {
    cwd: projectRoot,
    stdio: "inherit",
  });

  if (result.error) {
    throw result.error;
  }
  return result.status ?? 1;
}

if (path.resolve(process.argv[1] ?? "") === fileURLToPath(import.meta.url)) {
  runTests().then(
    (status) => {
      process.exitCode = status;
    },
    (error) => {
      console.error(error instanceof Error ? error.message : error);
      process.exitCode = 1;
    },
  );
}
