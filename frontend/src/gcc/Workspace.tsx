import { useEffect, useRef, useState } from "react";
import { api } from "../api";
import type { Health, HistoryItem, Investigation, Preset, Scope, VerifiedImport } from "../types";
import type { AppRoute, NavigableRoute } from "../routing/routes";
import { useCaseRecord } from "../state/useCaseRecord";
import { isPreset } from "../state/presets";
import { statusName } from "../format";
import Sidebar from "../components/Sidebar";
import InvestigationView from "../components/InvestigationView";
import ImportPanel from "../components/ImportPanel";
import ReviewPanel from "../components/ReviewPanel";
import SavedVersions from "./SavedVersions";
import InvestigationProgress from "./InvestigationProgress";
import { executionName, sourceName, liveAvailable } from "../state/execution";

type WorkRoute = Extract<AppRoute, { page: "home" | "case" | "tools" }>;
const initialScope: Scope = { address: "", token_address: "", from_block: 24106368, to_block: 24106388, mode: "offline", agent_mode: "offline" };
const isRunning = (item: Investigation | null) => ["pending", "running", "stopping"].includes(item?.status ?? "");
const rememberCase = (client: string, id: string) => { try { localStorage.setItem(`cluetide:${client}:investigation`, id); } catch { /* 案件身份仍保存在路由与服务端。 */ } };

