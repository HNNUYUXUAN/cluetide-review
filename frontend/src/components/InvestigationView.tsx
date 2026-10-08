import type { Investigation } from "../types";
import { integer, shortHash, statusName, tokenAmount } from "../format";
import AgentTrace from "./AgentTrace";
import EvidenceIndex, { evidenceEntries } from "./EvidenceIndex";
import EmptyState from "./EmptyState";
import Icon from "./Icon";
import CandidateAssessments from "./CandidateAssessments";
import InvestigationScope from "./InvestigationScope";

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
            ? "正在收集与调查"
            : investigation
              ? `调查${statusName(investigation.status)}`
              : "让大额事件有据可查"
        }
      >
        {busy
          ? "正在读取有限窗口，并按实际观察选择补查工具。停止按钮可终止服务端调查。"
          : investigation
            ? (investigation.error ?? "保留任务状态，可重新发起有限窗口调查。")
            : "选择公开案例预设，或输入 finalized 的有限区块窗口，开始一项只读调查。"}
      </EmptyState>
      </>
    );
  const evidence = investigation.evidence;
  const visibleAgent = busy && investigation.progress ? {
    status: "running", evidence: [], trace: investigation.progress.trace,
    model_requests: investigation.progress.model_requests, tool_attempts: investigation.progress.tool_attempts,
  } : investigation.agent;
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
    return <AgentTrace agent={visibleAgent} executionMode={investigation.report?.execution_mode ?? (investigation.input?.agent_mode === "offline" ? "FunctionModel" : (visibleAgent?.model_requests ?? 0) > 0 ? "Real model," : "no model request sent")} detailed />;
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
          <h2>公开原始数据</h2>
          <p className="muted">
            原始 RPC 观察、来源资料和覆盖状态保留在证据包中。
          </p>
          <details>
            <summary>查看证据 JSON</summary>
            <pre>{JSON.stringify(evidence, null, 2).slice(0, 80000)}</pre>
            {JSON.stringify(evidence).length > 80000 && (
              <p className="muted">浏览器预览已限长；完整数据见 ZIP。</p>
            )}
          </details>
        </section>
      </>
    );
  return (
    <>
      <InvestigationScope value={investigation.investigation_scope ?? investigation.report?.investigation_scope} />
      <section className="metrics-band" aria-label="范围与数据覆盖">
        <div>
          <span className="metric-label">区块窗口</span>
          <strong>
            {integer(evidence.request.from_block)} –{" "}
            {integer(evidence.request.to_block)}
          </strong>
          <p>
            {integer(
              evidence.request.to_block - evidence.request.from_block + 1,
            )}{" "}
            区块 · {hasFinalizedAnchor ? "已取得 finalized 锚点" : "finalized 锚点待核查"}
          </p>
        </div>
        <div>
          <span className="metric-label">查询覆盖</span>
          <strong>
            {coverage.completed_queries} / {coverage.planned_queries} 查询
          </strong>
          <p
            className={
              coverage.status === "complete" || coverage.status === "empty"
                ? ""
                : "warning-text"
            }
          >
            {statusName(coverage.status)} · Transfer 去重
          </p>
        </div>
        <div>
          <span className="metric-label">最大单笔告警</span>
          <strong>
            {amount
              ? `${tokenAmount(amount, evidence.metadata?.decimals)} ${symbol === "raw" ? "" : symbol}`
              : coverageComplete ? "未触发" : "尚无法判断"}
          </strong>
          <p>{amount ? "触发本地工程告警规则" : coverageComplete ? "按当前规则未触发告警" : "采集尚未完成，告警判断待补充"}</p>
        </div>
      </section>
      {!!evidence.alerts?.length && (
        <section className="alert-banner">
          <Icon name="alert" size={32} />
          <div>
            <h2>大额 Transfer 需要解释</h2>
            <p>规则触发的调查线索；事件本身不能证明攻击或 totalSupply 减少。</p>
            <details>
              <summary>查看告警规则与证据</summary>
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
          此窗口查询成功，未返回匹配的 Transfer。空结果不代表读取失败。
        </p>
      )}
      <div className="analysis-grid">
        <section className="panel report-panel">
          <h2 className="report-heading">
            调查结论
            {!!investigation.report?.corrections?.length && (
              <span className="correction-badge">
                人工更正 v{investigation.report.revision ?? 2}
              </span>
            )}
          </h2>
          {!!qualityFlags.length && (
            <div className="inline-notice quality-notice" role="status">
              <strong>自动文本检查提示需要复核</strong>
              <ul>
                {qualityFlags.map((flag, index) => (
                  <li key={`${flag.code}:${index}`}>{flag.message}</li>
                ))}
              </ul>
              <p>提示供人工复核参考，事实判断以公开证据为依据。</p>
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
                  <summary>结论边界</summary>
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
                ? "Agent 正在补查。"
                : "没有形成可引用的结论；保留已取得的证据供复核。"}
            </p>
          )}
          {investigation.report?.corrections?.map((correction, index) => (
            <p className="inline-notice correction-note" key={index}>
              更正：{correction.text}
            </p>
          ))}
          <CandidateAssessments value={report?.assessments} onEvidence={onEvidence} evidenceLabel={evidenceLabel} />
          <EvidenceIndex investigation={investigation} onSelect={onEvidence} />
        </section>
        <AgentTrace agent={visibleAgent} executionMode={investigation.report?.execution_mode ?? (investigation.input?.agent_mode === "offline" ? "FunctionModel" : "Real model,")} />
      </div>
      <section className="panel table-panel">
        <div className="section-title">
          <h2>Transfer 事件</h2>
          <span className="muted">{evidence.transfers.length} 条去重记录</span>
        </div>
        <div className="table-scroll">
          <table>
            <thead>
              <tr>
                <th>区块</th>
                <th>From → To</th>
                <th>数量 ({symbol})</th>
                <th>证据</th>
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
                      ? "没有匹配的 Transfer 记录。"
                      : "读取未完整完成；不能将缺少记录解释为空结果。"}
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </section>
      <p className="page-note">哈希验证用于完整性校验，事实判断仍需复核。</p>
    </>
  );
}
