import { useRef, useState, type KeyboardEvent, type ReactNode } from 'react'
import './story.css'

type StoryPageProps = {
  onDemo: (caseId: 'uniswap93' | 'euler-20230313') => void
  onWorkbench: () => void
  onTools: () => void
}

type IconName = 'arrow' | 'external' | 'transfer' | 'wallet' | 'document' | 'governance' | 'check' | 'question' | 'chevron' | 'archive'

const transactionUrl = 'https://etherscan.io/tx/0x091f0083242a777d55821c1189e568d6d033d9da501b75087dc736fa143d2c1e'
const proposalUrl = 'https://vote.uniswapfoundation.org/proposals/93'

function Icon({ name, className = '' }: { name: IconName; className?: string }) {
  const paths: Record<IconName, ReactNode> = {
    arrow: <><path d="M4 12h15M13 5l7 7-7 7" /></>,
    external: <><path d="M7 17 17 7M7 7h10v10" /></>,
    transfer: <><path d="M3 12h17M13 5l7 7-7 7" /></>,
    wallet: <><path d="M20 8H5a2 2 0 0 1 0-4h13v4M20 8v12H5a2 2 0 0 1-2-2V6" /><path d="M20 12h-6v4h6" /></>,
    document: <><path d="M6 3h9l4 4v14H6zM14 3v5h5M9 12h7M9 16h7" /></>,
    governance: <><path d="m3 8 9-5 9 5M3 9h18M4 21h16M6 12v6M12 12v6M18 12v6" /></>,
    check: <path d="m5 12 4 4L19 6" />,
    question: <><path d="M8 8a4 4 0 0 1 8 0c0 3-4 3-4 6" /><path d="M12 18h.01" /></>,
    chevron: <path d="m6 9 6 6 6-6" />,
    archive: <><path d="M4 8h16v13H4zM3 3h18v5H3zM9 12h6" /></>,
  }
  return <svg className={`gcc-story-icon ${className}`} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">{paths[name]}</svg>
}

function SourceLink({ href, children }: { href: string; children: ReactNode }) {
  return <a className="gcc-story-source" href={href} target="_blank" rel="noreferrer">{children}<Icon name="external" /><span className="gcc-story-sr-only">（新窗口）</span></a>
}

const stages = [
  { title: '发现线索', subtitle: '从大额转账出发' },
  { title: '补查证据', subtitle: '把观察连到来源' },
  { title: '复核解释', subtitle: '交接准确的版本' },
]

