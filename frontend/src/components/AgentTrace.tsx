import type { Agent } from "../types";
import { integer, shortHash, statusName, toolName } from "../format";

function argumentsDescription(value: unknown, detailed: boolean): string {
  if (!value || typeof value !== "object") return "";
  const args = value as Record<string, unknown>;
  if (!detailed && typeof args.tx_hash === "string")
    return `交易 ${shortHash(args.tx_hash)}`;
  if (!detailed && typeof args.source_id === "string")
    return `来源 ${args.source_id}`;
  if (!detailed && typeof args.block_number === "number")
    return `区块 ${integer(args.block_number)} · ${shortHash(String(args.token_address ?? ""))}`;
  return Object.entries(args)
    .map(([key, item]) => `${key}: ${String(item)}`)
    .join(" · ");
}

export default function AgentTrace({
  agent,
  detailed = false,
  executionMode,
}: {
  agent: Agent | null;
  detailed?: boolean;
  executionMode?: string;
}) {
  const mode = typeof executionMode === "string" ? executionMode : "";
  const simulated = mode.includes("FunctionModel");
  const executionLabel = simulated ? "FunctionModel 离线模拟" :
    mode.includes("Deterministic empty-window") ? "确定性采集报告" :
    mode.includes("no model request sent") ? "未发送模型请求" :
    mode.startsWith("Real model,") ? "真实模型执行" : "";
  const traces = agent?.trace ?? [];
  const visible = detailed
    ? traces
    : traces.filter(
        (row) => row.event === "tool_selected" || row.event === "tool_rejected",
      );
  return (
    <section className="panel trace-panel">
      <h2>Agent 补查轨迹</h2>
      {executionLabel && <p className="muted trace-execution">{executionLabel}</p>}
      {agent && (
        <p className="trace-meta">
          {statusName(agent.status)} · {simulated ? "模拟请求" : "模型"} {agent.model_requests} / 6 · 工具{" "}
          {agent.tool_attempts} / 8
        </p>
      )}
      {!visible.length ? (
        <p className="muted">
          {agent ? "未执行补查工具。" : "调查运行后显示实际工具选择与观察。"}
        </p>
      ) : (
        <ol className="trace-list">
          {visible.map((row, index) => (
            <li key={index}>
              <span className="step-number">{index + 1}</span>
              <div>
                <h3>
                  {row.event === "model_request"
                    ? `${simulated ? "模拟请求" : "模型请求"} ${String(row.number ?? "")}`
                    : row.event === "tool_observed"
                      ? `${toolName(row.tool)} · ${statusName(String(row.status))}`
                      : row.event === "tool_rejected"
                        ? "工具调用受限"
                        : toolName(row.tool ?? row.event)}
                </h3>
                <p title={JSON.stringify(row.arguments ?? {})}>
                  {row.event === "model_request"
                    ? String(row.model ?? "")
                    : row.event === "tool_observed"
                      ? String(row.evidence_id ?? "")
                      : row.event === "tool_rejected"
                        ? String(row.reason ?? "")
                        : row.arguments
                          ? argumentsDescription(row.arguments, detailed)
                          : String(row.reason ?? "")}
                </p>
                {detailed && (
                  <details>
                    <summary>查看轨迹 JSON</summary>
                    <pre>{JSON.stringify(row, null, 2)}</pre>
                  </details>
                )}
              </div>
            </li>
          ))}
        </ol>
      )}
      {agent?.stop_reason && (
        <p className="inline-notice">停止原因：{agent.stop_reason}</p>
      )}
    </section>
  );
}
