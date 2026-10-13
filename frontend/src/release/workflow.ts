import type {LocalTask,LocalVersion} from "./package.ts";
import type {Action,Getter} from "./model.ts";
import {authorizationState} from "./model.ts";

export interface WorkflowStep {action?:Action;role?:"owner"|"proposer"|"reviewer"|"executor";account?:string;text:string;gas:boolean}
export function nextStep(task:LocalTask,version:LocalVersion|undefined,getter:Getter|null):WorkflowStep{
  const auth=authorizationState(version,getter),count=Number(getter?.task?.version_count||0);
  const assigned=(action:Action,role:"owner"|"proposer"|"reviewer"|"executor",text:string)=>({action,role,account:task[role],text,gas:true});
  if(getter===null)return {text:"Read the current chain state before choosing a wallet operation. You can inspect and export the original files now.",gas:false};
  if(!getter?.task)return assigned("create_task","owner","Register the task’s roles and fixed publication target.");
  if(!version)return {text:"Import the Agent package to inspect the original files.",gas:false};
  if(version.version_id>count)return assigned("submit_version","proposer","Register this exact Agent version before review.");
  if(getter.attempt?.receipt_hash&&getter.attempt.receipt_hash!=="0x"+"0".repeat(64))return {text:String(getter.attempt.result)==="2"?"Execution failed. Save the signed failure evidence and ask the Agent for a new version.":"Delivery recorded. Verify originals, current chain commitment and downloaded bytes, then save the acceptance bundle.",gas:false};
  if(auth.label==="start_consumed")return {text:"Continue publication with the existing start. On another device, recover its hash first. Check the result and save the acceptance bundle.",gas:false};
  if(auth.superseded)return {text:"A newer version exists. Select and inspect it before another decision.",gas:false};
  if(auth.expired)return {text:"Ask the Agent for a new version with a current review deadline.",gas:false};
  if(auth.label==="approval_revoked")return {text:"Approval was revoked. Ask the Agent for a new version before another review.",gas:false};
  if(auth.label==="correction_requested")return {text:"Sync signed correction feedback, then ask the Agent to export the revised package.",gas:false};
  if(auth.canStart)return assigned("start_execution","executor","Authorize one start for this approved version.");
  if(auth.canApprove)return assigned("approve","reviewer","Inspect the files and tests, then approve or request a correction.");
  return {text:"Refresh the chain state and inspect the authorization before continuing.",gas:false};
}
