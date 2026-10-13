import { canonicalize } from "json-canonicalize";
import { zipSync } from "fflate";
import { sha256 as digestBytes } from "viem";
import { publicConfig } from "./public-config.ts";
import { isAddress, isTransactionHash } from "./primitives.ts";
import type { Task, Version } from "./model.ts";
import { PACKAGE_BYTES, scalarText, strictJsonParse, unzipPackage, utf8Decode } from "./exchange.ts";
export { PACKAGE_BYTES, strictJsonParse, unzipPackage, utf8Decode, utf8Decode as decodeUtf8 } from "./exchange.ts";

export interface ReleaseManifest extends Record<string, unknown> {
  schema: "cluetide.release-manifest.v1"; task_id: string; version_id: number; parent_version_id: number;
  summary: string; source_reference: string; artifact: {filename: "artifact.html"; sha256: string; bytes: number};
  operation: "publish_static"; target: "demo-site"; target_id: string; policy_version: string;
  expires_at: number; test_evidence: string[];
}
export interface LocalVersion extends Version {version_id: number; manifest: ReleaseManifest; manifest_text?: string; artifact_text: string; expires_at: number}
export interface LocalTask extends Task {versions: LocalVersion[]; created_at: string; version_count: number}
export interface ImportOptions {resolveTask?: (taskId: string) => Promise<Partial<LocalTask>>}
const encoder = new TextEncoder();
export const canonicalBytes = (value: unknown) => encoder.encode(canonicalize(value));
export async function sha256(bytes: Uint8Array): Promise<string> {
  return "0x" + Array.from(new Uint8Array(await crypto.subtle.digest("SHA-256", new Uint8Array(bytes)))).map(b => b.toString(16).padStart(2,"0")).join("");
}
export async function newTask(title: string, account: string, reviewer: string): Promise<LocalTask> {
  if (!scalarText(title,160,true)) throw new Error("Enter a release title of 1–160 Unicode characters.");
  if (!isAddress(account) || !isAddress(reviewer) || account.toLowerCase() === reviewer.toLowerCase()) throw new Error("Choose a valid reviewer wallet distinct from your proposer wallet.");
  const task_id = "0x" + Array.from(crypto.getRandomValues(new Uint8Array(32))).map(b=>b.toString(16).padStart(2,"0")).join("");
  return {task_id, title:title.trim(), owner:account.toLowerCase(), proposer:account.toLowerCase(), reviewer:reviewer.toLowerCase(), executor:account.toLowerCase(), receipt_signer:publicConfig.receiptSigner, target:"demo-site", target_id:await sha256(encoder.encode(publicConfig.policy+"/"+task_id)), created_at:new Date().toISOString(), version_count:0, versions:[], attempts:[]};
}
export async function buildVersion(task: LocalTask, input: {summary:string; source_reference:string; artifact_text:string; test_evidence:string[]; expires_at:number}, parent=task.versions.at(-1)?.version_id||0, now=Math.floor(Date.now()/1000)): Promise<LocalVersion> {
  const {summary,source_reference,artifact_text,test_evidence,expires_at}=input;
  if (!scalarText(summary,1000,true) || !scalarText(source_reference,300,true)) throw new Error("Provide a summary and source reference within the displayed limits.");
  const bytes=encoder.encode(artifact_text);
  if (!scalarText(artifact_text,publicConfig.artifactBytes,true) || bytes.length>publicConfig.artifactBytes || artifact_text.includes("\0")) throw new Error("Upload valid UTF-8 HTML up to 32 KiB.");
  if (!Array.isArray(test_evidence)||!test_evidence.length || test_evidence.length>12 || test_evidence.some(t=>!scalarText(t,1000,true))) throw new Error("Provide 1–12 test evidence entries, each up to 1,000 characters.");
  if (!Number.isSafeInteger(expires_at) || expires_at<=now || expires_at>now+30*86400) throw new Error("Choose an approval deadline within the next 30 days.");
  if (!Number.isSafeInteger(parent)||parent<0||parent>=50) throw new Error("The demonstration version limit has been reached.");
  const artifact_hash=await sha256(bytes),version_id=parent+1;
  const manifest:ReleaseManifest={schema:"cluetide.release-manifest.v1",task_id:task.task_id,version_id,parent_version_id:parent,summary,source_reference,artifact:{filename:"artifact.html",sha256:artifact_hash,bytes:bytes.length},operation:"publish_static",target:"demo-site",target_id:task.target_id,policy_version:publicConfig.policy,expires_at,test_evidence};
  return {version_id,manifest,manifest_hash:await sha256(canonicalBytes(manifest)),artifact_hash,artifact_text,summary,expires_at,bundle_url:""};
}
function validateTaskSync(value: unknown): LocalTask {
  if (!value || typeof value!=="object" || Array.isArray(value)) throw new Error("The task package is unreadable.");
  const t=value as LocalTask;
  if (!isTransactionHash(t.task_id)||!scalarText(t.title,160)||typeof t.created_at!=="string"||!Number.isFinite(Date.parse(t.created_at))||![t.owner,t.proposer,t.reviewer,t.executor,t.receipt_signer].every(isAddress)||t.proposer.toLowerCase()===t.reviewer.toLowerCase()||t.target!=="demo-site"||t.target_id!==digestBytes(encoder.encode(publicConfig.policy+"/"+t.task_id))||!Array.isArray(t.versions)||t.versions.length>50) throw new Error("The task identity, roles or target failed validation.");
  const versions:LocalVersion[]=[];
  for(const version of t.versions){
    const m=version?.manifest;
    if (!m || !Number.isSafeInteger(m.version_id)||m.version_id<1||m.version_id>50||m.parent_version_id!==m.version_id-1||versions.some(v=>v.version_id===m.version_id)) throw new Error("The version sequence failed validation.");
    if (m.schema!=="cluetide.release-manifest.v1"||m.task_id!==t.task_id||m.operation!=="publish_static"||m.target!==t.target||m.target_id!==t.target_id||m.policy_version!==publicConfig.policy||!scalarText(m.summary,1000)||!scalarText(m.source_reference,300)||!Number.isSafeInteger(m.expires_at)||m.expires_at<1||!Array.isArray(m.test_evidence)||!m.test_evidence.length||m.test_evidence.length>12||m.test_evidence.some(text=>!scalarText(text,1000))) throw new Error("The historical manifest fields failed validation.");
    const expectedKeys=["schema","task_id","version_id","parent_version_id","summary","source_reference","artifact","operation","target","target_id","policy_version","expires_at","test_evidence"].sort();
    if (Object.keys(m).sort().join("|")!==expectedKeys.join("|")||!m.artifact||Object.keys(m.artifact).sort().join("|")!=="bytes|filename|sha256"||m.artifact.filename!=="artifact.html"||!Number.isSafeInteger(m.artifact.bytes)||!scalarText(version.artifact_text,publicConfig.artifactBytes)||version.artifact_text.includes("\0")) throw new Error("The package artifact metadata failed validation.");
    const artifact=encoder.encode(version.artifact_text);
    const rawManifest=manifestBytes(version);
    if (artifact.length>publicConfig.artifactBytes||rawManifest.length>512*1024||canonicalize(strictJsonParse(utf8Decode(rawManifest)))!==canonicalize(m)) throw new Error("The package original manifest failed validation.");
    if (digestBytes(rawManifest)!==version.manifest_hash||digestBytes(artifact)!==version.artifact_hash||m.artifact.sha256!==version.artifact_hash||m.artifact.bytes!==artifact.length||version.version_id!==m.version_id||(version.summary!==undefined&&version.summary!==m.summary)||(version.expires_at!==undefined&&version.expires_at!==m.expires_at)) throw new Error("The package bytes do not match its commitments.");
    versions.push({version_id:m.version_id,manifest:m,...(version.manifest_text===undefined?{}:{manifest_text:version.manifest_text}),manifest_hash:version.manifest_hash,artifact_hash:version.artifact_hash,artifact_text:version.artifact_text,summary:m.summary,expires_at:m.expires_at,bundle_url:""});
  }
  return {task_id:t.task_id,title:t.title,owner:t.owner.toLowerCase(),proposer:t.proposer.toLowerCase(),reviewer:t.reviewer.toLowerCase(),executor:t.executor.toLowerCase(),receipt_signer:t.receipt_signer.toLowerCase(),target:t.target,target_id:t.target_id,created_at:t.created_at,version_count:Math.max(0,...versions.map(v=>v.version_id)),versions:versions.sort((a,b)=>a.version_id-b.version_id),attempts:[]};
}
export async function validateTask(value:unknown):Promise<LocalTask>{return validateTaskSync(value);}
export function manifestBytes(version:LocalVersion):Uint8Array {
  if(version.manifest_text!==undefined&&typeof version.manifest_text!=="string")throw new Error("The original manifest must be UTF-8 text.");
  return version.manifest_text===undefined?canonicalBytes(version.manifest):encoder.encode(version.manifest_text);
}
export function hasCompleteHistory(task:LocalTask,throughVersion=task.versions.at(-1)?.version_id||0):boolean {
  return Number.isSafeInteger(throughVersion)&&throughVersion>=0&&throughVersion<=50&&Array.from({length:throughVersion},(_,i)=>i+1).every(id=>task.versions.some(v=>v.version_id===id&&v.manifest.parent_version_id===id-1));
}
export function requireCompleteHistory(task:LocalTask,throughVersion?:number):void {
  if(!hasCompleteHistory(task,throughVersion))throw new Error("Import the original preceding versions before synchronizing this release for execution.");
}
/** The public exchange contains original versions and public roles only. */
export function exchangeTask(task:LocalTask,throughVersion=task.versions.at(-1)?.version_id||0):LocalTask {
  const {task_id,title,owner,proposer,reviewer,executor,receipt_signer,target,target_id,created_at}=task;
  const versions=task.versions.filter(v=>v.version_id<=throughVersion).map(v=>({version_id:v.version_id,manifest:v.manifest,...(v.manifest_text===undefined?{}:{manifest_text:v.manifest_text}),manifest_hash:v.manifest_hash,artifact_hash:v.artifact_hash,artifact_text:v.artifact_text,summary:v.manifest.summary,expires_at:v.manifest.expires_at,bundle_url:""}));
  return {task_id,title,owner,proposer,reviewer,executor,receipt_signer,target,target_id,created_at,version_count:Math.max(0,...versions.map(v=>v.version_id)),versions,attempts:[]};
}
export function packageZip(task:LocalTask, version:LocalVersion):Uint8Array {
  const selected=task.versions.find(v=>v.version_id===version.version_id);
  if(!selected||canonicalize(selected.manifest)!==canonicalize(version.manifest)||selected.manifest_hash!==version.manifest_hash||selected.artifact_text!==version.artifact_text||!sameBytes(manifestBytes(selected),manifestBytes(version)))throw new Error("Export the exact version stored in this task.");
  const entries={"task.json":canonicalBytes(validateTaskSync(exchangeTask(task,version.version_id))),"manifest.json":manifestBytes(version),"artifact.html":encoder.encode(version.artifact_text)};
  if(Object.values(entries).reduce((sum,b)=>sum+b.length,0)>PACKAGE_BYTES)throw new Error("This package exceeds the 4 MiB re-import limit. Export an earlier version or reduce future evidence text.");
  const bytes=zipSync(entries,{level:6}); if(bytes.length>PACKAGE_BYTES)throw new Error("This package exceeds the 4 MiB re-import limit.");
  unzipPackage(bytes); return bytes;
}
const sameBytes=(a:Uint8Array,b:Uint8Array)=>a.length===b.length&&a.every((byte,index)=>byte===b[index]);
export async function importPackage(bytes:Uint8Array,options:ImportOptions={}):Promise<LocalTask> {
  if(!bytes.length||bytes.length>PACKAGE_BYTES)throw new Error("Import a release package under 4 MiB.");
  if(bytes[0]===0x50&&bytes[1]===0x4b){
    const entries=unzipPackage(bytes);
    if(Object.keys(entries).some(name=>!["task.json","manifest.json","artifact.html"].includes(name)))throw new Error("The release ZIP contains an unsupported member.");
    const manifest=entries["manifest.json"],artifact=entries["artifact.html"],originalTask=entries["task.json"];
    if(!!manifest!==!!artifact)throw new Error("The release ZIP must include both original manifest and artifact.");
    if(originalTask){
      const task=await validateTask(strictJsonParse(utf8Decode(originalTask)));
      if(manifest&&artifact){
        const selected=strictJsonParse(utf8Decode(manifest)) as ReleaseManifest;
        const version=task.versions.find(v=>v.version_id===selected?.version_id);
        if(!version||!sameBytes(manifestBytes(version),manifest)||!sameBytes(encoder.encode(version.artifact_text),artifact))throw new Error("The ZIP copies do not match the task commitments.");
      }
      return task;
    }
    if(!manifest||!artifact)throw new Error("Import the ClueTide task or its original manifest and artifact.");
    const m=strictJsonParse(utf8Decode(manifest)) as ReleaseManifest;
    if(!m||!isTransactionHash(m.task_id))throw new Error("The manifest task identity failed validation.");
    if(!options.resolveTask)throw new Error("This original two-file package needs its task's public chain roles. Connect to BOT Mainnet or import a complete task package.");
    const roles=await options.resolveTask(m.task_id);
    if(roles.task_id!==undefined&&roles.task_id!==m.task_id)throw new Error("The resolved chain task does not match the package.");
    if(roles.target_id!==undefined&&roles.target_id!==m.target_id)throw new Error("The resolved chain target does not match the package.");
    const version:LocalVersion={version_id:m.version_id,manifest:m,manifest_text:utf8Decode(manifest),manifest_hash:await sha256(manifest),artifact_hash:await sha256(artifact),artifact_text:utf8Decode(artifact),summary:m.summary,expires_at:m.expires_at,bundle_url:""};
    return validateTask({...roles,task_id:m.task_id,title:roles.title||"Shared release",created_at:roles.created_at||new Date(0).toISOString(),target:"demo-site",target_id:m.target_id,versions:[version],version_count:m.version_id,attempts:[]});
  }
  return validateTask(strictJsonParse(utf8Decode(bytes)));
}
export function downloadFile(name:string,bytes:Uint8Array,type="application/octet-stream") {
  const url=URL.createObjectURL(new Blob([new Uint8Array(bytes)],{type})),a=document.createElement("a");a.href=url;a.download=name;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
}
export const backupBytes=(task:LocalTask)=>{const bytes=canonicalBytes(validateTaskSync(exchangeTask(task)));if(bytes.length>PACKAGE_BYTES)throw new Error("The task backup exceeds the 4 MiB re-import limit.");return bytes;};
export const exampleHtml='<!doctype html>\n<html lang="en"><meta charset="utf-8"><title>ClueTide release</title><h1>Approved release</h1><p>This artifact is part of a ClueTide review task.</p></html>\n';
