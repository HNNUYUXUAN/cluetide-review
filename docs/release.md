# 发布验收 · Release verification

验收日期 / Verified: **2026-10-08 (Asia/Shanghai)**.

| 发布项 / Release | Commit | 验证 / Validation |
| --- | --- | --- |
| [GCC](https://github.com/HNNUYUXUAN/cluetide-review/tree/GCC) | `f7c36a56300a2a34f67e3287939776e4b913875d` | 880 Python tests + 28 frontend tests passed |
| [BOT](https://github.com/HNNUYUXUAN/cluetide-review/tree/BOT) | `9ff17442c62ddbac5dfb4385b990d876c9a70361` | 1,078 Python tests + 8 frontend tests passed |
| [GitHub Pages](https://github.com/HNNUYUXUAN/cluetide-app/commit/399ad610c096cc3da469600fe79767cb40c5475d) | `399ad610c096cc3da469600fe79767cb40c5475d` | Pages build succeeded; both HTTPS demos opened |

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
