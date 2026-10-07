import { useEffect, useRef, useState } from "react";
import { verifyReplayBundle } from "./replay-bundle";
import type { VerifiedReplayBundle } from "./replay-bundle";
import type { ReactNode } from "react";

export default function BundleTools({ children, serverInitially = false }: { children: ReactNode; serverInitially?: boolean }) {
  const [localServer, setLocalServer] = useState(serverInitially);
  const [result, setResult] = useState<VerifiedReplayBundle | null>(null);
  const [file, setFile] = useState<File | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const generation = useRef(0), running = useRef(false);
  useEffect(() => () => { generation.current++; }, []);
  const reset = () => { generation.current++; running.current = false; setResult(null); setError(""); setBusy(false); };
  const verify = async () => {
    if (!file || running.current) return;
    if (file.size > 16 * 1024 * 1024 || !file.name.toLowerCase().endsWith(".zip")) { setError("请选择不超过 16 MiB 的证据 ZIP。"); return; }
    const current = ++generation.current; running.current = true; setBusy(true); setError(""); setResult(null);
    try { const checked = await verifyReplayBundle(new Uint8Array(await file.arrayBuffer())); if (current === generation.current) setResult(checked); }
    catch (failure) { if (current === generation.current) setError(failure instanceof Error ? failure.message : "案卷校验未通过。"); }
    finally { if (current === generation.current) { running.current = false; setBusy(false); } }
  };
  return <>
    <div className="gcc-tool-switch" role="group" aria-label="复验方式"><button aria-pressed={!localServer} className={!localServer ? "active" : ""} onClick={() => { reset(); setLocalServer(false); }}>浏览器本地复验</button><button aria-pressed={localServer} className={localServer ? "active" : ""} onClick={() => { reset(); setLocalServer(true); }}>本地调查服务与协作复核</button></div>
    {localServer ? children : <section className="gcc-workspace gcc-browser-tools"><div className="gcc-page-heading"><div><h1>让下一位读者，复验同一份证据。</h1><p>在当前浏览器核对案卷文件、报告与引用。文件保留在你的设备上。</p></div></div>
      <form className="gcc-verify-form" onSubmit={event => { event.preventDefault(); void verify(); }}><label>选择导出的公开证据 ZIP<input type="file" accept=".zip,application/zip" onChange={event => { reset(); setFile(event.target.files?.[0] ?? null); }} /></label><button className="gcc-primary" type="submit" disabled={!file || busy}>{busy ? "正在核对文件…" : "校验证据包"}</button><p>支持 ClueTide 的 ZIP_STORED 案卷，最大 16 MiB；其他压缩格式可使用本地调查服务。</p></form>
      {error && <p role="alert" className="error-banner">{error}</p>}
      {result && <div className="gcc-verified-result"><h2>文件完整性与引用结构通过</h2><p role="status">已核对 {result.verifiedFiles.length} 个文件 · 报告 v{result.report.revision} · {result.report.synthetic_fixture?.mode === "synthetic" ? "合成版本演示" : "导入案卷原文"}</p><p className="gcc-version-hash">Manifest SHA-256：{result.manifestHash}</p><h3>保存的报告</h3><p>{result.report.conclusion.summary}</p><details><summary>查看文件清单和报告原文</summary><ul>{result.verifiedFiles.map(name => <li key={name}>{name}</li>)}</ul><pre>{JSON.stringify(result.report, null, 2)}</pre></details><aside>哈希一致确认文件完整性，引用检查确认结构；解释的事实依据仍需核读原始来源。</aside></div>}
    </section>}
  </>;
}
