import { useEffect, useRef, useState } from 'react'
import type { ChangeEvent, ReactNode } from 'react'
import { downloadReplayBundle, loadReplayBundle, verifyReplayBundle } from './replay-bundle'
import type { VerifiedReplayBundle } from './replay-bundle'
import { formatRaw, replayCases } from './replay-data'
import type { ReplayCaseId, ReplayVersion } from './replay-data'
import './replay.css'

type EvidenceTab = 'transfer' | 'receipt' | 'context' | 'supply' | 'scope' | 'trace' | 'verify'
type Bundles = Record<ReplayVersion, VerifiedReplayBundle>

function Glyph({ name }: { name: 'file' | 'download' | 'external' | 'check' | 'arrow' }) {
  const paths = {
    file: <><path d="M6 3h8l4 4v14H6z" /><path d="M14 3v5h5M9 12h6M9 16h6" /></>,
    download: <><path d="M12 3v12m-5-5 5 5 5-5M4 17v4h16v-4" /></>,
    external: <><path d="M14 3h7v7m0-7-11 11M10 4H4v16h16v-6" /></>,
    check: <path d="m5 12 4 4L19 6" />,
    arrow: <path d="M3 12h18m-6-6 6 6-6 6" />,
  }
  return <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">{paths[name]}</svg>
}

function Detail({ name, children }: { name: string; children: ReactNode }) {
  return <div className="replay-detail"><dt>{name}</dt><dd>{children}</dd></div>
}

