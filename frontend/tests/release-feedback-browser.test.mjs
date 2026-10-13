import test from "node:test";
import assert from "node:assert/strict";
import {readFileSync} from "node:fs";
import {privateKeyToAccount} from "viem/accounts";
import {feedbackKey,feedbackTypedData,loadFeedbackDrafts,validateFeedbackDraft,validateReviewFeedback,readFeedback} from "../src/release/feedback.ts";
import {publicConfig} from "../src/release/public-config.ts";

const fixture=JSON.parse(readFileSync(new URL("./fixtures/release-public.json",import.meta.url),"utf8"));
const reviewer=privateKeyToAccount("0x"+"01".padStart(64,"0"));
const task={...fixture.task,reviewer:reviewer.address.toLowerCase()},version=fixture.version;
const draft={task_id:task.task_id,version_id:1,manifest_hash:version.manifest_hash,reason:"Restore the exact \"release\" heading. 中文 😀",acceptance_conditions:["The reviewed heading appears in the delivered HTML."],correction_transaction_hash:"0x"+"bb".repeat(32)};
async function signed(){const typed=await feedbackTypedData(draft),signature=await reviewer.signTypedData({...typed,message:{...typed.message,versionId:1n}});return {...draft,schema:"cluetide.review-feedback.v1",content_hash:typed.message.contentHash,signature,reviewer:task.reviewer,chain_id:677,contract_address:publicConfig.contract,received_at:"2026-10-13T05:00:00Z",verification:{status:"verified",confirmations:3}};}
test("review feedback authenticates designated signer, exact version, correction reference and reason",async()=>{
  const entry=await signed();assert.equal((await validateReviewFeedback(entry,task,version)).reason,draft.reason);
  for(const change of [{reason:"Changed after signature"},{acceptance_conditions:["Different condition"]},{version_id:2},{correction_transaction_hash:"0x"+"cc".repeat(32)},{reviewer:task.owner},{chain_id:1},{contract_address:task.owner},{signature:"0x"+"11".repeat(65)}])await assert.rejects(validateReviewFeedback({...entry,...change},task,version));
});
test("feedback reads verify signatures before returning content to the review page",async()=>{
  const previous=globalThis.fetch,entry=await signed();
  try{globalThis.fetch=async()=>Response.json({task_id:task.task_id,feedback:[entry]});assert.equal((await readFeedback(task,version)).length,1);globalThis.fetch=async()=>Response.json({task_id:task.task_id,feedback:[{...entry,reason:"tampered"}]});await assert.rejects(readFeedback(task,version),/digest/);}finally{globalThis.fetch=previous;}
});
test("persisted drafts preserve valid input and reject damaged shapes without breaking the task",()=>{
  const previous=globalThis.localStorage;
  try{globalThis.localStorage={getItem:()=>JSON.stringify({[feedbackKey(task.task_id,1)]:draft,damaged:{reason:[]}})};assert.deepEqual(Object.values(loadFeedbackDrafts()),[draft]);globalThis.localStorage={getItem:()=>"null"};assert.deepEqual(loadFeedbackDrafts(),{});}finally{globalThis.localStorage=previous;}
  for(const change of [{acceptance_conditions:[null]},{reason:"\ud800"},{version_id:true},{correction_transaction_hash:"0x"+"00".repeat(32)},{signature:"0x"+"11".repeat(65)}])assert.throws(()=>validateFeedbackDraft({...draft,...change}));
});
