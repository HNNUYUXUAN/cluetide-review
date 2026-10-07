import { useEffect, useRef, useState } from "react";
import type { ExplanationAssessment, Investigation } from "../types";
import { shortHash, statusName } from "../format";
import { isExplanationAssessment } from "../assessment";
import EmptyState from "./EmptyState";

interface Props {
  investigation: Investigation | null;
  reviewVersionId?: number;
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
function correctionDraft(investigation: Investigation | null) {
  const versions = investigation?.versions ?? [];
  const head = Number(investigation?.registry?.head_version_id ?? Math.max(0, ...versions.map((version) => version.version_id)));
  const conclusion = investigation?.report?.conclusion ?? investigation?.agent?.report;
  const sourceAssessments = conclusion?.assessments;
  return {
    caseId: investigation?.id,
    baseVersionId: head,
    baseSummary: conclusion?.summary ?? "",
    claims: conclusion?.claims ?? [],
    assessments: Array.isArray(sourceAssessments) && sourceAssessments.length <= 12 && sourceAssessments.every(isExplanationAssessment) ? sourceAssessments : [],
    classification: conclusion?.classification,
    parent: head,
    correctedSummary: conclusion?.summary ?? "",
    correction: "",
    claimIndex: -1,
    correctedClaim: "",
    assessmentIndex: -1,
    correctedAssessment: "",
    correctedClassification: "",
    dirty: false,
  };
}
export default function ReviewPanel({
  investigation,
  reviewVersionId,
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
    versions.some((version) => version.version_id === id)
      ? `v${versions.findIndex((version) => version.version_id === id) + 1}` : `版本 ${id}`;
  const reviewVersion = versions.find((version) => version.version_id === (reviewVersionId ?? head));
  const [reviewer, setReviewer] = useState("local-reviewer");
  const [comment, setComment] = useState("");
  const [author, setAuthor] = useState("local-author");
  const [draft, setDraft] = useState(() => correctionDraft(investigation));
  const { correction, correctedSummary, claims, claimIndex, correctedClaim, assessments,
    assessmentIndex, correctedAssessment, correctedClassification, parent } = draft;
  const editDraft = (change: Partial<typeof draft>) => setDraft((current) => ({ ...current, ...change, dirty: true }));
  const [busy, setBusy] = useState<"review" | "version" | null>(null);
  const [notice, setNotice] = useState("");
  const [failure, setFailure] = useState("");
  const activeRequest = useRef<symbol | null>(null);
  const latestInvestigation = useRef(investigation);
  latestInvestigation.current = investigation;
  const mounted = useRef(true);
  useEffect(() => {
    mounted.current = true;
    return () => { mounted.current = false; activeRequest.current = null; };
  }, []);
  useEffect(() => {
    setDraft((current) => {
      if (current.caseId !== investigation?.id || (!current.dirty && !activeRequest.current && current.baseVersionId !== head))
        return correctionDraft(investigation);
      return current;
    });
  }, [investigation, head, busy]);
  useEffect(() => {
    activeRequest.current = null;
    setBusy(null);
    setNotice("");
    setFailure("");
    setComment("");
  }, [investigation?.id]);
  if (!investigation?.evidence || !versions.length)
    return (
      <EmptyState title="先准备一份可复核的报告">
        完成调查或导入已验证的公开证据 ZIP 后，可记录复核意见并创建更正版本。
      </EmptyState>
    );
  const submit = async (event: React.FormEvent, kind: "review" | "version") => {
    event.preventDefault();
    if (activeRequest.current || draft.caseId !== investigation.id) return;
    if (kind === "review" && !reviewVersion) {
      setFailure("所选复核版本尚未找到，请重新读取调查。");
      return;
    }
    if (kind === "version" && parent !== head) {
      setFailure("当前父版本已更新。请核对保留的草稿并采用当前版本作为父版本。");
      return;
    }
    let replacement: Record<number, ExplanationAssessment> | undefined;
    if (kind === "version" && assessmentIndex >= 0) {
      try {
        const parsed: unknown = JSON.parse(correctedAssessment);
        if (!isExplanationAssessment(parsed)) throw new Error("候选评估 JSON 必须符合公开评估字段及长度约束。");
        if (parsed.explanation_id !== assessments[assessmentIndex]?.explanation_id)
          throw new Error("候选 ID 应与所选评估一致。");
        replacement = { [assessmentIndex]: parsed };
      } catch (error) {
        setFailure(error instanceof SyntaxError ? "候选评估 JSON 格式不正确，请核对后提交。" : error instanceof Error ? error.message : "候选评估字段需要复核。");
        return;
      }
    }
    const request = Symbol(kind);
    const caseId = investigation.id;
    activeRequest.current = request;
    const isCurrent = () => mounted.current && activeRequest.current === request && latestInvestigation.current?.id === caseId;
    setBusy(kind);
    setNotice("");
    setFailure("");
    try {
      if (kind === "review") {
        await onReview(reviewer.trim(), comment.trim());
        if (!isCurrent()) return;
        setComment("");
        setNotice("复核意见已记录，并绑定所复核的版本与内容哈希。");
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
        if (!isCurrent()) return;
        setDraft(correctionDraft(latestInvestigation.current));
        setNotice("更正版本已创建，修订后的摘要已写入报告，原版本仍可追溯。");
      }
    } catch (error) {
      if (isCurrent()) setFailure(error instanceof Error ? error.message : "本地登记未完成。");
    } finally {
      if (isCurrent()) { activeRequest.current = null; setBusy(null); }
    }
  };
  const adoptHead = () => {
    setDraft((current) => {
      const next = correctionDraft(investigation);
      const claimIndex = current.claimIndex < next.claims.length ? current.claimIndex : -1;
      const selectedExplanation = current.assessments[current.assessmentIndex]?.explanation_id;
      const assessmentIndex = selectedExplanation ? next.assessments.findIndex((item) => item.explanation_id === selectedExplanation) : -1;
      return { ...next, correctedSummary: current.correctedSummary, correction: current.correction,
        correctedClassification: current.correctedClassification, claimIndex, correctedClaim: current.correctedClaim,
        assessmentIndex, correctedAssessment: current.correctedAssessment, dirty: true };
    });
    setNotice("已采用当前版本作为父版本。更正草稿已保留，请逐项核对当前声明与候选解释后提交。");
  };
  return (
    <section className="panel review-panel">
      <h2>本地协作复核</h2>
      <p className="muted">
        本演示使用本地登记模拟。两个本地角色不构成独立认证。
      </p>
      <ol className="version-flow">
        <li>v1 原始报告</li>
        <li className="current">复核意见</li>
        <li>v{Math.max(2, headRevision)} 更正报告</li>
      </ol>
      <div className="review-grid">
        <form
          className="subpanel"
          onSubmit={(event) => void submit(event, "review")}
        >
          <h3>提交复核</h3>
          {reviewVersion ? <p className="inline-notice" data-testid="review-version-binding">
            复核绑定 {revisionName(reviewVersion.version_id)} · 内容哈希 <span className="mono">{reviewVersion.content_hash}</span>
          </p> : <p className="error-banner" role="alert">所选复核版本尚未找到，请重新读取调查。</p>}
          <label>
            复核者
            <input
              value={reviewer}
              onChange={(event) => setReviewer(event.target.value)}
              required
              maxLength={100}
              disabled={!!busy}
            />
          </label>
          <label>
            复核意见
            <textarea
              value={comment}
              onChange={(event) => setComment(event.target.value)}
              required
              maxLength={4000}
              rows={4}
              placeholder="记录证据支持程度与需要更正之处。"
              disabled={!!busy}
            />
          </label>
          <button className="button primary" disabled={!!busy || !reviewVersion} type="submit">
            {busy === "review" ? "记录中…" : "记录复核"}
          </button>
        </form>
        <form
          className="subpanel"
          onSubmit={(event) => void submit(event, "version")}
        >
          <h3>创建更正版本</h3>
          <p className="muted" data-testid="correction-baseline">草稿基线 {revisionName(draft.baseVersionId)} · 当前版本 {revisionName(head)}{draft.dirty ? " · 草稿已编辑" : ""}</p>
          {draft.baseVersionId !== head ? <div className="inline-notice" role="status">
            <p>服务端已有新版本，当前更正草稿保留在原基线上。请核对差异后更新父版本。</p>
            <details><summary>对照摘要</summary><p>草稿基线：{draft.baseSummary}</p><p>当前版本：{investigation.report?.conclusion?.summary ?? investigation.agent?.report?.summary ?? "尚无摘要"}</p></details>
            <button className="button outline" type="button" disabled={!!busy} onClick={adoptHead}>采用当前版本，保留草稿</button>
          </div> : null}
          <label>
            作者
            <input
              value={author}
              onChange={(event) => setAuthor(event.target.value)}
              required
              maxLength={100}
              disabled={!!busy}
            />
          </label>
          <label>
            父版本
            <select
              value={parent}
              onChange={(event) => {
                const nextParent = Number(event.target.value);
                if (nextParent === head && draft.baseVersionId !== head) adoptHead();
                else editDraft({ parent: nextParent });
              }}
              disabled={!!busy}
            >
              {versions.map((version) => (
                <option key={version.version_id} value={version.version_id}>
                  {revisionName(version.version_id)} ·{" "}
                  {version.version_id === head ? "当前版本" : "已保留"}
                </option>
              ))}
            </select>
          </label>
          <label className="summary-editor">
            修订后的摘要
            <textarea
              aria-label="修订后的摘要"
              aria-describedby="summary-edit-help"
              value={correctedSummary}
              onChange={(event) => editDraft({ correctedSummary: event.target.value })}
              required
              maxLength={1800}
              rows={4}
              disabled={!!busy}
            />
            <span className="muted" id="summary-edit-help">
              新摘要将写入更正报告，原版本继续保留。
            </span>
          </label>
          {!!claims.length && (
            <label className="summary-editor">
              需更正的声明
              <select
                value={claimIndex}
                disabled={!!busy}
                onChange={(event) => {
                  const index = Number(event.target.value);
                  editDraft({ claimIndex: index, correctedClaim: index >= 0 ? claims[index].text : "" });
                }}
              >
                <option value={-1}>不更正声明</option>
                {claims.map((claim, index) => (
                  <option key={index} value={index}>
                    第 {index + 1} 条 · {claim.text.slice(0, 70)}
                  </option>
                ))}
              </select>
            </label>
          )}
          {claimIndex >= 0 && (
            <label className="summary-editor">
              修订后的声明
              <textarea
                aria-label="修订后的声明"
                value={correctedClaim}
                onChange={(event) => editDraft({ correctedClaim: event.target.value })}
                required
                maxLength={1400}
                rows={4}
                disabled={!!busy}
              />
              <span className="muted">
                仅修订文字，保留本声明原有的证据引用。
              </span>
            </label>
          )}
          {!!assessments.length && <details className="assessment-editor">
            <summary>更正候选评估与结论分类</summary>
            <p className="muted">选择一个候选后修订公开 JSON。证据引用须对应已成功取得的观察；治理候选与结论分类应保持一致。</p>
            <label>
              需更正的候选评估
              <select aria-label="需更正的候选评估" value={assessmentIndex} disabled={!!busy}
                onChange={(event) => {
                  const index = Number(event.target.value);
                  editDraft({ assessmentIndex: index, correctedAssessment: index >= 0 ? JSON.stringify(assessments[index], null, 2) : "" });
                }}>
                <option value={-1}>沿用当前候选评估</option>
                {assessments.map((item, index) => <option key={item.explanation_id} value={index}>第 {index + 1} 项 · {item.explanation.slice(0, 60)}</option>)}
              </select>
            </label>
            {assessmentIndex >= 0 && <label>
              修订后的候选评估 JSON
              <textarea aria-label="修订后的候选评估 JSON" className="mono"
                value={correctedAssessment} onChange={(event) => editDraft({ correctedAssessment: event.target.value })}
                required maxLength={64000} rows={12} disabled={!!busy} />
            </label>}
            <label>
              修订后的结论分类
              <select aria-label="修订后的结论分类" value={correctedClassification}
                onChange={(event) => editDraft({ correctedClassification: event.target.value })} disabled={!!busy}>
                <option value="">沿用基线分类（{statusName(draft.classification)}）</option>
                <option value="governance_explained">治理执行有解释</option>
                <option value="unresolved">原因尚未确定</option>
                <option value="needs_review">需要复核</option>
              </select>
            </label>
          </details>}
          <label>
            更正说明
            <textarea
              value={correction}
              onChange={(event) => editDraft({ correction: event.target.value })}
              required
              maxLength={4000}
              rows={3}
              placeholder="说明更正的依据与原因。"
              disabled={!!busy}
            />
          </label>
          <button className="button primary" disabled={!!busy || parent !== head} type="submit">
            {busy === "version" ? "创建中…" : `创建 v${headRevision + 1}`}
          </button>
        </form>
      </div>
      {notice && (
        <p className="inline-notice" role="status">
          {notice}
        </p>
      )}
      {failure && <p className="error-banner" role="alert">{failure}</p>}
      <div className="table-scroll">
        <table>
          <thead>
            <tr>
              <th>版本</th>
              <th>作者</th>
              <th>父版本</th>
              <th>内容哈希</th>
              <th>状态</th>
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
                      {version.version_id === head ? "当前版本" : "已保留"}
                    </span>
                  </td>
                </tr>
              ))}
          </tbody>
        </table>
      </div>
      {!!investigation.reviews?.length && (
        <div className="review-history">
          <h3>复核记录</h3>
          {investigation.reviews.map((review, index) => (
            <div className="review-record" key={review.review_id ?? index}>
              <strong>{review.reviewer}</strong>
              <span className="muted">
                {revisionName(review.version_id ?? versions[0].version_id)}
              </span>
              <p>{review.comment ?? review.decision ?? "复核已记录"}</p>
            </div>
          ))}
        </div>
      )}
      <p className="page-note">哈希证明内容未变，不能证明事实正确。</p>
    </section>
  );
}
