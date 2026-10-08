import type { Health, Scope } from "../types";

export const liveAvailable = (health: Health | null) => health?.live_available === true || health?.live_agent_available === true || health?.capabilities?.live_agent_available === true;
export const executionName = (scope?: Scope) => scope?.agent_mode === "live" ? "实时 Agent" : "离线工具演示";
export const sourceName = (scope?: Scope) => scope?.mode === "rpc" ? "实时只读 RPC" : "公开缓存";
export const executionDescription = (scope: Scope) => scope.agent_mode === "live"
  ? scope.mode === "rpc" ? "现场读取链上数据，由真实模型选择补查并生成报告。" : "由真实模型现场分析已保存的公开证据，按线索补查并生成报告。"
  : scope.mode === "rpc" ? "现场读取链上数据，使用离线规则演示调查流程。" : "使用已保存证据和离线规则演示调查流程。";

export const stopExplanation = (reason?: string | null) => ({
  budget_or_price_gate: "本次请求未通过预算或报价检查。请核对剩余额度与当前报价，再开始新的调查。",
  provider_balance_exhausted: "模型账户可用余额不足。补充余额后可重新调查。",
  paid_session_unavailable: "实时模型会话已到期或尚未启用。请检查本地会话配置。",
  provider_preflight_or_transport_unavailable: "模型服务暂未连通，或余额、报价检查未完成。请检查网络后重试。",
  deadline_exceeded: "已达到本次调查的180秒时限。已取得的证据保留在当前案卷中。",
  user_stop: "调查已按你的请求停止，已取得的证据保留在当前案卷中。",
  model_request_limit: "已达到本次模型请求上限。可先核对已有证据，再决定是否重新调查。",
  tool_attempt_limit: "已达到补查次数上限。可核对已有证据或调整调查范围。",
  missing_finalized_anchor_or_transaction: "尚未取得可调查的交易或最终区块锚点，请核对范围与数据源。",
  agent_validation_or_runtime_error: "本轮未形成通过校验的完整报告，可核对已取得的证据与轨迹。",
}[reason ?? ""] ?? "本轮调查已结束，可核对已取得的证据和执行轨迹，再决定下一步。");
