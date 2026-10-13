import type { LocalTask } from "./package.ts";
import type { Getter, StartReference } from "./model.ts";

export interface StoredAcceptanceEvidence {
  task:LocalTask; version_id:number; attempt_id:string; observed_at:string;
  start_reference:StartReference; receipt_bytes:Uint8Array; artifact_bytes?:Uint8Array;
  transactions:{action:string;transaction_hash:string}[];
  observations:{chain?:{block_number:string;block_hash:string;confirmations:number;getter_state:Getter};artifact?:{url:string;downloaded_at:string}};
}
const database="cluetide.acceptance.evidence.v1",store="evidence";
const storageError=()=>new Error("Acceptance evidence could not be saved. Free browser storage or export the acceptance bundle now before leaving this page.");
async function openEvidence():Promise<IDBDatabase>{
  if(typeof indexedDB==="undefined")throw storageError();
  return new Promise((resolve,reject)=>{
    const request=indexedDB.open(database,1);
    request.onupgradeneeded=()=>request.result.createObjectStore(store);
    request.onerror=()=>reject(storageError());
    request.onblocked=()=>reject(new Error("Close another ClueTide tab to update acceptance evidence storage."));
    request.onsuccess=()=>resolve(request.result);
  });
}
export async function saveEvidence(evidence:StoredAcceptanceEvidence):Promise<void>{
  const db=await openEvidence();
  try{await new Promise<void>((resolve,reject)=>{
    const tx=db.transaction(store,"readwrite");
    tx.oncomplete=()=>resolve();tx.onerror=()=>reject(storageError());tx.onabort=()=>reject(storageError());
    tx.objectStore(store).put(evidence,`${evidence.task.task_id}/${evidence.version_id}/${evidence.attempt_id}`);
  });}catch{throw storageError();}finally{db.close();}
}
export async function getEvidence(taskId:string,versionId:number,attemptId?:string):Promise<StoredAcceptanceEvidence|null>{
  const db=await openEvidence();
  try{return await new Promise<StoredAcceptanceEvidence|null>((resolve,reject)=>{
    const prefix=`${taskId}/${versionId}/`,range=attemptId?IDBKeyRange.only(prefix+attemptId):IDBKeyRange.bound(prefix,prefix+"\uffff"),request=db.transaction(store,"readonly").objectStore(store).openCursor(range);
    let latest:StoredAcceptanceEvidence|null=null;
    request.onerror=()=>reject(new Error("Stored acceptance evidence is unavailable. Import your exported acceptance bundle."));
    request.onsuccess=()=>{
      const cursor=request.result;if(!cursor){resolve(latest);return;}
      const evidence=cursor.value as StoredAcceptanceEvidence;
      if(evidence.task.task_id===taskId&&evidence.version_id===versionId&&(!attemptId||evidence.attempt_id===attemptId)&&(!latest||evidence.observed_at>latest.observed_at))latest=evidence;
      cursor.continue();
    };
  });}finally{db.close();}
}
