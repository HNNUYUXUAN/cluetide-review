import { useState } from "react";
import type { CitationValidation, VerifiedImport } from "../types";
import Icon from "./Icon";
import { shortHash } from "../format";
import { uiError } from "../api";
import CandidateAssessments from "./CandidateAssessments";
import InvestigationScope from "./InvestigationScope";

// Read only the API's independent result, never report-supplied validation.
// A malformed result cannot become a successful structural check.
function citationResult(value: unknown): CitationValidation | "unavailable" | null {
  if (value === undefined) return null;
  if (!value || typeof value !== "object" || Array.isArray(value))
    return "unavailable";
  const item = value as Record<string, unknown>;
  if (
    typeof item.status !== "string" ||
    !["valid", "needs_review", "not_applicable"].includes(item.status) ||
    typeof item.scope !== "string" ||
    item.scope.length === 0 ||
    item.scope.length > 2000 ||
    !Array.isArray(item.issues) ||
    item.issues.length > 64 ||
    !item.issues.every(
      (issue) =>
        issue &&
        typeof issue === "object" &&
        !Array.isArray(issue) &&
        typeof issue.code === "string" &&
        issue.code.length <= 96 &&
        typeof issue.message === "string" &&
        issue.message.length <= 1000 &&
        (issue.location === undefined ||
          (typeof issue.location === "string" && issue.location.length <= 240)),
    ) ||
    (item.status !== "needs_review" && item.issues.length !== 0)
  )
    return "unavailable";
  return item as unknown as CitationValidation;
}

