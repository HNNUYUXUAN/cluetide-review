import { zipSync } from "fflate";
import { canonicalBytes, manifestBytes, sha256, strictJsonParse, unzipPackage, utf8Decode, validateTask } from "./package.ts";
import { publicConfig } from "./public-config.ts";
import { isTransactionHash } from "./primitives.ts";
import { record } from "./model.ts";
import type { Receipt } from "./model.ts";
import { verifyReceiptEnvelope } from "./publisher.ts";
import type { StoredAcceptanceEvidence } from "./evidence-store.ts";

export interface AcceptanceIndex {
  schema:"cluetide.acceptance-bundle.v1"; chain_id:677; contract_address:string;
  task_id:string; version_id:number; attempt_id:string; observed_at:string;
  start_reference:StoredAcceptanceEvidence["start_reference"];
  transactions:StoredAcceptanceEvidence["transactions"];
  observations:StoredAcceptanceEvidence["observations"];
  files:{path:string;bytes:number;sha256:string}[];
}
const limit=4*1024*1024;
const sameBytes=(a:Uint8Array,b:Uint8Array)=>a.length===b.length&&a.every((value,index)=>value===b[index]);
export async function createAcceptanceBundle(evidence:StoredAcceptanceEvidence):Promise<Uint8Array>{
  const task=await validateTask(evidence.task);
  const entries:Record<string,Uint8Array>={"task.json":canonicalBytes(task),"receipt.json":evidence.receipt_bytes};
  for(const version of task.versions){
    entries[`versions/${version.version_id}/manifest.json`]=manifestBytes(version);
    entries[`versions/${version.version_id}/artifact.html`]=new TextEncoder().encode(version.artifact_text);
  }
  if(evidence.artifact_bytes)entries["published/artifact.html"]=evidence.artifact_bytes;
  const files=await Promise.all(Object.entries(entries).map(async([path,bytes])=>({path,bytes:bytes.length,sha256:await sha256(bytes)})));
  const index:AcceptanceIndex={schema:"cluetide.acceptance-bundle.v1",chain_id:677,contract_address:publicConfig.contract,task_id:task.task_id,version_id:evidence.version_id,attempt_id:evidence.attempt_id,observed_at:evidence.observed_at,start_reference:evidence.start_reference,transactions:evidence.transactions,observations:evidence.observations,files};
  entries["acceptance.json"]=canonicalBytes(index);
  if(Object.values(entries).reduce((total,bytes)=>total+bytes.length,0)>limit)throw new Error("The acceptance bundle exceeds 4 MiB. Export a smaller complete release history.");
  const bytes=zipSync(entries,{level:6});
  await verifyAcceptanceBundle(bytes);
  return bytes;
}
/** This verifier reads only its input bytes and performs local cryptography. */
export async function verifyAcceptanceBundle(bytes:Uint8Array){
  const entries=unzipPackage(bytes);
  if(!entries["acceptance.json"]||!entries["task.json"]||!entries["receipt.json"])throw new Error("The acceptance bundle is missing its index or original evidence.");
  const raw=strictJsonParse(utf8Decode(entries["acceptance.json"]));
  if(!record(raw)||raw.schema!=="cluetide.acceptance-bundle.v1"||raw.chain_id!==677||raw.contract_address!==publicConfig.contract||!isTransactionHash(raw.task_id)||!Number.isSafeInteger(raw.version_id)||Number(raw.version_id)<1||typeof raw.attempt_id!=="string"||!/^\d+$/.test(raw.attempt_id)||BigInt(raw.attempt_id)<1n||typeof raw.observed_at!=="string"||!Number.isFinite(Date.parse(raw.observed_at))||!Array.isArray(raw.files)||!Array.isArray(raw.transactions)||!record(raw.observations)||!record(raw.start_reference))throw new Error("The acceptance index failed validation.");
  const index=raw as unknown as AcceptanceIndex,seen=new Set<string>();
  for(const file of index.files){
    if(!record(file)||typeof file.path!=="string"||file.path==="acceptance.json"||seen.has(file.path)||!entries[file.path]||!Number.isSafeInteger(file.bytes)||entries[file.path].length!==file.bytes||!isTransactionHash(file.sha256)||await sha256(entries[file.path])!==file.sha256)throw new Error("An acceptance evidence file differs from its digest or is listed more than once.");
    seen.add(file.path);
  }
  if(seen.size!==Object.keys(entries).length-1)throw new Error("Every acceptance evidence file must appear in the index.");
  const task=await validateTask(strictJsonParse(utf8Decode(entries["task.json"]))),version=task.versions.find(v=>v.version_id===index.version_id);
  if(task.task_id!==index.task_id||!version)throw new Error("The acceptance task or exact version does not match its index.");
  const expected=new Set(["acceptance.json","task.json","receipt.json"]);
  for(const item of task.versions){
    const manifestPath=`versions/${item.version_id}/manifest.json`,artifactPath=`versions/${item.version_id}/artifact.html`;
    expected.add(manifestPath);expected.add(artifactPath);
    if(!entries[manifestPath]||!entries[artifactPath]||!sameBytes(entries[manifestPath],manifestBytes(item))||!sameBytes(entries[artifactPath],new TextEncoder().encode(item.artifact_text)))throw new Error("The task and its duplicated original version files disagree.");
  }
  const reference=index.start_reference;
  if(reference.schema!=="cluetide.start-reference.v1"||reference.task_id!==task.task_id||reference.version_id!==version.version_id||!isTransactionHash(reference.transaction_hash))throw new Error("The acceptance start reference differs from its exact version.");
  if(index.transactions.length>100||index.transactions.some(tx=>!record(tx)||typeof tx.action!=="string"||!isTransactionHash(tx.transaction_hash))||!index.transactions.some(tx=>tx.action==="start_execution"&&tx.transaction_hash===reference.transaction_hash))throw new Error("The acceptance transaction references failed validation.");
  const envelope=strictJsonParse(utf8Decode(entries["receipt.json"])) as Receipt;
  const result=await verifyReceiptEnvelope(task,version,index.attempt_id,envelope,reference.transaction_hash);
  const artifact=entries["published/artifact.html"];
  if(artifact)expected.add("published/artifact.html");
  if(Object.keys(entries).some(path=>!expected.has(path)))throw new Error("The acceptance bundle contains an unsupported evidence file.");
  if(result===1&&(!artifact||artifact.length>publicConfig.artifactBytes||await sha256(artifact)!==version.artifact_hash))throw new Error("The delivered artifact differs from the approved version or is missing.");
  if(result!==1&&artifact)throw new Error("A failed execution cannot contain a successful delivery artifact.");
  const evidence:StoredAcceptanceEvidence={task,version_id:version.version_id,attempt_id:index.attempt_id,observed_at:index.observed_at,start_reference:reference,receipt_bytes:entries["receipt.json"],artifact_bytes:artifact,transactions:index.transactions,observations:index.observations};
  return {evidence,task,version,envelope,offline:{files:true as const,signature:true as const,artifact:result===1},chain:{status:"not_checked" as const},onlineArtifact:{status:"not_checked" as const}};
}
