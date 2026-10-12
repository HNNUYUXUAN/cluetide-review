import { canonicalize } from "json-canonicalize";
import { strFromU8, strToU8, unzipSync, zipSync } from "fflate";
import { publicConfig } from "./public-config.ts";
import { isAddress, isTransactionHash } from "./primitives.ts";
import type { Task, Version } from "./model.ts";

export interface ReleaseManifest extends Record<string, unknown> {
  schema: "cluetide.release-manifest.v1"; task_id: string; version_id: number; parent_version_id: number;
  summary: string; source_reference: string; artifact: {filename: "artifact.html"; sha256: string; bytes: number};
  operation: "publish_static"; target: "demo-site"; target_id: string; policy_version: string;
  expires_at: number; test_evidence: string[];
}
export interface LocalVersion extends Version {version_id: number; manifest: ReleaseManifest; artifact_text: string; expires_at: number}
export interface LocalTask extends Task {versions: LocalVersion[]; created_at: string; version_count: number}
const encoder = new TextEncoder();
export const canonicalBytes = (value: unknown) => encoder.encode(canonicalize(value));
export async function sha256(bytes: Uint8Array): Promise<string> {
  return "0x" + Array.from(new Uint8Array(await crypto.subtle.digest("SHA-256", new Uint8Array(bytes)))).map(b => b.toString(16).padStart(2,"0")).join("");
}
export async function newTask(title: string, account: string, reviewer: string): Promise<LocalTask> {
  if (!title.trim() || title.trim().length > 160) throw new Error("Enter a release title of 1–160 characters.");
  if (!isAddress(account) || !isAddress(reviewer) || account.toLowerCase() === reviewer.toLowerCase()) throw new Error("Choose a valid reviewer wallet distinct from your proposer wallet.");
  const task_id = "0x" + Array.from(crypto.getRandomValues(new Uint8Array(32))).map(b=>b.toString(16).padStart(2,"0")).join("");
  return {task_id, title:title.trim(), owner:account.toLowerCase(), proposer:account.toLowerCase(), reviewer:reviewer.toLowerCase(), executor:account.toLowerCase(), receipt_signer:publicConfig.receiptSigner, target:"demo-site", target_id:await sha256(encoder.encode(publicConfig.policy+"/"+task_id)), created_at:new Date().toISOString(), version_count:0, versions:[], attempts:[]};
}
export async function buildVersion(task: LocalTask, input: {summary:string; source_reference:string; artifact_text:string; test_evidence:string[]; expires_at:number}, parent=task.versions.at(-1)?.version_id||0, now=Math.floor(Date.now()/1000)): Promise<LocalVersion> {
  const {summary,source_reference,artifact_text,test_evidence,expires_at}=input;
  if (!summary.trim() || summary.length>1000 || !source_reference.trim() || source_reference.length>300) throw new Error("Provide a summary and source reference within the displayed limits.");
  const bytes=encoder.encode(artifact_text);
  if (!bytes.length || bytes.length>publicConfig.artifactBytes || artifact_text.includes("\0") || /[\uD800-\uDBFF](?![\uDC00-\uDFFF])|(?<![\uD800-\uDBFF])[\uDC00-\uDFFF]/u.test(artifact_text)) throw new Error("Upload valid UTF-8 HTML up to 32 KiB.");
  if (!test_evidence.length || test_evidence.length>12 || test_evidence.some(t=>!t.trim()||t.length>1000)) throw new Error("Provide 1–12 test evidence entries, each up to 1,000 characters.");
  if (!Number.isSafeInteger(expires_at) || expires_at<=now || expires_at>now+30*86400) throw new Error("Choose an approval deadline within the next 30 days.");
  if (!Number.isSafeInteger(parent)||parent<0||parent>=50) throw new Error("The demonstration version limit has been reached.");
  const artifact_hash=await sha256(bytes),version_id=parent+1;
  const manifest:ReleaseManifest={schema:"cluetide.release-manifest.v1",task_id:task.task_id,version_id,parent_version_id:parent,summary,source_reference,artifact:{filename:"artifact.html",sha256:artifact_hash,bytes:bytes.length},operation:"publish_static",target:"demo-site",target_id:task.target_id,policy_version:publicConfig.policy,expires_at,test_evidence};
  return {version_id,manifest,manifest_hash:await sha256(canonicalBytes(manifest)),artifact_hash,artifact_text,summary,expires_at,bundle_url:""};
}
export async function validateTask(value: unknown): Promise<LocalTask> {
  if (!value || typeof value!=="object") throw new Error("The task package is unreadable.");
  const t=value as LocalTask;
  if (!isTransactionHash(t.task_id)||typeof t.title!=="string"||!t.title.trim()||t.title.length>160||typeof t.created_at!=="string"||!Number.isFinite(Date.parse(t.created_at))||![t.owner,t.proposer,t.reviewer,t.executor,t.receipt_signer].every(isAddress)||t.proposer.toLowerCase()===t.reviewer.toLowerCase()||t.target!=="demo-site"||t.target_id!==await sha256(encoder.encode(publicConfig.policy+"/"+t.task_id))||!Array.isArray(t.versions)||t.versions.length>50) throw new Error("The task identity, roles or target failed validation.");
  const versions:LocalVersion[]=[];
  for(const version of t.versions){
    const m=version.manifest;
    if (!m || !Number.isSafeInteger(m.version_id)||m.version_id<1||m.version_id>50||m.parent_version_id!==m.version_id-1||versions.some(v=>v.version_id===m.version_id)) throw new Error("The version sequence failed validation.");
    // Historical deadlines remain valid evidence; input validation uses the original review window.
    const rebuilt=await buildVersion(t,{summary:m.summary,source_reference:m.source_reference,artifact_text:version.artifact_text,test_evidence:m.test_evidence,expires_at:m.expires_at},m.parent_version_id,m.expires_at-1);
    if (canonicalize(rebuilt.manifest)!==canonicalize(m)||rebuilt.manifest_hash!==version.manifest_hash||rebuilt.artifact_hash!==version.artifact_hash||version.version_id!==m.version_id) throw new Error("The package bytes do not match its commitments.");
    versions.push(rebuilt);
  }
  return {task_id:t.task_id,title:t.title,owner:t.owner.toLowerCase(),proposer:t.proposer.toLowerCase(),reviewer:t.reviewer.toLowerCase(),executor:t.executor.toLowerCase(),receipt_signer:t.receipt_signer.toLowerCase(),target:t.target,target_id:t.target_id,created_at:t.created_at,version_count:Math.max(0,...versions.map(v=>v.version_id)),versions:versions.sort((a,b)=>a.version_id-b.version_id),attempts:[]};
}
export function packageZip(task:LocalTask, version:LocalVersion):Uint8Array {
  return zipSync({"task.json":canonicalBytes(task),"manifest.json":canonicalBytes(version.manifest),"artifact.html":encoder.encode(version.artifact_text)},{level:6});
}
export async function importPackage(bytes:Uint8Array):Promise<LocalTask> {
  if(bytes.length>4*1024*1024)throw new Error("Import a release package under 4 MiB.");
  let raw:string;
  if(bytes[0]===0x50&&bytes[1]===0x4b){
    const entries=unzipSync(bytes,{filter:file=>{if(file.originalSize>4*1024*1024)throw new Error("The package expands beyond the import limit.");return file.name==="task.json";}});
    if(!entries["task.json"])throw new Error("Import the ClueTide task package, including task.json.");
    raw=strFromU8(entries["task.json"]);
  }else raw=new TextDecoder("utf-8",{fatal:true}).decode(bytes);
  return validateTask(JSON.parse(raw));
}
export function downloadFile(name:string,bytes:Uint8Array,type="application/octet-stream") {
  const url=URL.createObjectURL(new Blob([new Uint8Array(bytes)],{type})),a=document.createElement("a");a.href=url;a.download=name;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
}
export const backupBytes=(task:LocalTask)=>strToU8(canonicalize(task));
export const exampleHtml='<!doctype html>\n<html lang="en"><meta charset="utf-8"><title>ClueTide release</title><h1>Approved release</h1><p>This artifact is part of a ClueTide review task.</p></html>\n';
