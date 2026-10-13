import {useMemo} from "react";
import type {LocalVersion} from "./package.ts";
import {lineDiff,previewDocument} from "./review-display.ts";
export function ReviewEvidence({version,previous}:{version:LocalVersion;previous?:LocalVersion}){
  const preview=useMemo(()=>previewDocument(version.artifact_text),[version.artifact_text]);
  const diff=useMemo(()=>lineDiff(previous?.artifact_text||"",version.artifact_text),[previous?.artifact_text,version.artifact_text]);
  return <div className="review-evidence"><details open><summary>Artifact preview</summary><p className="small">Structural preview. Scripts, forms, external resources and source styling are disabled. Download the original to inspect all bytes.</p><iframe title={`Version ${version.version_id} inert HTML preview`} sandbox="" srcDoc={preview} referrerPolicy="no-referrer"/></details>{!previous&&version.version_id>1?<p className="notice">Import the preceding version’s original package to compare actual line changes.</p>:<details><summary>Actual line changes {previous?`from version ${previous.version_id}`:"in the first version"}</summary><p className="small">Line endings and original bytes are preserved in the download.</p><div className="artifact-diff" role="region" aria-label="Artifact line differences" tabIndex={0}>{diff.map((line,index)=><div className={`diff-line ${line.kind}`} key={index}><span aria-label="Previous line">{line.oldLine||""}</span><span aria-label="Current line">{line.newLine||""}</span><code>{line.kind==="added"?"+":line.kind==="removed"?"−":" "} {line.text}</code></div>)}</div></details>}</div>;
}