export default function StoryPage({ onDemo, onWorkbench, onTools }: StoryPageProps) {
  const [activeStage, setActiveStage] = useState(0)
  const [scopeOpen, setScopeOpen] = useState(false)
  const [inputMode, setInputMode] = useState<'pointer' | 'keyboard'>('pointer')
  const tabs = useRef<(HTMLButtonElement | null)[]>([])
  const scope = useRef<HTMLDetailsElement>(null)

  function moveTab(event: KeyboardEvent<HTMLButtonElement>, index: number) {
    const next = event.key === 'ArrowRight' ? (index + 1) % stages.length
      : event.key === 'ArrowLeft' ? (index + stages.length - 1) % stages.length
        : event.key === 'Home' ? 0 : event.key === 'End' ? stages.length - 1 : null
    if (next === null) return
    event.preventDefault()
    setActiveStage(next)
    tabs.current[next]?.focus()
  }

  function showScope(keyboard: boolean) {
    setActiveStage(0)
    setScopeOpen(true)
    requestAnimationFrame(() => {
      const reduceMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches
      scope.current?.scrollIntoView({ behavior: keyboard || reduceMotion ? 'instant' : 'smooth', block: 'center' })
      scope.current?.querySelector('summary')?.focus({ preventScroll: true })
    })
  }

  return (
    <div className="gcc-story" data-input={inputMode} onPointerDownCapture={() => setInputMode('pointer')} onKeyDownCapture={() => setInputMode('keyboard')}>
      <section className="gcc-story-hero" aria-labelledby="gcc-story-title">
        <div className="gcc-story-intro">
          <h1 id="gcc-story-title">让团队带着证据，<br />判断链上异动。</h1>
          <p className="gcc-story-deck">从一条大额转账线索出发，查看补查过程、证据边界与更正记录。</p>
          <div className="gcc-story-hero-actions">
            <button className="gcc-story-primary" onClick={() => onDemo('uniswap93')}>体验 UNI 案例<Icon name="arrow" /></button>
            <button className="gcc-story-text-button" aria-controls="gcc-story-scope" onClick={(event) => showScope(event.detail === 0)}>了解调查范围<Icon name="arrow" /></button>
          </div>
          <p className="gcc-story-provenance">公开历史事件<span aria-hidden="true"> · </span>引导式案例回放</p>
        </div>

        <div className="gcc-story-case" aria-label="UNI 事件的线索、证据与解释">
          <div className="gcc-story-case-heading">
            <h2>1 亿 UNI 转入 dead 地址</h2>
            <p>区块 <span className="gcc-story-number">24106368–24106388</span></p>
          </div>
          <div className="gcc-story-evidence-flow">
            <section className="gcc-story-flow-column gcc-story-flow-blue">
              <div className="gcc-story-flow-step"><span>1</span><i aria-hidden="true" /></div>
              <h3>转账线索</h3>
              <div className="gcc-story-flow-content">
                <div className="gcc-story-fact"><span className="gcc-story-icon-disc"><Icon name="transfer" /></span><strong className="gcc-story-transfer-value">100,000,000 UNI</strong></div>
                <div className="gcc-story-fact"><span className="gcc-story-icon-disc"><Icon name="wallet" /></span><strong>转入 dead 地址</strong></div>
                <SourceLink href={transactionUrl}>打开交易来源</SourceLink>
              </div>
            </section>
            <section className="gcc-story-flow-column gcc-story-flow-teal">
              <div className="gcc-story-flow-step"><span>2</span><i aria-hidden="true" /></div>
              <h3>交易与治理证据</h3>
              <div className="gcc-story-flow-content">
                <div className="gcc-story-fact"><span className="gcc-story-icon-disc gcc-story-icon-blue"><Icon name="document" /></span><div><strong>Transfer 已执行</strong><SourceLink href={`${transactionUrl}#eventlog`}>交易回执</SourceLink></div></div>
                <div className="gcc-story-fact"><span className="gcc-story-icon-disc"><Icon name="governance" /></span><div><strong>治理背景</strong><SourceLink href={proposalUrl}>提案 93</SourceLink></div></div>
              </div>
            </section>
            <section className="gcc-story-flow-column gcc-story-flow-amber">
              <div className="gcc-story-flow-step"><span>3</span></div>
              <h3>可复核的解释</h3>
              <div className="gcc-story-flow-content">
                <div className="gcc-story-fact gcc-story-judgment"><span className="gcc-story-icon-disc gcc-story-icon-teal"><Icon name="check" /></span><strong>治理执行：<br className="gcc-story-judgment-break" />有证据支持</strong></div>
                <div className="gcc-story-fact gcc-story-judgment"><span className="gcc-story-icon-disc"><Icon name="question" /></span><strong>完整因果：<br className="gcc-story-judgment-break" />继续核查</strong></div>
              </div>
            </section>
          </div>
        </div>
      </section>

      <section className="gcc-story-narrative" aria-label="一笔交易成为可交接案卷的过程">
        <div className="gcc-story-narrative-main">
          <div className="gcc-story-tabs" role="tablist" aria-label="调查故事" aria-orientation="horizontal">
            {stages.map((stage, index) => (
              <button key={stage.title} id={`gcc-story-tab-${index}`} role="tab" aria-selected={activeStage === index} aria-controls={`gcc-story-panel-${index}`} tabIndex={activeStage === index ? 0 : -1} ref={(element) => { tabs.current[index] = element }} onClick={() => setActiveStage(index)} onKeyDown={(event) => moveTab(event, index)}>
                <span className="gcc-story-tab-number">{index + 1}</span><span><strong>{stage.title}</strong><small>{stage.subtitle}</small></span>
              </button>
            ))}
            <span className="gcc-story-tab-indicator" style={{ transform: `translateX(${activeStage * 100}%)` }} aria-hidden="true" />
          </div>

          <div className="gcc-story-panel" id="gcc-story-panel-0" role="tabpanel" aria-labelledby="gcc-story-tab-0" hidden={activeStage !== 0} tabIndex={0}>
            <h2>一笔交易，怎样成为可交接的案卷？</h2>
            <p className="gcc-story-panel-deck">先把一条告警，变成范围清楚的观察。</p>
            <div className="gcc-story-observation">
              <div className="gcc-story-observation-label"><span className="gcc-story-small-dot" />已观察到的转账</div>
              <div className="gcc-story-transfer-line"><span>Uniswap Timelock<small>财库执行合约</small></span><span className="gcc-story-transfer-connector"><b>100,000,000 UNI</b><Icon name="arrow" /></span><span>dead 地址<small>0x0000…dead</small></span></div>
              <p>回执中日志索引为 11 的记录对应这笔转账。确定收发方、金额和执行交易后，再核对这笔操作的治理背景。</p>
            </div>
            <details id="gcc-story-scope" className="gcc-story-scope" ref={scope} open={scopeOpen} onToggle={(event) => setScopeOpen(event.currentTarget.open)}>
              <summary><span>这次调查覆盖到哪里？<small>Ethereum · 21 个区块</small></span><Icon name="chevron" /></summary>
              <div className="gcc-story-scope-content">
                <dl><div><dt>调查窗口</dt><dd>24106368–24106388</dd></div><div><dt>主体与代币</dt><dd>Uniswap Timelock / UNI</dd></div><div><dt>窗口内返回</dt><dd>5 条 UNI Transfer 日志</dd></div><div><dt>主体相关转账</dt><dd>1 笔流出，0 笔流入</dd></div></dl>
                <p>这些观察来自 2026-10-06 UTC 捕获的公共 RPC 缓存。窗口长度和查询完成度分别核对；公开案卷保留成功、空结果及失败读取的记录。</p>
                <p>此窗口支持核查所述 Timelock 事件。完整因果、窗口外交易和独立共识证明仍需对应的证据。</p>
                <button className="gcc-story-text-button" onClick={() => onDemo('uniswap93')}>在案卷中核对范围<Icon name="arrow" /></button>
              </div>
            </details>
          </div>

          <div className="gcc-story-panel" id="gcc-story-panel-1" role="tabpanel" aria-labelledby="gcc-story-tab-1" hidden={activeStage !== 1} tabIndex={0}>
            <h2>沿着一个问题，补上下一份证据。</h2>
            <p className="gcc-story-panel-deck">交易说明发生了什么，治理材料提供背景，历史状态限定解释。</p>
            <ol className="gcc-story-evidence-list">
              <li><span className="gcc-story-evidence-number">1</span><div><h3>核对执行与回执</h3><p>GovernorBravo 调用 execute(93)，成功回执包含转入 dead 地址的 UNI Transfer。</p><SourceLink href={transactionUrl}>查看执行交易</SourceLink></div></li>
              <li><span className="gcc-story-evidence-number">2</span><div><h3>连接治理来源</h3><p>提案 93 中的转账动作与链上观察相符，为治理执行这一解释提供支持。</p><SourceLink href={proposalUrl}>查看提案 93</SourceLink></div></li>
              <li><span className="gcc-story-evidence-number">3</span><div><h3>检查相邻区块的供应量</h3><p>已保存的历史 totalSupply() 读取，在区块 24106377 与 24106378 均返回 10 亿 UNI。转账和供应量是两个分别核查的事实。</p><button className="gcc-story-text-button" onClick={() => onDemo('uniswap93')}>查看证据与补查过程<Icon name="arrow" /></button></div></li>
            </ol>
          </div>

          <div className="gcc-story-panel" id="gcc-story-panel-2" role="tabpanel" aria-labelledby="gcc-story-tab-2" hidden={activeStage !== 2} tabIndex={0}>
            <h2>把判断与依据，一起交给下一位。</h2>
            <p className="gcc-story-panel-deck">每条解释连接证据，每次复核指向准确的版本。</p>
            <div className="gcc-story-review-flow">
              <div><span className="gcc-story-version-label">原始 v1</span><h3>保存当时的报告</h3><p>保留解释、引用和调查范围，让接续调查的人能够回到相同依据。</p></div>
              <div><span className="gcc-story-version-label gcc-story-version-teal">复核记录</span><h3>把意见留在此版本</h3><p>核对来源和数值，记录仍待确定的问题，以及更正的具体依据。</p></div>
              <div><span className="gcc-story-version-label gcc-story-version-amber">更正 v2</span><h3>让修改可以追溯</h3><p>保留父版本与修改理由，再导出对应版本的证据包。</p></div>
            </div>
            <p className="gcc-story-version-note">公开体验使用合成 v1/v2 演示复核关系；真实模型报告与人工更正按各自来源保存。</p>
            <div className="gcc-story-panel-actions"><button className="gcc-story-primary gcc-story-primary-small" onClick={() => onDemo('uniswap93')}>体验版本复核<Icon name="arrow" /></button><button className="gcc-story-text-button" onClick={onTools}>复验证据包<Icon name="archive" /></button></div>
          </div>
        </div>

        <aside className="gcc-story-aside">
          <p>每一步都能打开来源。<br />每次更正都保留原版本。</p>
          <button className="gcc-story-text-button" onClick={() => onDemo('euler-20230313')}>查看 Euler 案例<Icon name="arrow" /></button>
          <div className="gcc-story-aside-note"><span>另一个事件，相同的调查方法</span><p>从首笔 DAI 交易出发，连接链上观察与事故复盘材料。</p><small>Ethereum 16817995–16817997</small></div>
        </aside>
      </section>

      <section className="gcc-story-handoff" aria-labelledby="gcc-story-handoff-title">
        <div><h2 id="gcc-story-handoff-title">带着证据，开始下一次调查。</h2><p>选择公开案例，或在工作台中接续一份已有案卷。</p></div>
        <div className="gcc-story-handoff-actions"><button className="gcc-story-text-button" onClick={onTools}>证据包复验<Icon name="archive" /></button><button className="gcc-story-primary" onClick={onWorkbench}>打开工作台<Icon name="arrow" /></button></div>
      </section>
    </div>
  )
}
