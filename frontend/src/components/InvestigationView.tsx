import type { Investigation } from "../types";
import { integer, shortHash, statusName, tokenAmount } from "../format";
import AgentTrace from "./AgentTrace";
import EvidenceIndex, { evidenceEntries } from "./EvidenceIndex";
import EmptyState from "./EmptyState";
import Icon from "./Icon";
import CandidateAssessments from "./CandidateAssessments";
import InvestigationScope from "./InvestigationScope";
import { uiError } from "../api";

export type InvestigationTab = "overview" | "evidence" | "trace" | "versions";
interface Props {
  investigation: Investigation | null;
  busy: boolean;
  tab: InvestigationTab;
  selectedId: string | null;
  onEvidence: (id: string) => void;
}
export default function InvestigationView({
  investigation,
  busy,
  tab,
  selectedId,
  onEvidence,
}: Props) {
  if (!investigation?.evidence)
    return (
      <>
      <InvestigationScope value={investigation?.investigation_scope ?? investigation?.report?.investigation_scope} />
      <EmptyState
        title={
          busy
            ? "Collecting and investigating"
            : investigation
              ? `Investigation · ${statusName(investigation.status)}`
              : "Make large transfers explainable"
        }
      >
        {busy
          ? "Reading the bounded window and selecting follow-up tools from observed evidence. Stop ends the server-side investigation."
          : investigation
            ? uiError(investigation.error, "The task state is preserved. You can start a new investigation within a bounded window.")
            : "Select a public case or enter a bounded, finalized block window to start a read-only investigation."}
      </EmptyState>
      </>
    );
  const evidence = investigation.evidence;
  const coverage = evidence.coverage;
  const coverageComplete = coverage.status === "complete" || coverage.status === "empty";
  const hasFinalizedAnchor = typeof evidence.finalized_anchor?.number === "number" &&
    Number.isSafeInteger(evidence.finalized_anchor.number) &&
    typeof evidence.finalized_anchor.block_hash === "string" && /^0x[0-9a-fA-F]{64}$/.test(evidence.finalized_anchor.block_hash);
  const amount = (Array.isArray(evidence.alerts) ? evidence.alerts : [])
    .map((alert) => alert.observed_value_raw)
    .filter((value): value is string => typeof value === "string" && /^(0|[1-9][0-9]{0,77})$/.test(value))
    .reduce<string | undefined>((largest, value) =>
      largest === undefined || BigInt(value) > BigInt(largest) ? value : largest, undefined);
  const symbol =
    typeof evidence.metadata?.symbol === "string"
      ? evidence.metadata.symbol
      : "raw";
  const report =
    investigation.report?.conclusion ?? investigation.agent?.report;
  const qualityFlags = investigation.report?.quality_validation?.flags ?? [];
  const entries = evidenceEntries(investigation);
  const evidenceLabel = (id: string) => {
    const index = entries.findIndex((entry) => entry.evidence_id === id);
    return index < 0 ? shortHash(id, 8) : `E${index + 1}`;
  };
  if (tab === "trace")
    return <AgentTrace agent={investigation.agent} executionMode={investigation.report?.execution_mode} detailed />;
  if (tab === "evidence")
    return (
      <>
        <EvidenceIndex
          investigation={investigation}
          selectedId={selectedId}
          onSelect={onEvidence}
          detailed
        />
        <section className="panel raw-panel">
          <h2>Original public data</h2>
          <p className="muted">
            Original RPC observations, source materials, and coverage status are preserved in the evidence bundle.
          </p>
          <details>
            <summary>View evidence JSON</summary>
            <pre>{JSON.stringify(evidence, null, 2).slice(0, 80000)}</pre>
            {JSON.stringify(evidence).length > 80000 && (
              <p className="muted">Browser preview is limited. Complete data is in the ZIP.</p>
            )}
          </details>
        </section>
      </>
    );
  return (
    <>
      <p className="inline-notice">Original evidence: saved findings, source annotations, and review comments retain their source language.</p>
      <InvestigationScope value={investigation.investigation_scope ?? investigation.report?.investigation_scope} />
      <section className="metrics-band" aria-label="Scope and data coverage">
        <div>
          <span className="metric-label">Block window</span>
          <strong>
            {integer(evidence.request.from_block)} –{" "}
            {integer(evidence.request.to_block)}
          </strong>
          <p>
            {integer(
              evidence.request.to_block - evidence.request.from_block + 1,
            )}{" "}
            blocks · {hasFinalizedAnchor ? "Finalized anchor obtained" : "Finalized anchor needs review"}
          </p>
        </div>
        <div>
          <span className="metric-label">Query coverage</span>
          <strong>
            {coverage.completed_queries} / {coverage.planned_queries} queries
          </strong>
          <p
            className={
              coverage.status === "complete" || coverage.status === "empty"
                ? ""
                : "warning-text"
            }
          >
            {statusName(coverage.status)} · Deduplicated Transfers
          </p>
        </div>
        <div>
          <span className="metric-label">Largest single-transfer alert</span>
          <strong>
            {amount
              ? `${tokenAmount(amount, evidence.metadata?.decimals)} ${symbol === "raw" ? "" : symbol}`
              : coverageComplete ? "Not triggered" : "Undetermined"}
          </strong>
          <p>{amount ? "Local alert rule triggered" : coverageComplete ? "No alert under the current rule" : "Collection is incomplete; alert assessment is pending"}</p>
        </div>
      </section>
      {!!evidence.alerts?.length && (
        <section className="alert-banner">
          <Icon name="alert" size={32} />
          <div>
            <h2>A large Transfer needs explanation</h2>
            <p>A rule-based investigation lead. The event alone does not establish an attack or a decrease in totalSupply.</p>
            <details>
              <summary>View alert rule and evidence</summary>
              {evidence.alerts.map((alert) => (
                <div key={alert.alert_id}>
                  <p>{alert.explanation}</p>
                  <p className="mono">{alert.evidence_ids.join(" · ")}</p>
                </div>
              ))}
            </details>
          </div>
        </section>
      )}
      {(coverage.issues?.length > 0 || evidence.warnings?.length > 0) && (
        <div className="inline-notice" role="status">
          {[...(coverage.issues ?? []), ...(evidence.warnings ?? [])].map(
            (warning, index) => (
              <p key={index}>{warning}</p>
            ),
          )}
        </div>
      )}
      {coverage.status === "empty" && (
        <p className="inline-notice">
          This window was queried successfully with no matching Transfers. An empty result is distinct from a read failure.
        </p>
      )}
      <div className="analysis-grid">
        <section className="panel report-panel">
          <h2 className="report-heading">
            Investigation findings
            {!!investigation.report?.corrections?.length && (
              <span className="correction-badge">
                Human correction v{investigation.report.revision ?? 2}
              </span>
            )}
          </h2>
          {!!qualityFlags.length && (
            <div className="inline-notice quality-notice" role="status">
              <strong>Automated text checks require review</strong>
              <ul>
                {qualityFlags.map((flag, index) => (
                  <li key={`${flag.code}:${index}`}>{flag.message}</li>
                ))}
              </ul>
              <p>These notices guide human review. Base factual judgments on the public evidence.</p>
            </div>
          )}
          {report?.summary ? (
            <>
              <p className="report-summary">{report.summary}</p>
              {report.claims?.map((claim, index) => (
                <p className="claim" key={index}>
                  {claim.text}{" "}
                  {claim.evidence_ids.map((id) => (
                    <button
                      className="citation"
                      type="button"
                      key={id}
                      onClick={() => onEvidence(id)}
                      title={id}
                    >
                      [{evidenceLabel(id)}]
                    </button>
                  ))}
                </p>
              ))}
              {!!report.limitations?.length && (
                <details>
                  <summary>Finding limitations</summary>
                  <ul>
                    {report.limitations.map((text, index) => (
                      <li key={index}>{text}</li>
                    ))}
                  </ul>
                </details>
              )}
            </>
          ) : (
            <p className="muted">
              {busy
                ? "The Agent is investigating."
                : "No citable finding was produced. Collected evidence is preserved for review."}
            </p>
          )}
          {investigation.report?.corrections?.map((correction, index) => (
            <p className="inline-notice correction-note" key={index}>
              Correction: {correction.text}
            </p>
          ))}
          <CandidateAssessments value={report?.assessments} onEvidence={onEvidence} evidenceLabel={evidenceLabel} />
          <EvidenceIndex investigation={investigation} onSelect={onEvidence} />
        </section>
        <AgentTrace agent={investigation.agent} executionMode={investigation.report?.execution_mode} />
      </div>
      <section className="panel table-panel">
        <div className="section-title">
          <h2>Transfer events</h2>
          <span className="muted">{evidence.transfers.length} deduplicated records</span>
        </div>
        <div className="table-scroll">
          <table>
            <thead>
              <tr>
                <th>Block</th>
                <th>From → To</th>
                <th>Amount ({symbol})</th>
                <th>Evidence</th>
              </tr>
            </thead>
            <tbody>
              {evidence.transfers.map((row) => (
                <tr key={`${row.transaction_hash}:${row.log_index}`}>
                  <td>{integer(row.block_number)}</td>
                  <td
                    className="mono"
                    title={`${row.from_address} → ${row.to_address}`}
                  >
                    {shortHash(row.from_address)} → {shortHash(row.to_address)}
                  </td>
                  <td className="amount-cell" title={row.value_raw}>
                    {tokenAmount(row.value_raw, evidence.metadata?.decimals)}
                  </td>
                  <td>
                    <button
                      className="citation"
                      type="button"
                      onClick={() => onEvidence(row.evidence_id)}
                    >
                      {evidenceLabel(row.evidence_id)}
                    </button>
                  </td>
                </tr>
              ))}
              {!evidence.transfers.length && (
                <tr>
                  <td colSpan={4} className="empty-cell">
                    {coverage.status === "empty" ||
                    coverage.status === "complete"
                      ? "No matching Transfer records."
                      : "The read is incomplete. Missing records must not be interpreted as an empty result."}
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </section>
      <p className="page-note">Hash verification checks integrity. Findings still require evidence review.</p>
    </>
  );
}
