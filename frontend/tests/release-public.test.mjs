import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { buildVersion, canonicalBytes, importPackage, packageZip, sha256, validateTask } from "../src/release/package.ts";
import { actionData, normalizeRecord, validateChainTask } from "../src/release/browser-chain.ts";
import { mergeTask } from "../src/release/browser-store.ts";
import { verifyReceiptEnvelope } from "../src/release/publisher.ts";

const fixture=JSON.parse(readFileSync(new URL("./fixtures/release-public.json",import.meta.url),"utf8"));
test("ABI uint8 decisions and bigint identifiers share the gateway state representation",()=>{
  const decoded={versionId:1n,parentVersionId:0n,decision:4,attemptId:1n};
  assert.deepEqual(normalizeRecord(decoded),{version_id:"1",parent_version_id:"0",decision:"4",attempt_id:"1"});
});
test("browser manifests and UTF-8 artifact hashes match Python JCS golden bytes",async()=>{
  const {task,version}=fixture;
  const rebuilt=await buildVersion({...task,versions:[]},{...version.manifest,artifact_text:version.artifact_text},0,version.expires_at-86400);
  assert.equal(new TextDecoder().decode(canonicalBytes(rebuilt.manifest)),fixture.canonical_manifest);
  assert.equal(rebuilt.manifest_hash,version.manifest_hash);
  assert.equal(rebuilt.artifact_hash,version.artifact_hash);
  assert.equal(await sha256(canonicalBytes(fixture.jcs_values)),fixture.jcs_hash);
});
test("browser calldata matches the Python ABI adapter for every authorization action",()=>{
  for(const [action,data] of Object.entries(fixture.calldata))assert.equal(actionData(action,fixture.task,fixture.version),data);
});
test("downloaded package preserves task evidence and rejects altered artifact bytes",async()=>{
  const restored=await importPackage(packageZip(fixture.task,fixture.version));
  assert.equal(restored.versions[0].manifest_hash,fixture.version.manifest_hash);
  const altered=structuredClone(fixture.task);altered.versions[0].artifact_text+="altered";
  await assert.rejects(validateTask(altered),/commitments/);
  const badTarget=structuredClone(fixture.task);badTarget.target_id="0x"+"aa".repeat(32);
  await assert.rejects(validateTask(badTarget),/target/);
});
test("a changed artifact requires a different version and commitment",async()=>{
  const v=await buildVersion(fixture.task,{...fixture.version.manifest,artifact_text:fixture.version.artifact_text+"\n"},1,fixture.version.expires_at-86400);
  assert.equal(v.version_id,2);assert.equal(v.manifest.parent_version_id,1);
  assert.notEqual(v.manifest_hash,fixture.version.manifest_hash);assert.notEqual(v.artifact_hash,fixture.version.artifact_hash);
});
test("local task imports merge exact versions and reject immutable conflicts",()=>{
  assert.equal(mergeTask([fixture.task],fixture.task).length,1);
  const altered=structuredClone(fixture.task);altered.versions[0].manifest_hash="0x"+"aa".repeat(32);
  assert.throws(()=>mergeTask([fixture.task],altered),/different package/);
  const wrong=structuredClone(fixture.task);wrong.reviewer=wrong.owner;
  assert.throws(()=>mergeTask([fixture.task],wrong),/different roles/);
});
test("chain task comparison rejects changed reviewer and target",()=>{
  const task=fixture.task,c={...task,target_hash:task.target_id};
  assert.doesNotThrow(()=>validateChainTask(task,{task:c}));
  assert.throws(()=>validateChainTask(task,{task:{...c,reviewer:task.owner}}),/roles/);
  assert.throws(()=>validateChainTask(task,{task:{...c,target_hash:"0x"+"aa".repeat(32)}}),/target/);
});
test("recorded mainnet receipt independently verifies its actual EIP-712 signature",async()=>{
  const envelope=fixture.receipt,r=envelope.receipt;
  const task={...fixture.task,task_id:r.task_id,owner:r.executor,proposer:r.executor,executor:r.executor,receipt_signer:r.receipt_signer,target_id:r.target_id};
  const v={...fixture.version,version_id:r.version_id,manifest_hash:r.manifest_hash,artifact_hash:r.artifact_hash};
  assert.equal(await verifyReceiptEnvelope(task,v,r.attempt_id,envelope,r.authorization_tx),1);
  await assert.rejects(verifyReceiptEnvelope(task,v,r.attempt_id,{...envelope,signature:"0x"+"00".repeat(65)},r.authorization_tx));
  const altered=structuredClone(envelope);altered.receipt.output_hash="0x"+"aa".repeat(32);
  await assert.rejects(verifyReceiptEnvelope(task,v,r.attempt_id,altered),/commitments/);
});
