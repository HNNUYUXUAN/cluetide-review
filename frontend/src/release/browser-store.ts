import { PACKAGE_BYTES, strictJsonParse, validateTask } from "./package.ts";
import type { LocalTask } from "./package.ts";
const key="cluetide.public.tasks.v1";
export function saveTasks(tasks:LocalTask[]){
  const raw=JSON.stringify(tasks);
  if(tasks.length>100||new TextEncoder().encode(raw).length>PACKAGE_BYTES)throw new Error("Local evidence storage is full. Export your tasks before removing older local copies.");
  try{localStorage.setItem(key,raw);}catch{throw new Error("Enable browser storage before registering this release. Download a task backup to preserve its original files.");}
}
export async function loadTasks():Promise<LocalTask[]>{
  const raw=localStorage.getItem(key);
  if(!raw)return [];
  if(new TextEncoder().encode(raw).length>PACKAGE_BYTES)throw new Error("Local task storage exceeds the import limit. Restore a downloaded backup.");
  const parsed:unknown=strictJsonParse(raw);
  if(!Array.isArray(parsed)||parsed.length>100)throw new Error("Local task storage is unreadable. Restore a task backup.");
  return Promise.all(parsed.map(validateTask));
}
export function mergeTask(tasks:LocalTask[], incoming:LocalTask):LocalTask[]{
  const existing=tasks.find(t=>t.task_id===incoming.task_id);
  if(!existing)return [...tasks,incoming];
  for(const key of ["owner","proposer","reviewer","executor","receipt_signer","target_id"] as const)if(existing[key]!==incoming[key])throw new Error("An existing local task has different roles or target.");
  const versions=new Map(existing.versions.map(v=>[v.version_id,v]));
  for(const version of incoming.versions){const old=versions.get(version.version_id);if(old&&(old.manifest_hash!==version.manifest_hash||old.artifact_hash!==version.artifact_hash))throw new Error("An existing local version has different package bytes.");versions.set(version.version_id,version);}
  return tasks.map(t=>t.task_id===incoming.task_id?{...existing,title:existing.title==="Shared release"?incoming.title:existing.title,created_at:existing.title==="Shared release"?incoming.created_at:existing.created_at,versions:[...versions.values()].sort((a,b)=>a.version_id-b.version_id),version_count:Math.max(existing.version_count,incoming.version_count)}:t);
}
