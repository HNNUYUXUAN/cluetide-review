import { useEffect, useRef, useState } from "react";
import { assetUrl, bundles, networkLabel, story } from "./data";
import { Icon } from "./icons";
import type { VerifiedBundle } from "./bundle-verifier";

type CheckResult = { name: string; bundle: VerifiedBundle; known: "v1" | "v2" | null };
export default function Verify() {
  const [result, setResult] = useState<CheckResult | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const generation = useRef(0);
  const chooser = useRef<HTMLInputElement>(null);
  useEffect(() => () => { generation.current++; }, []);
  async function inspect(name: string, getBytes: () => Promise<ArrayBuffer>, expected?: "v1" | "v2") {
    const sequence = ++generation.current;
    setBusy(true); setError(""); setResult(null);
    try {
      const [bytes, verifier] = await Promise.all([getBytes(), import("./bundle-verifier.js")]);
      const bundle = await verifier.verifyBundle(new Uint8Array(bytes));
      const known = (["v1", "v2"] as const).find(key => bundles[key].archive === bundle.archiveHash && bundles[key].manifest === bundle.manifestHash && story.case_id === bundle.report.case_id && bundles[key].revision === bundle.report.revision) ?? null;
      if (expected && known !== expected) throw new Error("This download does not match the published archive and version commitments.");
      if (sequence === generation.current) setResult({ name, bundle, known });
    } catch (failure) { if (sequence === generation.current) setError(failure instanceof Error && !/[\u3400-\u9fff]/.test(failure.message) ? failure.message : "The evidence bundle could not be verified. Check the five-file ClueTide ZIP format, size and file integrity."); }
    finally { if (sequence === generation.current) setBusy(false); }
  }
  const loadSample = (version: "v1" | "v2") => void inspect(`UNI report ${version}`, async () => {
    const response = await fetch(assetUrl(bundles[version].file), { credentials: "omit" });
    if (!response.ok) throw new Error("The published bundle could not be loaded. Please retry.");
    return response.arrayBuffer();
  }, version);
  return <div className="ct-page ct-verify-page"><div className="ct-page-intro"><h1>Trust the handoff.<br />Check the evidence.</h1><p>Verify an evidence bundle in your browser. Match its files and exact version before you build on the conclusion.</p></div>
    <div className="ct-verify-grid"><section className="ct-upload ct-panel"><div className="ct-upload-mark"><Icon name="file" /></div><h2>Bring your evidence bundle.</h2><p>Choose a ClueTide ZIP, up to 16 MiB.<br />Your file stays in this browser.</p><input ref={chooser} type="file" accept=".zip,application/zip" className="ct-file-input" aria-label="Choose evidence ZIP" onChange={event => { const file = event.target.files?.[0]; event.target.value = ""; if (!file) return; if (file.size > 16 * 1024 * 1024) { generation.current++; setBusy(false); setError("Choose a ZIP file no larger than 16 MiB."); setResult(null); return; } void inspect(file.name, () => file.arrayBuffer()); }} /><button className="ct-button" onClick={() => chooser.current?.click()} disabled={busy}><Icon name="search" />{busy ? "Checking evidence…" : "Choose ZIP file"}</button></section>
    <section className="ct-sample-bundles"><h2>Try the recorded UNI case.</h2><p>These original archived bundles back the content commitments on {networkLabel}.</p>{(["v1", "v2"] as const).map(key => <div className="ct-bundle-row" key={key}><span><b>{key}</b>{key === "v1" ? "Original AI report" : "Corrected report"}</span><div><button className="ct-text-link" disabled={busy} onClick={() => loadSample(key)}>Verify <Icon name="arrow" /></button><a href={assetUrl(bundles[key].file)} download aria-label={`Download ${key} evidence bundle`}><Icon name="download" /></a></div></div>)}<p className="ct-adaptation">Source documents retain their original language. The product walkthrough provides an English interpretation.</p></section></div>
    <div aria-live="polite">{error ? <div className="ct-verify-error"><Icon name="alert" /><div><h2>Verification could not complete.</h2><p>{error}</p></div></div> : null}{result ? <section className="ct-verification-result ct-panel"><h2><span className="ct-check"><Icon name="check" /></span>File integrity verified</h2><p>{result.name} · {result.bundle.verifiedFiles.length} content files checked</p><div className="ct-result-statement">{result.known ? `Matches the recorded UNI ${result.known} archive and its on-chain content commitment.` : "The bundle is internally consistent. It has not been matched to this demo’s recorded chain commitments."}</div><p className="ct-citation-result">{result.known ? "2 archived citation links still require review: transaction/receipt references lack a matching raw RPC observation. Byte integrity is verified; those citation gaps remain open." : "Reference IDs were checked for existence. Complete citation validation, including raw RPC linkage and status bindings, has not been performed."}</p><dl><div><dt>Report version</dt><dd>{result.bundle.report.revision}</dd></div><div><dt>Manifest SHA-256</dt><dd><code>{result.bundle.manifestHash}</code></dd></div><div><dt>Archive SHA-256</dt><dd><code>{result.bundle.archiveHash}</code></dd></div><div><dt>Parent commitment</dt><dd><code>{result.bundle.report.parent_manifest_hash ?? "Initial version"}</code></dd></div></dl><details><summary>Verified file inventory</summary><ul>{result.bundle.verifiedFiles.map(name => <li key={name}>{name}<span>{result.bundle.manifest.files[name].size.toLocaleString("en-GB")} bytes</span></li>)}</ul></details></section> : null}</div>
    <section className="ct-verification-scope"><h2>Know what the check proves.</h2><div><p><b>File integrity</b>ZIP structure, canonical JSON, file sizes, CRC32 and SHA-256 commitments.</p><p><b>Reference structure</b>Reference-ID existence, report version fields and parent commitment format. Raw RPC linkage and explanation-status bindings require a fuller review.</p><p><b>Interpretation</b>Factual support, source authenticity and reviewer independence still require evidence review.</p></div></section>
  </div>;
}
