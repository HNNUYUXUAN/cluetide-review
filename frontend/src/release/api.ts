import { parseVerification, safeText, validatePrepared, record } from "./model.ts";
import type { Config, Task, Version, Attempt, Receipt, PrepareRequest, Prepared, Verification, Getter } from "./model.ts";

async function request<T>(path: string, body?: unknown): Promise<T> {
  let response: Response;
  try { response = await fetch(`/api/releases${path}`, {method:body === undefined ? "GET" : "POST", headers:body === undefined ? undefined : {"Content-Type":"application/json"}, body:body === undefined ? undefined : JSON.stringify(body), signal:AbortSignal.timeout(60_000)}); }
  catch { throw new Error("The release service could not be reached. Your wallet records are preserved on this device."); }
  let value: unknown;
  try { value = await response.json(); } catch { throw new Error("The release service returned an unreadable response."); }
  if (!response.ok) {
    const detail = record(value) ? value.detail : undefined;
    throw new Error(safeText(record(detail) ? detail.message || detail.error : detail, `The release service returned HTTP ${response.status}.`));
  }
  return value as T;
}
export const api = {
  config: () => request<Config>("/config"),
  tasks: () => request<{tasks:Task[]}>("/tasks"),
  task: (id:string) => request<Task>(`/tasks/${encodeURIComponent(id)}`),
  create: (body: unknown) => request<Task>("/tasks",body),
  version: (id:string,body:unknown) => request<Version>(`/tasks/${encodeURIComponent(id)}/versions`,body),
  prepare: async (body:PrepareRequest,contract:string|null):Promise<Prepared> => validatePrepared(await request("/prepare",body),body,contract),
  verify: async (body:PrepareRequest,transactionHash:string):Promise<Verification> => parseVerification(await request("/verify",{...body,transaction_hash:transactionHash}),transactionHash),
  read: (body:{task_id:string;version_id?:string|number;attempt_id?:string}) => request<{getter_state:Getter;read_block_number?:string}>("/read",body),
  execute: (id:string,body:{version_id:string|number;attempt_id:string;start_transaction_hash:string}) => request<Attempt>(`/tasks/${encodeURIComponent(id)}/execute`,body),
  receipt: (id:string,taskId?:string) => request<Receipt>(`/attempts/${encodeURIComponent(id)}/receipt${taskId?`?task_id=${encodeURIComponent(taskId)}`:""}`),
};
