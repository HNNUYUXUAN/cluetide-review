import test from "node:test";
import assert from "node:assert/strict";
import {IntentVerifier,inspectReceipt,mergeIntent,ReceiptLookupController,receiptLinks} from "../src/release/async-identity.ts";
import {authorizationState} from "../src/release/model.ts";

const taskA="0x"+"11".repeat(32),taskB="0x"+"22".repeat(32),hashA="0x"+"aa".repeat(32),hashB="0x"+"bb".repeat(32),account="0x"+"33".repeat(20);
const intent=()=>({id:"intent-one",created_at:"2026-10-11T00:00:00Z",request:{action:"start_execution",account,task_id:taskA,version_id:1},prepared:{transaction:{}},transaction_hash:hashA,status:"pending"});
function deferred(){let resolve,reject;const promise=new Promise((yes,no)=>{resolve=yes;reject=no;});return {promise,resolve,reject};}
const flush=async()=>{await Promise.resolve();await Promise.resolve();};
function verifierHarness(requests){
  let current=intent();
  const verifier=new IntentVerifier({current:()=>current,save:(_id,changes)=>{current=mergeIntent(current,changes);},verify:()=>requests.shift().promise});
  return {verifier,get current(){return current;},replace(changes){verifier.invalidate(current.id);current=mergeIntent(current,changes);}};
}
const verified=(hash,attempt="1")=>({status:"verified",transaction_hash:hash,onchain_ids:{attempt_id:attempt}});
const envelope=(taskId,attemptId)=>({receipt:{task_id:taskId,attempt_id:attemptId,version_id:1,status:"succeeded"},receipt_hash:hashA,signature:"signature",signer:account,verification:{signature_valid:true,artifact_matches:true}});
const getter=(attemptId)=>({attempt:{attempt_id:attemptId,version_id:"1",receipt_hash:hashA,result:"1"}});

