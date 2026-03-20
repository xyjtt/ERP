export const SUPPORTED_PLATFORM_KEYS = [
  "douyin",
  "kuaishou",
  "pinduoduo",
  "taobao",
  "tmall",
  "xiaohongshu",
] as const;

export type PlatformKey = (typeof SUPPORTED_PLATFORM_KEYS)[number];

export interface SourceRow {
  rowNumber: number;
  statDate: string;
  productCode: string;
  productionStatus: string;
  publicStock: string;
  factoryStock: string;
  totalStock: string;
  offlineRemark: string;
  channelStock: string;
  actionRemark: string;
  replaceProductCode: string;
  priceDiff: string;
  needImageChange: string;
  differenceDescription: string;
}

export interface PlatformBatch {
  platform: PlatformKey;
  productCodes: string[];
}

export interface UpdatePopupRecord {
  platform: PlatformKey;
  batchIndex: number;
  productCodes: string[];
  message: string;
  capturedAt: string;
}

export interface RemainingRow {
  platform: PlatformKey;
  rawText: string;
}

export interface PlatformRunResult {
  platform: PlatformKey;
  totalRequested: number;
  updatePopups: UpdatePopupRecord[];
  remainingRows: RemainingRow[];
}

