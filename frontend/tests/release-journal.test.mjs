import test from "node:test";
import assert from "node:assert/strict";
import {readFileSync} from "node:fs";
import {journalKey,readJournal,writeJournal,journalContext,reconcileJournal,walletContextKey} from "../src/release/transaction-journal.ts";
import {retryRead} from "../src/release/service-recovery.ts";
import {nextStep} from "../src/release/workflow.ts";
import {send,selectRoleAccount} from "../src/release/wallet.ts";
import {publicConfig} from "../src/release/public-config.ts";
import {IntentVerifier,mergeIntent} from "../src/release/async-identity.ts";
const fixture=JSON.parse(readFileSync(new URL("./fixtures/release-public.json",import.meta.url),"utf8")),task=fixture.task,version=fixture.version;
const hash="0x"+"aa".repeat(32),other="0x"+"bb".repeat(32);
const intent={id:"request-1",created_at:new Date().toISOString(),request:{action:"approve",account:task.reviewer,task_id:task.task_id,version_id:1},prepared:{action:"approve",prepared_block:"100",prepared_nonce:"17",ready_to_send:true,transaction:{from:task.reviewer,to:publicConfig.contract,data:"0x1234",chainId:"0x2a5",value:"0x0",gas:"0x5208",gasPrice:"0x2"},commitments:{task_id:task.task_id},fees:{estimated_max_fee_wei:"42000",balance_wei:"50000",sufficient_balance:true}},status:"awaiting_wallet"};
const memory=()=>{const entries=new Map();return {getItem:key=>entries.get(key)??null,setItem:(key,value)=>entries.set(key,value)};};
test("journal keeps precise request parameters and restores an interrupted wallet response",()=>{
  const storage=memory();writeJournal([intent],storage);const [saved]=readJournal(storage);
  assert.equal(saved.status,"unknown");assert.deepEqual(journalContext(saved),{chain_id:677,contract:publicConfig.contract,account:task.reviewer,calldata:"0x1234",value:"0x0",prepared_block:"100",nonce:"17"});
  assert.equal(saved.id,intent.id);
});
test("legacy intents migrate and damaged journals prevent silent request loss",()=>{
  const storage=memory();storage.setItem("cluetide.public.intents.v1",JSON.stringify([intent]));assert.equal(readJournal(storage).length,1);
  storage.setItem(journalKey,"bad JSON");assert.throws(()=>readJournal(storage));
  assert.throws(()=>writeJournal([intent],{setItem:()=>{throw new Error("Quota");}}),/Download the journal/);
});
test("bounded reconciliation scans 4,000 blocks in batches and chooses the exact original",async()=>{
  const ranges=[];const result=await reconcileJournal(intent,[],{head:async()=>10000n,hashes:async(from,to)=>{ranges.push([from,to]);return from===350n?[hash,other]:[];},matches:async candidate=>candidate===hash});
  assert.equal(result.status,"found");assert.equal(result.hash,hash);assert.equal(result.scanned_blocks,4000);assert.equal(ranges.length,16);assert.deepEqual(ranges.at(-1),[3850n,4099n]);
});
test("ambiguous matches and exhausted ranges require a wallet hash instead of resending",async()=>{
  const dependencies={head:async()=>10000n,hashes:async()=>[],matches:async()=>true};
  assert.equal((await reconcileJournal(intent,[hash,other],dependencies)).status,"ambiguous");
  assert.equal((await reconcileJournal(intent,[],dependencies)).status,"scan_limit_reached");
  const legacy={...intent,prepared:{...intent.prepared,prepared_block:undefined}};
  assert.equal((await reconcileJournal(legacy,[],dependencies)).status,"manual_hash_required");
});
test("duplicate account events have the same identity while a real network change differs",()=>{
  assert.equal(walletContextKey({account:task.reviewer.toUpperCase(),chainId:677}),walletContextKey({account:task.reviewer,chainId:677}));
  assert.notEqual(walletContextKey({account:task.reviewer,chainId:677}),walletContextKey({account:task.reviewer,chainId:1}));
});
test("a returned hash remains the original request's result after the wallet switches",async()=>{
  let account=task.reviewer;
  const provider={request:async({method})=>{if(method==="eth_accounts")return [account];if(method==="eth_chainId")return "0x2a5";if(method==="eth_sendTransaction"){account=task.executor;return hash;}throw new Error(method);}};
  const returned=await send(provider,intent.prepared,()=>true);
  assert.equal(returned,hash);assert.equal(intent.request.account,task.reviewer);
});
test("role selection checks the full assigned address and gives an unsupported-wallet instruction",async()=>{
  const methods=[];const provider={request:async({method})=>{methods.push(method);return method==="eth_accounts"?[task.reviewer]:method==="eth_chainId"?"0x2a5":[];}};
  assert.equal((await selectRoleAccount(provider,task.reviewer)).account,task.reviewer);assert.equal(methods[0],"wallet_requestPermissions");
  await assert.rejects(selectRoleAccount({request:async()=>{throw {code:4200};}},task.reviewer),/account menu/);
});
test("readonly recovery follows 2,5,10-second backoff inside the 30-second budget",async()=>{
  let clock=0,calls=0;const waits=[];
  assert.equal(await retryRead(async()=>{calls++;clock+=1000;if(calls<4)throw new Error("Cold");return "ready";},{now:()=>clock,sleep:async ms=>{waits.push(ms);clock+=ms;}}),"ready");
  assert.deepEqual(waits,[2000,5000,10000]);assert.ok(clock<=30000);
});
test("the next step names the role from the imported task and revisits approval after revision",()=>{
  const state={task:{version_count:"1"},version:{version_id:"1",manifest_hash:version.manifest_hash,artifact_hash:version.artifact_hash,deadline:String(Date.now()/1000+3600),decision:"0"}};
  assert.equal(nextStep(task,version,state).account,task.reviewer);
  assert.equal(nextStep(task,{...version,version_id:2},state).role,"proposer");
  assert.equal(nextStep(task,version,{...state,version:{...state.version,decision:"1"}}).account,task.executor);
});
test("loading the chain never suggests an unverified wallet operation",()=>{
  assert.equal(nextStep(task,version,null).action,undefined);
  assert.equal(nextStep(task,version,null).gas,false);
});
test("a replacement hash retains the original reference and parameter mismatch is explicit",async()=>{
  let current={...intent,status:"unknown",transaction_hash:hash};
  const replaced=mergeIntent(current,{transaction_hash:other});
  assert.deepEqual(replaced.transaction_hashes,[hash]);
  const verifier=new IntentVerifier({current:()=>current,save:(_,changes)=>{current={...current,...changes};},verify:async()=>{throw Object.assign(new Error("Different calldata"),{code:"PARAMETER_MISMATCH"});}});
  await verifier.verify(current);
  assert.equal(current.status,"mismatch");assert.match(current.message,/Different calldata/);
});
