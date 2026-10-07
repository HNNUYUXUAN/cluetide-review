import { useEffect, useRef, useState } from "react";
import type { CitationValidation, VerifiedImport } from "../types";
import Icon from "./Icon";
import { shortHash } from "../format";
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
  onReset,
}: {
  imported: VerifiedImport | null;
  onImport: (file: File) => Promise<void>;
  onReset?: () => void;
}) {
  const [file, setFile] = useState<File | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [selectedEvidence, setSelectedEvidence] = useState<{ hash: string; id: string } | null>(null);
  const requestSequence = useRef(0);
  const activeRequest = useRef<number | null>(null);
  useEffect(() => () => { requestSequence.current += 1; activeRequest.current = null; }, []);
  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    if (!file || activeRequest.current !== null) return;
    if (
      !file.name.toLowerCase().endsWith(".zip") ||
      file.size > 16 * 1024 * 1024
    ) {
      setError("请选择不超过 16 MiB 的公开证据 ZIP。");
      return;
    }
    const sequence = ++requestSequence.current;
    activeRequest.current = sequence;
    setError("");
    setBusy(true);
    try {
      await onImport(file);
    } catch (error) {
      if (sequence === requestSequence.current)
        setError(error instanceof Error ? error.message : "导入未完成。");
    } finally {
      if (sequence === requestSequence.current) {
        activeRequest.current = null;
        setBusy(false);
      }
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
      : "未知案件";
  const revisionLabel =
    typeof report?.revision === "number" &&
    Number.isSafeInteger(report.revision) &&
    report.revision >= 1
      ? `v${report.revision}`
      : "未知版本";
  const summary =
    typeof report?.conclusion?.summary === "string"
      ? report.conclusion.summary
      : "此版本未形成可显示的摘要。";
  const corrections = Array.isArray(report?.corrections)
    ? report.corrections.filter(
        (item) =>
          item && typeof item === "object" && typeof item.text === "string",
      )
    : [];
  return (
    <section className="panel import-panel">
      <h2>导入公开证据 ZIP</h2>
      <form className="import-form" onSubmit={(event) => void submit(event)}>
        <label className="file-input">
          <Icon name="file" />
          <span>{file?.name ?? "选择 .zip 文件"}</span>
          <input
            type="file"
            accept=".zip,application/zip"
            aria-label="选择公开证据 ZIP 文件"
            onChange={(event) => {
              requestSequence.current += 1;
              activeRequest.current = null;
              setFile(event.target.files?.[0] ?? null);
              event.target.value = "";
              setBusy(false);
              setError("");
              setSelectedEvidence(null);
              onReset?.();
            }}
          />
        </label>
        <button
          className="button primary"
          type="submit"
          disabled={!file || busy}
        >
          {busy ? "验证中…" : "验证并导入"}
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
            <h3>文件哈希与 manifest 校验通过</h3>
            <p>完整性通过；事实结论仍需复核。</p>
          </div>
        </div>
      )}
      {imported && (
        <>
          <div className="import-summary">
            <div>
              <span className="metric-label">案件</span>
              <strong>{caseLabel}</strong>
            </div>
            <div>
              <span className="metric-label">版本</span>
              <strong>{revisionLabel}</strong>
            </div>
            <div>
              <span className="metric-label">来源</span>
              <strong>第二客户端导入</strong>
            </div>
            <div>
              <span className="metric-label">数据</span>
              <strong>公开证据快照</strong>
            </div>
          </div>
          {imported.manifest_hash && (
            <p className="manifest-hash mono" title={imported.manifest_hash}>
              manifest SHA-256 · {shortHash(imported.manifest_hash, 18)}
            </p>
          )}
          <p className="inline-notice import-boundary">
            字节校验结果描述文件完整性，事实解释需核对公开来源。
            <br />
            同一主机的第二客户端与本地服务器，使用受控演示角色。
          </p>
          {!!qualityFlags.length && (
            <div className="inline-notice quality-notice" role="status">
              <strong>自动文本检查提示需要复核</strong>
              <ul>
                {qualityFlags.map((flag, index) => (
                  <li key={`${flag.code}:${index}`}>{flag.message}</li>
                ))}
              </ul>
              <p>
                服务端基于导入内容重新检查，原报告字节保持不变；事实判断以公开证据为依据。
              </p>
            </div>
          )}
          {citations === "unavailable" ? (
            <p className="inline-notice citation-notice muted" role="status">
              引用结构检查结果不可用，请复核原始证据。
            </p>
          ) : citations?.status === "needs_review" ? (
            <div className="inline-notice quality-notice citation-notice" role="status">
              <strong>证据引用结构需要复核</strong>
              <ul>
                {citations.issues.map((issue, index) => (
                  <li key={`${issue.code}:${index}`}>{issue.message}</li>
                ))}
              </ul>
              <p>
                检查依据为导入后的独立计算结果，原报告字节保持不变；结构检查不证明事实或来源真实性。
              </p>
            </div>
          ) : citations?.status === "valid" ? (
            <p className="inline-notice citation-notice muted" role="status">
              证据引用结构检查通过；仅检查引用对应关系，不证明事实或来源真实性。
            </p>
          ) : citations?.status === "not_applicable" ? (
            <p className="inline-notice citation-notice muted" role="status">
              此包没有适用的证据引用结构检查项；事实判断仍需复核。
            </p>
          ) : null}
          <InvestigationScope value={report?.investigation_scope} />
          <details className="imported-report-preview">
            <summary>查看导入版本 {revisionLabel} 的报告与证据</summary>
            <h3>导入版本摘要</h3>
            <p>{summary}</p>
            <CandidateAssessments value={report?.conclusion?.assessments}
              onEvidence={(id) => setSelectedEvidence({hash: imported.manifest_hash, id})} />
            {selectedId && <div className="evidence-inspector">
              <h4 className="mono">{selectedId}</h4>
              {referencedEvidence ? <pre>{(JSON.stringify(referencedEvidence, null, 2) ?? "null").slice(0, 80000)}</pre> :
                <p className="muted">此引用在导入的 Agent 证据和 Transfer 记录中尚未找到，请复核原始证据 ZIP。</p>}
            </div>}
            <ul>
              {(Array.isArray(report?.conclusion?.claims)
                ? report.conclusion.claims
                : []
              ).map((claim, index) => (
                <li key={index}>
                  {typeof claim?.text === "string"
                    ? claim.text
                    : "声明格式不完整，请复核原始报告。"}{" "}
                  <span className="mono">
                    [
                    {Array.isArray(claim?.evidence_ids)
                      ? claim.evidence_ids
                          .filter((id) => typeof id === "string")
                          .join(", ")
                      : "引用字段不完整"}
                    ]
                  </span>
                </li>
              ))}
            </ul>
            {corrections.map((correction, index) => (
              <p key={index}>更正：{correction.text}</p>
            ))}
            <details>
              <summary>查看该版本公开证据 JSON</summary>
              <pre>
                {JSON.stringify(imported.evidence, null, 2).slice(0, 80000)}
              </pre>
              {JSON.stringify(imported.evidence).length > 80000 && (
                <p className="muted">
                  浏览器预览已限长；完整数据见导入的 ZIP。
                </p>
              )}
            </details>
          </details>
          {!!imported.manifest?.files && (
            <details>
              <summary>查看逐文件校验清单</summary>
              <div className="table-scroll">
                <table>
                  <thead>
                    <tr>
                      <th>文件</th>
                      <th>字节</th>
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
          证据包包含
          manifest、公开原始数据、规范化证据和引用报告。导入会校验文件路径、大小与
          SHA-256。
        </p>
      )}
    </section>
  );
}
