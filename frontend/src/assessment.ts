import type { ExplanationAssessment } from "./types";

const fields = new Set([
  "explanation_id", "explanation", "status", "support_evidence_ids",
  "counter_evidence_ids", "unknowns", "checks",
]);
const textList = (value: unknown, limit: number, maximum: number) =>
  Array.isArray(value) && value.length <= limit &&
  value.every((item) => typeof item === "string" && item.length > 0 && item.length <= maximum);
const references = (value: unknown) =>
  textList(value, 8, 240) && Array.isArray(value) && new Set(value).size === value.length;

export function isExplanationAssessment(value: unknown): value is ExplanationAssessment {
  if (!value || typeof value !== "object" || Array.isArray(value)) return false;
  const item = value as Record<string, unknown>;
  return Object.keys(item).every((key) => fields.has(key)) &&
    typeof item.explanation_id === "string" && /^[a-z][a-z0-9_]{0,63}$/.test(item.explanation_id) &&
    typeof item.explanation === "string" && item.explanation.length > 0 && item.explanation.length <= 1400 &&
    ["supported", "refuted", "unknown"].some((status) => item.status === status) &&
    references(item.support_evidence_ids) && references(item.counter_evidence_ids) &&
    textList(item.unknowns, 8, 600) && textList(item.checks, 8, 600) &&
    Array.isArray(item.checks) && item.checks.length > 0 &&
    (item.status !== "supported" || (item.support_evidence_ids as string[]).length > 0) &&
    (item.status !== "refuted" || (item.counter_evidence_ids as string[]).length > 0) &&
    (item.status !== "unknown" || (item.unknowns as string[]).length > 0);
}
