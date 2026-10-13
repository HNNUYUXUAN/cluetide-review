import { verifyTypedData } from "viem";
import type { Address, Hex } from "viem";
import { publicConfig } from "./public-config.ts";
import { canonicalBytes, sha256, strictJsonParse, utf8Decode } from "./package.ts";
import type { LocalTask, LocalVersion } from "./package.ts";
import type { Attempt, Config, Receipt, VerifiedStartGrant } from "./model.ts";
import { isVerifiedStartGrant, verifyStartReference } from "./browser-chain.ts";
import { saveEvidence } from "./evidence-store.ts";
import type { StoredAcceptanceEvidence } from "./evidence-store.ts";
import {retryRead} from "./service-recovery.ts";

export const publisherBase=()=>typeof location!=="undefined"&&(["127.0.0.1","localhost"].includes(location.hostname)||location.hostname.endsWith(".vercel.app"))?"":publicConfig.publisher;
async function request<T>(path:string,body?:unknown):Promise<T>{
  let response:Response;
  try{const perform=async(signal:AbortSignal)=>{const result=await fetch(publisherBase()+"/api/releases"+path,{method:body===undefined?"GET":"POST",headers:body===undefined?undefined:{"Content-Type":"application/json"},body:body===undefined?undefined:JSON.stringify(body),credentials:"omit",signal});if(body===undefined&&[429,502,503,504].includes(result.status))throw new Error("The service is resuming.");return result;};response=body===undefined?await retryRead(perform):await perform(AbortSignal.timeout(15_000));}
  catch{throw new Error("The publication service is unavailable. Your package and chain records remain on this device. Retry when the service is online.");}
  if(!response.ok){let detail="";try{detail=(await response.json()).detail;}catch{/* The HTTP status remains the observable failure. */}throw new Error(typeof detail==="string"&&detail?detail:`The publication service returned HTTP ${response.status}.`);}
  return response.json() as Promise<T>;
}
export async function checkPublisher(task?:LocalTask):Promise<Config>{
  const ready=await request<{schema:string;chain_id:number;contract_address:string;receipt_signer:string;writes_enabled:boolean}>("/readiness");
  if(ready.schema!=="cluetide.readiness.v1"||ready.chain_id!==677||ready.contract_address!==publicConfig.contract||ready.receipt_signer!==publicConfig.receiptSigner||!ready.writes_enabled)throw new Error("The publication service is restoring or writes are paused. Chain review, file import and evidence export remain available.");
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
    const result=await request<{status:string;task_id:string;manifest_hash:string;artifact_hash:string}>("/imports",{task:{task_id,title,owner,proposer,reviewer,executor,receipt_signer,target_id,created_at},manifest:version.manifest,...(version.manifest_text===undefined?{}:{manifest_text:version.manifest_text}),artifact_text:version.artifact_text});
    if(result.status!=="imported"||result.task_id!==task_id||result.manifest_hash!==version.manifest_hash||result.artifact_hash!==version.artifact_hash)throw new Error("The service did not confirm the exact imported package.");
  }
}
export async function executePublication(task:LocalTask,version:LocalVersion,start:VerifiedStartGrant):Promise<Attempt>{
  if(!isVerifiedStartGrant(start))throw new Error("Verify the start transaction online before requesting publication.");
  const current=await verifyStartReference(start,task,version);
  if(current.receipt_recorded)throw new Error("This attempt already has a recorded receipt. Verify its publication result.");
  await syncPublisher(task,version.version_id);
  const statusPath=`/tasks/${task.task_id}/attempts/${current.attempt_id}/status`;
  const before=await request<Attempt&{can_resume:boolean;receipt_available:boolean}>(statusPath);
  if(before.receipt_available||!before.can_resume)return before;
  try{return await request<Attempt>(`/tasks/${task.task_id}/execute`,{version_id:version.version_id,attempt_id:current.attempt_id,start_transaction_hash:current.transaction_hash});}
  catch{const observed=await request<Attempt&{can_resume:boolean}>(statusPath);return observed;}
}
export async function verifyReceiptEnvelope(task:LocalTask,version:LocalVersion,attemptId:string,envelope:Receipt,startHash?:string){
  if(!envelope||typeof envelope!=="object"||envelope.signature_scheme!=="EIP-712"||!envelope.receipt||typeof envelope.receipt!=="object"||typeof envelope.signature!=="string"||typeof envelope.signer!=="string")throw new Error("The executor receipt envelope failed validation.");
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
  const receiptUrl=archived?new URL("./verified-receipt.json",import.meta.url).href:publisherBase()+`/api/releases/attempts/${attemptId}/receipt?task_id=${task.task_id}`;
  const receiptBytes=await readResponseBytes(receiptUrl,512*1024);
  const envelope=strictJsonParse(utf8Decode(receiptBytes)) as Receipt;
  const result=await verifyReceiptEnvelope(task,version,attemptId,envelope,startHash);
  let artifactMatches=false,artifactBytes:Uint8Array|undefined,artifactUrl="";
  if(result===1){
    if(archived){artifactBytes=new TextEncoder().encode(version.artifact_text);artifactMatches=await sha256(artifactBytes)===version.artifact_hash;}
    else{
    artifactUrl=publisherBase()+`/api/releases/attempts/${attemptId}/artifact?task_id=${task.task_id}`;
    artifactBytes=await readResponseBytes(artifactUrl,publicConfig.artifactBytes);
    artifactMatches=await sha256(artifactBytes)===version.artifact_hash;
    }
    if(!artifactMatches)throw new Error("The downloaded artifact differs from the approved package.");
  }
  let chainMatches=false,chainError="",chainStatus:"matched"|"unrecorded"|"mismatch"|"unavailable"="unavailable",chainObservation:StoredAcceptanceEvidence["observations"]["chain"];
  try{
    const grant=await verifyStartReference({schema:"cluetide.start-reference.v1",task_id:task.task_id,version_id:version.version_id,transaction_hash:String(envelope.receipt.authorization_tx)},task,version);
    chainMatches=grant.attempt_id===attemptId&&grant.getter_state.attempt?.receipt_hash===envelope.receipt_hash&&String(grant.getter_state.attempt?.result)===String(result);
    chainStatus=chainMatches?"matched":grant.attempt_id===attemptId&&grant.getter_state.attempt?.receipt_hash==="0x"+"0".repeat(64)?"unrecorded":"mismatch";
    if(chainStatus==="mismatch")chainError="The current confirmed receipt commitment differs from this signed receipt. Inspect the exact attempt before continuing.";
    chainObservation={block_number:grant.snapshot.block_number,block_hash:grant.snapshot.block_hash,confirmations:publicConfig.confirmations,getter_state:grant.getter_state};
  }
  catch{chainError="Chain commitment could not be checked. Retry when the RPC is available.";}
  const observedAt=new Date().toISOString();
  const evidence:StoredAcceptanceEvidence={task,version_id:version.version_id,attempt_id:attemptId,observed_at:observedAt,start_reference:{schema:"cluetide.start-reference.v1",task_id:task.task_id,version_id:version.version_id,transaction_hash:String(envelope.receipt.authorization_tx)},receipt_bytes:receiptBytes,artifact_bytes:artifactBytes,transactions:[{action:"start_execution",transaction_hash:String(envelope.receipt.authorization_tx)}],observations:{...(chainObservation?{chain:chainObservation}:{}),...(artifactUrl?{artifact:{url:artifactUrl,downloaded_at:observedAt}}:{})}};
  let persistenceError="";try{await saveEvidence(evidence);}catch(error){persistenceError=error instanceof Error?error.message:"Acceptance evidence could not be saved.";}
  return {envelope,result,artifactMatches,attemptId,archived,chainMatches,chainStatus,chainError,evidence,persistenceError,observedAt};
}
export async function readResponseBytes(url:string,limit:number):Promise<Uint8Array>{
  return retryRead(async signal=>{
  const response=await fetch(url,{credentials:"omit",signal});
  if([429,502,503,504].includes(response.status))throw new Error("The publication service is resuming.");
  if(!response.ok)throw Object.assign(new Error(`Evidence download returned HTTP ${response.status}. Retry when the publication service is available.`),{retryable:false});
  const declared=Number(response.headers.get("content-length"));
  if(declared>limit)throw Object.assign(new Error("The downloaded evidence exceeds its size limit."),{retryable:false});
  if(!response.body)throw Object.assign(new Error("The evidence response has no readable body."),{retryable:false});
  const reader=response.body.getReader(),chunks:Uint8Array[]=[];let size=0;
  try{for(;;){const {done,value}=await reader.read();if(done)break;size+=value.length;if(size>limit){await reader.cancel();throw Object.assign(new Error("The downloaded evidence exceeds its size limit."),{retryable:false});}chunks.push(value);}}finally{reader.releaseLock();}
  const bytes=new Uint8Array(size);let offset=0;for(const chunk of chunks){bytes.set(chunk,offset);offset+=chunk.length;}return bytes;
  });
}
