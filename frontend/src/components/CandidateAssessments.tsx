import type { ExplanationAssessment } from "../types";
import { shortHash } from "../format";
import { isExplanationAssessment } from "../assessment";

const statusLabels = {
  supported: "证据支持",
  refuted: "证据反驳",
  unknown: "尚未确定",
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
  ) : <span className="muted">尚无对应证据</span>;
  return (
    <section className="candidate-assessments" aria-label="候选解释评估">
      <h3>候选解释评估</h3>
      {entries === "invalid" ? (
        <p className="inline-notice" role="status">候选评估结构需要复核，请核读原始报告与证据。</p>
      ) : (
        <>
          <p className="muted assessment-boundary">逐项比较已取得证据；结论适用于本次调查范围。</p>
          <div className="assessment-list">
            {entries.map((item, index) => (
              <article className={`assessment-card assessment-${item.status}`} key={`${item.explanation_id}:${index}`}>
                <div className="assessment-heading">
                  <h4>{item.explanation}</h4>
                  <span className={`assessment-status ${item.status}`}>{statusLabels[item.status]}</span>
                </div>
                <div className="assessment-evidence"><strong>支持证据</strong>{references(item.support_evidence_ids)}</div>
                <div className="assessment-evidence"><strong>反驳证据</strong>{references(item.counter_evidence_ids)}</div>
                {!!item.unknowns.length && (
                  <div className="assessment-unknowns"><strong>未知与待核项</strong><ul>{item.unknowns.map((text, n) => <li key={n}>{text}</li>)}</ul></div>
                )}
                <details><summary>核查依据与方法</summary><ul>{item.checks.map((text, n) => <li key={n}>{text}</li>)}</ul></details>
              </article>
            ))}
          </div>
        </>
      )}
    </section>
  );
}
