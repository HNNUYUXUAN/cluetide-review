import test from "node:test";
import assert from "node:assert/strict";
import {readFileSync} from "node:fs";
import {decodeFunctionData,encodeAbiParameters,encodeEventTopics,encodeFunctionResult} from "viem";
import {actionData,isVerifiedStartGrant,verifyStartReference,verifyPublicIntent,readConfirmedPublicChain,verifyCorrectionReference} from "../src/release/browser-chain.ts";
import {publicConfig} from "../src/release/public-config.ts";
import {authorizationState} from "../src/release/model.ts";

const fixture=JSON.parse(readFileSync(new URL("./fixtures/release-public.json",import.meta.url),"utf8"));
const artifact=JSON.parse(readFileSync(new URL("../../contracts/artifacts/ClueTideReleaseWitness.json",import.meta.url),"utf8"));
const {abi}=artifact,task=fixture.task,version=fixture.version;
const hash="0x"+"ab".repeat(32),blockHash="0x"+"cd".repeat(32),otherHash="0x"+"ef".repeat(32);
const reference={schema:"cluetide.start-reference.v1",task_id:task.task_id,version_id:1,transaction_hash:hash};
function rpc(options={}){
  let blockReads=0;const calls=[];
  const taskRecord={owner:task.owner,proposer:task.proposer,reviewer:task.reviewer,executor:task.executor,receiptSigner:task.receipt_signer,targetHash:task.target_id,versionCount:4n,attemptCount:1n,createdAt:1n};
  const versionRecord={versionId:1n,parentVersionId:0n,manifestHash:version.manifest_hash,artifactHash:version.artifact_hash,deadline:BigInt(version.expires_at),decision:4,approvedAt:1n,attemptId:1n};
  const attemptRecord={attemptId:1n,versionId:1n,startedAt:2n,receiptHash:options.completed?otherHash:"0x"+"00".repeat(32),result:options.completed?1:0,recordedAt:options.completed?3n:0n};
  return {calls,request:async({method,params=[]})=>{
    calls.push({method,params});
    if(method==="eth_chainId")return options.network||"0x2a5";
    if(method==="eth_blockNumber")return options.head||"0x66";
    if(method==="eth_getCode")return options.code||artifact.deployedBytecode;
    if(method==="eth_getBlockByNumber"){
      blockReads++;return {number:params[0],hash:options.reorg&&blockReads>2?otherHash:blockHash,timestamp:"0x40000000",transactions:[],gasLimit:"0x100000",gasUsed:"0x1",size:"0x10",difficulty:"0x0",totalDifficulty:"0x0"};
    }
    if(method==="eth_getTransactionByHash")return {hash,blockHash,blockNumber:"0x64",chainId:options.txNetwork||"0x2a5",from:options.from||task.executor,to:options.to||publicConfig.contract,input:options.input||actionData("start_execution",task,version),value:options.value||"0x0",type:"0x0",gas:"0x50000",gasPrice:"0x1",nonce:"0x1",transactionIndex:"0x0",v:"0x1",r:hash,s:hash};
    if(method==="eth_getTransactionReceipt")return {transactionHash:hash,transactionIndex:"0x0",blockHash,blockNumber:"0x64",from:options.from||task.executor,to:publicConfig.contract,status:options.reverted?"0x0":"0x1",type:"0x0",gasUsed:"0x100",effectiveGasPrice:"0x1",cumulativeGasUsed:"0x100",logs:options.noEvent?[]:[{address:options.eventAddress||publicConfig.contract,data:options.correction?encodeAbiParameters([{type:"uint8"}],[3]):"0x",topics:encodeEventTopics({abi,eventName:options.correction?"DecisionRecorded":"ApprovalConsumed",args:{taskId:task.task_id,versionId:options.eventVersion||1n,attemptId:1n}}),blockHash,blockNumber:"0x64",transactionHash:hash,transactionIndex:"0x0",logIndex:"0x0",removed:false}],logsBloom:"0x"+"00".repeat(256)};
    if(method==="eth_call"){
      const decoded=decodeFunctionData({abi,data:params[0].data});
      const result=decoded.functionName==="getTask"?{...taskRecord,...options.task}:decoded.functionName==="getVersion"?{...versionRecord,...options.version}:{...attemptRecord,...options.attempt};
      return encodeFunctionResult({abi,functionName:decoded.functionName,result});
    }
    throw new Error("Unexpected RPC "+method);
  }};
}
test("empty-device recovery grants the original attempt despite a later version and elapsed deadline",async()=>{
  const provider=rpc(),grant=await verifyStartReference(reference,task,version,provider);
  assert.equal(grant.attempt_id,"1");assert.equal(grant.confirmations,3);assert.equal(grant.getter_state.task.version_count,"4");assert.equal(grant.getter_state.version.deadline,String(version.expires_at));
  assert.equal(isVerifiedStartGrant(grant),true);assert.equal(isVerifiedStartGrant(JSON.parse(JSON.stringify(grant))),false);
  assert.ok(provider.calls.filter(call=>call.method==="eth_call").every(call=>call.params[1]==="0x64"));
  const afterDeadline=authorizationState(version,grant.getter_state,version.expires_at+1);
  assert.equal(afterDeadline.expired,true);assert.equal(afterDeadline.label,"start_consumed");
});
test("normal wallet verification uses the same event and snapshot validation",async()=>{
  const intent={request:{action:"start_execution",account:task.executor,task_id:task.task_id,version_id:1},transaction_hash:hash,prepared:{transaction:{from:task.executor},commitments:{manifest_hash:version.manifest_hash,artifact_hash:version.artifact_hash,expires_at:version.expires_at}}};
  const result=await verifyPublicIntent(intent,rpc());assert.equal(result.status,"verified");assert.equal(result.onchain_ids.attempt_id,"1");
  await assert.rejects(verifyPublicIntent(intent,rpc({noEvent:true})),/ApprovalConsumed/);
  await assert.rejects(verifyPublicIntent(intent,rpc({version:{deadline:1n}})),/package or attempt/);
});
test("recovery checks the immutable deadline and parent version without reapplying expiry",async()=>{
  for(const changed of [{deadline:BigInt(version.expires_at)+1n},{deadline:1n},{parentVersionId:1n}])await assert.rejects(verifyStartReference(reference,task,version,rpc({version:changed})),/package or attempt/);
  await assert.rejects(verifyStartReference(reference,task,{...version,expires_at:version.expires_at+1},rpc()),/exact task and version/);
});
test("recovery rejects wrong account, network, value, function, event and role",async()=>{
  for(const options of [{from:task.reviewer},{network:"0x1"},{txNetwork:"0x1"},{value:"0x1"},{input:actionData("approve",task,version)},{noEvent:true},{eventVersion:2n},{eventAddress:task.reviewer},{task:{reviewer:task.owner}},{version:{artifactHash:otherHash}},{attempt:{versionId:2n}},{reverted:true},{code:"0x01"}])await assert.rejects(verifyStartReference(reference,task,version,rpc(options)),JSON.stringify(options,(_,v)=>typeof v==="bigint"?String(v):v));
});
test("two confirmations and a canonical block change cannot yield a verified grant",async()=>{
  await assert.rejects(verifyStartReference(reference,task,version,rpc({head:"0x65"})),/three/);
  await assert.rejects(verifyStartReference(reference,task,version,rpc({reorg:true})),/block|canonical/);
});
test("completed attempts recover into result inspection and confirmed reads stay three blocks deep",async()=>{
  const grant=await verifyStartReference(reference,task,version,rpc({completed:true}));assert.equal(grant.receipt_recorded,true);
  const provider=rpc({head:"0x70"}),state=await readConfirmedPublicChain(task.task_id,1,"1",provider);
  assert.equal(state.read_block_number,"110");assert.equal(state.confirmations,3);assert.ok(provider.calls.filter(c=>c.method==="eth_call").every(c=>c.params[1]==="0x6e"));
});
test("feedback signing requires the exact confirmed correction transaction and decision event",async()=>{
  const options={correction:true,from:task.reviewer,input:actionData("request_correction",task,version),version:{decision:3,deadline:BigInt(version.expires_at)}};
  const result=await verifyCorrectionReference(task,version,hash,rpc(options));assert.equal(result.status,"verified");assert.equal(result.getter_state.version.decision,"3");
  for(const changed of [{noEvent:true},{eventVersion:2n},{from:task.executor},{head:"0x65"},{reorg:true},{version:{decision:3,deadline:1n}}])await assert.rejects(verifyCorrectionReference(task,version,hash,rpc({...options,...changed})));
});