test("a late hash-A response cannot overwrite the replacement hash-B verification",async()=>{
  const a=deferred(),b=deferred(),h=verifierHarness([a,b]);
  const pendingA=h.verifier.verify(h.current);
  h.replace({transaction_hash:hashB,status:"pending"});
  assert.equal(h.current.verification,undefined);
  const pendingB=h.verifier.verify(h.current);
  b.resolve(verified(hashB,"2"));await pendingB;
  a.resolve(verified(hashA,"1"));await pendingA;
  assert.equal(h.current.transaction_hash,hashB);assert.equal(h.current.verification.transaction_hash,hashB);assert.equal(h.current.verification.onchain_ids.attempt_id,"2");
});
test("a slow failed check cannot replace a newer successful check for the same hash",async()=>{
  const old=deferred(),latest=deferred(),h=verifierHarness([old,latest]);
  const first=h.verifier.verify(h.current),second=h.verifier.verify(h.current);
  latest.resolve(verified(hashA));await second;
  old.reject(new Error("Older request timed out"));await first;
  assert.equal(h.current.status,"verified");assert.equal(h.current.message,undefined);
});
test("changing request scope invalidates an in-flight result even if the hash is unchanged",async()=>{
  const pending=deferred(),h=verifierHarness([pending]);
  const check=h.verifier.verify(h.current);
  h.replace({request:{...h.current.request,version_id:2}});
  pending.resolve(verified(hashA));await check;
  assert.equal(h.current.request.version_id,2);assert.equal(h.current.verification,undefined);
});
test("a committed replacement hash clears all prior verification facts",()=>{
  const prior={...intent(),status:"verified",verification:verified(hashA),message:"Old observation"};
  const next=mergeIntent(prior,{transaction_hash:hashB,status:"pending",verification:prior.verification});
  assert.equal(next.verification,undefined);assert.equal(next.message,undefined);
});
test("Inspect receipt opens the clicked earlier attempt instead of the last attempt",async()=>{
  const attempts=[{attempt_id:"1"},{attempt_id:"2"}],navigation={page:"releases",selection:null};
  inspectReceipt(taskA,attempts[0].attempt_id,selection=>{navigation.selection=selection;},()=>{navigation.page="verify";});
  const calls=[];
  const controller=new ReceiptLookupController(navigation.selection,{receipt:async(id,task)=>{calls.push({id,task});return envelope(task,id);},read:async({attemptId})=>getter(attemptId)});
  await controller.lookup();
  assert.equal(navigation.page,"verify");assert.deepEqual(calls,[{id:"1",task:taskA}]);assert.equal(controller.state.result.identity.attemptId,"1");
});
test("changing receipt input discards a late envelope and prevents its chain lookup",async()=>{
  const pending=deferred();let reads=0;
  const controller=new ReceiptLookupController({taskId:taskA,attemptId:"1"},{receipt:()=>pending.promise,read:async()=>{reads++;return getter("1");}});
  const check=controller.lookup();controller.setInput({taskId:taskB,attemptId:"2"});pending.resolve(envelope(taskA,"1"));await check;
  assert.equal(reads,0);assert.equal(controller.state.result,null);assert.equal(controller.state.busy,false);assert.equal(controller.state.input.taskId,taskB);
});
test("late chain checks cannot put old green checks under a newer task and attempt",async()=>{
  const chainA=deferred(),chainB=deferred();
  const controller=new ReceiptLookupController({taskId:taskA,attemptId:"1"},{receipt:async(id,task)=>envelope(task,id),read:({taskId})=>taskId===taskA?chainA.promise:chainB.promise});
  const first=controller.lookup();await flush();assert.equal(controller.state.result.identity.taskId,taskA);
  controller.setInput({taskId:taskB,attemptId:"2"});assert.equal(controller.state.result,null);
  const second=controller.lookup();await flush();chainB.resolve(getter("2"));await second;
  chainA.resolve(getter("1"));await first;
  assert.equal(controller.state.result.identity.taskId,taskB);assert.equal(controller.state.result.chain.attempt.attempt_id,"2");
  assert.deepEqual(receiptLinks(controller.state.result.identity),{receipt:`/api/releases/attempts/2/receipt?task_id=${taskB}`,artifact:`/api/releases/attempts/2/artifact?task_id=${taskB}`});
});
test("a late receipt error cannot erase a newer successful lookup",async()=>{
  const old=deferred(),latest=deferred(),queue=[old,latest];
  const controller=new ReceiptLookupController({taskId:taskA,attemptId:"1"},{receipt:()=>queue.shift().promise,read:async()=>getter("1")});
  const first=controller.lookup(),second=controller.lookup();latest.resolve(envelope(taskA,"1"));await second;old.reject(new Error("Late failure"));await first;
  assert.equal(controller.state.error,"");assert.equal(controller.state.result.chain.attempt.result,"1");
});
test("a receipt for an unexpected identity is rejected before checks or downloads",async()=>{
  let reads=0;
  const controller=new ReceiptLookupController({taskId:taskA,attemptId:"1"},{receipt:async()=>envelope(taskB,"2"),read:async()=>{reads++;return getter("2");}});
  await controller.lookup();assert.match(controller.state.error,/different release or attempt/);assert.equal(controller.state.result,null);assert.equal(reads,0);
});
test("review controls follow each contract decision transition",()=>{
  const version={version_id:1,manifest_hash:hashA,artifact_hash:hashA,manifest:{},bundle_url:"/bundle"};
  const state=decision=>authorizationState(version,{task:{version_count:"1"},version:{version_id:"1",manifest_hash:hashA,artifact_hash:hashA,deadline:"200",decision}},100);
  assert.equal(state("0").canApprove,true);assert.equal(state("1").canApprove,false);
  assert.equal(state("0").canRevoke,true);assert.equal(state("1").canRevoke,true);
  assert.equal(state("0").canRequestCorrection,true);assert.equal(state("1").canRequestCorrection,true);
  for(const decision of ["2","3","4"]){assert.equal(state(decision).canApprove,false);assert.equal(state(decision).canRevoke,false);assert.equal(state(decision).canRequestCorrection,false);}
});
