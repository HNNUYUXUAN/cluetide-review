import {verifyTypedData} from "viem";
import type {Address,Hex} from "viem";
import type {WalletProvider} from "../bot-wallet.ts";
import type {LocalTask,LocalVersion} from "./package.ts";
import {canonicalBytes,sha256,strictJsonParse,utf8Decode} from "./package.ts";
import {publicConfig} from "./public-config.ts";
import {publisherBase,readResponseBytes} from "./publisher.ts";
import {walletSnapshot} from "./wallet.ts";
import {record} from "./model.ts";
import {isTransactionHash} from "./primitives.ts";
import {scalarText} from "./exchange.ts";
import {verifyCorrectionReference} from "./browser-chain.ts";

export interface FeedbackDraft {task_id:string;version_id:number;manifest_hash:string;reason:string;acceptance_conditions:string[];correction_transaction_hash?:string;content_hash?:string;signature?:string}
export interface ReviewFeedback extends FeedbackDraft {schema:string;reviewer:string;received_at:string;chain_id:number;contract_address:string;verification:Record<string,unknown>}
const key="cluetide.review-feedback-drafts.v1";
export function feedbackKey(taskId:string,versionId:number){return `${taskId}:${versionId}`;}
export function validateFeedbackDraft(value:unknown):FeedbackDraft{
  if(!record(value)||!isTransactionHash(value.task_id)||!Number.isSafeInteger(value.version_id)||Number(value.version_id)<1||Number(value.version_id)>50||!isTransactionHash(value.manifest_hash)||!scalarText(value.reason,4000,true)||!Array.isArray(value.acceptance_conditions)||value.acceptance_conditions.length>12||value.acceptance_conditions.some(c=>!scalarText(c,1000,true))||(value.correction_transaction_hash!==undefined&&!isTransactionHash(value.correction_transaction_hash))||(value.content_hash!==undefined&&!isTransactionHash(value.content_hash))||(value.signature!==undefined&&(typeof value.signature!=="string"||!/^0x[0-9a-fA-F]{130}$/.test(value.signature)||!value.content_hash||!value.correction_transaction_hash)))throw new Error("The feedback draft failed validation. Restore its reason and exact version.");
  const {task_id,version_id,manifest_hash,reason,acceptance_conditions,correction_transaction_hash,content_hash,signature}=value;
  return {task_id,version_id:Number(version_id),manifest_hash,reason,acceptance_conditions:acceptance_conditions as string[],...(correction_transaction_hash?{correction_transaction_hash:String(correction_transaction_hash)}:{}),...(content_hash?{content_hash:String(content_hash)}:{}),...(signature?{signature:String(signature)}:{})};
}
export function loadFeedbackDrafts():Record<string,FeedbackDraft>{
  try{
    const raw=localStorage.getItem(key)||"{}";if(raw.length>4*1024*1024)return {};
    const parsed=strictJsonParse(raw);if(!record(parsed))return {};
    const entries:Record<string,FeedbackDraft>={};
    for(const [storedKey,value] of Object.entries(parsed).slice(-200)){try{const draft=validateFeedbackDraft(value);if(storedKey===feedbackKey(draft.task_id,draft.version_id))entries[storedKey]=draft;}catch{/* Each valid draft remains available when another stored record is damaged. */}}
    return entries;
  }catch{return {};}
}
export function saveFeedbackDraft(draft:FeedbackDraft){const current=validateFeedbackDraft(draft),entries=loadFeedbackDrafts();entries[feedbackKey(current.task_id,current.version_id)]=current;try{localStorage.setItem(key,JSON.stringify(entries));}catch{throw new Error("Feedback draft could not be saved. Enable browser storage before recording the correction.");}}
export function validateFeedbackText(reason:string,conditions:string[]){
  if(!scalarText(reason,4000,true)||!Array.isArray(conditions)||conditions.length>12||conditions.some(c=>!scalarText(c,1000,true)))throw new Error("Enter a correction reason up to 4,000 characters and at most 12 acceptance conditions up to 1,000 characters each.");
}
export async function feedbackTypedData(draft:FeedbackDraft){
  validateFeedbackDraft(draft);
  validateFeedbackText(draft.reason,draft.acceptance_conditions);
  if(!/^0x[0-9a-fA-F]{64}$/.test(draft.correction_transaction_hash||""))throw new Error("Verify the correction transaction before signing its feedback.");
  const contentHash=await sha256(canonicalBytes({reason:draft.reason,acceptance_conditions:draft.acceptance_conditions}));
  return {domain:{name:"ClueTideReviewFeedback",version:"1",chainId:677,verifyingContract:publicConfig.contract},types:{ReviewFeedback:[{name:"taskId",type:"bytes32"},{name:"versionId",type:"uint256"},{name:"manifestHash",type:"bytes32"},{name:"correctionTransactionHash",type:"bytes32"},{name:"contentHash",type:"bytes32"}]},primaryType:"ReviewFeedback" as const,message:{taskId:draft.task_id as Hex,versionId:String(draft.version_id),manifestHash:draft.manifest_hash as Hex,correctionTransactionHash:draft.correction_transaction_hash as Hex,contentHash:contentHash as Hex}};
}
export async function signFeedback(draft:FeedbackDraft,task:LocalTask,version:LocalVersion,provider:WalletProvider):Promise<FeedbackDraft>{
  if(draft.task_id!==task.task_id||draft.version_id!==version.version_id||draft.manifest_hash!==version.manifest_hash)throw new Error("The feedback draft belongs to another exact version.");
  const account=await walletSnapshot(provider);
  if(account.chainId!==677||account.account.toLowerCase()!==task.reviewer)throw new Error("Connect the designated reviewer on BOT Mainnet to sign the feedback message.");
  await verifyCorrectionReference(task,version,String(draft.correction_transaction_hash));
  const typed=await feedbackTypedData(draft);
  const walletTyped={...typed,types:{...typed.types,EIP712Domain:[{name:"name",type:"string"},{name:"version",type:"string"},{name:"chainId",type:"uint256"},{name:"verifyingContract",type:"address"}]}};
  const signature=await provider.request({method:"eth_signTypedData_v4",params:[account.account,JSON.stringify(walletTyped)]});
  if(typeof signature!=="string")throw new Error("The wallet did not return a feedback signature.");
  const valid=await verifyTypedData({...typed,address:task.reviewer as Address,message:{...typed.message,versionId:BigInt(draft.version_id)},signature:signature as Hex});
  if(!valid)throw new Error("The feedback signature does not match the designated reviewer.");
  const signed={...draft,content_hash:typed.message.contentHash,signature};try{saveFeedbackDraft(signed);}catch(error){throw Object.assign(error instanceof Error?error:new Error("Save the signed feedback immediately."),{signed_feedback:signed});}return signed;
}
export async function uploadFeedback(draft:FeedbackDraft):Promise<ReviewFeedback>{
  if(!draft.signature||!draft.content_hash)throw new Error("Sign the feedback message before uploading it.");
  const {task_id,...body}=draft;
  const response=await fetch(publisherBase()+`/api/releases/tasks/${task_id}/feedback`,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(body),credentials:"omit",signal:AbortSignal.timeout(15_000)});
  if(!response.ok){let detail="";try{detail=(await response.json()).detail;}catch{}throw new Error(detail||"Correction recorded, feedback pending. Retry sync when the service is online.");}
  return response.json();
}
export async function validateReviewFeedback(value:unknown,task:LocalTask,version:LocalVersion):Promise<ReviewFeedback>{
  const draft=validateFeedbackDraft(value);
  if(!record(value)||value.schema!=="cluetide.review-feedback.v1"||value.chain_id!==677||value.contract_address!==publicConfig.contract||value.reviewer!==task.reviewer||draft.task_id!==task.task_id||draft.version_id!==version.version_id||draft.manifest_hash!==version.manifest_hash||!draft.signature||typeof value.received_at!=="string"||!Number.isFinite(Date.parse(value.received_at))||!record(value.verification))throw new Error("The review feedback differs from the designated reviewer or exact version.");
  const typed=await feedbackTypedData(draft);
  if(draft.content_hash!==typed.message.contentHash||!await verifyTypedData({...typed,address:task.reviewer as Address,message:{...typed.message,versionId:BigInt(version.version_id)},signature:draft.signature as Hex}))throw new Error("The review feedback signature or content digest is invalid.");
  return {...draft,schema:"cluetide.review-feedback.v1",reviewer:task.reviewer,received_at:value.received_at,chain_id:677,contract_address:publicConfig.contract,verification:value.verification};
}
export async function readFeedback(task:LocalTask,version:LocalVersion):Promise<ReviewFeedback[]>{
  const bytes=await readResponseBytes(publisherBase()+`/api/releases/tasks/${task.task_id}/feedback?version=${version.version_id}`,512*1024),value=strictJsonParse(utf8Decode(bytes));
  if(!record(value)||value.task_id!==task.task_id||!Array.isArray(value.feedback)||value.feedback.length>1)throw new Error("The service returned invalid exact-version feedback.");
  return Promise.all(value.feedback.map(item=>validateReviewFeedback(item,task,version)));
}
