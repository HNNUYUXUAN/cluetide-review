export type ReplayCaseId = 'uniswap93' | 'euler-20230313'
export type ReplayVersion = 1 | 2

export const replayCases = {
  uniswap93: {
    title: 'UNI 大额转账调查', short: 'UNI 案卷', symbol: 'UNI',
    window: '24106368–24106388', blocks: 21, captured: '2026-10-06 UTC',
    amount: '100,000,000 UNI', amountLabel: '转账金额',
    introduction: '1 亿 UNI 从 Timelock 转入 dead 地址。先核对转账事实，再查阅治理背景。',
    headline: '从转账事实，走向有依据的解释。',
    summary: '回执记录执行成功，提案 93 提供治理背景。合成报告将完整因果留待复核。',
    supportTitle: '交易回执与治理材料',
    support: '回执支持转账已执行；提案列出的目标与金额可与链上记录对照。',
    boundaryTitle: '转账与供给量是两项主张',
    boundary: '相邻两块的 totalSupply() 均为 10 亿 UNI。单笔转账不能直接说明整体供给量变化。',
    unknown: '更广的资金流向、账户控制状态及完整因果，仍需扩大调查范围。',
    contextLabel: '治理材料', contextDetail: '提案 93',
    v1: '转入 dead 地址，金额为 100,000,000 UNI。',
    v2: '转入 dead 地址，金额为 100,000,000 UNI。',
    correction: '这笔 Transfer 本身不能证明 totalSupply 变化。',
    review: '在金额主张中明确供给量边界，保留原始观察与引用。',
    limit: '21 块窗口支持已记录的转账事实；完整因果仍需结合更广范围核查。',
    tx: '0x091f0083242a777d55821c1189e568d6d033d9da501b75087dc736fa143d2c1e',
  },
  'euler-20230313': {
    title: 'Euler DAI 转账调查', short: 'Euler 案卷', symbol: 'DAI',
    window: '16817995–16817997', blocks: 3, captured: '2026-10-07 UTC',
    amount: '1 笔流出 / 2 笔流入', amountLabel: '主体 DAI Transfer',
    introduction: '从 Euler 首笔 DAI 交易出发，连接三条 Transfer、成功回执与署名事故复盘。',
    headline: '先确认观察，再限定解释的范围。',
    summary: '三块窗口记录一笔流出和两笔流入。Euler Labs 与 Omniscia 提供事故背景。',
    supportTitle: '转账记录与成功回执',
    support: '同一笔交易中，主体收到 3,000 万 DAI，并转出约 3,890.45 万 DAI。精确原始值可逐条核对。',
    boundaryTitle: '净转账流限定于主体与窗口',
    boundary: '入减出为 −8,904,507.348306697267428294 DAI。该数值不等同利润、美元损失或全部事件规模。',
    unknown: '内部债务健康状态、多资产累计影响与后续资金恢复，需要额外调查。',
    contextLabel: '事故复盘', contextDetail: 'Euler Labs / Omniscia',
    v1: '本窗口记录 Euler 主体的 DAI 流入、流出。',
    v2: '本窗口记录 Euler 主体的 DAI 流入、流出。',
    correction: '入减出只表示所选窗口的净 Transfer 流，不能直接用作事件损失。',
    review: '补充净流量的计算口径，明确主体、代币与三块窗口的边界。',
    limit: '本回放覆盖首笔 DAI 交易；内部状态、多资产事件与后续恢复仍在调查范围之外。',
    tx: '0xc310a0affe2169d1f6feec1c63dbc7f7c62a887fa48795d327d4d2da2d6b111d',
  },
} as const

export function formatRaw(raw: string, decimals = 18): string {
  const negative = raw.startsWith('-')
  const digits = (negative ? raw.slice(1) : raw).padStart(decimals + 1, '0')
  const whole = (decimals ? digits.slice(0, -decimals) : digits).replace(/\B(?=(\d{3})+(?!\d))/g, ',')
  const fraction = decimals ? digits.slice(-decimals).replace(/0+$/, '') : ''
  return `${negative ? '−' : ''}${whole}${fraction ? `.${fraction}` : ''}`
}
