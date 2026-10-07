import { useState } from "react";
import type { Health, HistoryItem, Preset, Scope, Screen } from "../types";
import Icon from "./Icon";

interface Props {
  screen: Screen;
  onScreen: (screen: Screen) => void;
  scope: Scope;
  onScope: (scope: Scope) => void;
  onStart: () => void;
  onPreset: (id: string) => void;
  presets: Preset[];
  presetLoading: boolean;
  onStop: () => void;
  busy: boolean;
  stopping: boolean;
  health: Health | null;
  history: HistoryItem[];
  onOpen: (id: string) => void;
}
export default function Sidebar(props: Props) {
  const [formError, setFormError] = useState("");
  const selectedPreset = props.presets.find((item) =>
    item.address.toLowerCase() === props.scope.address.toLowerCase() &&
    item.token_address.toLowerCase() === props.scope.token_address.toLowerCase() &&
    item.from_block === props.scope.from_block && item.to_block === props.scope.to_block)?.case_id ?? "";
  const set = (field: keyof Scope, value: string) =>
    props.onScope({
      ...props.scope,
      [field]:
        field === "from_block" || field === "to_block" ? Number(value) : value,
    });
  const submit = (event: React.FormEvent) => {
    event.preventDefault();
    if (
      !/^0x[0-9a-fA-F]{40}$/.test(props.scope.address) ||
      !/^0x[0-9a-fA-F]{40}$/.test(props.scope.token_address)
    ) {
      setFormError("地址须为 0x 开头的 40 位十六进制字符。");
      return;
    }
    if (
      !Number.isSafeInteger(props.scope.from_block) ||
      !Number.isSafeInteger(props.scope.to_block) ||
      props.scope.from_block < 0 ||
      props.scope.to_block < props.scope.from_block ||
      props.scope.to_block - props.scope.from_block + 1 > 2000
    ) {
      setFormError("区块须为非负整数，窗口有序且不超过 2,000 区块。");
      return;
    }
    setFormError("");
    props.onStart();
  };
  const rpcAvailable =
    props.health?.rpc_available === true ||
    props.health?.capabilities?.rpc_available === true;
  const liveAvailable =
    props.health?.live_available === true ||
    props.health?.live_agent_available === true ||
    props.health?.capabilities?.live_agent_available === true;
  return (
    <aside className="sidebar">
      <a className="brand" href="/" aria-label="ClueTide 调查工作台">
        ClueTide
      </a>
      <nav aria-label="主要导航">
        {(
          [
            ["workbench", "search", "调查工作台"],
            ["bundles", "upload", "证据包导入"],
            ["review", "people", "协作复核"],
          ] as const
        ).map(([screen, icon, label]) => (
          <button
            key={screen}
            type="button"
            className={`nav-item ${props.screen === screen ? "selected" : ""}`}
            onClick={() => props.onScreen(screen)}
            aria-current={props.screen === screen ? "page" : undefined}
          >
            <Icon name={icon} />
            <span>{label}</span>
          </button>
        ))}
      </nav>
      {props.screen === "workbench" && (
        <form className="scope-form" onSubmit={submit}>
          <div className="scope-title">
            <h2>调查范围</h2>
            <button
              type="button"
              className="text-button light"
              onClick={() => props.onPreset(selectedPreset || "uniswap93")}
              disabled={props.busy || props.presetLoading}
            >
              加载案例
            </button>
          </div>
          <label>
            案例预设
            <select value={selectedPreset} disabled={props.busy || props.presetLoading}
              onChange={(event) => {
                if (event.target.value) {
                  setFormError("");
                  props.onPreset(event.target.value);
                }
              }}>
              <option value="" disabled>{props.presetLoading ? "加载中…" : "自定义范围"}</option>
              {props.presets.map((item) => <option key={item.case_id} value={item.case_id}>{item.title}</option>)}
            </select>
          </label>
          <label>
            地址 / 合约
            <input
              value={props.scope.address}
              onChange={(e) => set("address", e.target.value.trim())}
              required
              spellCheck={false}
              title={props.scope.address}
              placeholder="0x…"
              disabled={props.busy}
            />
          </label>
          <label>
            ERC-20 合约
            <input
              value={props.scope.token_address}
              onChange={(e) => set("token_address", e.target.value.trim())}
              required
              spellCheck={false}
              title={props.scope.token_address}
              placeholder="0x…"
              disabled={props.busy}
            />
          </label>
          <div className="block-inputs">
            <label>
              起始区块
              <input
                type="number"
                min="0"
                step="1"
                value={props.scope.from_block}
                onChange={(e) => set("from_block", e.target.value)}
                required
                disabled={props.busy}
              />
            </label>
            <label>
              结束区块
              <input
                type="number"
                min="0"
                step="1"
                value={props.scope.to_block}
                onChange={(e) => set("to_block", e.target.value)}
                required
                disabled={props.busy}
              />
            </label>
          </div>
          <label>
            读取模式
            <select
              value={props.scope.mode}
              onChange={(e) => set("mode", e.target.value)}
              disabled={props.busy}
            >
              <option value="offline">公开缓存</option>
              <option value="rpc" disabled={!rpcAvailable}>
                只读 RPC{!rpcAvailable ? "（不可用）" : ""}
              </option>
            </select>
          </label>
          <label>
            Agent 模式
            <select
              value={props.scope.agent_mode}
              onChange={(e) => set("agent_mode", e.target.value)}
              disabled={props.busy}
            >
              <option value="offline">离线工具演示</option>
              <option value="live" disabled={!liveAvailable}>
                实时 Agent{!liveAvailable ? "（不可用）" : ""}
              </option>
            </select>
          </label>
          {formError && (
            <p className="form-error" role="alert">
              {formError}
            </p>
          )}
          {props.scope.agent_mode === "live" && !liveAvailable && (
            <p className="form-error" role="status">
              实时 Agent 当前不可用，请选择离线工具演示。
            </p>
          )}
          <button
            className="button primary start-button"
            type="submit"
            disabled={
              props.busy ||
              props.presetLoading ||
              (props.scope.agent_mode === "live" && !liveAvailable)
            }
          >
            {props.busy ? "调查中…" : "开始调查"}
          </button>
          {props.busy && (
            <button
              type="button"
              className="button stop-button"
              onClick={props.onStop}
              disabled={props.stopping}
            >
              <Icon name="stop" size={16} />
              {props.stopping ? "正在停止…" : "停止调查"}
            </button>
          )}
          {!!props.history.length && (
            <label className="history-label">
              历史调查
              <select
                defaultValue=""
                onChange={(event) =>
                  event.target.value && props.onOpen(event.target.value)
                }
                disabled={props.busy}
              >
                <option value="">选择已保存调查</option>
                {props.history.slice(0, 20).map((item) => (
                  <option key={item.id} value={item.id}>
                    {item.title ?? item.id.slice(0, 10)}
                  </option>
                ))}
              </select>
            </label>
          )}
        </form>
      )}
      <p className="rail-footer">Ethereum · 只读</p>
    </aside>
  );
}
