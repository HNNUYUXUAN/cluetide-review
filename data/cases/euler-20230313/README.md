# Euler 2023-03-13 DAI 公共案卷

本目录保留 Euler V1 首笔 DAI 利用交易的公开来源和真实只读 RPC 捕获。交易由 Euler Labs 官方复盘链接，并由 Omniscia 技术复盘 Sources 第 6 项明确列出：`0xc310a0affe2169d1f6feec1c63dbc7f7c62a887fa48795d327d4d2da2d6b111d`。

## 调查参数与链上观察

| 参数 | 已核验值 |
| --- | --- |
| 链 | Ethereum 主网，chain ID 1 |
| 主体 | `0x27182842e098f60e3d576794a5bffb0777e025d3`，Euler V1 主协议地址 |
| 代币 | `0x6b175474e89094c44da98b954eedeac495271d0f`，DAI |
| 窗口 | 16817995–16817997，含首尾三块 |
| 事件区块 | 16817996，`0xde714b65354d54e684a95d80a1da7f668e8b2ee6ae8b5779f69ab9e04468b859` |
| 区块时间 | 2023-03-13T08:50:59Z |
| 回执 | 成功，transactionIndex 0，共 56 条日志 |
| 主体 outgoing | 1 条，日志 46，raw `38904507348306697267428294` |
| 主体 incoming | 2 条，日志 5 与 17，raw 分别为 `20000000000000000000000000` 与 `10000000000000000000000000` |

窗口末块哈希下的三个 EIP-1898 getter 返回 decimals 18、symbol DAI、name Dai Stablecoin。历史 `totalSupply()` 在 16817995、16817996 两块也使用已捕获的真实 blockHash 与 `requireCanonical=true`，都返回 raw `6250900648863044172620917232`。这些 DAI getter 不代表 Euler 内部 eDAI/dDAI 债务健康状态。

主体净 Transfer 流为 raw `-8904507348306697267428294`。它是本窗口 DAI 转账的入减出，不等同代币余额、攻击者利润、USD 损失或全部事件规模。回执另保留闪电贷借入及偿还日志；更广的多资产利用与后续资金恢复不在三块窗口内。

## 文件与取得状态

- `rpc.json`：严格窗口缓存，包括链 ID、provider-reported finalized 锚点、三个历史 header、双向日志、完整回执和交易、代币 metadata 与历史供给 getter。
- `raw/*.request.json`、`raw/*.response.json`：实际请求和响应原字节。`capture-*.json` 保留公共 endpoint、UTC 时间、HTTP 结果和摘要。初次普通本地环境的四次 TLS 传输失败保留在 `capture-initial-local.json`；随后在授权执行环境取得 14 个成功的公共只读结果。TLS 验证保持启用。
- `sources.json`、`raw/*.excerpt.txt`：来源署名、URL、节或源码行锚点、原始发布日、取得日、实际核读范围和短摘录 SHA-256。
- `normalized-transfers.json`、`case.json`、`verification.json`：精确 uint256 字符串、交易身份及相邻窗口 header 关联核验。
- `fixture-manifest.json`：公开证据文件的字节数与 SHA-256；采集/组装脚本和 manifest 自身独立保留。

首次失败捕获的记录原字节另存为 `capture-initial-local-original.json`。失败请求与后续成功请求使用相同公开字段而具有不同 JSON 字段顺序；失败请求的原序列化已按其当时记录的 SHA-256 精确恢复到 `raw/local-*.request.json`。规范记录链接恢复后的文件，成功捕获字节保持原样。采集脚本要求新的文件位置以保存后续捕获。

Euler Labs 官方复盘与 Omniscia 署名复盘均已取得并核读指定段落。固定 commit 的 `EToken.sol` 已核读 SPDX 及完整 `donateToReserves` 函数，许可说明为 `GPL-2.0-or-later`；本目录仅保留短摘录与原件指针。官方 V1 地址页取得的是搜索提供方留存的官方索引摘录；当前原 URL 打开后转向 V2 文档，故该条来源明确记录这一取得范围。

Omniscia 的 Attack Scenario 首段日期与该文章事件日期、Euler 官方时间线不同。案卷保留来源质量注记，链上时间使用本次真实区块 timestamp。

## 使用范围

来源类别为 `incident_postmortem`，提供历史事件解释材料。程序通过公共案件目录加载，并作为 `public_context_source` 交给调查工具。Transfer 大额规则产生调查线索，解释需对照回执与来源。

两向 `getLogs` 成功响应建立了所选 provider、本窗口、过滤条件的查询覆盖。三个相邻 header 的父哈希已核对；远端 finalized 锚点仍依赖 provider 报告。文件哈希不证明事实真实性。本次没有 trace RPC、内部状态重演、模型调用、签名或广播。
