# 发布验收 · Release verification

验收日期 / Verified: **2026-10-08 (Asia/Shanghai)**.

| 发布项 / Release | Commit | 验证 / Validation |
| --- | --- | --- |
| [GCC](https://github.com/HNNUYUXUAN/cluetide-review/tree/GCC) | `f7c36a56300a2a34f67e3287939776e4b913875d` | 880 Python tests + 28 frontend tests passed |
| [BOT](https://github.com/HNNUYUXUAN/cluetide-review/tree/BOT) | `e3bf718d7653563f4da3f43b69d1a56f10b312d9` | Mainnet update: 28 Python + 8 frontend tests, 9 browser flows + 3 live readbacks passed |
| [GitHub Pages](https://github.com/HNNUYUXUAN/cluetide-app/commit/6bc9cefe525367009873c3f34aedb19c50075aaa) | `6bc9cefe525367009873c3f34aedb19c50075aaa` | Pages deployment succeeded; mainnet HTTPS page and all five proof files verified |

GCC 与 BOT 分别在独立 Python 环境安装锁定依赖、构建前端并完成测试。各分支记录精确文件摘要和源码来源，附带第三方原始许可。源码发布与 demo 均经过私有状态及凭据扫描。

Each track was installed in an independent Python environment, built from locked frontend dependencies, and tested. Branch provenance documents and file manifests record the release bytes. Original dependency notices are included. Release source and demos passed private-state and configured-credential checks.

浏览器验收覆盖桌面 1536×1024 与手机 390×844。GCC 完成案例回放、证据下载与重新导入；BOT 完成首页、准确版本路由、原始证据包验证、开发者入口和工作台引导。BOT 两处归档引用缺口在字节校验通过后仍有明确提示。

Browser acceptance covered desktop 1536×1024 and mobile 390×844. GCC exercised case replay, evidence download and re-import. BOT exercised product navigation, exact-version routes, original bundle validation, developer links and workspace guidance. Its two archived citation gaps remain visible after successful byte verification.

- [GCC detailed validation](https://github.com/HNNUYUXUAN/cluetide-review/blob/GCC/docs/VALIDATION.md)
- [BOT detailed validation and visual review](https://github.com/HNNUYUXUAN/cluetide-review/blob/BOT/docs/validation.md)
- [GCC live demo](https://hnnuyuxuan.github.io/cluetide-app/gcc/)
- [BOT live demo](https://hnnuyuxuan.github.io/cluetide-app/bot/)

评审源码仓库当前为私有，默认分支为 `main`；公开访问要求见 [提交说明](submission.md)。

The review repository is private with `main` as the default branch. The public-access condition is explained in the [submission notes](submission.md).

## 主网凭证 · Mainnet evidence

BOT 主网 677 的部署、v1、准确版本复核和 v2 共四笔成功交易，原始回执和完整 getter 清单可从[公开凭证](https://hnnuyuxuan.github.io/cluetide-app/bot/mainnet/bot-mainnet-workflow-20261008.json)下载。总费用为 0.04501682 BOT。评审 BOT 分支的 494 项 Git 文件字节已与来源清单逐项比对；主网故事与旧测试网故事分别校验，旧证据包按原始字节保留。

Four successful transactions on BOT Mainnet 677 cover deployment, v1, its exact-version review, and v2. [Public proof](https://hnnuyuxuan.github.io/cluetide-app/bot/mainnet/bot-mainnet-workflow-20261008.json) includes original receipts and the complete getter inventory. Total fees were 0.04501682 BOT. All 494 committed BOT release files match the source manifest. Mainnet and historical testnet story archives are validated separately, preserving original evidence bytes.

本轮检查针对主网记录、打包和前端更新；前次 BOT 完整 1,078 项 Python 验证结果及本轮详细范围见对应分支的 validation 文档。比赛表单第 7、8 项已填入并回读，完整表单待作者补齐其他必填项后提交。

This update tested the affected mainnet story, package, and frontend behavior. The preceding full 1,078-test Python run and current validation scope are recorded in the BOT validation document. Form fields 7 and 8 were filled and read back; the author must complete the remaining required fields before submitting the whole form.
