# ClueTide 产品发布与验证 · Product release and validation

验证日期 / Verified: **2026-10-08 (Asia/Shanghai)**.

GCC 提供 Ethereum 调查、案卷交接与指定版本复核；BOT 将证据内容承诺、复核和更正关系记录在链上。下表列出各项验证对应的源码基线；当前源码和运行步骤由产品分支维护。

GCC provides Ethereum investigations, evidence handoffs, and exact-version reviews. BOT records evidence commitments, reviews, and corrections on chain. The table identifies the source baseline for each recorded validation; the product branches maintain the current source and setup instructions.

| 产品 / Product | 验证基线 / Validated baseline | 验证 / Validation |
| --- | --- | --- |
| [GCC](https://github.com/HNNUYUXUAN/cluetide-review/tree/GCC) | `f7c36a56300a2a34f67e3287939776e4b913875d` | 880 Python tests + 28 frontend tests passed |
| [BOT](https://github.com/HNNUYUXUAN/cluetide-review/tree/BOT) | `e3bf718d7653563f4da3f43b69d1a56f10b312d9` | Mainnet update: 28 Python + 8 frontend tests, 9 browser flows + 3 live readbacks passed |
| [GitHub Pages](https://github.com/HNNUYUXUAN/cluetide-app/commit/6bc9cefe525367009873c3f34aedb19c50075aaa) | `6bc9cefe525367009873c3f34aedb19c50075aaa` | Pages deployment succeeded; mainnet HTTPS page and all five proof files verified |

GCC 与 BOT 分别在独立 Python 环境安装锁定依赖、构建前端并完成测试。[GCC 来源说明](https://github.com/HNNUYUXUAN/cluetide-review/blob/GCC/SOURCE.md)与 [BOT 来源说明](https://github.com/HNNUYUXUAN/cluetide-review/blob/BOT/SOURCE.md)记录源码基线、证据范围和许可；各分支的来源清单记录文件摘要。

Each product was installed in an independent Python environment, built from locked frontend dependencies, and tested. The [GCC provenance](https://github.com/HNNUYUXUAN/cluetide-review/blob/GCC/SOURCE.md) and [BOT provenance](https://github.com/HNNUYUXUAN/cluetide-review/blob/BOT/SOURCE.md) documents identify source baselines, evidence scope, and licenses. Each branch's source manifest records file digests.

浏览器验收覆盖桌面 1536×1024 与手机 390×844。GCC 完成案例回放、证据下载与重新导入；BOT 完成首页、准确版本路由、原始证据包验证、开发者入口和工作台引导。BOT 两处归档引用缺口在字节校验通过后仍有明确提示。

Browser acceptance covered desktop 1536×1024 and mobile 390×844. GCC exercised case replay, evidence download and re-import. BOT exercised product navigation, exact-version routes, original bundle validation, developer links and workspace guidance. Its two archived citation gaps remain visible after successful byte verification.

- [GCC detailed validation](https://github.com/HNNUYUXUAN/cluetide-review/blob/GCC/docs/VALIDATION.md)
- [BOT detailed validation and visual review](https://github.com/HNNUYUXUAN/cluetide-review/blob/BOT/docs/validation.md)
- [GCC live demo](https://hnnuyuxuan.github.io/cluetide-app/gcc/)
- [BOT live demo](https://hnnuyuxuan.github.io/cluetide-app/bot/)

## 主网凭证 · Mainnet evidence

BOT 主网 677 的部署、v1、准确版本复核和 v2 共四笔成功交易，原始回执和完整 getter 清单可从[公开凭证](https://hnnuyuxuan.github.io/cluetide-app/bot/mainnet/bot-mainnet-workflow-20261008.json)下载。总费用为 0.04501682 BOT。表中 BOT 验证基线的 494 项 Git 文件字节已与来源清单逐项比对；主网与历史测试网记录分别校验，证据包保留原始字节。

Four successful transactions on BOT Mainnet 677 cover deployment, v1, its exact-version review, and v2. [Public proof](https://hnnuyuxuan.github.io/cluetide-app/bot/mainnet/bot-mainnet-workflow-20261008.json) includes original receipts and the complete getter inventory. Total fees were 0.04501682 BOT. The 494 committed files at the BOT validation baseline above matched its source manifest. Mainnet and historical testnet archives are validated separately, preserving original evidence bytes.

主网更新的检查覆盖链上记录、打包和前端行为；此前完整 1,078 项 Python 验证结果及主网更新的详细范围见 [BOT 验证记录](https://github.com/HNNUYUXUAN/cluetide-review/blob/BOT/docs/validation.md)。演示中的作者与复核者使用同一钱包；两项回执或交易引用仍缺少匹配的原始 RPC 观测，产品在文件完整性校验通过后继续展示这些待核事项。

The mainnet update tested the affected chain records, package, and frontend behavior. The [BOT validation record](https://github.com/HNNUYUXUAN/cluetide-review/blob/BOT/docs/validation.md) documents the preceding full 1,078-test Python run and the mainnet update's scope. The demonstration uses the same wallet for author and reviewer. Two receipt or transaction references still lack matching raw RPC observations; the product keeps those questions visible after file-integrity verification succeeds.