function SourceLink({ href, children }: { href: string; children: ReactNode }) {
  if (!/^https:\/\//i.test(href)) return <span>{children}</span>
  return <a href={href} target="_blank" rel="noreferrer">{children} <Glyph name="external" /></a>
}

export function ReplayVerifier({ knownBundles }: { knownBundles?: Bundles }) {
  const [result, setResult] = useState<VerifiedReplayBundle | null>(null)
  const [status, setStatus] = useState('选择已导出的案卷，在当前浏览器核对文件与引用。')
  const [busy, setBusy] = useState(false)
  const [failed, setFailed] = useState(false)
  const sequence = useRef(0)
  useEffect(() => () => { sequence.current += 1 }, [])

  async function selectFile(event: ChangeEvent<HTMLInputElement>) {
    const epoch = ++sequence.current
    const file = event.target.files?.[0]
    setResult(null)
    setFailed(false)
    if (!file) { setBusy(false); setStatus('请选择一个证据 ZIP。'); return }
    setBusy(true)
    setStatus(`正在复验 ${file.name}…`)
    try {
      if (file.size > 16 * 1024 * 1024) throw new Error('请选择 16 MiB 以内的证据 ZIP。')
      const verified = await verifyReplayBundle(new Uint8Array(await file.arrayBuffer()))
      if (sequence.current !== epoch) return
      setResult(verified)
      setStatus('文件完整性与包内引用结构校验通过。')
    } catch (error) {
      if (sequence.current !== epoch) return
      setFailed(true)
      setStatus(error instanceof Error ? error.message : '文件无法校验，请重新选择完整的证据 ZIP。')
    } finally {
      if (sequence.current === epoch) setBusy(false)
    }
  }

  const matched = result && knownBundles ? ([1, 2] as const).find(version => knownBundles[version].manifestHash === result.manifestHash) : undefined
  return <section className="replay-verifier" aria-label="浏览器本地复验">
    <p>文件只在当前浏览器中读取。更换文件会重新计算摘要与引用检查。</p>
    <label className="replay-file-label">选择证据 ZIP<input type="file" accept=".zip,application/zip" onChange={selectFile} /></label>
    <div className={`replay-verify-status ${failed ? 'is-error' : result ? 'is-success' : ''}`} role="status" aria-busy={busy}>{status}</div>
    {result && <div className="replay-verified">
      <dl>
        <Detail name="包内案件 / 版本">{typeof result.report.case_id === 'string' ? result.report.case_id : '未标注'} / {Number.isSafeInteger(result.report.revision) ? `v${result.report.revision}` : '未标注'}</Detail>
        <Detail name="五文件结构">manifest.json + {result.verifiedFiles.join('、')}</Detail>
        <Detail name="与当前回放关联">{matched ? `精确匹配当前案卷 v${matched}` : '此文件未与当前回放版本建立摘要匹配'}</Detail>
        <Detail name="Manifest SHA-256"><code>{result.manifestHash}</code></Detail>
        <Detail name="ZIP SHA-256"><code>{result.archiveHash}</code></Detail>
      </dl>
      <details><summary>读取本次导入的报告</summary><pre>{result.markdown}</pre></details>
    </div>}
    <p className="replay-fineprint">校验覆盖 ZIP 结构、CRC32、SHA-256、规范 JSON 和包内引用。文件摘要支持字节核对；事实真实性、来源真实性与复核者身份仍需人工判断。</p>
  </section>
}

export default function ReplayPage({ caseId, onWorkbench, onTools }: { caseId: ReplayCaseId; onWorkbench: () => void; onTools: () => void }) {
  const info = replayCases[caseId]
  const [step, setStep] = useState(1)
  const [node, setNode] = useState(0)
  const [version, setVersion] = useState<ReplayVersion>(2)
  const [bundles, setBundles] = useState<Bundles | null>(null)
  const [error, setError] = useState('')
  const [retry, setRetry] = useState(0)
  const [evidenceTab, setEvidenceTab] = useState<EvidenceTab | null>(null)
  const evidenceTitle = useRef<HTMLHeadingElement>(null)
  const evidencePanel = useRef<HTMLElement>(null)
  const drawerMotion = useRef<Animation | null>(null)
  const pointerInput = useRef(false)
  const animateOpen = useRef(false)
  const openingFrame = useRef<{ opacity: string; transform: string } | null>(null)
  const origin = useRef<HTMLElement | null>(null)
  const sectionTitle = useRef<HTMLHeadingElement>(null)
  const bundle = bundles?.[version]
  const [downloadStatus, setDownloadStatus] = useState('')

  useEffect(() => {
    const controller = new AbortController()
    setBundles(null)
    setError('')
    setStep(1)
    setNode(0)
    setVersion(2)
    setEvidenceTab(null)
    Promise.all([loadReplayBundle(caseId, 1, controller.signal), loadReplayBundle(caseId, 2, controller.signal)])
      .then(([v1, v2]) => {
        if (controller.signal.aborted) return
        if (v2.report.parent_manifest_hash !== v1.manifestHash) throw new Error('v2 的父版本摘要与 v1 不一致。')
        setBundles({ 1: v1, 2: v2 })
      }).catch(reason => { if (!controller.signal.aborted) setError(reason instanceof Error ? reason.message : '公开案卷读取失败。') })
    return () => controller.abort()
  }, [caseId, retry])

  useEffect(() => {
    if (!evidenceTab) return
    const panel = evidencePanel.current
    if (animateOpen.current && panel) {
      const reduce = window.matchMedia('(prefers-reduced-motion: reduce)').matches
      const frame = openingFrame.current
      drawerMotion.current = panel.animate(
        reduce ? [{ opacity: frame?.opacity ?? .6 }, { opacity: 1 }] : [frame ?? { opacity: .6, transform: 'translateX(12px)' }, { opacity: 1, transform: 'translateX(0)' }],
        { duration: reduce ? 160 : 240, easing: 'cubic-bezier(0.32,0.72,0,1)' },
      )
    }
    animateOpen.current = false
    openingFrame.current = null
    evidenceTitle.current?.focus({ preventScroll: true })
    if (window.matchMedia('(max-width: 850px)').matches) evidenceTitle.current?.scrollIntoView({ block: 'start', behavior: 'instant' })
    const escape = (event: KeyboardEvent) => {
      if (event.key === 'Escape') { event.preventDefault(); closeEvidence() }
    }
    window.addEventListener('keydown', escape)
    return () => { window.removeEventListener('keydown', escape); drawerMotion.current?.cancel(); drawerMotion.current = null }
  }, [evidenceTab])

  function showEvidence(tab: EvidenceTab, target?: HTMLElement) {
    const panel = evidencePanel.current
    const currentStyle = panel && drawerMotion.current ? getComputedStyle(panel) : null
    const frame = currentStyle ? { opacity: currentStyle.opacity, transform: currentStyle.transform } : null
    drawerMotion.current?.cancel()
    drawerMotion.current = null
    animateOpen.current = (!evidenceTab || !!frame) && pointerInput.current
    openingFrame.current = frame
    if (target) origin.current = target
    setEvidenceTab(tab)
    if (tab === evidenceTab) {
      if (frame && panel && pointerInput.current) {
        const reduce = window.matchMedia('(prefers-reduced-motion: reduce)').matches
        drawerMotion.current = panel.animate(reduce ? [{ opacity: frame.opacity }, { opacity: 1 }] : [frame, { opacity: 1, transform: 'translateX(0)' }], { duration: reduce ? 160 : 240, easing: 'cubic-bezier(0.32,0.72,0,1)' })
      }
      animateOpen.current = false
      openingFrame.current = null
      evidenceTitle.current?.focus()
    }
  }
  function closeEvidence() {
    const finish = () => {
      setEvidenceTab(null)
      if (origin.current?.isConnected) origin.current.focus({ preventScroll: true })
    }
    const panel = evidencePanel.current
    const opacity = panel ? getComputedStyle(panel).opacity : '1'
    const transform = panel ? getComputedStyle(panel).transform : 'none'
    drawerMotion.current?.cancel()
    if (!panel || !pointerInput.current) { finish(); return }
    const reduce = window.matchMedia('(prefers-reduced-motion: reduce)').matches
    const animation = panel.animate(
      reduce ? [{ opacity }, { opacity: 0 }] : [{ opacity, transform }, { opacity: 0, transform: 'translateX(12px)' }],
      { duration: reduce ? 160 : 240, easing: 'cubic-bezier(0.32,0.72,0,1)', fill: 'forwards' },
    )
    drawerMotion.current = animation
    animation.finished.then(() => { if (drawerMotion.current === animation) finish() }).catch(() => {})
  }
  function goStep(next: number) {
    drawerMotion.current?.cancel()
    drawerMotion.current = null
    setStep(next)
    setEvidenceTab(null)
    if (next === 2) setNode(1)
    requestAnimationFrame(() => sectionTitle.current?.focus({ preventScroll: true }))
  }
  function download() {
    if (!bundle) return
    downloadReplayBundle(bundle, caseId, version)
    setDownloadStatus(`已准备 v${version} 合成案卷下载，含 5 个文件。`)
  }

  const tabLabels: Record<EvidenceTab, string> = { transfer: '转账记录', receipt: '交易回执', context: info.contextLabel, supply: '供给量核对', scope: '调查范围', trace: '保存的观察', verify: '浏览器本地复验' }
  const pathTitles = ['读取转账日志', '核对交易回执', `查看${info.contextLabel}`, '组织可复核解释']
  const pathDescriptions = [info.amount, 'Transfer 已执行', info.contextDetail, '支持、边界与未知']
  const pathTabs: EvidenceTab[] = ['transfer', 'receipt', 'context', 'scope']
  const receipt = bundle?.report.agent?.evidence.find(item => item.kind === 'receipt')
  const states = bundle?.report.agent?.evidence.filter(item => item.kind === 'token_state') ?? []

  function evidenceBody() {
    if (evidenceTab === 'verify') return <ReplayVerifier knownBundles={bundles ?? undefined} />
    if (!bundle) return null
    if (evidenceTab === 'context') return <>{bundle.evidence.sources.map((source, index) => <article className="replay-source-card" key={source.url}>
      <p className="replay-muted">{source.publisher}</p><h3>{index === 0 ? info.contextDetail : source.title}</h3>
      <p>{source.summary}</p><dl><Detail name="原文定位">{source.locator}</Detail><Detail name="既有核读范围">{source.actual_read_scope ?? source.locator}</Detail><Detail name="材料取得日">{source.observed_on_date_utc ?? info.captured}</Detail></dl>
      {source.source_quality_note && <p className="replay-fineprint">来源注记：{source.source_quality_note}</p>}
      <SourceLink href={source.url}>打开原始来源</SourceLink>
    </article>)}</>
    if (evidenceTab === 'transfer') return <>{bundle.evidence.transfers.map(transfer => <article className="replay-source-card" key={transfer.evidence_id}>
      <p className="replay-muted">公开链上记录 / {transfer.directions.includes('out') ? '主体流出' : '主体流入'}</p>
      <h3>{formatRaw(transfer.value_raw, bundle.evidence.metadata.decimals)} {info.symbol}</h3>
      <dl><Detail name="事件定位">区块 {transfer.block_number} / 日志 {transfer.log_index}</Detail><Detail name="转出地址"><code>{transfer.from_address}</code></Detail><Detail name="收款地址"><code>{transfer.to_address}</code></Detail><Detail name="原始单位 / decimals = 18"><code>{transfer.value_raw}</code></Detail><Detail name="交易哈希"><code>{transfer.transaction_hash}</code></Detail></dl>
      <SourceLink href={`https://etherscan.io/tx/${transfer.transaction_hash}#eventlog`}>打开交易日志</SourceLink>
    </article>)}</>
    if (evidenceTab === 'supply') return <><p>以下读数来自已保存的历史 getter。两个版本均保留这些观察。</p>{states.map(state => <article className="replay-source-card" key={state.evidence_id}>
      <p className="replay-muted">区块 {String(state.payload.block_number)} 末状态</p><h3>{formatRaw(String(state.payload.total_supply_raw))} {info.symbol}</h3>
      <dl><Detail name="原始单位"><code>{String(state.payload.total_supply_raw)}</code></Detail><Detail name="状态锚定方式">{String(state.payload.block_selection)}{state.payload.block_hash ? ' / 已保存区块哈希' : ' / 原观察未保存区块哈希'}</Detail>{Boolean(state.payload.block_hash) && <Detail name="区块哈希"><code>{String(state.payload.block_hash)}</code></Detail>}</dl>
      <SourceLink href={`https://etherscan.io/block/${String(state.payload.block_number)}`}>打开对应区块</SourceLink>
    </article>)}<p className="replay-fineprint">{caseId === 'uniswap93' ? '两块读数相同，反驳“这对区块间 getter 净减少”的候选。完整因果仍待核查。' : 'DAI 的供给 getter 不代表 Euler 内部 eDAI / dDAI 债务健康状态。'}</p></>
    if (evidenceTab === 'scope') return <article className="replay-source-card"><p className="replay-muted">有限窗口 / 已保存查询</p><h3>{info.blocks} 个区块</h3><dl><Detail name="区块范围">{info.window}，包含首尾</Detail><Detail name="计划查询">{bundle.evidence.coverage.completed_queries} / {bundle.evidence.coverage.planned_queries} 项已完成</Detail><Detail name="主体"><code>{bundle.evidence.request.address}</code></Detail><Detail name="代币"><code>{bundle.evidence.request.token_address}</code></Detail><Detail name="目标 Transfer">{bundle.evidence.transfers.length} 条</Detail><Detail name="取得日期">{info.captured}</Detail></dl><p>{info.limit}</p><p className="replay-fineprint">查询覆盖依赖保存的提供方响应；区块窗口长度与已完成查询数分别表示范围和执行覆盖。</p></article>
    if (evidenceTab === 'trace') return <><p>这是按保存观察组织的引导式回放。步骤由你点击推进，模型调用次数为 0。</p><ol className="replay-observations">{bundle.report.agent?.evidence.map(item => <li key={item.evidence_id}><strong>{item.kind}</strong><code>{item.evidence_id}</code></li>)}</ol></>
    return <article className="replay-source-card"><p className="replay-muted">公开链上记录</p><h3>{receipt?.payload.status === '0x1' ? 'Transfer 已执行' : '查看保存回执'}</h3><p>{caseId === 'uniswap93' ? '1 亿 UNI 转入 dead 地址' : '同一交易中的 3 条目标 DAI Transfer'}</p><hr /><h4>与当前解释的关系</h4><p>回执支持执行事实；解释范围结合{info.contextLabel}与具体调查窗口判断。</p><dl><Detail name="回执状态">{String(receipt?.payload.status ?? '未提供')}</Detail><Detail name="交易哈希"><code>{info.tx}</code></Detail><Detail name="调用目标"><code>{String(receipt?.payload.to ?? '未提供')}</code></Detail></dl><SourceLink href={`https://etherscan.io/tx/${info.tx}`}>打开原始来源</SourceLink></article>
  }

  const sidePanel = evidenceTab && <aside ref={evidencePanel} className="replay-evidence" aria-labelledby="replay-evidence-title">
    <header><h2 id="replay-evidence-title" ref={evidenceTitle} tabIndex={-1}>{evidenceTab === 'verify' ? '本地复验' : '来源核读'}</h2><button className="replay-close" onClick={closeEvidence} aria-label="关闭来源侧栏">×</button></header>
    {evidenceTab !== 'verify' && <nav className="replay-evidence-tabs" aria-label="证据类型">{(['transfer', 'receipt', 'context', 'supply'] as const).map(tab => <button key={tab} aria-pressed={tab === evidenceTab} onClick={() => setEvidenceTab(tab)}>{tabLabels[tab]}</button>)}</nav>}
    <div className="replay-evidence-content">{evidenceBody()}</div>
    <footer>{evidenceTab === 'verify' ? '当前文件仅在此浏览器复验' : `${tabLabels[evidenceTab]} / 当前合成案卷 v${version}`}</footer>
  </aside>

  return <div className={`gcc-replay ${evidenceTab ? 'has-evidence' : ''} ${step === 3 ? 'is-review' : ''}`} onPointerDown={() => { pointerInput.current = true }} onKeyDown={() => { pointerInput.current = false }}>
    <header className="replay-page-heading">
      <div><h1 tabIndex={-1} ref={sectionTitle}>{step === 3 ? '每次更正，都能回到依据。' : info.title}</h1><p>{step === 3 ? '保留原版本、复核意见和引用证据，让下一位读者接着判断。' : `区块 ${info.window} · 公开历史案卷`}<span className="replay-mode">引导式回放 · 合成报告</span></p></div>
      <div className="replay-heading-actions">
        {step === 3 ? <button onClick={event => showEvidence('verify', event.currentTarget)} className="replay-button"><Glyph name="file" />复验证据包</button> : <button onClick={event => showEvidence('context', event.currentTarget)} className="replay-button" disabled={!bundle}><Glyph name="external" />查看来源</button>}
        <button onClick={download} className="replay-button is-primary" disabled={!bundle}><Glyph name="download" />导出 v{version} 案卷</button>
      </div>
    </header>
    <p className="replay-download-status" role="status">{downloadStatus}</p>
    {!bundles && <div className={`replay-loading ${error ? 'is-error' : ''}`} role="status">{error || '正在读取公开案卷并计算文件摘要…'}{error && <button className="replay-button" onClick={() => setRetry(value => value + 1)}>重新读取</button>}</div>}
    {step !== 3 && <nav className="replay-steps" aria-label="案例回放步骤">{['发现线索', '补查证据', '复核解释'].map((label, index) => <button key={label} aria-current={step === index + 1 ? 'step' : undefined} onClick={() => goStep(index + 1)}><span>{index + 1}</span><strong>{label}</strong>{index < 2 && <i aria-hidden="true">⟶</i>}</button>)}</nav>}
    <div className="replay-workspace">
      {step !== 3 ? <section className="replay-investigation" aria-label="调查回放">
        <aside className="replay-path"><h2>调查路径</h2><p className="replay-muted">按保存观察回放</p><ol>{pathTitles.map((title, index) => <li key={title} className={node === index ? 'is-active' : node > index ? 'is-done' : ''}><button onClick={event => { setNode(index); setStep(index === 0 ? 1 : 2); showEvidence(pathTabs[index], event.currentTarget) }} disabled={!bundle}><span className="replay-node">{index + 1}</span><span><strong>{title}</strong><small>{pathDescriptions[index]}</small></span></button></li>)}</ol><button className="replay-text-link replay-trace-link" onClick={event => showEvidence('trace', event.currentTarget)} disabled={!bundle}>查看保存观察 <Glyph name="arrow" /></button></aside>
        <article className="replay-report"><div className="replay-report-label"><h2>{step === 1 ? '一条待解释的线索' : '解释草稿'}</h2><span>合成示例</span></div>
          <h3 className="replay-report-headline">{step === 1 ? caseId === 'uniswap93' ? '1 亿 UNI，去了哪里？' : '三条转账，说明了什么？' : info.headline}</h3>
          <p className="replay-lede">{step === 1 ? info.introduction : info.summary} <button className="replay-citation" aria-label="打开转账证据引用 1" disabled={!bundle} onClick={event => showEvidence('transfer', event.currentTarget)}>[1]</button> <button className="replay-citation" aria-label="打开背景材料引用 2" disabled={!bundle} onClick={event => showEvidence('context', event.currentTarget)}>[2]</button></p>
          <div className="replay-findings">
            <section className="replay-finding support"><div className="replay-finding-status"><span><Glyph name="check" /></span><strong>支持</strong></div><div><h4>{info.supportTitle}</h4><p>{info.support}</p><button className="replay-citation" onClick={event => showEvidence('receipt', event.currentTarget)} disabled={!bundle}>核对回执</button></div></section>
            <section className="replay-finding boundary"><div className="replay-finding-status"><span><Glyph name="file" /></span><strong>边界</strong></div><div><h4>{info.boundaryTitle}</h4><p>{info.boundary}</p><button className="replay-citation" onClick={event => showEvidence(caseId === 'uniswap93' ? 'supply' : 'transfer', event.currentTarget)} disabled={!bundle}>展开依据</button></div></section>
            <section className="replay-finding unknown"><div className="replay-finding-status"><span>?</span><strong>未知</strong></div><div><h4>完整因果仍需结合调查范围核查</h4><p>{info.unknown}</p></div></section>
          </div>
          <div className="replay-next"><button className="replay-text-link" onClick={() => goStep(step === 1 ? 2 : 3)}>{step === 1 ? '沿证据补查' : '进入复核'} <Glyph name="arrow" /></button><button className="replay-citation" onClick={event => showEvidence('scope', event.currentTarget)} disabled={!bundle}>核对调查范围</button></div>
          <div className="replay-stats"><div><strong>{info.amount}</strong><span>{info.amountLabel}</span></div><div><strong>{info.blocks} 个区块</strong><span>窗口范围</span></div></div>
        </article>
      </section> : <section className="replay-review" aria-label="版本与复核">
        <div className="replay-version-line"><button aria-pressed={version === 1} onClick={() => setVersion(1)}><span>1</span><div><strong>v1</strong><small>初始解释</small></div></button><i aria-hidden="true">⟶</i><div><span className="is-teal">2</span><strong>复核意见</strong></div><i aria-hidden="true">⟶</i><button aria-pressed={version === 2} onClick={() => setVersion(2)}><span>3</span><div><strong>v2</strong><small>明确解释边界</small></div></button></div>
        <div className="replay-review-grid">
          <article className="replay-comparison"><header><h2>版本对照</h2><span>合成报告文本 · 中文摘要</span></header><div className="replay-version-columns">
            <section className={version === 1 ? 'is-selected' : ''}><button onClick={() => setVersion(1)} aria-pressed={version === 1}>v1 · 原版本{version === 1 && <small>已选版本</small>}</button><p>{info.v1}</p><span className="replay-muted">原文与引用保留</span></section>
            <section className={version === 2 ? 'is-selected' : ''}><button onClick={() => setVersion(2)} aria-pressed={version === 2}>v2 · 更正版本{version === 2 && <small>已选版本</small>}</button><p>{info.v2}<br /><mark>{info.correction}</mark></p><button className="replay-citation" disabled={!bundle} onClick={event => showEvidence(caseId === 'uniswap93' ? 'supply' : 'transfer', event.currentTarget)}>[2] 核对更正依据</button></section>
          </div><div className="replay-limit"><span>!</span><div><strong>调查边界</strong><p>{info.limit}</p></div></div><details className="replay-original"><summary>读取已选 v{version} 原报告与版本摘要</summary>{bundle && <><p>{bundle.report.conclusion.summary}</p><ul>{bundle.report.conclusion.claims.map((claim, index) => <li key={index}>{claim.text}</li>)}</ul><dl><Detail name="当前 manifest SHA-256"><code>{bundle.manifestHash}</code></Detail><Detail name="父版本 manifest SHA-256"><code>{bundle.report.parent_manifest_hash ?? '初始版本'}</code></Detail></dl></>}</details></article>
          <article className="replay-review-note"><h2>复核意见</h2><p className="replay-muted">合成示例复核者</p><blockquote>{info.review}</blockquote><div className="replay-review-success"><Glyph name="check" />已纳入 v2 合成文本</div><hr /><h3>本次依据</h3><button className="replay-evidence-link" onClick={event => showEvidence('transfer', event.currentTarget)} disabled={!bundle}><span><Glyph name="file" /></span><div><strong>[1] 转账记录</strong><small>{info.amount}</small></div></button><button className="replay-evidence-link" onClick={event => showEvidence(caseId === 'uniswap93' ? 'supply' : 'scope', event.currentTarget)} disabled={!bundle}><span><Glyph name="file" /></span><div><strong>[2] {caseId === 'uniswap93' ? '供给量核对' : '窗口与计算口径'}</strong><small>{caseId === 'uniswap93' ? '相邻两块均为 1,000,000,000 UNI' : '三块窗口 / DAI / 主体入减出'}</small></div></button><button className="replay-text-link" onClick={event => showEvidence('context', event.currentTarget)} disabled={!bundle}>打开来源与摘要 <Glyph name="arrow" /></button></article>
        </div><div className="replay-review-footer"><button className="replay-text-link" onClick={() => { setVersion(version === 2 ? 1 : 2) }}>查看{version === 2 ? '父版本 v1' : '更正版本 v2'}</button><p>{bundles ? 'v2 父摘要已与 v1 精确匹配。' : '正在核对版本关系。'}</p><button className="replay-text-link" onClick={event => showEvidence('verify', event.currentTarget)}>在浏览器复验 <Glyph name="arrow" /></button></div>
      </section>}
      {sidePanel}
    </div>
    <footer className="replay-page-footer"><p>公开历史观察 / {info.captured} 取得；引导步骤、报告与复核为合成示例。实际模型请求 0 次，签名与广播 0 次。</p><button className="replay-text-link" onClick={() => step === 3 ? goStep(2) : onWorkbench()}>{step === 3 ? '返回调查' : '返回公共案例'}</button><button className="replay-text-link" onClick={onTools}>打开证据包工具 <Glyph name="arrow" /></button></footer>
  </div>
}
