# 作品提交信息 · Submission details

| 字段 / Field | 内容 / Value |
| --- | --- |
| 项目 / Project | ClueTide |
| GitHub 仓库 / Repository | https://github.com/HNNUYUXUAN/cluetide-review |
| 默认分支 / Default branch | main |
| GCC 源码 / GCC source | https://github.com/HNNUYUXUAN/cluetide-review/tree/GCC |
| BOT 源码 / BOT source | https://github.com/HNNUYUXUAN/cluetide-review/tree/BOT |
| GCC Demo | https://hnnuyuxuan.github.io/cluetide-app/gcc/ |
| BOT Demo | https://hnnuyuxuan.github.io/cluetide-app/bot/ |
| 当前仓库可见性 / Current visibility | 私有 / Private |

## 可直接填写的说明

ClueTide 的拟开源评审仓库当前为私有。main 分支提供 GCC 与 BOT 两个独立赛道的评审导航；各赛道分支附源码、图文说明、在线 Demo、来源与本地复现步骤。请主办方确认私有仓库的评审访问安排；如按公开仓库要求提交，由项目作者在正式提交前切换可见性。Demo 页面已采用公开案例及可分发材料。

本文件提供填写内容。表单提交、主办方联系及评审账号授权以实际执行记录为准。

## Suggested form note

ClueTide's review repository is currently private and prepared for open-source publication. The main branch directs reviewers to separate GCC and BOT branches, each with source, illustrated documentation, a live demo, attribution, and local reproduction steps. Please confirm a private-repository review arrangement with the organizers; otherwise, the project author can make the repository public before formal submission. The demo sites use public cases and distributable materials.

This file supplies the form content. Form submission, organizer contact, and reviewer access grants are recorded separately when performed.

## BOT 主网提交凭证 · Mainnet submission evidence

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

2026-10-08：上述两个字段已填写并从浏览器回读；整张报名表仍由作者补齐其他必填项后提交。 / These two fields were filled and read back on 8 October 2026; the author must complete the remaining required fields before submitting the full form.
