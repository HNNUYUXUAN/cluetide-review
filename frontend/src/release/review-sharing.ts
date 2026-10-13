import {importPackage,requireCompleteHistory} from "./package.ts";
import type {LocalTask} from "./package.ts";
import {publisherBase,syncPublisher,readResponseBytes} from "./publisher.ts";

export function taskRoute(hash:string):{id:string;version?:number}{
  const match=/^#?\/task\/(0x[0-9a-fA-F]{64})(?:\?(.*))?$/.exec(hash);
  if(!match)return {id:""};
  const requested=new URLSearchParams(match[2]||"").get("version");
  if(requested!==null&&!/^[1-9][0-9]{0,8}$/.test(requested))throw new Error("The review link has an invalid version.");
  return {id:match[1].toLowerCase(),...(requested?{version:Number(requested)}:{})};
}
export async function fetchReviewPackage(taskId:string,versionId?:number):Promise<LocalTask>{
  let bytes:Uint8Array;
  try{bytes=await readResponseBytes(publisherBase()+`/api/releases/tasks/${taskId}/bundle${versionId?`?version=${versionId}`:""}`,4*1024*1024);}
  catch{throw new Error("The public review package is unavailable. Retry the service or import the original package to continue reviewing the chain record.");}
  const task=await importPackage(bytes);
  if(task.task_id!==taskId||versionId&&!task.versions.some(v=>v.version_id===versionId))throw new Error("The public package does not match this fixed-version link.");
  return task;
}
export async function publishReviewPackage(task:LocalTask,versionId:number):Promise<string>{
  requireCompleteHistory(task,versionId);
  await syncPublisher(task,versionId);
  const checked=await fetchReviewPackage(task.task_id,versionId);
  for(const source of task.versions.filter(v=>v.version_id<=versionId)){
    const received=checked.versions.find(v=>v.version_id===source.version_id);
    if(received?.manifest_hash!==source.manifest_hash||received.artifact_hash!==source.artifact_hash)throw new Error("The downloaded public package differs from the release originals.");
  }
  const url=new URL(location.href);url.hash=`/task/${task.task_id}?version=${versionId}`;
  return url.href;
}
