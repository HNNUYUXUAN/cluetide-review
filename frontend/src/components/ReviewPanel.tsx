import { useEffect, useState } from "react";
import type { ExplanationAssessment, Investigation } from "../types";
import { shortHash, statusName } from "../format";
import { isExplanationAssessment } from "../assessment";
import { uiError } from "../api";
import EmptyState from "./EmptyState";

interface Props {
  investigation: Investigation | null;
  onReview: (reviewer: string, comment: string) => Promise<void>;
  onVersion: (
    author: string,
    correction: string,
    parent: number,
    correctedSummary?: string,
    claimReplacements?: Record<number, string>,
    assessmentReplacements?: Record<number, ExplanationAssessment>,
    correctedClassification?: string,
  ) => Promise<void>;
}
export default function ReviewPanel({
  investigation,
  onReview,
  onVersion,
}: Props) {
  const versions = investigation?.versions ?? [];
  const head = Number(
    investigation?.registry?.head_version_id ??
      Math.max(0, ...versions.map((version) => version.version_id)),
  );
  const headRevision = Math.max(
    1,
    versions.findIndex((version) => version.version_id === head) + 1,
  );
  const revisionName = (id: number) =>
    `v${versions.findIndex((version) => version.version_id === id) + 1}`;
  const [reviewer, setReviewer] = useState("local-reviewer");
  const [comment, setComment] = useState("");
  const [author, setAuthor] = useState("local-author");
  const [correction, setCorrection] = useState("");
  const [correctedSummary, setCorrectedSummary] = useState(
    investigation?.report?.conclusion?.summary ??
      investigation?.agent?.report?.summary ??
      "",
  );
  const claims =
    investigation?.report?.conclusion?.claims ??
    investigation?.agent?.report?.claims ??
    [];
  const [claimIndex, setClaimIndex] = useState(-1);
  const [correctedClaim, setCorrectedClaim] = useState("");
  const sourceAssessments = investigation?.report?.conclusion?.assessments ?? investigation?.agent?.report?.assessments;
  const assessments = Array.isArray(sourceAssessments) && sourceAssessments.length <= 12 && sourceAssessments.every(isExplanationAssessment) ? sourceAssessments : [];
  const [assessmentIndex, setAssessmentIndex] = useState(-1);
  const [correctedAssessment, setCorrectedAssessment] = useState("");
  const [correctedClassification, setCorrectedClassification] = useState("");
  const [parent, setParent] = useState(head);
  const [busy, setBusy] = useState<"review" | "version" | null>(null);
  const [notice, setNotice] = useState("");
  useEffect(() => {
    setParent(head);
  }, [investigation?.id, head]);
  useEffect(() => {
    setCorrectedSummary(
      investigation?.report?.conclusion?.summary ??
        investigation?.agent?.report?.summary ??
        "",
    );
    setClaimIndex(-1);
    setCorrectedClaim("");
    setAssessmentIndex(-1);
    setCorrectedAssessment("");
    setCorrectedClassification("");
  }, [investigation?.id, head]);
  useEffect(() => {
    setNotice("");
  }, [investigation?.id]);
  if (!investigation?.evidence || !versions.length)
    return (
      <EmptyState title="Prepare a report for review">
        Complete an investigation or import a verified public evidence ZIP to record a review and create a corrected version.
      </EmptyState>
    );
  const submit = async (event: React.FormEvent, kind: "review" | "version") => {
    event.preventDefault();
    let replacement: Record<number, ExplanationAssessment> | undefined;
    if (kind === "version" && assessmentIndex >= 0) {
      try {
        const parsed: unknown = JSON.parse(correctedAssessment);
        if (!isExplanationAssessment(parsed)) throw new Error("The assessment JSON must meet the public schema and length limits.");
        if (parsed.explanation_id !== assessments[assessmentIndex]?.explanation_id)
          throw new Error("The explanation ID must match the selected assessment.");
        replacement = { [assessmentIndex]: parsed };
      } catch (error) {
        setNotice(error instanceof SyntaxError ? "Invalid assessment JSON. Check it before submitting." : error instanceof Error ? error.message : "The assessment fields need review.");
        return;
      }
    }
    setBusy(kind);
    setNotice("");
    try {
      if (kind === "review") {
        await onReview(reviewer.trim(), comment.trim());
        setComment("");
        setNotice("Review recorded and bound to the reviewed version and content hash.");
      } else {
        await onVersion(
          author.trim(),
          correction.trim(),
          parent,
          correctedSummary.trim(),
          claimIndex >= 0 ? { [claimIndex]: correctedClaim.trim() } : undefined,
          replacement,
          correctedClassification || undefined,
        );
        setCorrection("");
        setNotice("Corrected version created. The revised summary is saved in the report, and the original version remains traceable.");
      }
    } catch (error) {
      setNotice(uiError(error, "Local registration could not be completed."));
    } finally {
      setBusy(null);
    }
  };
  return (
    <section className="panel review-panel">
      <h2>Local collaborative review</h2>
      <p className="muted">
        This demonstration uses a local registry simulation. Two local roles do not establish independent verification.
      </p>
      <p className="muted">Original evidence: saved report text and review comments retain their source language. Edits create a traceable new version.</p>
      <ol className="version-flow">
        <li>v1 original report</li>
        <li className="current">Review comments</li>
        <li>v{Math.max(2, headRevision)} Corrected report</li>
      </ol>
      <div className="review-grid">
        <form
          className="subpanel"
          onSubmit={(event) => void submit(event, "review")}
        >
          <h3>Submit review</h3>
          <label>
            Reviewer
            <input
              value={reviewer}
              onChange={(event) => setReviewer(event.target.value)}
              required
              maxLength={100}
              disabled={!!busy}
            />
          </label>
          <label>
            Review comments
            <textarea
              value={comment}
              onChange={(event) => setComment(event.target.value)}
              required
              maxLength={4000}
              rows={4}
              placeholder="Record what the evidence supports and what needs correction."
              disabled={!!busy}
            />
          </label>
          <button className="button primary" disabled={!!busy} type="submit">
            {busy === "review" ? "Recording…" : "Record review"}
          </button>
        </form>
        <form
          className="subpanel"
          onSubmit={(event) => void submit(event, "version")}
        >
          <h3>Create corrected version</h3>
          <label>
            Author
            <input
              value={author}
              onChange={(event) => setAuthor(event.target.value)}
              required
              maxLength={100}
              disabled={!!busy}
            />
          </label>
          <label>
            Parent version
            <select
              value={parent}
              onChange={(event) => setParent(Number(event.target.value))}
              disabled={!!busy}
            >
              {versions.map((version) => (
                <option key={version.version_id} value={version.version_id}>
                  {revisionName(version.version_id)} ·{" "}
                  {version.version_id === head ? "Current version" : "Preserved"}
                </option>
              ))}
            </select>
          </label>
          <label className="summary-editor">
            Revised summary
            <textarea
              aria-label="Revised summary"
              aria-describedby="summary-edit-help"
              value={correctedSummary}
              onChange={(event) => setCorrectedSummary(event.target.value)}
              required
              maxLength={1800}
              rows={4}
              disabled={!!busy}
            />
            <span className="muted" id="summary-edit-help">
              The new summary is saved in the corrected report. The original version is preserved.
            </span>
          </label>
          {!!claims.length && (
            <label className="summary-editor">
              Claim to correct
              <select
                value={claimIndex}
                disabled={!!busy}
                onChange={(event) => {
                  const index = Number(event.target.value);
                  setClaimIndex(index);
                  setCorrectedClaim(index >= 0 ? claims[index].text : "");
                }}
              >
                <option value={-1}>Keep current claims</option>
                {claims.map((claim, index) => (
                  <option key={index} value={index}>
                    Item  {index + 1}  ·  {claim.text.slice(0, 70)}
                  </option>
                ))}
              </select>
            </label>
          )}
          {claimIndex >= 0 && (
            <label className="summary-editor">
              Revised claim
              <textarea
                aria-label="Revised claim"
                value={correctedClaim}
                onChange={(event) => setCorrectedClaim(event.target.value)}
                required
                maxLength={1400}
                rows={4}
                disabled={!!busy}
              />
              <span className="muted">
                Revise the text while preserving this claim’s original evidence references.
              </span>
            </label>
          )}
          {!!assessments.length && <details className="assessment-editor">
            <summary>Correct an assessment and classification</summary>
            <p className="muted">Select an explanation and revise its public JSON. Evidence references must point to successfully collected observations. Governance assessments and the finding classification must remain consistent.</p>
            <label>
              Assessment to correct
              <select aria-label="Assessment to correct" value={assessmentIndex} disabled={!!busy}
                onChange={(event) => {
                  const index = Number(event.target.value);
                  setAssessmentIndex(index);
                  setCorrectedAssessment(index >= 0 ? JSON.stringify(assessments[index], null, 2) : "");
                }}>
                <option value={-1}>Keep current assessments</option>
                {assessments.map((item, index) => <option key={item.explanation_id} value={index}>Item  {index + 1}  ·  {item.explanation.slice(0, 60)}</option>)}
              </select>
            </label>
            {assessmentIndex >= 0 && <label>
              Revised assessment JSON
              <textarea aria-label="Revised assessment JSON" className="mono"
                value={correctedAssessment} onChange={(event) => setCorrectedAssessment(event.target.value)}
                required maxLength={64000} rows={12} disabled={!!busy} />
            </label>}
            <label>
              Revised classification
              <select aria-label="Revised classification" value={correctedClassification}
                onChange={(event) => setCorrectedClassification(event.target.value)} disabled={!!busy}>
                <option value="">Keep current classification ({statusName(typeof investigation.report?.conclusion?.classification === "string" ? investigation.report.conclusion.classification : undefined)})</option>
                <option value="governance_explained">Governance execution explained</option>
                <option value="unresolved">Cause unresolved</option>
                <option value="needs_review">Needs review</option>
              </select>
            </label>
          </details>}
          <label>
            Correction note
            <textarea
              value={correction}
              onChange={(event) => setCorrection(event.target.value)}
              required
              maxLength={4000}
              rows={3}
              placeholder="Explain the evidence and reason for this correction."
              disabled={!!busy}
            />
          </label>
          <button className="button primary" disabled={!!busy} type="submit">
            {busy === "version" ? "Creating…" : `Create v${headRevision + 1}`}
          </button>
        </form>
      </div>
      {notice && (
        <p className="inline-notice" role="status">
          {notice}
        </p>
      )}
      <div className="table-scroll">
        <table>
          <thead>
            <tr>
              <th>Version</th>
              <th>Author</th>
              <th>Parent version</th>
              <th>Content hash</th>
              <th>Status</th>
            </tr>
          </thead>
          <tbody>
            {[...versions]
              .sort((a, b) => b.version_id - a.version_id)
              .map((version) => (
                <tr key={version.version_id}>
                  <td>{revisionName(version.version_id)}</td>
                  <td>{version.author}</td>
                  <td>
                    {version.parent_version_id
                      ? revisionName(version.parent_version_id)
                      : "—"}
                  </td>
                  <td className="mono" title={version.content_hash}>
                    {shortHash(version.content_hash, 8)}
                  </td>
                  <td>
                    <span
                      className={
                        version.version_id === head
                          ? "version-current"
                          : "version-retained"
                      }
                    >
                      {version.version_id === head ? "Current version" : "Preserved"}
                    </span>
                  </td>
                </tr>
              ))}
          </tbody>
        </table>
      </div>
      {!!investigation.reviews?.length && (
        <div className="review-history">
          <h3>Review records</h3>
          {investigation.reviews.map((review, index) => (
            <div className="review-record" key={review.review_id ?? index}>
              <strong>{review.reviewer}</strong>
              <span className="muted">
                {revisionName(review.version_id ?? versions[0].version_id)}
              </span>
              <p>{review.comment ?? review.decision ?? "Review recorded"}</p>
            </div>
          ))}
        </div>
      )}
      <p className="page-note">A hash verifies unchanged content. It does not establish factual accuracy.</p>
    </section>
  );
}
