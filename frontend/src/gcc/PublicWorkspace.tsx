export default function PublicWorkspace({ onDemo }: {onDemo: (id: "uniswap93" | "euler-20230313") => void}) {
  return <section className="gcc-workspace gcc-public-workspace">
    <div className="gcc-page-heading"><div><span className="gcc-eyebrow">公开体验 · 无需配置</span><h1>先沿证据，走完一次调查。</h1><p>查看公开历史观察、核对引用，再比较报告版本。回放报告为标明来源的合成示例。</p></div></div>
    <div className="gcc-public-options"><article><span className="gcc-eyebrow">01 / 治理事件</span><h2>一亿 UNI 转账，意味着什么？</h2><p>转账记录 → 治理材料 → 供给核对 → 版本更正</p><button className="gcc-primary" onClick={() => onDemo("uniswap93")}>打开 UNI 案例</button></article><article><span className="gcc-eyebrow">02 / 安全事件</span><h2>Euler 的窗口净流，覆盖到哪里？</h2><p>两笔流入与一笔流出 → 调查范围 → 事故背景</p><button className="gcc-secondary" onClick={() => onDemo("euler-20230313")}>打开 Euler 案例</button></article></div>
    <section className="gcc-local-guide"><span className="gcc-eyebrow">实时 Agent / 本地工作台</span><h2>让模型现场选择补查，再生成报告。</h2><p>完整产品在你的电脑上运行。启动本地服务并配置模型会话后，选择案例与“实时 Agent”开始调查；模型调用使用你配置的预算。</p><div className="gcc-run-actions"><a className="gcc-secondary" href="http://127.0.0.1:5186/#/app" target="_blank" rel="noreferrer">打开本机工作台</a><a href="https://github.com/HNNUYUXUAN/cluetide-review/tree/GCC" target="_blank" rel="noreferrer">查看源码与启动说明 ↗</a></div><p className="gcc-source-note">本机入口连接当前设备。在线 Demo 提供公开回放、证据下载与浏览器复验。</p></section>
  </section>;
}
