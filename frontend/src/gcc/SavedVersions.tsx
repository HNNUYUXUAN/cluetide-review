import { useEffect, useState } from "react";
import { api } from "../api";
import type { Investigation, VerifiedImport } from "../types";
import ReviewPanel from "../components/ReviewPanel";

export default function SavedVersions({ investigation, refresh, client }: { investigation: Investigation; refresh: () => Promise<void>; client: string }) {
  const [selected, setSelected] = useState(investigation.versions.at(-1)?.version_id ?? 0);
  const [loaded, setLoaded] = useState<{ caseId: string; versionId: number; contentHash: string; result: VerifiedImport } | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const version = investigation.versions.find(item => item.version_id === selected);
  useEffect(() => {
    if (!investigation.versions.some(item => item.version_id === selected) && investigation.versions.length)
      setSelected(investigation.versions.at(-1)!.version_id);
  }, [investigation.versions, selected]);
  const bundle = loaded?.caseId === investigation.id && loaded.versionId === selected && loaded.contentHash === version?.content_hash ? loaded.result : null;
  useEffect(() => {
    let active = true; const controller = new AbortController();
    setLoaded(null); setError("");
    if (!version) return;
    setLoading(true);
    void (async () => {
      try {
        const response = await fetch(api.bundleUrl(investigation.id, selected), { signal: controller.signal });
        if (!response.ok) throw new Error(`版本读取未完成 (HTTP ${response.status})`);
        const file = new File([await response.blob()], `case-${selected}.zip`, { type: "application/zip" });
        const result = await api.import(file);
        if (result.manifest_hash !== version.content_hash || result.report.case_id !== investigation.id) throw new Error("版本内容摘要或案件身份不匹配。");
        if (active) setLoaded({ caseId: investigation.id, versionId: selected, contentHash: version.content_hash, result });
      } catch (failure) { if (active) setError(failure instanceof Error ? failure.message : "版本读取失败。"); }
      finally { if (active) setLoading(false); }
    })();
    return () => { active = false; controller.abort(); };
  }, [investigation.id, selected, version?.content_hash]);
  const revision = investigation.versions.findIndex(item => item.version_id === selected) + 1;
  return <div className="gcc-saved-versions"><h2>每次更正，都能回到依据。</h2><p>原版本、准确版本的复核意见与人工更正共同保存在案卷中。</p>
    <div className="gcc-version-picker"><label>核读版本 <select aria-label="核读版本" value={selected} onChange={event => setSelected(Number(event.target.value))}>{investigation.versions.map((item, index) => <option value={item.version_id} key={item.version_id}>v{index + 1} · {index === 0 ? "原始报告" : "人工更正"}</option>)}</select></label>{version && <a className="gcc-primary" href={api.bundleUrl(investigation.id, selected)} download>导出 v{revision} 案卷</a>}<a className="gcc-secondary" href={`#/tools?case=${investigation.id}&client=${client === "primary" ? "second" : "primary"}`} target="_blank" rel="noreferrer">在另一客户端复验</a></div>
    {version && <p className="gcc-version-hash">版本 ID {selected} · 父版本 ID {version.parent_version_id} · {version.content_hash}</p>}
    {loading && <p role="status">正在从该版本的证据包读取原文…</p>}{error && <p role="alert" className="error-banner">{error}</p>}
    {bundle && <div className="gcc-version-report"><h3>v{revision} 保存的报告</h3><p>{bundle.report.conclusion?.summary ?? "本版本未形成结论，执行与证据仍可核读。"}</p><details><summary>核读此版本的报告与引用</summary><pre>{JSON.stringify(bundle.report, null, 2)}</pre></details></div>}
    <ReviewPanel investigation={investigation} reviewVersionId={selected} onReview={async (reviewer, comment) => { if (!bundle) throw new Error("请等待所选版本读取完成。"); await api.review(investigation.id, reviewer, comment, selected); await refresh(); }} onVersion={async (...args) => { try { await api.version(investigation.id, ...args); await refresh(); } catch (failure) { await refresh(); throw failure; } }} />
  </div>;
}
