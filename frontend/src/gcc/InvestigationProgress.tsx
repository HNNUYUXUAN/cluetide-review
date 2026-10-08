import type { Investigation } from "../types";
import { executionName, sourceName, stopExplanation } from "../state/execution";
import { toolName } from "../format";

export default function InvestigationProgress({ value, busy, onTab, onRepeat }: {
  value: Investigation; busy: boolean; onTab: (tab: "overview" | "evidence" | "trace" | "versions") => void; onRepeat: () => void;
}) {
  const progress = value.progress;
  const count = busy ? progress?.model_requests ?? 0 : value.agent?.model_requests ?? 0;
  const tools = busy ? progress?.tool_attempts ?? 0 : value.agent?.tool_attempts ?? 0;
  const stage = progress?.stage ?? (value.evidence ? "reasoning" : "collecting");
  const report = value.report?.conclusion ?? value.agent?.report;
  const trace = busy ? progress?.trace ?? [] : value.agent?.trace ?? [];
  const last = trace.at(-1);
  const title = busy ? ({collecting:"正在取得窗口内的链上观察",preflight:"正在核对模型会话、余额与报价",reading:"Agent 正在补查证据",reasoning:"Agent 正在分析证据",reporting:"正在形成引用报告",validating:"正在核对并修正报告引用"}[stage] ?? "调查正在进行")
    : value.status === "completed" ? "调查完成 · 接下来核对证据" : "调查已结束 · 已取得的证据可继续核对";
  return <section className={`gcc-run-status ${busy ? "is-running" : ""}`} aria-label="调查进度">
    <div className="gcc-run-heading"><div><span className="gcc-eyebrow">{sourceName(value.input)} · {executionName(value.input)}</span><h2 aria-live="polite">{title}</h2></div><span className="gcc-run-count">{value.input?.agent_mode === "live" ? "模型请求" : "模拟请求"} {count} / 6 · 补查 {tools} / 8</span></div>
    <ol className="gcc-run-steps" aria-label="调查步骤">{["取得观察", "分析与补查", "报告与复核"].map((label, index) => <li key={label} className={(index === 0 ? !!value.evidence : !busy && !!report) ? "done" : ""}>{index + 1}. {label}</li>)}</ol>
    {busy ? <p>{last?.event === "tool_selected" ? `正在${toolName(last.tool)}。` : last?.event === "tool_observed" ? `已返回${toolName(last.tool)}的读取结果，继续分析。` : "进度随实际请求与工具结果更新。可以查看当前证据，或停止调查。"} 本次最长180秒。</p>
      : <p>{value.status === "completed" ? "报告已保存。沿引用检查依据，再对准确版本留下复核意见或导出案卷。" : stopExplanation(value.agent?.stop_reason)}</p>}
    <div className="gcc-run-actions">{!busy && report && <button onClick={() => onTab("overview")}>阅读报告</button>}<button onClick={() => onTab("evidence")} disabled={!value.evidence}>查看证据</button><button onClick={() => onTab("trace")}>查看执行轨迹</button>{!busy && value.versions.length > 0 && <button onClick={() => onTab("versions")}>复核这个版本</button>}{!busy && <button onClick={onRepeat}>按此范围再调查</button>}</div>
  </section>;
}
