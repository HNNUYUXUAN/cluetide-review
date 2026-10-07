import { useState } from "react";
import type { Health, HistoryItem, Preset, Scope, Screen } from "../types";
import Icon from "./Icon";
import { caseTitle } from "../format";

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
      setFormError("Enter a 0x-prefixed address with 40 hexadecimal characters.");
      return;
    }
    if (
      !Number.isSafeInteger(props.scope.from_block) ||
      !Number.isSafeInteger(props.scope.to_block) ||
      props.scope.from_block < 0 ||
      props.scope.to_block < props.scope.from_block ||
      props.scope.to_block - props.scope.from_block + 1 > 2000
    ) {
      setFormError("Blocks must be nonnegative integers in order, with a window no larger than 2,000 blocks.");
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
      <a className="brand" href="/" aria-label="ClueTide investigation workspace">
        ClueTide
      </a>
      <nav aria-label="Main navigation">
        {(
          [
            ["workbench", "search", "Investigation workspace"],
            ["bundles", "upload", "Import evidence"],
            ["review", "people", "Collaborative review"],
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
            <h2>Investigation scope</h2>
            <button
              type="button"
              className="text-button light"
              onClick={() => props.onPreset(selectedPreset || "uniswap93")}
              disabled={props.busy || props.presetLoading}
            >
              Load case
            </button>
          </div>
          <label>
            Case presets
            <select value={selectedPreset} disabled={props.busy || props.presetLoading}
              onChange={(event) => {
                if (event.target.value) {
                  setFormError("");
                  props.onPreset(event.target.value);
                }
              }}>
              <option value="" disabled>{props.presetLoading ? "Loading…" : "Custom scope"}</option>
              {props.presets.map((item) => <option key={item.case_id} value={item.case_id}>{caseTitle(item.title, item.case_id)}</option>)}
            </select>
          </label>
          <label>
            Address / Contract
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
            ERC-20 contract
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
              From block
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
              To block
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
            Read mode
            <select
              value={props.scope.mode}
              onChange={(e) => set("mode", e.target.value)}
              disabled={props.busy}
            >
              <option value="offline">Public snapshot</option>
              <option value="rpc" disabled={!rpcAvailable}>
                Read-only RPC{!rpcAvailable ? "(unavailable)" : ""}
              </option>
            </select>
          </label>
          <label>
            Agent mode
            <select
              value={props.scope.agent_mode}
              onChange={(e) => set("agent_mode", e.target.value)}
              disabled={props.busy}
            >
              <option value="offline">Offline tool demo</option>
              <option value="live" disabled={!liveAvailable}>
                Live Agent{!liveAvailable ? "(unavailable)" : ""}
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
              Live Agent is unavailable. Select the offline tool demo.
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
            {props.busy ? "Investigating…" : "Start investigation"}
          </button>
          {props.busy && (
            <button
              type="button"
              className="button stop-button"
              onClick={props.onStop}
              disabled={props.stopping}
            >
              <Icon name="stop" size={16} />
              {props.stopping ? "Stopping…" : "Stop investigation"}
            </button>
          )}
          {!!props.history.length && (
            <label className="history-label">
              Investigation history
              <select
                defaultValue=""
                onChange={(event) =>
                  event.target.value && props.onOpen(event.target.value)
                }
                disabled={props.busy}
              >
                <option value="">Select a saved investigation</option>
                {props.history.slice(0, 20).map((item) => (
                  <option key={item.id} value={item.id}>
                    {caseTitle(item.title, item.id)}
                  </option>
                ))}
              </select>
            </label>
          )}
        </form>
      )}
      <p className="rail-footer">Ethereum · Read only</p>
    </aside>
  );
}
