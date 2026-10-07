import type { Preset } from "../types";

export const isPreset = (value: unknown): value is Preset => {
  if (!value || typeof value !== "object" || Array.isArray(value)) return false;
  const item = value as Record<string, unknown>;
  return typeof item.case_id === "string" && /^[A-Za-z0-9_-]{1,64}$/.test(item.case_id) &&
    typeof item.title === "string" && item.title.length > 0 && item.title.length <= 200 &&
    typeof item.address === "string" && /^0x[0-9a-fA-F]{40}$/.test(item.address) &&
    typeof item.token_address === "string" && /^0x[0-9a-fA-F]{40}$/.test(item.token_address) &&
    typeof item.from_block === "number" && Number.isSafeInteger(item.from_block) && item.from_block >= 0 &&
    typeof item.to_block === "number" && Number.isSafeInteger(item.to_block) && item.to_block >= item.from_block &&
    item.to_block - item.from_block < 2000;
};