export default function ImportPanel({
  imported,
  onImport,
}: {
  imported: VerifiedImport | null;
  onImport: (file: File) => Promise<void>;
}) {
  const [file, setFile] = useState<File | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [selectedEvidence, setSelectedEvidence] = useState<{ hash: string; id: string } | null>(null);
  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    if (!file) return;
    if (
      !file.name.toLowerCase().endsWith(".zip") ||
      file.size > 16 * 1024 * 1024
    ) {
      setError("Select a public evidence ZIP no larger than 16 MiB.");
      return;
    }
    setError("");
    setBusy(true);
    try {
      await onImport(file);
    } catch (error) {
      setError(uiError(error, "Import could not be completed."));
    } finally {
      setBusy(false);
    }
  };
  const verified = imported?.validation?.verified === true;
  const qualityFlags = imported?.report_quality?.flags ?? [];
  const citations = citationResult(imported?.citation_validation);
  const report = imported?.report;
  const selectedId = selectedEvidence?.hash === imported?.manifest_hash ? selectedEvidence?.id : null;
  const referencedEvidence = selectedId ? (
    (Array.isArray(report?.agent?.evidence) ? report.agent.evidence : []).find((item) => item?.evidence_id === selectedId) ??
    (Array.isArray(imported?.evidence?.transfers) ? imported.evidence.transfers : []).find((item) => item?.evidence_id === selectedId)
  ) : undefined;
  const caseLabel =
    typeof report?.case_id === "string" && report.case_id.length <= 64
      ? shortHash(report.case_id, 8)
      : "Unknown case";
  const revisionLabel =
    typeof report?.revision === "number" &&
    Number.isSafeInteger(report.revision) &&
    report.revision >= 1
      ? `v${report.revision}`
      : "Unknown version";
  const summary =
    typeof report?.conclusion?.summary === "string"
      ? report.conclusion.summary
      : "This version has no available summary.";
  const corrections = Array.isArray(report?.corrections)
    ? report.corrections.filter(
        (item) =>
          item && typeof item === "object" && typeof item.text === "string",
      )
    : [];
  return (
    <section className="panel import-panel">
      <h2>Import public evidence ZIP</h2>
      <form className="import-form" onSubmit={(event) => void submit(event)}>
        <label className="file-input">
          <Icon name="file" />
          <span>{file?.name ?? "Select a .zip file"}</span>
          <input
            type="file"
            accept=".zip,application/zip"
            aria-label="Select a public evidence ZIP file"
            onChange={(event) => {
              setFile(event.target.files?.[0] ?? null);
              setError("");
            }}
            disabled={busy}
          />
        </label>
        <button
          className="button primary"
          type="submit"
          disabled={!file || busy}
        >
          {busy ? "Verifying…" : "Verify and import"}
        </button>
      </form>
      {error && (
        <p className="error-banner" role="alert">
          {error}
        </p>
      )}
      {verified && (
        <div className="success-banner" role="status">
          <Icon name="check" size={34} />
          <div>
            <h3>File hashes and manifest verified</h3>
            <p>Integrity verified. Findings still require review.</p>
          </div>
        </div>
      )}
      {imported && (
        <>
          <div className="import-summary">
            <div>
              <span className="metric-label">Case</span>
              <strong>{caseLabel}</strong>
            </div>
            <div>
              <span className="metric-label">Version</span>
              <strong>{revisionLabel}</strong>
            </div>
            <div>
              <span className="metric-label">Source</span>
              <strong>Second-client import</strong>
            </div>
            <div>
              <span className="metric-label">Data</span>
              <strong>Public evidence snapshot</strong>
            </div>
          </div>
          {imported.manifest_hash && (
            <p className="manifest-hash mono" title={imported.manifest_hash}>
              manifest SHA-256 · {shortHash(imported.manifest_hash, 18)}
            </p>
          )}
          <p className="inline-notice import-boundary">
            Independent on-chain commitment:{" "}
            {imported.registry_verification.startsWith("unknown")
              ? "Not read; authenticity remains unknown."
              : imported.registry_verification}
            <br />
            This second client and local server run on the same host with controlled demo roles.
          </p>
          {!!qualityFlags.length && (
            <div className="inline-notice quality-notice" role="status">
              <strong>Automated text checks require review</strong>
              <ul>
                {qualityFlags.map((flag, index) => (
                  <li key={`${flag.code}:${index}`}>{flag.message}</li>
                ))}
              </ul>
              <p>
                The server rechecks imported content while preserving original report bytes. Base factual judgments on the public evidence.
              </p>
            </div>
          )}
          {citations === "unavailable" ? (
            <p className="inline-notice citation-notice muted" role="status">
              Citation structure checks are unavailable. Review the original evidence.
            </p>
          ) : citations?.status === "needs_review" ? (
            <div className="inline-notice quality-notice citation-notice" role="status">
              <strong>Evidence citation structure needs review</strong>
              <ul>
                {citations.issues.map((issue, index) => (
                  <li key={`${issue.code}:${index}`}>{issue.message}</li>
                ))}
              </ul>
              <p>
                Checks are independently computed after import. Original report bytes are preserved. Structural checks do not establish facts or source authenticity.
              </p>
            </div>
          ) : citations?.status === "valid" ? (
            <p className="inline-notice citation-notice muted" role="status">
              Citation structure checks passed. They verify reference relationships, not facts or source authenticity.
            </p>
          ) : citations?.status === "not_applicable" ? (
            <p className="inline-notice citation-notice muted" role="status">
              This bundle has no applicable citation structure checks. Findings still require review.
            </p>
          ) : null}
          <InvestigationScope value={report?.investigation_scope} />
          <details className="imported-report-preview">
            <summary>View imported version  {revisionLabel}  report and evidence</summary>
            <h3>Imported version summary</h3>
            <p className="muted">Original evidence: report text, citations, and corrections retain their source language.</p>
            <p>{summary}</p>
            <CandidateAssessments value={report?.conclusion?.assessments}
              onEvidence={(id) => setSelectedEvidence({hash: imported.manifest_hash, id})} />
            {selectedId && <div className="evidence-inspector">
              <h4 className="mono">{selectedId}</h4>
              {referencedEvidence ? <pre>{(JSON.stringify(referencedEvidence, null, 2) ?? "null").slice(0, 80000)}</pre> :
                <p className="muted">This citation was not found in the imported Agent evidence or Transfer records. Review the original evidence ZIP.</p>}
            </div>}
            <ul>
              {(Array.isArray(report?.conclusion?.claims)
                ? report.conclusion.claims
                : []
              ).map((claim, index) => (
                <li key={index}>
                  {typeof claim?.text === "string"
                    ? claim.text
                    : "Incomplete claim format. Review the original report."}{" "}
                  <span className="mono">
                    [
                    {Array.isArray(claim?.evidence_ids)
                      ? claim.evidence_ids
                          .filter((id) => typeof id === "string")
                          .join(", ")
                      : "Incomplete citation field"}
                    ]
                  </span>
                </li>
              ))}
            </ul>
            {corrections.map((correction, index) => (
              <p key={index}>Correction: {correction.text}</p>
            ))}
            <details>
              <summary>View public evidence JSON for this version</summary>
              <pre>
                {JSON.stringify(imported.evidence, null, 2).slice(0, 80000)}
              </pre>
              {JSON.stringify(imported.evidence).length > 80000 && (
                <p className="muted">
                  Browser preview is limited. Complete data is available in the imported ZIP.
                </p>
              )}
            </details>
          </details>
          {!!imported.manifest?.files && (
            <details>
              <summary>View file verification manifest</summary>
              <div className="table-scroll">
                <table>
                  <thead>
                    <tr>
                      <th>File</th>
                      <th>Bytes</th>
                      <th>SHA-256</th>
                    </tr>
                  </thead>
                  <tbody>
                    {Object.entries(imported.manifest.files).map(
                      ([name, item]) => (
                        <tr key={name}>
                          <td>{name}</td>
                          <td>{item.size}</td>
                          <td className="mono" title={item.sha256}>
                            {shortHash(item.sha256, 12)}
                          </td>
                        </tr>
                      ),
                    )}
                  </tbody>
                </table>
              </div>
            </details>
          )}
        </>
      )}
      {!imported && (
        <p className="muted import-help">
          Evidence bundles contain a manifest, public source data, normalized evidence, and a cited report. Import checks file paths, sizes, and SHA-256 hashes.
        </p>
      )}
    </section>
  );
}
