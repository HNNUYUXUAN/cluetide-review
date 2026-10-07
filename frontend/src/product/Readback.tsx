import { useEffect, useRef, useState } from "react";
import type { BotVerification } from "../bot-types";
import { preparedRequest, stepData, story } from "./data";
import { hasArchiveContext } from "./archive-context";
import type { Backend } from "./backend";
import type { StepKey } from "./routes";
import { caseHref } from "./routes";
import { Icon } from "./icons";

type Result = { status: "loading" } | { status: "error" } | { status: "done"; result: BotVerification; at: string };
const labels = { verified: "Current verification passed", pending: "Waiting for confirmations", mismatch: "Commitment mismatch", failed: "Transaction execution failed" };

export function Readback({ step, backend }: { step: StepKey; backend: Backend }) {
  const [results, setResults] = useState<Partial<Record<StepKey, Result>>>({});
  const inFlight = useRef(new Set<StepKey>());
  const mounted = useRef(true);
  const [archiveContext, setArchiveContext] = useState<unknown>(null);
  const [contextChecked, setContextChecked] = useState(false);
  const [contextAttempt, setContextAttempt] = useState(0);
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; }; }, []);
  useEffect(() => {
    if (backend !== "available") return;
    const controller = new AbortController();
    setContextChecked(false);
    void import("../api").then(({ api }) => api.get(story.case_id, AbortSignal.any([controller.signal, AbortSignal.timeout(5000)])))
      .then(record => { if (!controller.signal.aborted) { setArchiveContext(record); setContextChecked(true); } })
      .catch(() => { if (!controller.signal.aborted) { setArchiveContext(null); setContextChecked(true); } });
    return () => controller.abort();
  }, [backend, contextAttempt]);
  const selectedRecord = stepData(step);
  const canVerify = backend === "available" && hasArchiveContext(archiveContext, { caseId: story.case_id, localVersionId: selectedRecord.local_version_id, contentHash: selectedRecord.content_hash, localReviewId: selectedRecord.local_review_id, reviewHash: selectedRecord.review_hash, decision: selectedRecord.decision });
  const current = results[step];
  async function verify() {
    if (inFlight.current.has(step) || !canVerify) return;
    const selected = step;
    inFlight.current.add(selected);
    setResults(previous => ({ ...previous, [selected]: { status: "loading" } }));
    try {
      const { botApi } = await import("../bot-wallet");
      const result = await botApi.verify(stepData(selected).transaction_hash, preparedRequest(selected), 3);
      if (mounted.current) setResults(previous => ({ ...previous, [selected]: { status: "done", result, at: new Date().toISOString() } }));
    } catch { if (mounted.current) setResults(previous => ({ ...previous, [selected]: { status: "error" } })); }
    finally { inFlight.current.delete(selected); }
  }
  return <div className="ct-readback">
    {canVerify ? <button className="ct-button ct-full" disabled={current?.status === "loading"} onClick={() => void verify()}><Icon name="search" />{current?.status === "loading" ? "Reading transaction…" : "Verify this transaction"}</button> : backend === "available" ? <a className="ct-button ct-full" href="#/verify"><Icon name="search" />Verify evidence bundle</a> : <a className="ct-button ct-full" href={`http://127.0.0.1:8765/bot.html${caseHref(step)}`} target="_blank" rel="noreferrer"><Icon name="external" />Open local verifier</a>}
    <div role="status" className="ct-readback-status">
      {backend === "checking" ? <p>Checking the local service…</p> : backend !== "available" ? <p>Current readback runs through your local ClueTide service. The record above is the archived result.</p> : !contextChecked ? <p>Checking the original local case and exact version…</p> : !canVerify ? <p>Original archived case required for this readback. This installation does not contain the matching saved version or review. You can verify its published bundle or inspect the transaction in the explorer. <button className="ct-inline-button" onClick={() => setContextAttempt(value => value + 1)}>Check again</button></p> : !current ? <p>Read-only verification. No wallet connection required.</p> : current.status === "loading" ? <p>Checking this transaction, receipt and exact version commitment.</p> : current.status === "error" ? <p className="ct-warning-text">Readback could not complete. Check the service and RPC, then retry. The archived result is unchanged.</p> : <p className={current.result.status === "verified" ? "ct-positive" : "ct-warning-text"}>{labels[current.result.status]}<br /><span className="ct-secondary">{current.result.confirmations} confirmations · minimum {current.result.minimum_confirmations}<br /><time dateTime={current.at}>{new Date(current.at).toLocaleString("en-GB", { timeZone: "UTC" })} UTC</time></span></p>}
    </div>
  </div>;
}
