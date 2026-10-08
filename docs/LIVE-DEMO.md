# 实时调查体验 / Live investigation

## 操作路径 / Walkthrough

1. 打开本地 `http://127.0.0.1:5186/#/app`，检查服务与实时 Agent 状态。选择案例，再选择“使用实时 Agent”。切换案例与重置范围保留当前执行方式。
2. 点击“开始实时调查”。公开缓存模式由真实模型分析保存的公开观察；只读 RPC 模式读取请求范围内的链上观察。页面分别标明数据来源与执行方式。
3. 查看真实阶段、请求计数与补查轨迹。每次最多 6 个模型请求、8 次工具尝试、180 秒；进度随请求及工具观察更新。
4. 完成后阅读报告、点击引用核对证据，再选择“复核这个版本”。复核绑定准确版本 ID 与内容摘要，ZIP 可在第二客户端复验。
5. “按此范围再调查”恢复表单；下一次模型执行由开始按钮发起。部分完成与早停保留证据及实际停止原因。

Open the local workbench, select a case and live execution, then start the investigation. Case changes preserve the selected data and execution modes. Progress shows observed stages, model requests and tool attempts. Follow the completed report into citations, evidence and exact-version review. Reusing the scope returns to the form; starting another run is an explicit action.

## 配置 / Configuration

默认启动脚本使用离线预览。实时执行需配置 `TOKENDANCE_API_KEY`，提供有效的本地 `local-data/paid-session.json`（`enabled: true` 和未来的 UTC `expires_at_utc`），并在启动服务时设置 `CLUETIDE_PREVIEW_ONLY=0`。会话配置与凭据保存在本地。

`CLUETIDE_BUDGET_PATH` 可指向已有累计账本；未配置时使用当前状态目录。源码累计预算上限为人民币 20 元。已有账本保留原上限，扩大已有授权需由账本所有者明确批准并保留原记录。页面展示保守预留余额；每次付费请求继续核对有效会话、报价、账户余额和账本额度。预留金额与实际扣费分别记录。

The default launcher uses offline preview. Live execution requires local gateway credentials, an enabled session with a future UTC expiry, and `CLUETIDE_PREVIEW_ONLY=0`. The optional `CLUETIDE_BUDGET_PATH` shares an existing cumulative ledger. The source cap is CNY 20; existing ledgers retain their recorded cap. Changing an existing allowance requires explicit owner authorization and preservation of history. Every paid request checks session, pricing, balance and conservative reservations.

## 公开 Demo / Hosted demo

[公开工作台入口](https://hnnuyuxuan.github.io/cluetide-app/gcc/#/app)提供 UNI / Euler 回放、证据下载、浏览器复验和本地启动指引。回放报告为标明来源的合成示例；本机工作台链接指向访问网页的当前设备。

The hosted entry provides public replays, evidence downloads, browser verification and local setup guidance. Replay reports are labelled synthetic examples. The loopback link opens the workbench on the device viewing the page.

公开构建设置 `VITE_STATIC_DEMO=1`；普通构建提供完整本地工作台。/ Set `VITE_STATIC_DEMO=1` for the hosted static build; a normal build provides the local API workbench.
