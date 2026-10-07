export const shortHash = (value: string | undefined, length = 6): string =>
  !value
    ? "—"
    : value.length > length * 2 + 2
      ? `${value.slice(0, length + 2)}…${value.slice(-length)}`
      : value;
export const integer = (value: number | string | undefined): string =>
  value === undefined
    ? "—"
    : String(value).replace(/\B(?=(\d{3})+(?!\d))/g, ",");

// Navigation labels are separate from the original titles preserved in evidence.
export function caseTitle(title?: string, id?: string): string {
  if (title && !/[\u3400-\u9fff]/u.test(title)) return title;
  const reference = `${id ?? ""} ${title ?? ""}`;
  if (/uniswap|\bUNI\b/i.test(reference)) return "UNI governance transfer";
  if (/euler/i.test(reference)) return "Euler incident investigation";
  return id ? `Investigation ${id.slice(0, 10)}` : "Ethereum event investigation";
}
export const statusName = (value?: string): string =>
  ({
    completed: "Completed",
    complete: "Complete",
    empty: "Empty result",
    partial: "Partial",
    error: "Read failed",
    stopped: "Stopped",
    budget_exhausted: "Budget exhausted",
    running: "Investigating",
    pending: "Pending",
    governance_explained: "Governance explanation supported",
    needs_review: "Needs review",
    unresolved: "Unresolved",
    ok: "Success",
  })[value ?? ""] ??
  value ??
  "—";

// Keep uint256 values as decimal strings throughout. No Number/parseFloat rounding.
export function tokenAmount(
  raw: string | undefined,
  decimals?: number | null,
): string {
  if (!raw || !/^\d+$/.test(raw)) return "—";
  if (
    decimals === undefined ||
    decimals === null ||
    !Number.isInteger(decimals) ||
    decimals < 0 ||
    decimals > 255
  )
    return `${integer(raw)} raw`;
  if (!decimals) return integer(raw);
  const padded = raw.padStart(decimals + 1, "0");
  const whole = padded.slice(0, -decimals);
  const fraction = padded.slice(-decimals).replace(/0+$/, "");
  return `${integer(whole)}${fraction ? `.${fraction}` : ""}`;
}

export const toolName = (name?: unknown) =>
  ({
    get_receipt: "Read transaction receipt",
    get_transaction: "Read transaction details",
    get_token_state: "Read token state",
    get_governance_source: "Check public sources",
    final_report: "Generate cited report",
  })[String(name)] ?? String(name ?? "Observation");
