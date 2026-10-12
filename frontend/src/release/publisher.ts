import { verifyTypedData } from "viem";
import type { Address, Hex } from "viem";
import { publicConfig } from "./public-config.ts";
import { canonicalBytes, sha256 } from "./package.ts";
import type { LocalTask, LocalVersion } from "./package.ts";
import type { Attempt, Config, Intent, Receipt } from "./model.ts";
import { readPublicChain } from "./browser-chain.ts";

export const publisherBase=()=>typeof location!=="undefined"&&["127.0.0.1","localhost"].includes(location.hostname)?"":publicConfig.publisher;
async function request<T>(path:string,body?:unknown):Promise<T>{
  let response:Response;
  try{response=await fetch(publisherBase()+"/api/releases"+path,{method:body===undefined?"GET":"POST",headers:body===undefined?undefined:{"Content-Type":"application/json"},body:body===undefined?undefined:JSON.stringify(body),credentials:"omit",signal:AbortSignal.timeout(15_000)});}
  catch{throw new Error("The publication service is unavailable. Your package and chain records remain on this device. Retry when the service is online.");}
  if(!response.ok){let detail="";try{detail=(await response.json()).detail;}catch{/* The HTTP status remains the observable failure. */}throw new Error(typeof detail==="string"&&detail?detail:`The publication service returned HTTP ${response.status}.`);}
  return response.json() as Promise<T>;
}
export async function checkPublisher(task?:LocalTask):Promise<Config>{
  const config=await request<Config>("/config");
  if(config.chain.chain_id!==677||config.chain.contract_address?.toLowerCase()!==publicConfig.contract||config.executor.operation!=="publish_static"||config.executor.target!=="demo-site"||config.executor.receipt_signer.toLowerCase()!==(task?.receipt_signer||publicConfig.receiptSigner).toLowerCase())throw new Error("The publication service identity or target does not match this task.");
  return config;
}
export async function syncPublisher(task:LocalTask,throughVersion:number){
  await checkPublisher(task);
  for(let id=1;id<=throughVersion;id++){
    const version=task.versions.find(v=>v.version_id===id);
    if(!version)throw new Error("Import all preceding package versions before preparing publication.");
    const {task_id,title,owner,proposer,reviewer,executor,receipt_signer,target_id,created_at}=task;
    const result=await request<{status:string;task_id:string;manifest_hash:string;artifact_hash:string}>("/imports",{task:{task_id,title,owner,proposer,reviewer,executor,receipt_signer,target_id,created_at},manifest:version.manifest,artifact_text:version.artifact_text});
    if(result.status!=="imported"||result.task_id!==task_id||result.manifest_hash!==version.manifest_hash||result.artifact_hash!==version.artifact_hash)throw new Error("The service did not confirm the exact imported package.");
  }
}
export async function executePublication(task:LocalTask,version:LocalVersion,start:Intent):Promise<Attempt>{
  const attempt=start.verification?.onchain_ids?.attempt_id;
  if(!attempt||!start.transaction_hash)throw new Error("Verify the start transaction before requesting publication.");
  await syncPublisher(task,version.version_id);
  return request<Attempt>(`/tasks/${task.task_id}/execute`,{version_id:version.version_id,attempt_id:attempt,start_transaction_hash:start.transaction_hash});
}
export async function verifyReceiptEnvelope(task:LocalTask,version:LocalVersion,attemptId:string,envelope:Receipt,startHash?:string){
  const r=envelope.receipt;
  const expected={schema:"cluetide.execution-receipt.v1",chain_id:677,contract_address:publicConfig.contract,task_id:task.task_id,version_id:version.version_id,attempt_id:attemptId,manifest_hash:version.manifest_hash,artifact_hash:version.artifact_hash,target_id:task.target_id,operation:"publish_static",environment:"isolated-demo",executor:task.executor,receipt_signer:task.receipt_signer};
  if(Object.entries(expected).some(([key,value])=>r[key]!==value)||!['succeeded','failed'].includes(String(r.status))||r.output_hash!==(r.status==="succeeded"?version.artifact_hash:null)||startHash&&r.authorization_tx!==startHash||envelope.signer.toLowerCase()!==task.receipt_signer||await sha256(canonicalBytes(r))!==envelope.receipt_hash)throw new Error("The receipt identity, output or commitments do not match this execution.");
  const result=r.status==="succeeded"?1:2;
  const signatureValid=await verifyTypedData({address:task.receipt_signer as Address,domain:{name:"ClueTideReleaseWitness",version:"1",chainId:677,verifyingContract:publicConfig.contract},types:{Receipt:[{name:"taskId",type:"bytes32"},{name:"versionId",type:"uint256"},{name:"attemptId",type:"uint256"},{name:"receiptHash",type:"bytes32"},{name:"result",type:"uint8"}]},primaryType:"Receipt",message:{taskId:task.task_id as Hex,versionId:BigInt(version.version_id),attemptId:BigInt(attemptId),receiptHash:envelope.receipt_hash as Hex,result},signature:envelope.signature as Hex});
  if(!signatureValid)throw new Error("The executor receipt signature is invalid.");
  return result;
}
export async function loadVerifiedReceipt(task:LocalTask,version:LocalVersion,attemptId:string,startHash?:string){
  const archived=task.task_id===publicConfig.verifiedTask&&attemptId==="1";
  const envelope:Receipt=archived?(await import("./verified-receipt.json")).default as unknown as Receipt:await request<Receipt>(`/attempts/${attemptId}/receipt?task_id=${task.task_id}`);
  const result=await verifyReceiptEnvelope(task,version,attemptId,envelope,startHash);
  let artifactMatches=false;
  if(result===1){
    if(archived){artifactMatches=await sha256(new TextEncoder().encode(version.artifact_text))===version.artifact_hash;}
    else{
    const response=await fetch(publisherBase()+`/api/releases/attempts/${attemptId}/artifact?task_id=${task.task_id}`,{credentials:"omit",signal:AbortSignal.timeout(15_000)});
    if(!response.ok)throw new Error("The published artifact is unavailable for independent comparison.");
    const declared=Number(response.headers.get("content-length"));
    if(declared>publicConfig.artifactBytes)throw new Error("The published artifact exceeds the configured limit.");
    const bytes=new Uint8Array(await response.arrayBuffer());
    artifactMatches=bytes.length<=publicConfig.artifactBytes&&await sha256(bytes)===version.artifact_hash;
    }
    if(!artifactMatches)throw new Error("The downloaded artifact differs from the approved package.");
  }
  let chainMatches=false,chainError="";
  try{const state=await readPublicChain(task.task_id,version.version_id,attemptId);chainMatches=state.getter_state.attempt?.receipt_hash===envelope.receipt_hash&&String(state.getter_state.attempt?.result)===String(result);}
  catch{chainError="Chain commitment could not be checked. Retry when the RPC is available.";}
  return {envelope,result,artifactMatches,attemptId,archived,chainMatches,chainError};
}
