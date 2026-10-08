# ClueTide 项目与主网凭证 · Project and mainnet evidence

| 字段 / Field | 内容 / Value |
| --- | --- |
| 项目 / Project | ClueTide |
| GitHub 仓库 / Repository | https://github.com/HNNUYUXUAN/cluetide-review |
| 默认分支 / Default branch | main |
| GCC 源码 / GCC source | https://github.com/HNNUYUXUAN/cluetide-review/tree/GCC |
| BOT 源码 / BOT source | https://github.com/HNNUYUXUAN/cluetide-review/tree/BOT |
| GCC Demo | https://hnnuyuxuan.github.io/cluetide-app/gcc/ |
| BOT Demo | https://hnnuyuxuan.github.io/cluetide-app/bot/ |

## 产品说明

我们通过 ClueTide GCC 组织链上调查、证据和解释，通过 ClueTide BOT 保存内容承诺、指定版本复核与更正关系。`main` 提供两个产品的入口；`GCC` 与 `BOT` 分支分别维护源码、图文说明、在线应用、来源及本地运行步骤。在线应用提供公开案例，完整调查和持久化案件功能通过本地服务运行。

GCC 的 UNI 与 Euler 回放使用公开链上观测和明确标注的合成报告。BOT 的 UNI 案例展示真实归档模型报告及主网版本关系；作者与复核者在演示中使用同一钱包，两项引用仍缺少匹配的原始 RPC 观测。证据包、来源和验证范围均可沿产品文档核查。

## Product description

We use ClueTide GCC to organize on-chain investigations, evidence, and explanations, and ClueTide BOT to preserve content commitments, exact-version reviews, and corrections. The `main` branch introduces both products. The `GCC` and `BOT` branches each maintain source, illustrated documentation, a hosted application, provenance, and local setup instructions. Hosted applications provide public cases; full investigations and persistent case history run through the local service.

GCC's UNI and Euler replays combine public on-chain observations with clearly labelled synthetic reports. BOT's UNI case presents real archived model reports and mainnet version relationships. Its demonstration uses the same wallet for author and reviewer, and two citation references still lack matching raw RPC observations. Product documentation links the evidence packages, sources, and validation scope.

## BOT 主网凭证 · Mainnet evidence

**BOT Chain主网合约或应用地址**

- Network: BOT Chain Mainnet, chain ID 677.
- Contract: `0x951f7b5c68adba4cefd7fa851cb8e030426e81aa`.
- Explorer: https://scan.botchain.ai/address/0x951f7b5c68adba4cefd7fa851cb8e030426e81aa
- Application: https://hnnuyuxuan.github.io/cluetide-app/bot/

**可验证的主网交易记录**

| Step | Relationship | Transaction |
| --- | --- | --- |
| Registry deployment | Creation and runtime match the included artifact | [Open transaction](https://scan.botchain.ai/tx/0xd7d4d91b0b4158adb4da9cdccb0d71739fc670ba7801f382eb08742d3274fe8c) |
| v1 registration | Version 1, parent 0 | [Open transaction](https://scan.botchain.ai/tx/0x936c81539bfeafbc14cdd83ee27059d9979262267b74d3062aaef1e37804d398) |
| Exact-version review | Review 1 binds to version 1 | [Open transaction](https://scan.botchain.ai/tx/0x51af7682149dd9f28e6ceffe8e8044028d3ed437e2cfcfdc5f1dfa3facf7aabd) |
| Corrected version | Version 2, parent 1 | [Open transaction](https://scan.botchain.ai/tx/0xb1a45ace22fee58c97a0c218cb67deeea07cc5b778fa13371e730c56bda72328) |

四笔交易成功，回执、规范区块、部署字节码及合约 getter 均已通过主网 RPC 回验。最终状态为版本 2、父版本 1、一次绑定版本 1 的复核；总费用为 0.04501682 BOT。完整归档见[公开主网凭证](https://hnnuyuxuan.github.io/cluetide-app/bot/mainnet/bot-mainnet-workflow-20261008.json)。

All four transactions succeeded. Mainnet RPC verification checked receipts, canonical blocks, deployment bytecode, and contract getters. The final state is version 2 with parent 1 and one review bound to version 1; total fees were 0.04501682 BOT. The [public mainnet archive](https://hnnuyuxuan.github.io/cluetide-app/bot/mainnet/bot-mainnet-workflow-20261008.json) contains the verification details.

[产品发布与验证](release.md) · [BOT 来源说明](https://github.com/HNNUYUXUAN/cluetide-review/blob/BOT/SOURCE.md)
