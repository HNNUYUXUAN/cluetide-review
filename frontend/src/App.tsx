import { useEffect, useRef, useState } from "react";
import { api, uiError } from "./api";
import { caseTitle, statusName } from "./format";
import type {
  Health,
  HistoryItem,
  Investigation,
  Preset,
  Scope,
  Screen,
  VerifiedImport,
} from "./types";
import Sidebar from "./components/Sidebar";
import InvestigationView from "./components/InvestigationView";
import type { InvestigationTab } from "./components/InvestigationView";
import ImportPanel from "./components/ImportPanel";
import ReviewPanel from "./components/ReviewPanel";
import BotPanel from "./components/BotPanel";

const initialScope: Scope = {
  address: "",
  token_address: "",
  from_block: 24106368,
  to_block: 24106388,
  mode: "offline",
  agent_mode: "offline",
};
const pending = (investigation: Investigation | null) =>
  ["running", "pending", "stopping"].includes(investigation?.status ?? "");
const params = new URLSearchParams(window.location.search);
const storageKey = `cluetide:${params.get("client") === "second" ? "second" : "primary"}:investigation`;
const isPreset = (value: unknown): value is Preset => {
  if (!value || typeof value !== "object" || Array.isArray(value)) return false;
  const item = value as Record<string, unknown>;
  return typeof item.case_id === "string" && /^[A-Za-z0-9_-]{1,64}$/.test(item.case_id) &&
    typeof item.title === "string" && item.title.length > 0 && item.title.length <= 200 &&
    typeof item.address === "string" && /^0x[0-9a-fA-F]{40}$/.test(item.address) &&
    typeof item.token_address === "string" && /^0x[0-9a-fA-F]{40}$/.test(item.token_address) &&
    typeof item.from_block === "number" && Number.isSafeInteger(item.from_block) && item.from_block >= 0 &&
    typeof item.to_block === "number" && Number.isSafeInteger(item.to_block) && item.to_block >= item.from_block &&
    item.to_block - item.from_block < 2000;
};

