import test from "node:test";
import assert from "node:assert/strict";
import {authorizationState,canSend,validatePrepared,validateRoles,loadIntents,parseVerification,walletError,safeText} from "../src/release/model.ts";
import {send,switchToBot} from "../src/release/wallet.ts";
const owner="0x"+"11".repeat(20),reviewer="0x"+"22".repeat(20),contract="0x"+"33".repeat(20),hash="0x"+"44".repeat(32);
const request={action:"approve",account:reviewer,task_id:hash,version_id:"2"};
const prepared={action:"approve",ready_to_send:true,transaction:{from:reviewer,to:contract,chainId:"0x2a5",data:"0x12345678",value:"0x0",gas:"0x5208",gasPrice:"0x2"},commitments:{task_id:hash},fees:{gas_limit:"21000",gas_price_wei:"2",estimated_max_fee_wei:"42000",balance_wei:"50000",sufficient_balance:true}};
test("transaction is bound to BOT677, selected account and contract",()=>{
  assert.equal(validatePrepared(prepared,request,contract),prepared);
  for(const tx of [{...prepared.transaction,chainId:"0x3c8"},{...prepared.transaction,from:owner},{...prepared.transaction,to:owner},{...prepared.transaction,value:"0x1"}])assert.throws(()=>validatePrepared({...prepared,transaction:tx},request,contract));
});
test("signing requires successful preflight, complete fee arithmetic and sufficient balance",()=>{
  assert.equal(canSend(prepared),true);
  assert.equal(canSend({...prepared,ready_to_send:false}),false);
  assert.equal(canSend({...prepared,fees:{...prepared.fees,estimated_max_fee_wei:"42001"}}),false);
  assert.equal(canSend({...prepared,fees:{...prepared.fees,balance_wei:"1"}}),false);
});
test("refresh recovers a request with no returned hash as unknown",()=>{
  const intents=loadIntents(JSON.stringify([{id:"one",created_at:new Date().toISOString(),request,prepared,status:"awaiting_wallet"}]));
  assert.equal(intents[0].status,"unknown");assert.equal(loadIntents("bad json").length,0);
});
test("verification rejects a mismatched hash and nondecimal attempt identifiers",()=>{
  assert.equal(parseVerification({status:"verified",transaction_hash:hash,onchain_ids:{attempt_id:"1"}},hash).status,"verified");
  assert.throws(()=>parseVerification({status:"verified",transaction_hash:owner},hash));
  assert.throws(()=>parseVerification({status:"verified",transaction_hash:hash,onchain_ids:{attempt_id:hash}},hash));
});
test("reviewer is distinct and wallet rejection is explicit English",()=>{
  assert.equal(validateRoles({owner,proposer:owner,reviewer,executor:owner}),null);
  assert.equal(validateRoles({title:"Release preview",owner,proposer:owner,reviewer,executor:owner}),null);
  assert.match(validateRoles({owner,proposer:owner,reviewer:owner,executor:owner}),/distinct/);
  assert.equal(walletError({code:4001}).rejected,true);
  assert.equal(/[\u3400-\u9fff]/.test(safeText(new Error("服务不可用"))),false);
});
test("wallet account change blocks eth_sendTransaction",async()=>{
  const calls=[];const provider={request:async({method})=>{calls.push(method);return method==="eth_accounts"?[owner]:"0x2a5";}};
  await assert.rejects(send(provider,prepared,()=>true),/account or network changed/);
  assert.equal(calls.includes("eth_sendTransaction"),false);
});
test("unknown chain is added then explicitly switched to BOT",async()=>{
  const calls=[];let first=true;const provider={request:async({method,params})=>{calls.push({method,params});if(method==="wallet_switchEthereumChain"&&first){first=false;throw {code:4902};}if(method==="eth_accounts")return [owner];if(method==="eth_chainId")return "0x2a5";return null;}};
  assert.equal((await switchToBot(provider)).chainId,677);
  assert.deepEqual(calls.slice(0,3).map(call=>call.method),["wallet_switchEthereumChain","wallet_addEthereumChain","wallet_switchEthereumChain"]);
});
test("approval applies only to the latest exact unexpired package",()=>{
  const version={version_id:1,manifest_hash:hash,artifact_hash:hash,manifest:{},bundle_url:"/bundle"};
  const getter={task:{version_count:"1"},version:{version_id:"1",manifest_hash:hash,artifact_hash:hash,deadline:"200",decision:"1"}};
  assert.equal(authorizationState(version,getter,100).canStart,true);
  assert.equal(authorizationState({...version,version_id:2},getter,100).canStart,false);
  assert.equal(authorizationState({...version,artifact_hash:"0x"+"55".repeat(32)},getter,100).canStart,false);
  assert.equal(authorizationState(version,{...getter,task:{version_count:"2"}},100).label,"superseded");
  assert.equal(authorizationState(version,getter,200).label,"approval_expired");
  assert.equal(authorizationState(version,null,100).canStart,false);
});
