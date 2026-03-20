import ExcelJS from "exceljs";
import { appConfig } from "./config";
import { SourceRow } from "./types";

function normalizeCellValue(value: ExcelJS.CellValue): string {
  if (value === null || value === undefined) {
    return "";
  }

  if (value instanceof Date) {
    return value.toISOString().slice(0, 10);
  }

  if (typeof value === "object") {
    if ("text" in value && typeof value.text === "string") {
      return value.text.trim();
    }
    if ("result" in value && value.result !== undefined && value.result !== null) {
      return String(value.result).trim();
    }
  }

  return String(value).trim();
}

function normalizeDateString(value: string): string {
  if (!value) {
    return "";
  }

  return value.replace(/\//g, "-").slice(0, 10);
}

const HEADERS = {
  statDate: "统计日期新",
  productCode: "商品编码",
  productionStatus: "生产状态",
  publicStock: "公有可用数",
  factoryStock: "工厂剩余库存",
  totalStock: "总库存",
  offlineRemark: "下架备注",
  channelStock: "渠道专用仓库存",
  actionRemark: "处理说明",
  replaceProductCode: "可替换商品编码",
  priceDiff: "价格差",
  needImageChange: "是否换图1.是2.否",
  differenceDescription: "区别点描述",
} as const;

export async function loadFilteredSourceRows(): Promise<SourceRow[]> {
  const workbook = new ExcelJS.Workbook();
  await workbook.xlsx.readFile(appConfig.excelPath);

  const worksheet =
    (appConfig.excelSheetName
      ? workbook.getWorksheet(appConfig.excelSheetName)
      : workbook.getWorksheet(1) ?? workbook.worksheets[0]) ?? null;

  if (!worksheet) {
    const sheetNames = workbook.worksheets.map((sheet) => sheet.name).filter(Boolean);
    const sheetNameText = sheetNames.length > 0 ? sheetNames.join(", ") : "未识别到任何工作表";
    throw new Error(`Excel 工作表不存在，请检查 EXCEL_SHEET_NAME 配置。当前工作表: ${sheetNameText}`);
  }

  const headerRow = worksheet.getRow(1);
  const headers = new Map<string, number>();

  headerRow.eachCell((cell, colNumber) => {
    headers.set(normalizeCellValue(cell.value), colNumber);
  });

  const requiredHeaders = Object.values(HEADERS);
  const missingHeaders = requiredHeaders.filter((header) => !headers.has(header));

  if (missingHeaders.length > 0) {
    throw new Error(`Excel 缺少列: ${missingHeaders.join(", ")}`);
  }

  const rows: SourceRow[] = [];

  for (let rowNumber = 2; rowNumber <= worksheet.rowCount; rowNumber += 1) {
    const row = worksheet.getRow(rowNumber);
    const current = (header: string) => normalizeCellValue(row.getCell(headers.get(header) ?? 0).value);

    const sourceRow: SourceRow = {
      rowNumber,
      statDate: normalizeDateString(current(HEADERS.statDate)),
      productCode: current(HEADERS.productCode),
      productionStatus: current(HEADERS.productionStatus),
      publicStock: current(HEADERS.publicStock),
      factoryStock: current(HEADERS.factoryStock),
      totalStock: current(HEADERS.totalStock),
      offlineRemark: current(HEADERS.offlineRemark),
      channelStock: current(HEADERS.channelStock),
      actionRemark: current(HEADERS.actionRemark),
      replaceProductCode: current(HEADERS.replaceProductCode),
      priceDiff: current(HEADERS.priceDiff),
      needImageChange: current(HEADERS.needImageChange),
      differenceDescription: current(HEADERS.differenceDescription),
    };

    if (!sourceRow.productCode) {
      continue;
    }

    if (sourceRow.statDate !== appConfig.targetDate) {
      continue;
    }

    if (sourceRow.actionRemark !== "下架") {
      continue;
    }

    if (sourceRow.offlineRemark.includes("京喜需下架")) {
      continue;
    }

    rows.push(sourceRow);
  }

  return rows;
}