export default function App() {
  const [showBot, setShowBot] = useState(false);
  const [botOpened, setBotOpened] = useState(false);
  const [screen, setScreen] = useState<Screen>(
    params.get("view") === "bundles" ? "bundles" : "workbench",
  );
  const [tab, setTab] = useState<InvestigationTab>("overview");
  const [scope, setScope] = useState(initialScope);
  const [presets, setPresets] = useState<Preset[]>([]);
  const [presetLoading, setPresetLoading] = useState(false);
  const [health, setHealth] = useState<Health | null>(null);
  const [history, setHistory] = useState<HistoryItem[]>([]);
  const [investigation, setInvestigation] = useState<Investigation | null>(
    null,
  );
  const [imported, setImported] = useState<VerifiedImport | null>(null);
  const [importCase, setImportCase] = useState<Investigation | null>(null);
  const [importNotice, setImportNotice] = useState("");
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [error, setError] = useState("");
  const [starting, setStarting] = useState(false);
  const [stopping, setStopping] = useState(false);
  const startController = useRef<AbortController | null>(null);
  const startInFlight = useRef(false);
  const lastStartAt = useRef<number | null>(null);
  const stopRequestedRef = useRef(false);
  const presetLoadRef = useRef(0);
  const presetInFlight = useRef(false);
  const busy = starting || pending(investigation);
  const refreshHistory = () =>
    api
      .history()
      .then(setHistory)
      .catch(() => undefined);
  const loadPreset = async (id = "uniswap93", prepare = false) => {
    const sequence = ++presetLoadRef.current;
    presetInFlight.current = true;
    setPresetLoading(true);
    try {
      const preset = await api.preset(id);
      if (sequence !== presetLoadRef.current) return;
      if (!isPreset(preset)) throw new Error("Invalid case preset");
      setPresets((previous) => previous.some((item) => item.case_id === preset.case_id) ? previous : [...previous, preset]);
      setScope((previous) => ({
        ...previous,
        address: preset.address,
        token_address: preset.token_address,
        from_block: preset.from_block,
        to_block: preset.to_block,
        mode: "offline",
        agent_mode: "offline",
      }));
      if (prepare) {
        setInvestigation(null);
        setSelectedId(null);
        localStorage.removeItem(storageKey);
        setTab("overview");
      }
      setError("");
    } catch {
      if (sequence === presetLoadRef.current)
        setError("Could not load this case. Check that the local API is running and retry.");
    } finally {
      if (sequence === presetLoadRef.current) {
        presetInFlight.current = false;
        setPresetLoading(false);
      }
    }
  };
  const restoreInvestigation = (result: Investigation) => {
    setInvestigation(result);
    const savedScope = result.input ?? result.evidence?.request;
    if (savedScope)
      setScope((previous) => ({
        ...previous,
        ...savedScope,
        mode: result.input?.mode ?? (result.mode === "rpc" ? "rpc" : "offline"),
        agent_mode: result.input?.agent_mode ?? previous.agent_mode,
      }));
  };
  const openInvestigation = async (id: string) => {
    try {
      const result = await api.get(id);
      restoreInvestigation(result);
      setScreen("workbench");
      setTab("overview");
      setError("");
    } catch {
      setError("Could not open the saved investigation.");
    }
  };
  useEffect(() => {
    void refreshHistory();
    void api.catalog().then((catalog) => {
      if (!Array.isArray(catalog?.cases)) return;
      const unique = new Map<string, Preset>();
      catalog.cases.slice(0, 32).filter(isPreset).forEach((item) => unique.set(item.case_id, item));
      if (unique.size) setPresets([...unique.values()]);
    }).catch(() => undefined);
    void api
      .health()
      .then(setHealth)
      .catch(() => setError("The local API is disconnected. Start the backend, then select Retry connection."));
    const requestedId = params.get("investigation");
    const storedId = requestedId && /^[A-Za-z0-9_-]{1,64}$/.test(requestedId)
      ? requestedId : localStorage.getItem(storageKey);
    if (storedId)
      void api
        .get(storedId)
        .then(restoreInvestigation)
        .catch(() => {
          localStorage.removeItem(storageKey);
          void loadPreset();
        });
    else void loadPreset();
    return () => startController.current?.abort();
  }, []);
  useEffect(() => {
    if (investigation?.id) localStorage.setItem(storageKey, investigation.id);
  }, [investigation?.id]);
  useEffect(() => {
    if (!pending(investigation)) return;
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout>;
    const controller = new AbortController();
    const poll = async () => {
      try {
        const result = await api.get(investigation!.id, controller.signal);
        if (cancelled) return;
        setInvestigation(result);
        if (pending(result)) timer = setTimeout(() => void poll(), 1000);
        else {
          setStopping(false);
          void refreshHistory();
        }
      } catch (error) {
        if (!cancelled) {
          setError(
            uiError(error, "Could not read the investigation status."),
          );
          timer = setTimeout(() => void poll(), 3000);
        }
      }
    };
    timer = setTimeout(() => void poll(), 600);
    return () => {
      cancelled = true;
      clearTimeout(timer);
      controller.abort();
    };
  }, [investigation?.id, investigation?.status]);
  const start = async () => {
    const now = performance.now();
    if (
      startInFlight.current ||
      presetInFlight.current ||
      busy ||
      (lastStartAt.current !== null && now - lastStartAt.current < 600)
    )
      return;
    if (
      scope.agent_mode === "live" &&
      health?.live_available !== true &&
      health?.live_agent_available !== true &&
      health?.capabilities?.live_agent_available !== true
    ) {
      setError("Live Agent is unavailable. Select the offline tool demo.");
      return;
    }
    startInFlight.current = true;
    lastStartAt.current = now;
    stopRequestedRef.current = false;
    setStarting(true);
    setError("");
    setScreen("workbench");
    setTab("overview");
    setSelectedId(null);
    setInvestigation(null);
    const controller = new AbortController();
    startController.current = controller;
    try {
      const accepted = await api.investigate(scope, controller.signal);
      setInvestigation(accepted);
      if (stopRequestedRef.current) {
        const stopped = await api.stop(accepted.id);
        setInvestigation(stopped);
      }
    } catch (error) {
      if (controller.signal.aborted) setError("Waiting cancelled; a task ID has not been received.");
      else
        setError(uiError(error, "The investigation request could not be completed."));
    } finally {
      startInFlight.current = false;
      stopRequestedRef.current = false;
      startController.current = null;
      setStopping(false);
      setStarting(false);
    }
  };
  const stop = async () => {
    if (!investigation?.id) {
      if (startInFlight.current) {
        stopRequestedRef.current = true;
        setStopping(true);
      }
      return;
    }
    setStopping(true);
    try {
      const result = await api.stop(investigation.id);
      setInvestigation(result);
      if (!pending(result)) setStopping(false);
    } catch (error) {
      setStopping(false);
      setError(uiError(error, "The stop request could not be completed."));
    }
  };
  const reviewTarget = screen === "bundles" ? importCase : investigation;
  const importedVersion = importCase?.versions.find(
    (version) => version.content_hash === imported?.manifest_hash,
  );
  const mutate = async (operation: () => Promise<unknown>) => {
    await operation();
    const result = await api.get(reviewTarget!.id);
    setInvestigation(result);
    if (importCase?.id === result.id) setImportCase(result);
  };
  const onImport = async (file: File) => {
    setImported(null);
    setImportCase(null);
    setImportNotice("");
    setError("");
    const result = await api.import(file);
    setImported(result);
    const caseId = result.report.case_id;
    if (typeof caseId === "string" && /^[A-Za-z0-9_-]{1,64}$/.test(caseId)) {
      try {
        const original = await api.get(caseId);
        if (
          original.versions.some(
            (version) => version.content_hash === result.manifest_hash,
          )
        ) {
          setImportCase(original);
          setInvestigation(original);
        } else
          setImportNotice(
            "Integrity verified. No matching content hash was found in the local registry, so a local version review cannot be linked.",
          );
      } catch {
        setImportNotice(
          "Integrity verified. This investigation is not available on the current local server. You can review the public evidence in the ZIP.",
        );
      }
    } else setImportNotice("Integrity verified. This bundle does not identify an available local investigation.");
    void refreshHistory();
  };
  const title =
    screen === "bundles"
      ? "Import and review evidence"
      : screen === "review"
        ? "Collaborative review and versions"
        : (investigation?.title ?? presets.find((item) =>
          item.address.toLowerCase() === scope.address.toLowerCase() &&
          item.token_address.toLowerCase() === scope.token_address.toLowerCase() &&
          item.from_block === scope.from_block && item.to_block === scope.to_block)?.title ?? "Ethereum event investigation");
  return (
    <div className="app-shell">
      <Sidebar
        screen={screen}
        onScreen={setScreen}
        scope={scope}
        onScope={setScope}
        onStart={() => void start()}
        onStop={() => void stop()}
        onPreset={(id) => void loadPreset(id, true)}
        presets={presets}
        presetLoading={presetLoading}
        busy={busy}
        stopping={stopping}
        health={health}
        history={history}
        onOpen={(id) => void openInvestigation(id)}
      />
      <main className="workspace">
        <div className="workbench-frame">
          <header className="page-header">
            <div>
              <h1>{caseTitle(title, investigation?.id)}</h1>
              <p>
                {screen === "bundles"
                  ? "Verify integrity in a second client, then review the evidence and findings."
                  : screen === "review"
                    ? "Preserve original reports, review comments, and corrections."
                    : "Follow the evidence from an event alert to possible explanations."}
              </p>
            </div>
            <div className="header-actions">
              <a className="button outline" href="/bot.html">BOT product overview</a>
              <button className="button outline" type="button" data-testid="bot-open" aria-expanded={showBot}
                onClick={() => { setBotOpened(true); setShowBot((previous) => !previous); }}>
                {showBot ? "Collapse BOT registry" : "BOT on-chain registry"}
              </button>
              {screen === "workbench" ? (
                <>
                  <span className="run-status" aria-live="polite">
                    {scope.mode === "offline" ? "Public snapshot" : "Read-only RPC"} ·{" "}
                    {busy
                      ? "Investigating"
                      : investigation
                        ? statusName(investigation.status)
                        : "Local review"}
                  </span>
                  {investigation?.report && !busy ? (
                    <a
                      className="button outline"
                      href={api.bundleUrl(investigation.id)}
                      download
                    >
                      Export evidence ZIP
                    </a>
                  ) : (
                    <button className="button outline" disabled>
                      Export evidence ZIP
                    </button>
                  )}
                </>
              ) : (
                <button
                  className="button outline"
                  type="button"
                  onClick={() => setScreen("workbench")}
                >
                  Back to investigation
                </button>
              )}
            </div>
          </header>
          {error && (
            <div className="error-banner global-error" role="alert">
              <span>{error}</span>
              <button
                type="button"
                className="text-button"
                onClick={() => {
                  void api
                    .health()
                    .then((result) => {
                      setHealth(result);
                      setError("");
                      void loadPreset();
                    })
                    .catch(() =>
                      setError("The local API is disconnected. Start the backend and retry."),
                    );
                }}
              >
                Retry connection
              </button>
            </div>
          )}
          {botOpened ? <div hidden={!showBot} data-testid="bot-panel-container"><BotPanel investigation={reviewTarget} /></div> : null}
          {screen === "workbench" && (
            <nav className="tabs" aria-label="Investigation content">
              {(
                [
                  ["overview", "Overview"],
                  ["evidence", "Original evidence"],
                  ["trace", "Agent trace"],
                  ["versions", "Versions and reviews"],
                ] as const
              ).map(([key, label]) => (
                <button
                  key={key}
                  type="button"
                  className={tab === key ? "active" : ""}
                  onClick={() => setTab(key)}
                  aria-current={tab === key ? "page" : undefined}
                >
                  {label}
                </button>
              ))}
            </nav>
          )}
          <div
            className={`page-content ${screen !== "workbench" ? "standalone-content" : ""}`}
          >
            {screen === "workbench" && tab !== "versions" && (
              <InvestigationView
                investigation={investigation}
                busy={busy}
                tab={tab}
                selectedId={selectedId}
                onEvidence={(id) => {
                  setSelectedId(id);
                  setTab("evidence");
                }}
              />
            )}
            {screen === "bundles" && (
              <ImportPanel imported={imported} onImport={onImport} />
            )}
            {screen === "bundles" && importNotice && (
              <p className="inline-notice" role="status">
                {importNotice}
              </p>
            )}
            {screen === "bundles" && importedVersion && (
              <p className="inline-notice">
                Local registry match: imported report v{imported?.report.revision ?? 1}
                . Reviews bind to this version and its content hash.
              </p>
            )}
            {(screen === "review" ||
              screen === "bundles" ||
              tab === "versions") && (
              <ReviewPanel
                investigation={reviewTarget}
                onReview={(reviewer, comment) =>
                  mutate(() =>
                    api.review(
                      reviewTarget!.id,
                      reviewer,
                      comment,
                      screen === "bundles"
                        ? importedVersion?.version_id
                        : undefined,
                    ),
                  )
                }
                onVersion={(
                  author,
                  correction,
                  parent,
                  correctedSummary,
                  claimReplacements,
                  assessmentReplacements,
                  correctedClassification,
                ) =>
                  mutate(() =>
                    api.version(
                      reviewTarget!.id,
                      author,
                      correction,
                      parent,
                      correctedSummary,
                      claimReplacements,
                      assessmentReplacements,
                      correctedClassification,
                    ),
                  )
                }
              />
            )}
            {screen === "workbench" && investigation?.evidence && (
              <div className="second-client-row">
                <a
                  className="text-button"
                  href="/?client=second&view=bundles#/workspace"
                  target="_blank"
                  rel="noopener noreferrer"
                >
                  Open a second review client
                </a>
                <span className="muted">Export the ZIP, then import it in that client.</span>
              </div>
            )}
          </div>
        </div>
      </main>
    </div>
  );
}
