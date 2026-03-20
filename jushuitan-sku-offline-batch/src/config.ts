import "dotenv/config";
import path from "node:path";
import { format } from "date-fns";
import { z } from "zod";
import { PlatformKey, SUPPORTED_PLATFORM_KEYS } from "./types";

const envSchema = z.object({
  JST_LOGIN_URL: z.string().min(1, "JST_LOGIN_URL 未配置"),
  JST_PRODUCT_URL: z.string().min(1, "JST_PRODUCT_URL 未配置"),
  JST_USERNAME: z.string().min(1, "JST_USERNAME 未配置"),
  JST_PASSWORD: z.string().min(1, "JST_PASSWORD 未配置"),
  EXCEL_PATH: z.string().min(1, "EXCEL_PATH 未配置"),
  EXCEL_SHEET_NAME: z.string().optional(),
  TARGET_DATE: z.string().optional(),
  TARGET_PLATFORMS: z.string().default(SUPPORTED_PLATFORM_KEYS.join(",")),
  REPLACEMENT_ONLINE_SKU: z.string().default("txcj"),
  BATCH_SIZE: z.coerce.number().int().positive().default(50),
  MAX_PRODUCT_CODES: z.coerce.number().int().positive().optional(),
  HEADLESS: z
    .string()
    .default("false")
    .transform((value) => value.toLowerCase() === "true"),
  BROWSER_CHANNEL: z.enum(["chrome", "msedge"]).optional(),
  BROWSER_EXECUTABLE_PATH: z.string().optional(),
  SLOW_MO: z.coerce.number().int().nonnegative().default(200),
  LOGIN_WAIT_MS: z.coerce.number().int().positive().default(60000),
  SEARCH_WAIT_MS: z.coerce.number().int().positive().default(8000),
  MANUAL_LOGIN_TIMEOUT_MS: z.coerce.number().int().positive().default(180000),
  KEEP_BROWSER_OPEN_ON_ERROR: z
    .string()
    .default("true")
    .transform((value) => value.toLowerCase() === "true"),
  SAVE_STORAGE_STATE: z
    .string()
    .default("true")
    .transform((value) => value.toLowerCase() === "true"),
  STORAGE_STATE_PATH: z.string().default("./storage/jushuitan.json"),
});

function parsePlatforms(rawValue: string): PlatformKey[] {
  const keys = rawValue
    .split(",")
    .map((value) => value.trim().toLowerCase())
    .filter(Boolean);

  const invalidKeys = keys.filter(
    (key): key is string => !SUPPORTED_PLATFORM_KEYS.includes(key as PlatformKey),
  );

  if (invalidKeys.length > 0) {
    throw new Error(`TARGET_PLATFORMS 包含不支持的平台: ${invalidKeys.join(", ")}`);
  }

  return keys as PlatformKey[];
}

const parsedEnv = envSchema.parse(process.env);

export const appConfig = {
  loginUrl: parsedEnv.JST_LOGIN_URL,
  productUrl: parsedEnv.JST_PRODUCT_URL,
  username: parsedEnv.JST_USERNAME,
  password: parsedEnv.JST_PASSWORD,
  excelPath: path.resolve(parsedEnv.EXCEL_PATH),
  excelSheetName: parsedEnv.EXCEL_SHEET_NAME?.trim() || undefined,
  targetDate: parsedEnv.TARGET_DATE?.trim() || format(new Date(), "yyyy-MM-dd"),
  targetPlatforms: parsePlatforms(parsedEnv.TARGET_PLATFORMS),
  replacementOnlineSku: parsedEnv.REPLACEMENT_ONLINE_SKU.trim(),
  batchSize: parsedEnv.BATCH_SIZE,
  maxProductCodes: parsedEnv.MAX_PRODUCT_CODES,
  headless: parsedEnv.HEADLESS,
  browserChannel: parsedEnv.BROWSER_CHANNEL,
  browserExecutablePath: parsedEnv.BROWSER_EXECUTABLE_PATH?.trim() || undefined,
  slowMo: parsedEnv.SLOW_MO,
  loginWaitMs: parsedEnv.LOGIN_WAIT_MS,
  searchWaitMs: parsedEnv.SEARCH_WAIT_MS,
  manualLoginTimeoutMs: parsedEnv.MANUAL_LOGIN_TIMEOUT_MS,
  keepBrowserOpenOnError: parsedEnv.KEEP_BROWSER_OPEN_ON_ERROR,
  saveStorageState: parsedEnv.SAVE_STORAGE_STATE,
  storageStatePath: path.resolve(parsedEnv.STORAGE_STATE_PATH),
  resultsDir: path.resolve("results"),
  artifactsDir: path.resolve("artifacts"),
} as const;
