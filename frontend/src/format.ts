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
export const statusName = (value?: string): string =>
  ({
    completed: "已完成",
    complete: "完整",
    empty: "空结果",
    partial: "部分完成",
    error: "读取失败",
    stopped: "已停止",
    budget_exhausted: "预算已耗尽",
    running: "调查中",
    pending: "等待中",
    governance_explained: "具有治理解释",
    needs_review: "需要复核",
    unresolved: "待解释",
    ok: "成功",
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
    get_receipt: "获取交易回执",
    get_transaction: "获取交易详情",
    get_token_state: "读取代币状态",
    get_governance_source: "补查公开资料",
    final_report: "输出引用报告",
  })[String(name)] ?? String(name ?? "观察");
