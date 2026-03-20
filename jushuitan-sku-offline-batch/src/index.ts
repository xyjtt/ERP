import path from "node:path";
import { format } from "date-fns";
import { appConfig } from "./config";
import { loadFilteredSourceRows } from "./excel";
import { runJushuitanFlow } from "./jushuitan";
import { writePlatformResultFiles } from "./report";
import { ensureDir, writeTextFile } from "./utils";

async function main(): Promise<void> {
  const runId = format(new Date(), "yyyyMMdd-HHmmss");

  await ensureDir(appConfig.resultsDir);
  await ensureDir(appConfig.artifactsDir);

  const sourceRows = await loadFilteredSourceRows();

  if (sourceRows.length === 0) {
    const message = `未找到满足条件的数据。执行日期: ${appConfig.targetDate}`;
    await writeTextFile(path.join(appConfig.resultsDir, runId, "empty.txt"), message);
    console.log(message);
    return;
  }

  console.log(`已筛选到 ${sourceRows.length} 条待处理数据，执行日期 ${appConfig.targetDate}`);

  const results = await runJushuitanFlow(runId, sourceRows);

  for (const result of results) {
    await writePlatformResultFiles(runId, result);
  }

  console.log(`执行完成，结果目录: ${path.join(appConfig.resultsDir, runId)}`);
}

main().catch((error) => {
  console.error("执行失败:", error);
  process.exitCode = 1;
});

