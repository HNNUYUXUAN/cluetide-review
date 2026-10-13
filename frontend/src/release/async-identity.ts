import { safeText } from "./model.ts";
import type { Getter, Intent, PrepareRequest, Receipt, Verification } from "./model.ts";
import { isDecimalId, isTransactionHash } from "./primitives.ts";

export function intentIdentity(intent:Pick<Intent,"id"|"request"|"transaction_hash">):string {
  const request=intent.request;
  return JSON.stringify([intent.id,intent.transaction_hash?.toLowerCase()||"",request.action,request.account.toLowerCase(),request.task_id?.toLowerCase()||"",String(request.version_id||""),request.attempt_id||"",request.expires_at||null]);
}

interface IntentVerificationDependencies {
  current:(id:string)=>Intent|undefined;
  save:(id:string,changes:Partial<Intent>)=>void;
  verify:(request:PrepareRequest,transactionHash:string)=>Promise<Verification>;
}

export class IntentVerifier {
  private generations=new Map<string,number>();
  private dependencies:IntentVerificationDependencies;
  constructor(dependencies:IntentVerificationDependencies){this.dependencies=dependencies;}
  invalidate(id:string){this.generations.set(id,(this.generations.get(id)||0)+1);}
  async verify(intent:Intent):Promise<Verification|null>{
    const transactionHash=intent.transaction_hash;
    if(!transactionHash)return null;
    const identity=intentIdentity(intent),current=this.dependencies.current(intent.id);
    if(!current||intentIdentity(current)!==identity)return null;
    const generation=(this.generations.get(intent.id)||0)+1;
    this.generations.set(intent.id,generation);
    const isCurrent=()=>{
      const latest=this.dependencies.current(intent.id);
      return this.generations.get(intent.id)===generation&&!!latest&&intentIdentity(latest)===identity;
    };
    try{
      const result=await this.dependencies.verify({...intent.request},transactionHash);
      if(!isCurrent())return null;
      this.dependencies.save(intent.id,{status:result.status,verification:{...result,observed_at:new Date().toISOString()},message:result.reason?safeText(result.reason):undefined});
      return result;
    }catch(error){
      if(isCurrent())this.dependencies.save(intent.id,{status:(error as {code?:string})?.code==="PARAMETER_MISMATCH"?"mismatch":"unknown",verification:undefined,message:safeText(error,"The transaction could not be checked. Its outcome remains unknown.")});
      return null;
    }
  }
}

export function mergeIntent(current:Intent,changes:Partial<Intent>):Intent {
  const next={...current,...changes};
  if(intentIdentity(current)!==intentIdentity(next))return {...next,transaction_hashes:[...new Set([...(current.transaction_hashes||[]),...(current.transaction_hash?[current.transaction_hash]:[])])].slice(-20),verification:undefined,message:undefined};
  return next;
}

export interface ReceiptIdentity {taskId:string;attemptId:string}
export interface ReceiptSnapshot {identity:ReceiptIdentity;envelope:Receipt;chain:Getter|null;chainError:string}
export interface ReceiptLookupState {input:ReceiptIdentity;busy:boolean;error:string;result:ReceiptSnapshot|null}
interface ReceiptLookupDependencies {
  receipt:(attemptId:string,taskId:string)=>Promise<Receipt>;
  read:(identity:ReceiptIdentity)=>Promise<Getter>;
}
const receiptKey=(identity:ReceiptIdentity)=>JSON.stringify([identity.taskId.toLowerCase(),identity.attemptId]);
export function receiptLinks(identity:ReceiptIdentity){
  return {
    receipt:`/api/releases/attempts/${encodeURIComponent(identity.attemptId)}/receipt?task_id=${encodeURIComponent(identity.taskId)}`,
    artifact:`/api/releases/attempts/${encodeURIComponent(identity.attemptId)}/artifact?task_id=${encodeURIComponent(identity.taskId)}`,
  };
}
export function selectReceipt(taskId:string,attemptId:string):ReceiptIdentity{return {taskId,attemptId};}
export function inspectReceipt(taskId:string,attemptId:string,onSelect:(identity:ReceiptIdentity)=>void,onOpen:()=>void){onSelect(selectReceipt(taskId,attemptId));onOpen();}

export class ReceiptLookupController {
  state:ReceiptLookupState;
  private generation=0;
  private listener:((state:ReceiptLookupState)=>void)|undefined;
  private dependencies:ReceiptLookupDependencies;
  constructor(input:ReceiptIdentity,dependencies:ReceiptLookupDependencies){this.state={input:{...input},busy:false,error:"",result:null};this.dependencies=dependencies;}
  subscribe(listener:(state:ReceiptLookupState)=>void){this.listener=listener;listener(this.state);return()=>{this.generation++;this.listener=undefined;};}
  private publish(state:ReceiptLookupState){this.state=state;this.listener?.(state);}
  setInput(input:ReceiptIdentity){this.generation++;this.publish({input:{...input},busy:false,error:"",result:null});}
  async lookup():Promise<void>{
    const input={...this.state.input},key=receiptKey(input),generation=++this.generation;
    const isCurrent=()=>this.generation===generation&&receiptKey(this.state.input)===key;
    this.publish({input,busy:true,error:"",result:null});
    try{
      const envelope=await this.dependencies.receipt(input.attemptId,input.taskId);
      if(!isCurrent())return;
      const taskId=envelope.receipt.task_id,attemptId=String(envelope.receipt.attempt_id);
      if(!isTransactionHash(taskId)||!isDecimalId(attemptId)||receiptKey({taskId,attemptId})!==key)throw new Error("The receipt response belongs to a different release or attempt.");
      const identity={taskId,attemptId};
      const snapshot:ReceiptSnapshot={identity,envelope,chain:null,chainError:""};
      this.publish({input,busy:true,error:"",result:snapshot});
      try{
        const chain=await this.dependencies.read(identity);
        if(isCurrent())this.publish({input,busy:false,error:"",result:{...snapshot,chain}});
      }catch(error){
        if(isCurrent())this.publish({input,busy:false,error:"",result:{...snapshot,chainError:safeText(error,"Chain receipt state is unavailable.")}});
      }
    }catch(error){if(isCurrent())this.publish({input,busy:false,error:safeText(error),result:null});}
  }
}
