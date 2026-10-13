export const readBackoff=[2000,5000,10000] as const;
export async function retryRead<T>(operation:(signal:AbortSignal)=>Promise<T>,options:{sleep?:(ms:number)=>Promise<void>;now?:()=>number;budget?:number}={}):Promise<T>{
  const clock=options.now||Date.now,start=clock(),budget=options.budget||30000;
  const sleep=options.sleep||((ms:number)=>new Promise<void>(resolve=>setTimeout(resolve,ms)));
  let error:unknown;
  for(let attempt=0;attempt<=readBackoff.length;attempt++){
    const remaining=budget-(clock()-start);if(remaining<=0)break;
    try{return await operation(AbortSignal.timeout(Math.max(1,Math.min(4000,remaining))));}
    catch(caught){error=caught;if((caught as {retryable?:boolean})?.retryable===false)throw caught;}
    const delay=readBackoff[attempt];if(delay===undefined||clock()-start+delay>=budget)break;
    await sleep(delay);
  }
  throw error||new Error("The service did not recover within 30 seconds. Retry or export your local evidence.");
}
export interface PendingWork {id:string;kind:"package"|"feedback";task_id:string;version_id:number;created_at:string;payload:unknown}
const key="cluetide.pending-public-work.v1";
export function pendingWork(storage:Pick<Storage,"getItem">=localStorage):PendingWork[]{
  const raw=storage.getItem(key);if(raw===null)return [];
  const items=JSON.parse(raw);
  if(!Array.isArray(items)||items.length>200||items.some(i=>!i||!['package','feedback'].includes(i.kind)||typeof i.id!=="string"||!/^0x[0-9a-fA-F]{64}$/.test(i.task_id)||!Number.isSafeInteger(i.version_id)||i.version_id<1))throw new Error("Pending public work could not be loaded. Export local evidence before restoring storage.");
  return items;
}
export function queueWork(work:Omit<PendingWork,"id"|"created_at">){
  const items=pendingWork(),id=`${work.kind}:${work.task_id}:${work.version_id}`;
  if(!items.some(i=>i.id===id))items.push({...work,id,created_at:new Date().toISOString()});
  try{localStorage.setItem(key,JSON.stringify(items));}catch{throw new Error("Pending work could not be saved. Download the original package or signed feedback before retrying.");}
  return id;
}
export function completeWork(id:string){localStorage.setItem(key,JSON.stringify(pendingWork().filter(i=>i.id!==id)));}
