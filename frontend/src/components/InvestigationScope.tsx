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
  if (!scope) return <p className="inline-notice" role="status">Investigation scope fields need review. Inspect the original record.</p>;
  const pendingLabel = scope.execution_status === "completed" && !scope.observed_transaction_hashes.length ? "Not applicable" : "Incomplete";
  return (
    <section className="panel investigation-scope" aria-label="Investigation scope">
      <h2>Investigation scope</h2>
      <div className="scope-summary">
        <div><span className="metric-label">Selected transaction</span><strong className="mono" title={scope.selected_transaction_hash ?? undefined}>{scope.selected_transaction_hash ? shortHash(scope.selected_transaction_hash, 12) : "None selected"}</strong></div>
        <div><span className="metric-label">Investigated transactions</span><strong className="mono" title={scope.investigated_transaction_hash ?? undefined}>{scope.investigated_transaction_hash ? shortHash(scope.investigated_transaction_hash, 12) : pendingLabel}</strong></div>
        <div><span className="metric-label">Transactions observed in window</span><strong>{scope.observed_transaction_hashes.length} transactions</strong></div>
        <div><span className="metric-label">Remaining transactions to investigate</span><strong>{scope.uninvestigated_transaction_hashes.length} transactions</strong></div>
      </div>
      <p><span className="muted">Original evidence: </span>{scope.statement}</p>
      <details><summary>View transaction selection and scope record</summary>
        <p>{scope.selection_reason}</p>
        <dl className="scope-record"><dt>Selection rationale</dt><dd>{scope.selection_basis}</dd><dt>Selected transaction</dt><dd className="mono">{scope.selected_transaction_hash ?? "None selected"}</dd><dt>Investigated transactions</dt><dd className="mono">{scope.investigated_transaction_hash ?? pendingLabel}</dd></dl>
        {!!scope.uninvestigated_transaction_hashes.length && <><h3>Remaining transactions to investigate</h3><ul className="scope-hashes">{scope.uninvestigated_transaction_hashes.slice(0, 20).map((tx, index) => <li className="mono" key={`${tx}:${index}`}>{tx}</li>)}</ul>{scope.uninvestigated_transaction_hashes.length > 20 && <p className="muted">The first 20 transactions are shown here. The complete list is in the evidence ZIP.</p>}</>}
      </details>
    </section>
  );
}
