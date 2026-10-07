import { shortHash, statusName } from "../format";
import type { Investigation, ToolEvidence } from "../types";

export function evidenceEntries(investigation: Investigation): ToolEvidence[] {
  const agentEntries = investigation.agent?.evidence ?? [];
  const transfers = (investigation.evidence?.transfers ?? [])
    .filter(
      (row) =>
        !agentEntries.some((item) => item.evidence_id === row.evidence_id),
    )
    .map((row) => ({
      evidence_id: row.evidence_id,
      kind: "Transfer event",
      status: "ok",
      payload: row,
    }));
  return [...agentEntries, ...transfers];
}

const kindTitle = (kind: string) =>
  ({
    receipt: "Ethereum transaction receipt",
    transaction: "Ethereum transaction details",
    governance_source: "Governance source",
    token_state: "Token state",
    get_receipt: "Ethereum transaction receipt",
    get_transaction: "Ethereum transaction details",
    get_governance_source: "Governance source",
    get_token_state: "Token state",
  })[kind] ?? kind;
function sourceDetails(entry: ToolEvidence) {
  const payload =
    entry.payload && typeof entry.payload === "object"
      ? (entry.payload as Record<string, unknown>)
      : {};
  let url: URL | null = null;
  try {
    if (typeof payload.url === "string") {
      const candidate = new URL(payload.url);
      if (candidate.protocol === "https:" || candidate.protocol === "http:")
        url = candidate;
    }
  } catch {
    /* A source without a valid public URL remains inspectable. */
  }
  return {
    title:
      typeof payload.title === "string" ? payload.title :
        payload.category === "incident_postmortem" ? "Incident analysis source" : kindTitle(entry.kind),
    subtitle: url?.hostname ?? shortHash(entry.evidence_id, 16),
    url,
  };
}
export default function EvidenceIndex({
  investigation,
  onSelect,
  selectedId,
  detailed = false,
}: {
  investigation: Investigation;
  onSelect: (id: string) => void;
  selectedId?: string | null;
  detailed?: boolean;
}) {
  const entries = evidenceEntries(investigation);
  const selected = entries.find((item) => item.evidence_id === selectedId);
  return (
    <section className="evidence-index">
      <h2>Evidence index</h2>
      <p className="muted">Original evidence: source titles and payloads retain their original language and content.</p>
      {!entries.length && (
        <p className="muted">
          No evidence is available to cite. Empty or failed responses do not establish facts.
        </p>
      )}
      <div className="evidence-rows">
        {entries.map((entry, index) => {
          const source = sourceDetails(entry);
          return (
            <button
              type="button"
              className={`evidence-row ${entry.evidence_id === selectedId ? "active" : ""}`}
              key={`${entry.evidence_id}:${index}`}
              onClick={() => onSelect(entry.evidence_id)}
            >
              <span className="evidence-symbol">E{index + 1}</span>
              <span>
                <strong>{source.title}</strong>
                <span className="evidence-caption">
                  {source.subtitle} · {statusName(entry.status)}
                </span>
              </span>
            </button>
          );
        })}
      </div>
      {detailed && selected && (
        <div className="evidence-inspector">
          <h3>{selected.evidence_id}</h3>
          {sourceDetails(selected).url && (
            <a
              className="text-button"
              href={sourceDetails(selected).url!.href}
              target="_blank"
              rel="noopener noreferrer"
            >
              Open public source
            </a>
          )}
          <pre>{JSON.stringify(selected.payload, null, 2).slice(0, 80000)}</pre>
          {JSON.stringify(selected.payload).length > 80000 && (
            <p className="muted">Browser preview is limited. The complete original data is preserved in the ZIP.</p>
          )}
        </div>
      )}
    </section>
  );
}
