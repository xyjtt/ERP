export type StoreSelectionCategory = "store_picker_unavailable" | "store_mismatch";

export interface StoreSelectionErrorDetails {
  attempted_selectors?: string[];
  [key: string]: unknown;
}

export class StoreSelectionError extends Error {
  constructor(
    readonly category: StoreSelectionCategory,
    message: string,
    readonly details: StoreSelectionErrorDetails = {},
  ) {
    super(message);
    this.name = "StoreSelectionError";
  }
}

export async function openStorePickerWithFallback<T>(options: {
  candidates: readonly string[];
  getOpenPicker: () => Promise<T | null>;
  clickCandidate: (candidate: string) => Promise<boolean>;
}): Promise<{ picker: T; attemptedSelectors: string[] } | null> {
  const alreadyOpen = await options.getOpenPicker();
  if (alreadyOpen) {
    return { picker: alreadyOpen, attemptedSelectors: [] };
  }

  const attemptedSelectors: string[] = [];
  for (const candidate of options.candidates) {
    const clicked = await options.clickCandidate(candidate).catch(() => false);
    if (!clicked) {
      continue;
    }
    attemptedSelectors.push(candidate);
    const picker = await options.getOpenPicker();
    if (picker) {
      return { picker, attemptedSelectors };
    }
  }

  return null;
}

export function normalizeStoreSelectionError(error: unknown): StoreSelectionError {
  if (error instanceof StoreSelectionError) {
    return error;
  }

  const message = error instanceof Error ? error.message : String(error);
  if (
    /exact jushuitan store was not found or selected|exact store selection verification failed|store filter is not exact/i.test(
      message,
    )
  ) {
    return new StoreSelectionError("store_mismatch", message);
  }

  return new StoreSelectionError("store_picker_unavailable", message);
}