export default function Workspace({ route, navigate, onDemo }: {
  route: WorkRoute; navigate: (route: NavigableRoute) => void;
  onDemo: (caseId: "uniswap93" | "euler-20230313") => void;
}) {
  const [scope, setScope] = useState(initialScope);
  const [presets, setPresets] = useState<Preset[]>([]);
  const [health, setHealth] = useState<Health | null>(null);
  const [healthRefreshing, setHealthRefreshing] = useState(false);
  const [history, setHistory] = useState<HistoryItem[]>([]);
  const [error, setError] = useState("");
  const [starting, setStarting] = useState(false);
  const [stopping, setStopping] = useState(false);
  const [presetLoading, setPresetLoading] = useState(false);
  const [imported, setImported] = useState<VerifiedImport | null>(null);
  const [importCase, setImportCase] = useState<Investigation | null>(null);
  const [notice, setNotice] = useState("");
  const [acceptedCase, setAcceptedCase] = useState<string | null>(null);
  const [stopFailure, setStopFailure] = useState<{id:string;message:string} | null>(null);
  const currentRoute = useRef(route); currentRoute.current = route;
  const startLock = useRef(false), stopIntent = useRef(false), mounted = useRef(true);
  const importGeneration = useRef(0), presetGeneration = useRef(0);
  const stopGeneration = useRef(0);
  const resource = useCaseRecord(route.page === "case" ? route.caseId : route.page === "tools" ? route.caseId ?? null : null);
  const investigation = resource.investigation;
  const busy = starting || isRunning(investigation);
  const openCase = (id: string) => navigate({ page: "case", client: route.client, caseId: id, tab: "overview" });
  const refreshHistory = () => api.history().then(setHistory).catch(() => undefined);
  const refreshHealth = async () => {
    setHealthRefreshing(true);
    try { const next = await api.health(); if (mounted.current) setHealth(next); }
    catch { if (mounted.current) setHealth(null); }
    finally { if (mounted.current) setHealthRefreshing(false); }
  };
  const connect = async () => {
    try {
      const [nextHealth, catalog, entries] = await Promise.all([api.health(), api.catalog(), api.history()]);
      if (!mounted.current) return;
      const valid = catalog.cases.filter(isPreset);
      setHealth(nextHealth); setPresets(valid); setHistory(entries); setError("");
      try { const saved = localStorage.getItem(`cluetide:${route.client}:investigation`); if (saved && entries.some(item => item.id === saved)) setAcceptedCase(saved); } catch { /* 已保存调查仍可从列表选择。 */ }
      setScope(previous => previous.address || !valid[0] ? previous : { address: valid[0].address, token_address: valid[0].token_address, from_block: valid[0].from_block, to_block: valid[0].to_block, mode: "offline", agent_mode: "offline" });
    } catch { if (mounted.current) { setHealth(null); setError("本地调查服务未连接。可重试连接，或先体验公开案例回放。"); } }
  };
  useEffect(() => {
    mounted.current = true; void connect();
    return () => { mounted.current = false; importGeneration.current++; presetGeneration.current++; };
  }, []);
  useEffect(() => {
    const focus = () => { if (!document.hidden) void refreshHealth(); };
    const timer = window.setInterval(focus, 15000);
    window.addEventListener("focus", focus);
    return () => { window.clearInterval(timer); window.removeEventListener("focus", focus); };
  }, []);
  useEffect(() => {
    if (investigation?.id) {
      rememberCase(route.client, investigation.id);
      if (investigation.input) setScope(investigation.input);
    }
    if (investigation && !isRunning(investigation)) { void refreshHistory(); void refreshHealth(); }
  }, [investigation?.id, investigation?.status]);
  useEffect(() => { importGeneration.current++; setImported(null); setImportCase(null); setNotice(""); }, [route.page]);
  useEffect(() => { stopGeneration.current++; if (!startLock.current) setStopping(false); setError(""); }, [route.page, route.page === "case" ? route.caseId : null]);
  const loadPreset = async (id: string) => {
    const generation = ++presetGeneration.current; setPresetLoading(true);
    try {
      const result = await api.preset(id);
      if (generation !== presetGeneration.current || !mounted.current) return;
      if (!isPreset(result)) throw new Error("案例预设格式无效。");
      setScope(previous => ({ ...previous, address: result.address, token_address: result.token_address, from_block: result.from_block, to_block: result.to_block })); setError("");
    } catch (failure) { if (generation === presetGeneration.current) setError(failure instanceof Error ? failure.message : "案例读取失败。"); }
    finally { if (generation === presetGeneration.current) setPresetLoading(false); }
  };
  const start = async () => {
    if (startLock.current || busy || presetLoading) return;
    if (!health) { setError("请先连接本地调查服务。"); return; }
    if (scope.agent_mode === "live" && !liveAvailable(health)) { setError("实时 Agent 当前不可用，请刷新状态并检查本地模型会话。"); return; }
    startLock.current = true; stopIntent.current = false; setAcceptedCase(null); setStopFailure(null); setStarting(true); setError("");
    const origin = currentRoute.current;
    try {
      const accepted = await api.investigate(scope);
      rememberCase(route.client, accepted.id);
      if (mounted.current) setAcceptedCase(accepted.id);
      if (stopIntent.current) {
        try { await api.stop(accepted.id); }
        catch (failure) { if (mounted.current) setStopFailure({id:accepted.id,message:`调查已创建，停止请求未完成：${failure instanceof Error ? failure.message : "请重新停止"}`}); }
      }
      if (mounted.current) { if (currentRoute.current === origin) openCase(accepted.id); void refreshHistory(); }
    } catch (failure) { if (mounted.current) setError(failure instanceof Error ? failure.message : "调查启动失败。"); }
    finally { startLock.current = false; if (mounted.current) { setStarting(false); setStopping(false); } }
  };
  const stop = async () => {
    if (starting) { stopIntent.current = true; setStopping(true); return; }
    if (!investigation || stopping) return;
    const generation = ++stopGeneration.current;
    setStopping(true); setStopFailure(null);
    try { await api.stop(investigation.id); await resource.refresh(); }
    catch (failure) { if (mounted.current && generation === stopGeneration.current) setError(failure instanceof Error ? failure.message : "停止请求失败。"); }
    finally { if (mounted.current && generation === stopGeneration.current) setStopping(false); }
  };
  const resetImport = () => { importGeneration.current++; setImported(null); setImportCase(null); setNotice(""); };
  const importFile = async (file: File) => {
    resetImport(); const generation = importGeneration.current;
    try {
      const result = await api.import(file);
      if (generation !== importGeneration.current) return;
      setImported(result);
      const id = result.report.case_id;
      if (!id || !/^[A-Za-z0-9_-]{1,64}$/.test(id)) { setNotice("完整性通过。案卷未关联本地调查。"); return; }
      try {
        const local = await api.get(id);
        if (generation !== importGeneration.current) return;
        if (local.versions.some(version => version.content_hash === result.manifest_hash)) setImportCase(local);
        else setNotice("完整性通过。本地调查未匹配此内容摘要，可继续核读 ZIP 原文。");
      } catch { if (generation === importGeneration.current) setNotice("完整性通过。此调查未保存于当前服务器，可继续核读 ZIP 原文。"); }
    } catch (failure) { if (generation === importGeneration.current) throw failure; }
  };
  const importVersion: Parameters<typeof ReviewPanel>[0]["onVersion"] = async (...args) => {
    if (!importCase) throw new Error("请先导入可关联的案卷。");
    const generation = importGeneration.current, id = importCase.id;
    try { const next = await api.version(id, ...args); if (generation === importGeneration.current) setImportCase(next); }
    catch (failure) { try { const next = await api.get(id); if (generation === importGeneration.current) setImportCase(next); } catch { /* 原提交错误保留供重试。 */ } throw failure; }
  };
  const tab = route.page === "case" ? route.tab : "overview";
  const version = importCase?.versions.find(item => item.content_hash === imported?.manifest_hash);
  const title = route.page === "home" ? "从一条线索，开始调查。" : route.page === "tools" ? "让下一位读者，复验同一份证据。" : investigation?.title ?? "读取调查案卷";
  const budget = health?.model_budget as {remaining_rmb?: string; cap_rmb?: string} | undefined;
  const repeat = () => { if (investigation?.input) setScope(investigation.input); navigate({page:"home", client:route.client}); };
  return <div className={`gcc-workspace gcc-${route.page}`}>
    <div className="gcc-page-heading"><div><h1>{title}</h1><p>{route.page === "home" ? "选择公开事件、限定区块范围，或接续一份已保存的调查。" : route.page === "tools" ? "读取原报告，核对文件摘要、引用结构与准确版本。" : `${investigation?.input?.mode === "rpc" ? "只读 RPC" : "公开缓存"} · ${investigation?.input?.agent_mode === "live" ? "实时 Agent" : "离线工具演示"}`}</p></div>
      {route.page === "case" && investigation && <div className="gcc-actions"><span role="status">{statusName(investigation.status)}</span>{busy && <button className="gcc-secondary" onClick={() => void stop()} disabled={stopping}>{stopping ? "正在停止…" : "停止调查"}</button>}{investigation.report && <a className="gcc-primary" href={api.bundleUrl(investigation.id)} download>导出案卷</a>}</div>}
    </div>
    {route.page === "home" && <section className="gcc-readiness" aria-label="执行状态"><div><strong>{!health ? "连接本地调查服务" : liveAvailable(health) ? "实时 Agent 已就绪" : "离线调查已就绪"}</strong><p>{liveAvailable(health) ? "选择案例与执行方式后开始。每次实时请求都会核对会话、报价与预算。" : "可使用公开证据演示流程；实时调查需要有效的本地模型会话。"}</p></div>{liveAvailable(health) && budget?.remaining_rmb && <span>预算可预留 ¥{Number(budget.remaining_rmb).toFixed(2)}<small>累计上限 ¥{budget.cap_rmb} · 保守预留口径</small></span>}<button onClick={() => void refreshHealth()} disabled={healthRefreshing}>{healthRefreshing ? "核对中…" : "刷新状态"}</button></section>}
    {(error || resource.error) && <div className="gcc-connection" role="alert"><p>{error || resource.error?.message}</p><button onClick={() => { void connect(); void resource.refresh(); }}>重试连接</button><button onClick={() => onDemo("uniswap93")}>体验公开回放</button></div>}
    {stopFailure && route.page === "case" && route.caseId === stopFailure.id && <div className="gcc-connection" role="alert"><p>{stopFailure.message}</p><button onClick={() => void stop()} disabled={stopping}>重新停止</button></div>}
    {acceptedCase && route.page !== "home" && (route.page !== "case" || route.caseId !== acceptedCase) && <div className="gcc-connection" role="status"><p>最近保存的调查可继续查看。</p><button onClick={() => openCase(acceptedCase)}>继续最近调查</button></div>}
    {route.page === "tools" && route.caseId && <div className="gcc-connection"><p>来自调查：{investigation?.title ?? route.caseId}。选择刚导出的指定版本案卷，复核将绑定其实际内容摘要。</p><button onClick={() => openCase(route.caseId!)}>返回来源调查</button></div>}
    {route.page === "home" ? <div className="gcc-home-layout">
      <Sidebar screen="workbench" onScreen={(screen) => screen === "bundles" ? navigate({ page: "tools", client: route.client }) : undefined} scope={scope} onScope={setScope} onStart={() => void start()} onStop={() => void stop()} onPreset={id => void loadPreset(id)} presets={presets} presetLoading={presetLoading} busy={busy} stopping={stopping} health={health} history={history} onOpen={openCase} />
      <section className="gcc-catalog"><div className="gcc-section-title"><h2>公共事件案卷</h2><span className="gcc-connection-state">{health ? "本地服务已连接" : "等待连接"}</span></div>
        {presets.map(preset => <article className="gcc-case-row" key={preset.case_id}><div className={`gcc-token ${preset.case_id === "uniswap93" ? "uni" : "dai"}`}>{preset.case_id === "uniswap93" ? "UNI" : "DAI"}</div><div><h3>{preset.title}</h3><p>Ethereum · 区块 {preset.from_block}–{preset.to_block}</p><span>{preset.case_id === "uniswap93" ? "从 1 亿 UNI 转账，核对治理执行与供应量。" : "从首笔 DAI 交易，核对有限窗口与事故背景。"}</span></div><div className="gcc-case-actions"><button onClick={() => void loadPreset(preset.case_id)} disabled={busy}>选择此范围</button><button onClick={() => onDemo(preset.case_id === "uniswap93" ? "uniswap93" : "euler-20230313")}>体验回放</button></div></article>)}
        <div className="gcc-section-title"><h2>已保存调查</h2><button onClick={() => void refreshHistory()}>刷新列表</button></div>
        {history.length ? <div className="gcc-history">{history.map(item => <button className="gcc-history-row" key={item.id} onClick={() => openCase(item.id)} disabled={starting}><span><strong>{item.title ?? "Ethereum 调查"}</strong><small>{item.input ? `${sourceName(item.input)} · ${executionName(item.input)}` : "已保存案卷"}{item.created_at ? ` · ${new Date(item.created_at).toLocaleString("zh-CN", {month:"2-digit",day:"2-digit",hour:"2-digit",minute:"2-digit"})}` : ""}</small><small>案卷 {item.id.slice(0,12)}</small></span><span>{statusName(item.status ?? "saved")}</span></button>)}</div> : <p>完成第一项调查后，案卷将保存在这里。</p>}
      </section></div> : route.page === "tools" ? <div className="gcc-tools-content"><p className="gcc-source-note">本地服务校验 · 文件完整性与引用结构分别核对，事实解释依照公开来源复核。</p><ImportPanel imported={imported} onImport={importFile} onReset={resetImport} />{notice && <p role="status" className="inline-notice">{notice}</p>}{importCase && version && <ReviewPanel key={importCase.id} investigation={importCase} reviewVersionId={version.version_id} onReview={async (reviewer, comment) => { const generation = importGeneration.current; const next = await api.review(importCase.id, reviewer, comment, version.version_id); if (generation === importGeneration.current) setImportCase(next); }} onVersion={importVersion} />}</div> : <>
      {investigation && <InvestigationProgress value={investigation} busy={busy} onTab={tab => navigate({...route, tab, evidenceId:undefined})} onRepeat={repeat} />}
      <nav className="gcc-case-tabs" aria-label="调查内容">{([["overview", "调查"], ["evidence", "证据"], ["trace", "Agent 轨迹"], ["versions", "版本与复核"]] as const).map(([key, label]) => <button key={key} aria-current={tab === key ? "page" : undefined} onClick={() => navigate({ ...route, tab: key, evidenceId: undefined })}>{label}</button>)}<button onClick={() => navigate({ page: "home", client: route.client })}>返回事件目录</button></nav>
      {!investigation ? <div className="gcc-empty" role="status"><h2>{resource.status === "error" ? "调查暂不可读取" : "正在读取案卷…"}</h2><p>{resource.status === "error" ? "核对调查 ID，或从事件目录选择已保存案卷。" : "证据、报告与版本将绑定同一案件。"}</p></div> : tab === "versions" ? <SavedVersions key={investigation.id} investigation={investigation} refresh={resource.refresh} client={route.client} /> : <InvestigationView investigation={investigation} busy={busy} tab={tab} selectedId={route.evidenceId ?? null} onEvidence={id => navigate({ ...route, tab: "evidence", evidenceId: id })} />}
    </>}
  </div>;
}
