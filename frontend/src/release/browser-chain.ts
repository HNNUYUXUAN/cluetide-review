import { createPublicClient, custom, defineChain, encodeFunctionData, http, hexToBytes, TransactionNotFoundError, TransactionReceiptNotFoundError } from "viem";
import type { Abi, Address, Hex } from "viem";
import contractArtifact from "./release-contract.json" with {type:"json"};
import { publicConfig } from "./public-config.ts";
import { sha256 } from "./package.ts";
import type { LocalTask, LocalVersion } from "./package.ts";
import type { WalletProvider } from "../bot-wallet.ts";
import type { Action, Getter, Intent, Prepared, PrepareRequest, Verification } from "./model.ts";

export const botChain=defineChain({id:677,name:"BOT Mainnet",nativeCurrency:{name:"BOT",symbol:"BOT",decimals:18},rpcUrls:{default:{http:[publicConfig.rpc]}},blockExplorers:{default:{name:"BOT Explorer",url:publicConfig.explorer}}});
const abi=contractArtifact.abi as Abi;
export const clientFor=(provider?:WalletProvider)=>createPublicClient({chain:botChain,transport:provider?custom(provider):http(publicConfig.rpc,{timeout:15_000,retryCount:1}),cacheTime:0});
type Client=ReturnType<typeof clientFor>;
const address=publicConfig.contract;
export const normalizeRecord=(record:unknown):Record<string,unknown>=>Object.fromEntries(Object.entries(record as Record<string,unknown>).map(([key,value])=>[key.replace(/[A-Z]/g,c=>"_"+c.toLowerCase()),typeof value==="bigint"||typeof value==="number"?value.toString():typeof value==="string"?value.toLowerCase():value]));
const normalized=normalizeRecord;
function unknownTask(error:unknown):boolean {
  let current=error;
  for(let depth=0;depth<12&&current&&typeof current==="object";depth++){
    const e=current as {data?:{errorName?:string};cause?:unknown};
    if(e.data?.errorName==="UnknownTask")return true;
    current=e.cause;
  }
  return false;
}
async function anchor(client:Client, blockNumber?:bigint){
  if(await client.getChainId()!==677)throw new Error("The RPC network differs from BOT Mainnet 677.");
  const block=await client.getBlock(blockNumber===undefined?{blockTag:"latest"}:{blockNumber});
  const code=await client.getBytecode({address,blockNumber:block.number});
  if(!code||await sha256(hexToBytes(code))!=="0x"+contractArtifact.runtime_sha256)throw new Error("The deployed witness code does not match the verified contract.");
  return block;
}
export async function readPublicChain(taskId:string, versionId?:number, attemptId?:string, provider?:WalletProvider):Promise<{getter_state:Getter;read_block_number:string;block_hash:string}> {
  const client=clientFor(provider),block=await anchor(client);
  let task:Record<string,unknown>;
  try{task=normalized(await client.readContract({address,abi,functionName:"getTask",args:[taskId as Hex],blockNumber:block.number}));}
  catch(error){if(unknownTask(error))return {getter_state:{task:null,version:null,attempt:null},read_block_number:block.number.toString(),block_hash:block.hash};throw error;}
  const state:Getter={task,version:null,attempt:null};
  const selected=versionId||Number(task.version_count)||undefined;
  if(selected&&BigInt(String(task.version_count))>=BigInt(selected))state.version=normalized(await client.readContract({address,abi,functionName:"getVersion",args:[taskId as Hex,BigInt(selected)],blockNumber:block.number}));
  if(!attemptId&&state.version?.decision==="4")attemptId=String(state.version.attempt_id);
  if(attemptId&&BigInt(String(task.attempt_count))>=BigInt(attemptId))state.attempt=normalized(await client.readContract({address,abi,functionName:"getAttempt",args:[taskId as Hex,BigInt(attemptId)],blockNumber:block.number}));
  if((await client.getBlock({blockNumber:block.number})).hash!==block.hash)throw new Error("The observed chain block changed. Refresh before continuing.");
  return {getter_state:state,read_block_number:block.number.toString(),block_hash:block.hash};
}
export function validateChainTask(task:LocalTask, getter:Getter){
  const chain=getter.task;
  if(!chain)return;
  for(const role of ["owner","proposer","reviewer","executor","receipt_signer"] as const)if(String(chain[role]).toLowerCase()!==task[role].toLowerCase())throw new Error("The imported task roles differ from the on-chain task.");
  if(chain.target_hash!==task.target_id)throw new Error("The task target differs from its chain commitment.");
}
export function actionData(action:Action,task:LocalTask,version?:LocalVersion,receipt?:{attemptId:string;receiptHash:string;result:number;signature:string}):Hex{
  if(action==="deploy")throw new Error("Use the existing verified witness contract.");
  const names:Record<Exclude<Action,"deploy">,string>={create_task:"createTask",submit_version:"proposeVersion",approve:"approve",request_correction:"requestCorrection",revoke:"revoke",start_execution:"consume",record_receipt:"recordReceipt"};
  let args:unknown[]=[task.task_id];
  if(action==="create_task")args.push(task.proposer,task.reviewer,task.executor,task.receipt_signer,task.target_id);
  else if(action==="record_receipt"){
    if(!receipt)throw new Error("Verify an executor receipt before registering it.");
    args.push(BigInt(receipt.attemptId),receipt.receiptHash,receipt.result,receipt.signature);
  }else{
    if(!version)throw new Error("Choose an exact release package.");
    if(action==="submit_version")args.push(BigInt(version.manifest.parent_version_id),version.manifest_hash,version.artifact_hash,BigInt(version.expires_at));
    else{args.push(BigInt(version.version_id));if(action==="approve"||action==="start_execution")args.push(version.manifest_hash,version.artifact_hash);if(action==="approve")args.push(BigInt(version.expires_at));}
  }
  return encodeFunctionData({abi,functionName:names[action],args});
}
export async function preparePublic(request:PrepareRequest,task:LocalTask,version:LocalVersion|undefined,provider:WalletProvider,receipt?:{attemptId:string;receiptHash:string;result:number;signature:string}):Promise<Prepared>{
  const client=clientFor(provider),block=await anchor(client),account=request.account as Address;
  const required=request.action==="create_task"?task.owner:request.action==="submit_version"?task.proposer:request.action==="start_execution"?task.executor:["approve","request_correction"].includes(request.action)?task.reviewer:null;
  if(required&&required.toLowerCase()!==account.toLowerCase())throw new Error("Connect the designated wallet for this action.");
  if(request.action==="revoke"&&![task.owner,task.reviewer].includes(account.toLowerCase()))throw new Error("Only the owner or reviewer can revoke approval.");
  const data=actionData(request.action,task,version,receipt);
  await client.call({account,to:address,data,value:0n,blockNumber:block.number});
  const [estimate,price,balance]=await Promise.all([client.estimateGas({account,to:address,data,value:0n}),client.getGasPrice(),client.getBalance({address:account})]);
  const gas=(estimate*120n+99n)/100n,maxFee=gas*price;
  if(balance<maxFee)throw new Error("Your BOT balance is below the estimated maximum gas fee. Fund this wallet before signing.");
  return {action:request.action,chain_id:677,ready_to_send:true,transaction:{from:account,to:address,chainId:"0x2a5",data,value:"0x0",gas:`0x${gas.toString(16)}`,gasPrice:`0x${price.toString(16)}`},commitments:{task_id:task.task_id,...(version?{version_id:version.version_id,manifest_hash:version.manifest_hash,artifact_hash:version.artifact_hash}:{}),...(receipt?{attempt_id:receipt.attemptId,receipt_hash:receipt.receiptHash,result:receipt.result}:{})},fees:{gas_limit:gas.toString(),gas_price_wei:price.toString(),estimated_max_fee_wei:maxFee.toString(),balance_wei:balance.toString(),sufficient_balance:true}};
}
export async function verifyPublicIntent(intent:Intent, provider?:WalletProvider):Promise<Verification>{
  if(!intent.transaction_hash)throw new Error("Recover a transaction hash from your wallet before checking this request.");
  const client=clientFor(provider),hash=intent.transaction_hash as Hex,base={transaction_hash:hash,contract_address:address};
  let tx,receipt;
  try{[tx,receipt]=await Promise.all([client.getTransaction({hash}),client.getTransactionReceipt({hash})]);}
  catch(error){if(error instanceof TransactionNotFoundError||error instanceof TransactionReceiptNotFoundError)return {...base,status:"pending"};throw error;}
  const expected=intent.prepared.transaction;
  if(tx.from.toLowerCase()!==expected.from.toLowerCase()||tx.to?.toLowerCase()!==address||tx.input.toLowerCase()!==expected.data.toLowerCase()||tx.value!==0n||tx.chainId!==677)throw new Error("The transaction differs from the saved account, contract, network or action.");
  if(receipt.status!=="success")return {...base,status:"failed",reason:"The transaction reverted on chain."};
  const block=await anchor(client,receipt.blockNumber);
  if(block.hash!==receipt.blockHash||tx.blockHash!==receipt.blockHash)throw new Error("The transaction block is no longer canonical.");
  const head=await client.getBlockNumber(),confirmations=head-receipt.blockNumber+1n;
  const result:Verification={...base,status:confirmations>=3n?"verified":"confirming",confirmations:Number(confirmations),block_number:receipt.blockNumber.toString(),block_hash:receipt.blockHash,gas_used:receipt.gasUsed.toString(),effective_gas_price:receipt.effectiveGasPrice.toString(),actual_fee_wei:(receipt.gasUsed*receipt.effectiveGasPrice).toString()};
  const c=intent.prepared.commitments,versionId=intent.request.version_id?Number(intent.request.version_id):undefined;
  const state:Getter={task:normalized(await client.readContract({address,abi,functionName:"getTask",args:[intent.request.task_id as Hex],blockNumber:receipt.blockNumber}))};
  if(versionId)state.version=normalized(await client.readContract({address,abi,functionName:"getVersion",args:[intent.request.task_id as Hex,BigInt(versionId)],blockNumber:receipt.blockNumber}));
  if(state.version&&["submit_version","approve","start_execution"].includes(intent.request.action)){
    if(state.version.manifest_hash!==c.manifest_hash||state.version.artifact_hash!==c.artifact_hash)throw new Error("The confirmed version commitments do not match the saved package.");
  }
  if(intent.request.action==="approve"&&state.version?.decision!=="1")throw new Error("The approval state did not match this transaction.");
  if(intent.request.action==="start_execution"){
    if(state.version?.decision!=="4")throw new Error("The start authorization was not consumed.");
    const attemptId=String(state.version.attempt_id);state.attempt=normalized(await client.readContract({address,abi,functionName:"getAttempt",args:[intent.request.task_id as Hex,BigInt(attemptId)],blockNumber:receipt.blockNumber}));
    if(state.attempt.version_id!==String(versionId))throw new Error("The execution attempt differs from this version.");
    result.onchain_ids={version_id:String(versionId),attempt_id:attemptId};
  }
  if(intent.request.action==="record_receipt"){
    state.attempt=normalized(await client.readContract({address,abi,functionName:"getAttempt",args:[intent.request.task_id as Hex,BigInt(intent.request.attempt_id!)],blockNumber:receipt.blockNumber}));
    if(state.attempt.receipt_hash!==c.receipt_hash||String(state.attempt.result)!==String(c.result))throw new Error("The receipt commitment did not match the confirmed state.");
  }
  if((await client.getBlock({blockNumber:receipt.blockNumber})).hash!==receipt.blockHash)throw new Error("The receipt block changed during verification.");
  return {...result,getter_state:state};
}
