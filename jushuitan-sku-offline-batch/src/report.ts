import path from "node:path";
import ExcelJS from "exceljs";
import { appConfig } from "./config";
import { PlatformRunResult } from "./types";
import { ensureDir, sanitizeFileName, writeTextFile } from "./utils";

export async function writePlatformResultFiles(
  runId: string,
  result: PlatformRunResult,
): Promise<void> {
  const baseDir = path.join(appConfig.resultsDir, runId, result.platform);
  await ensureDir(baseDir);

  const popupWorkbook = new ExcelJS.Workbook();
  const popupSheet = popupWorkbook.addWorksheet("批量更新结果");
  popupSheet.addRow(["平台", "批次序号", "商品编码", "弹窗提示", "记录时间"]);

  for (const record of result.updatePopups) {
    popupSheet.addRow([
      record.platform,
      record.batchIndex,
      record.productCodes.join(","),
      record.message,
      record.capturedAt,
    ]);
  }

  await popupWorkbook.xlsx.writeFile(path.join(baseDir, "update-popups.xlsx"));

  const remainingWorkbook = new ExcelJS.Workbook();
  const remainingSheet = remainingWorkbook.addWorksheet("复查仍可查询数据");
  remainingSheet.addRow(["平台", "原始行文本"]);

  for (const row of result.remainingRows) {
    remainingSheet.addRow([row.platform, row.rawText]);
  }

  await remainingWorkbook.xlsx.writeFile(path.join(baseDir, "remaining-rows.xlsx"));

  const summary = [
    `平台: ${result.platform}`,
    `计划处理商品编码数: ${result.totalRequested}`,
    `批量更新提示记录数: ${result.updatePopups.length}`,
    `复查仍可查询到的数据行数: ${result.remainingRows.length}`,
  ].join("\n");

  await writeTextFile(path.join(baseDir, "summary.txt"), summary);

  const groupMessage = result.remainingRows.length
    ? [
        `${result.platform} 复查后仍可查询到以下数据，请人工跟进：`,
        ...result.remainingRows.map((row) => row.rawText),
      ].join("\n")
    : `${result.platform} 已执行线上商品编码批量更新，复查未查询到剩余数据。`;

  await writeTextFile(
    path.join(baseDir, `${sanitizeFileName(result.platform)}-group-message.txt`),
    groupMessage,
  );
}

