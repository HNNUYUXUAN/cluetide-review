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
      kind: "Transfer 事件",
      status: "ok",
      payload: row,
    }));
  return [...agentEntries, ...transfers];
}

const kindTitle = (kind: string) =>
  ({
    receipt: "Ethereum 交易回执",
    transaction: "Ethereum 交易详情",
    governance_source: "治理来源",
    token_state: "代币状态",
    get_receipt: "Ethereum 交易回执",
    get_transaction: "Ethereum 交易详情",
    get_governance_source: "治理来源",
    get_token_state: "代币状态",
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
        payload.category === "incident_postmortem" ? "事件复盘来源" : kindTitle(entry.kind),
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
      <h2>证据索引</h2>
      {!entries.length && (
        <p className="muted">
          没有可引用的证据。空响应或失败不会生成事实依据。
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
              查看公开来源
            </a>
          )}
          <pre>{JSON.stringify(selected.payload, null, 2).slice(0, 80000)}</pre>
          {JSON.stringify(selected.payload).length > 80000 && (
            <p className="muted">浏览器预览已限长；完整原始数据保留在 ZIP。</p>
          )}
        </div>
      )}
    </section>
  );
}
