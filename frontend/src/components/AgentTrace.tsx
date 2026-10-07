import type { Agent } from "../types";
import { integer, shortHash, statusName, toolName } from "../format";

function argumentsDescription(value: unknown, detailed: boolean): string {
  if (!value || typeof value !== "object") return "";
  const args = value as Record<string, unknown>;
  if (!detailed && typeof args.tx_hash === "string")
    return `Transaction ${shortHash(args.tx_hash)}`;
  if (!detailed && typeof args.source_id === "string")
    return `Source ${args.source_id}`;
  if (!detailed && typeof args.block_number === "number")
    return `Block ${integer(args.block_number)} · ${shortHash(String(args.token_address ?? ""))}`;
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
  const executionLabel = simulated ? "Offline FunctionModel simulation" :
    mode.includes("Deterministic empty-window") ? "Deterministic collection report" :
    mode.includes("no model request sent") ? "No model request sent" :
    mode.startsWith("Real model,") ? "Live model execution" : "";
  const traces = agent?.trace ?? [];
  const visible = detailed
    ? traces
    : traces.filter(
        (row) => row.event === "tool_selected" || row.event === "tool_rejected",
      );
  return (
    <section className="panel trace-panel">
      <h2>Agent investigation trace</h2>
      <p className="muted">Original evidence: tool observations and recorded reasons retain their source language.</p>
      {executionLabel && <p className="muted trace-execution">{executionLabel}</p>}
      {agent && (
        <p className="trace-meta">
          {statusName(agent.status)} · {simulated ? "Simulated requests" : "Model"} {agent.model_requests} / 6 · Tools{" "}
          {agent.tool_attempts} / 8
        </p>
      )}
      {!visible.length ? (
        <p className="muted">
          {agent ? "No investigation tool has run." : "Actual tool choices and observations appear after an investigation runs."}
        </p>
      ) : (
        <ol className="trace-list">
          {visible.map((row, index) => (
            <li key={index}>
              <span className="step-number">{index + 1}</span>
              <div>
                <h3>
                  {row.event === "model_request"
                    ? `${simulated ? "Simulated requests" : "Model request"} ${String(row.number ?? "")}`
                    : row.event === "tool_observed"
                      ? `${toolName(row.tool)} · ${statusName(String(row.status))}`
                      : row.event === "tool_rejected"
                        ? "Tool call limited"
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
                    <summary>View trace JSON</summary>
                    <pre>{JSON.stringify(row, null, 2)}</pre>
                  </details>
                )}
              </div>
            </li>
          ))}
        </ol>
      )}
      {agent?.stop_reason && (
        <p className="inline-notice">Stop reason: {agent.stop_reason}</p>
      )}
    </section>
  );
}
