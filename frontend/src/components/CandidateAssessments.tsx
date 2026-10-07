import type { ExplanationAssessment } from "../types";
import { shortHash } from "../format";
import { isExplanationAssessment } from "../assessment";

const statusLabels = {
  supported: "Supported by evidence",
  refuted: "Refuted by evidence",
  unknown: "Unknown",
};
function assessments(value: unknown): ExplanationAssessment[] | "invalid" | null {
  if (value === undefined) return null;
  if (!Array.isArray(value) || value.length > 12) return "invalid";
  if (!value.every(isExplanationAssessment)) return "invalid";
  return value as ExplanationAssessment[];
}

export default function CandidateAssessments({
  value,
  onEvidence,
  evidenceLabel = (id: string) => shortHash(id, 12),
}: {
  value: unknown;
  onEvidence?: (id: string) => void;
  evidenceLabel?: (id: string) => string;
}) {
  const entries = assessments(value);
  if (!entries || (Array.isArray(entries) && entries.length === 0)) return null;
  const references = (ids: string[]) => ids.length ? ids.map((id, index) =>
    onEvidence ? (
      <button className="citation" type="button" key={`${id}:${index}`}
        onClick={() => onEvidence(id)} title={id}>
        [{evidenceLabel(id)}]
      </button>
    ) : <span className="assessment-reference mono" key={`${id}:${index}`} title={id}>{id}</span>
  ) : <span className="muted">No matching evidence yet</span>;
  return (
    <section className="candidate-assessments" aria-label="Possible explanations">
      <h3>Possible explanations</h3>
      {entries === "invalid" ? (
        <p className="inline-notice" role="status">The assessment structure needs review. Inspect the original report and evidence.</p>
      ) : (
        <>
          <p className="muted assessment-boundary">Original evidence: assessments retain the report’s source language. Findings apply to this investigation scope.</p>
          <div className="assessment-list">
            {entries.map((item, index) => (
              <article className={`assessment-card assessment-${item.status}`} key={`${item.explanation_id}:${index}`}>
                <div className="assessment-heading">
                  <h4>{item.explanation}</h4>
                  <span className={`assessment-status ${item.status}`}>{statusLabels[item.status]}</span>
                </div>
                <div className="assessment-evidence"><strong>Supporting evidence</strong>{references(item.support_evidence_ids)}</div>
                <div className="assessment-evidence"><strong>Counterevidence</strong>{references(item.counter_evidence_ids)}</div>
                {!!item.unknowns.length && (
                  <div className="assessment-unknowns"><strong>Unknowns and open checks</strong><ul>{item.unknowns.map((text, n) => <li key={n}>{text}</li>)}</ul></div>
                )}
                <details><summary>Basis and method</summary><ul>{item.checks.map((text, n) => <li key={n}>{text}</li>)}</ul></details>
              </article>
            ))}
          </div>
        </>
      )}
    </section>
  );
}
