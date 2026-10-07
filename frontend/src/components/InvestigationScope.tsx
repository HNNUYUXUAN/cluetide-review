import type { InvestigationScope as ScopeDocument } from "../types";
import { shortHash } from "../format";

const hash = (value: unknown) => typeof value === "string" && /^0x[0-9a-fA-F]{64}$/.test(value);
const hashes = (value: unknown) => Array.isArray(value) && value.length <= 10000 && value.every(hash);
function scopeDocument(value: unknown): ScopeDocument | null {
  if (!value || typeof value !== "object" || Array.isArray(value)) return null;
  const item = value as Record<string, unknown>;
  if (!(
    (item.selected_transaction_hash === null || hash(item.selected_transaction_hash)) &&
    (item.investigated_transaction_hash === null || hash(item.investigated_transaction_hash)) &&
    hashes(item.uninvestigated_transaction_hashes) && hashes(item.observed_transaction_hashes) &&
    hashes(item.alerted_transaction_hashes) &&
    ["selected_alert_ids", "selected_evidence_ids"].every((key) =>
      Array.isArray(item[key]) && item[key].length <= 10000 &&
      item[key].every((id: unknown) => typeof id === "string" && id.length > 0 && id.length <= 240)) &&
    ["selection_basis", "selection_reason", "statement"].every((key) =>
      typeof item[key] === "string" && item[key].length <= 2000)
  )) return null;
  return item as unknown as ScopeDocument;
}

export default function InvestigationScope({ value }: { value: unknown }) {
  if (value === undefined) return null;
  const scope = scopeDocument(value);
  if (!scope) return <p className="inline-notice" role="status">调查范围字段需要复核，请核读原始记录。</p>;
  const pendingLabel = scope.execution_status === "completed" && !scope.observed_transaction_hashes.length ? "不适用" : "尚未完成";
  return (
    <section className="panel investigation-scope" aria-label="本次调查范围">
      <h2>本次调查范围</h2>
      <div className="scope-summary">
        <div><span className="metric-label">选定交易</span><strong className="mono" title={scope.selected_transaction_hash ?? undefined}>{scope.selected_transaction_hash ? shortHash(scope.selected_transaction_hash, 12) : "未选定"}</strong></div>
        <div><span className="metric-label">已补查交易</span><strong className="mono" title={scope.investigated_transaction_hash ?? undefined}>{scope.investigated_transaction_hash ? shortHash(scope.investigated_transaction_hash, 12) : pendingLabel}</strong></div>
        <div><span className="metric-label">窗口观测交易</span><strong>{scope.observed_transaction_hashes.length} 笔</strong></div>
        <div><span className="metric-label">其余待补查交易</span><strong>{scope.uninvestigated_transaction_hashes.length} 笔</strong></div>
      </div>
      <p>{scope.statement}</p>
      <details><summary>查看交易选择与范围记录</summary>
        <p>{scope.selection_reason}</p>
        <dl className="scope-record"><dt>选择依据</dt><dd>{scope.selection_basis}</dd><dt>选定交易</dt><dd className="mono">{scope.selected_transaction_hash ?? "未选定"}</dd><dt>已补查交易</dt><dd className="mono">{scope.investigated_transaction_hash ?? pendingLabel}</dd></dl>
        {!!scope.uninvestigated_transaction_hashes.length && <><h3>其余待补查交易</h3><ul className="scope-hashes">{scope.uninvestigated_transaction_hashes.slice(0, 20).map((tx, index) => <li className="mono" key={`${tx}:${index}`}>{tx}</li>)}</ul>{scope.uninvestigated_transaction_hashes.length > 20 && <p className="muted">此处显示前 20 笔，完整列表见证据 ZIP。</p>}</>}
      </details>
    </section>
  );
}
