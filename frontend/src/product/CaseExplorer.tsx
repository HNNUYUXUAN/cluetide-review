import { useState } from "react";
import type { Backend } from "./backend";
import { archiveDate, assetUrl, bundles, chapterCopy, shortHash, stepData, stepLabels, steps, story } from "./data";
import { Icon } from "./icons";
import { Readback } from "./Readback";
import { caseHref } from "./routes";
import type { StepKey } from "./routes";

export function CaseExplorer({ step, backend }: { step: StepKey; backend: Backend }) {
  const record = stepData(step), copy = chapterCopy[step];
  const bundle = bundles[step === "v2" ? "v2" : "v1"];
  const [copied, setCopied] = useState<string | null>(null);
  async function copyHash() { try { await navigator.clipboard.writeText(record.content_hash); setCopied(record.content_hash); } catch { setCopied("unavailable"); } }
  return <div className="ct-page ct-case-page">
    <p className="ct-breadcrumb"><a href={caseHref()}>Case explorer</a><span>/</span>Uniswap proposal 93</p>
    <div className="ct-page-intro"><h1>One event. Three accountable steps.</h1><p>Follow an observed UNI transfer from the first report to a version-bound review and correction.</p></div>
    <nav className="ct-steps" aria-label="Case versions">{steps.map((key, index) => <a key={key} href={caseHref(key)} className={step === key ? "is-active" : ""} aria-current={step === key ? "step" : undefined}><span>0{index + 1}</span>{stepLabels[key]}</a>)}</nav>
    <div className="ct-case-grid">
      <article className="ct-report ct-panel">
        <div className="ct-report-meta"><span><Icon name="file" />{step === "review" ? "Review" : "Report"}</span><span>{step === "review" ? "Review 1 → v1" : `v${record.version_id}`} <b>·</b> Archived {archiveDate}</span></div>
        <h2>Uniswap proposal 93</h2><p className="ct-case-subtitle">100,000,000 UNI transferred to the dead address.</p>
        <section className="ct-chapter" aria-live="polite"><h3>{copy.heading}</h3><p>{copy.paragraph}</p></section>
        <dl className="ct-fact-rows"><div><dt>Observed</dt><dd>{copy.observed}</dd></div><div><dt>Interpretation</dt><dd>{copy.interpretation}</dd></div><div><dt>Unknown</dt><dd>{copy.unknown}</dd></div></dl>
        <div className="ct-report-bottom"><details className="ct-citation"><summary><Icon name="alert" /><span>2 citation links require review</span><Icon name="arrow" /></summary><p>Two transaction/receipt references lack a matching raw RPC observation in the archived Agent record. This structural review remains separate from the content commitment recorded on chain.</p></details><a className="ct-button ct-outline" href={assetUrl(bundle.file)} download><Icon name="download" />Download evidence bundle</a></div>
        <p className="ct-adaptation">English interpretation of the archived report and review. Source evidence retains its original language and bytes.</p>
      </article>
      <aside className="ct-onchain ct-panel">
        <h2><Icon name="link" />On-chain record</h2>
        <dl className="ct-chain-rows"><div><dt>{step === "review" ? "Reviewed version" : "Version"}</dt><dd>{record.version_id}</dd></div><div><dt>{step === "review" ? "Review ID" : "Parent"}</dt><dd>{step === "review" ? record.review_id : record.parent_version_id}</dd></div><div><dt>Network</dt><dd>BOT Testnet 968</dd></div><div><dt>Status</dt><dd className="ct-positive"><span className="ct-check"><Icon name="check" /></span>Archived verification passed</dd></div></dl>
        <div className="ct-commitment"><label htmlFor="commitment">Content commitment</label><button id="commitment" className="ct-copy" onClick={() => void copyHash()} title={record.content_hash}><code>{shortHash(record.content_hash)}</code><Icon name="copy" /><span className="ct-sr-only">Copy content commitment</span></button><span className="ct-copy-status" role="status">{copied === record.content_hash ? "Copied" : copied === "unavailable" ? "Copy unavailable. Full hash is shown below." : ""}</span></div>
        <Readback step={step} backend={backend} />
        <a className="ct-explorer-link" href={record.tx_url} target="_blank" rel="noreferrer"><Icon name="external" />Open block explorer</a>
        <p className="ct-scope-note"><Icon name="info" /><span>A content commitment binds bytes and versions. Evidence still needs interpretation.</span></p>
      </aside>
    </div>
    <div className="ct-case-trail"><p>{copy.note}</p><a className="ct-text-link" href={step === "v1" ? caseHref("review") : step === "review" ? caseHref("v2") : "#/verify"}>{step === "v1" ? "Continue to review" : step === "review" ? "See the correction" : "Verify the evidence bundle"}<Icon name="arrow" /></a></div>
    <details className="ct-record-details"><summary>Explore the full record and verification scope</summary><dl><div><dt>Investigation</dt><dd>{story.case_id}</dd></div><div><dt>Ethereum block</dt><dd>24,106,378</dd></div><div><dt>Transaction</dt><dd><a href={record.tx_url} target="_blank" rel="noreferrer">{record.transaction_hash}</a></dd></div><div><dt>Content commitment</dt><dd>{record.content_hash}</dd></div>{record.review_hash ? <div><dt>Review commitment</dt><dd>{record.review_hash}</dd></div> : null}<div><dt>Contract</dt><dd><a href={story.contract_url} target="_blank" rel="noreferrer">{story.contract_address}</a></dd></div><div><dt>Archive observation</dt><dd>{story.observed_at_beijing}</dd></div><div><dt>Participant relationship</dt><dd>The author and on-chain reviewer used the same wallet. Independent reviewer identity is outside this demonstration.</dd></div></dl><p>Historical supply getters, governance voting and execution authorization were outside the checked evidence. The local and on-chain version IDs identify different records; this view shows the on-chain IDs.</p></details>
  </div>;
}
