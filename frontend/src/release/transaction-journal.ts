import type {Intent} from "./model.ts";
import {loadIntents} from "./model.ts";
import {publicConfig} from "./public-config.ts";

export const journalKey="cluetide.transaction-journal.v1";
const legacyKey="cluetide.public.intents.v1";
export interface JournalContext {chain_id:677;contract:string;account:string;calldata:string;value:string;prepared_block?:string;nonce?:string}
export function journalContext(intent:Intent):JournalContext{
  const tx=intent.prepared.transaction;
  return {chain_id:677,contract:String(tx.to).toLowerCase(),account:tx.from.toLowerCase(),calldata:tx.data.toLowerCase(),value:tx.value,
    ...(typeof intent.prepared.prepared_block==="string"?{prepared_block:intent.prepared.prepared_block}:{}),
    ...(typeof intent.prepared.prepared_nonce==="string"?{nonce:intent.prepared.prepared_nonce}:{})};
}
export function readJournal(storage:Pick<Storage,"getItem">=localStorage):Intent[]{
  const raw=storage.getItem(journalKey);
  if(raw===null)return loadIntents(storage.getItem(legacyKey));
  const journal=JSON.parse(raw);
  if(journal?.schema!=="cluetide.transaction-journal.v1"||!Array.isArray(journal.entries)||journal.entries.length>200)throw new Error("Transaction storage could not be read. Export its raw journal before restoring storage.");
  const entries=loadIntents(JSON.stringify(journal.entries));
  if(entries.length!==journal.entries.length)throw new Error("A transaction journal record needs recovery. Preserve the journal before signing again.");
  for(const intent of entries){const context=journalContext(intent);if(context.chain_id!==677||context.contract!==publicConfig.contract||context.value!=="0x0")throw new Error("The saved transaction belongs to another network or contract.");}
  return entries;
}
export function journalBytes(entries:Intent[]):Uint8Array{
  return new TextEncoder().encode(JSON.stringify({schema:"cluetide.transaction-journal.v1",exported_at:new Date().toISOString(),entries:entries.map(i=>({...i,journal_context:journalContext(i)}))},null,2));
}
export function writeJournal(entries:Intent[],storage:Pick<Storage,"setItem">=localStorage){
  if(entries.length>200)throw new Error("Transaction storage is full. Export the journal before archiving completed requests.");
  try{storage.setItem(journalKey,new TextDecoder().decode(journalBytes(entries)));}
  catch{throw new Error("Transaction storage failed. Download the journal and preserve the wallet hash before continuing.");}
}
export function walletContextKey(snapshot?:{account:string;chainId:number}){
  return snapshot?`${snapshot.account.toLowerCase()}:${snapshot.chainId}`:"disconnected";
}
export function isUnresolved(intent:Intent){return ["awaiting_wallet","unknown","pending","confirming","mismatch"].includes(intent.status);}
export interface RecoveryDependencies {
  head:()=>Promise<bigint>;
  hashes:(from:bigint,to:bigint,taskId:string)=>Promise<string[]>;
  matches:(hash:string,intent:Intent)=>Promise<boolean>;
}
export async function reconcileJournal(intent:Intent,knownHashes:string[],dependencies:RecoveryDependencies){
  const candidates=new Set<string>();
  for(const hash of [...new Set(knownHashes)].slice(0,200))if(await dependencies.matches(hash,intent))candidates.add(hash);
  const block=journalContext(intent).prepared_block;
  if(block===undefined)return {status:candidates.size===1?"found":"manual_hash_required",hash:candidates.size===1?[...candidates][0]:undefined,scanned_blocks:0,checked_at:new Date().toISOString()};
  if(!/^[0-9]+$/.test(block))throw new Error("The prepared block could not be recovered. Paste the exact wallet hash.");
  const head=await dependencies.head(),from=BigInt(block),limit=from+3999n,end=head<limit?head:limit;
  const checked=new Set<string>();
  for(let start=from;start<=end;start+=250n){
    const finish=start+249n<end?start+249n:end;
    const hashes=await dependencies.hashes(start,finish,String(intent.request.task_id));
    for(const hash of hashes){
      if(checked.has(hash))continue;checked.add(hash);
      if(checked.size>200)throw new Error("Too many transaction candidates. Paste the exact hash from wallet activity.");
      if(await dependencies.matches(hash,intent))candidates.add(hash);
    }
  }
  return {status:candidates.size===1?"found":candidates.size>1?"ambiguous":head>limit?"scan_limit_reached":"not_found",
    hash:candidates.size===1?[...candidates][0]:undefined,scanned_blocks:end>=from?Number(end-from+1n):0,
    checked_at:new Date().toISOString()};
}
