import test from "node:test";
import assert from "node:assert/strict";
import {readFileSync} from "node:fs";
import {zipSync} from "fflate";
import {createAcceptanceBundle,verifyAcceptanceBundle} from "../src/release/acceptance.ts";
import {canonicalBytes,sha256,unzipPackage,utf8Decode} from "../src/release/package.ts";
import {readResponseBytes} from "../src/release/publisher.ts";
import {saveEvidence} from "../src/release/evidence-store.ts";

const originalTask=JSON.parse(readFileSync(new URL("../src/release/verified-release.json",import.meta.url),"utf8"));
const receiptBytes=new Uint8Array(readFileSync(new URL("../src/release/verified-receipt.json",import.meta.url)));
const receipt=JSON.parse(utf8Decode(receiptBytes));
function evidence(){return {task:structuredClone(originalTask),version_id:1,attempt_id:"1",observed_at:"2026-10-13T00:00:00Z",start_reference:{schema:"cluetide.start-reference.v1",task_id:originalTask.task_id,version_id:1,transaction_hash:receipt.receipt.authorization_tx},receipt_bytes:receiptBytes,artifact_bytes:new TextEncoder().encode(originalTask.versions[0].artifact_text),transactions:[{action:"start_execution",transaction_hash:receipt.receipt.authorization_tx}],observations:{chain:{block_number:"26335268",block_hash:receipt.receipt.chain_grant.read_block_hash,confirmations:52,getter_state:{}}}};}
async function repack(bytes,mutate){const entries=unzipPackage(bytes);await mutate(entries);const index=JSON.parse(utf8Decode(entries["acceptance.json"]));index.files=await Promise.all(Object.entries(entries).filter(([path])=>path!=="acceptance.json").map(async([path,bytes])=>({path,bytes:bytes.length,sha256:await sha256(bytes)})));entries["acceptance.json"]=canonicalBytes(index);return zipSync(entries);}
test("portable acceptance verifies raw originals and EIP-712 signature with every network call disabled",async()=>{
  const previous=globalThis.fetch;let networkCalls=0;
  globalThis.fetch=async()=>{networkCalls++;throw new Error("Offline verification attempted a network call");};
  try{
    const bytes=await createAcceptanceBundle(evidence()),result=await verifyAcceptanceBundle(bytes);
    assert.equal(networkCalls,0);assert.deepEqual(result.offline,{files:true,signature:true,artifact:true});
    assert.equal(result.chain.status,"not_checked");assert.equal(result.onlineArtifact.status,"not_checked");
    assert.deepEqual(result.evidence.receipt_bytes,receiptBytes);assert.equal(result.evidence.observations.chain.confirmations,52);
    assert.equal(result.envelope.verification.signature_valid,true);
  }finally{globalThis.fetch=previous;}
});
test("index rewrites cannot conceal an altered downloaded artifact, signature or duplicated original",async()=>{
  const bytes=await createAcceptanceBundle(evidence());
  for(const mutate of [entries=>{entries["published/artifact.html"][0]^=1;},entries=>{entries["versions/1/artifact.html"][0]^=1;},entries=>{const r=JSON.parse(utf8Decode(entries["receipt.json"]));r.signature="0x"+"11".repeat(65);entries["receipt.json"]=canonicalBytes(r);}])await assert.rejects(verifyAcceptanceBundle(await repack(bytes,mutate)));
});
test("a start reference from a different transaction cannot attest the signed receipt",async()=>{
  const input=evidence();input.start_reference.transaction_hash="0x"+"ab".repeat(32);input.transactions[0].transaction_hash=input.start_reference.transaction_hash;
  await assert.rejects(createAcceptanceBundle(input),/commitments/);
});
test("every evidence member is listed once and unsupported members are rejected",async()=>{
  const bytes=await createAcceptanceBundle(evidence()),entries=unzipPackage(bytes),index=JSON.parse(utf8Decode(entries["acceptance.json"]));
  index.files.push(index.files[0]);entries["acceptance.json"]=canonicalBytes(index);await assert.rejects(verifyAcceptanceBundle(zipSync(entries)),/more than once/);
  const extra=await repack(bytes,entries=>{entries["unexpected.json"]=canonicalBytes({verified:true});});await assert.rejects(verifyAcceptanceBundle(extra),/unsupported/);
});
test("evidence downloads enforce the streaming limit when content-length is absent",async()=>{
  const previous=globalThis.fetch;
  try{
    globalThis.fetch=async()=>new Response(new Uint8Array(101));await assert.rejects(readResponseBytes("https://example.test/artifact",100),/size limit/);
    globalThis.fetch=async()=>new Response(new Uint8Array(100));assert.equal((await readResponseBytes("https://example.test/artifact",100)).length,100);
  }finally{globalThis.fetch=previous;}
});
test("unavailable evidence storage explicitly requests immediate export",async()=>{
  const previous=globalThis.indexedDB;globalThis.indexedDB=undefined;
  try{await assert.rejects(saveEvidence(evidence()),/export the acceptance bundle now/);}finally{globalThis.indexedDB=previous;}
});
