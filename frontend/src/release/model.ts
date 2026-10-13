import { isAddress, isDecimalId, isTransactionHash, validateTransaction } from "./primitives.ts";
import type { BotTransaction } from "../bot-types.ts";

export type Action = "deploy" | "create_task" | "submit_version" | "approve" | "request_correction" | "revoke" | "start_execution" | "record_receipt";
export interface Config { schema_version: number; chain: {status: "unconfigured" | "configured"; chain_id: number; contract_address: string | null}; executor: {receipt_signer: string; operation: string; target: string; account?: string}; limits: {artifact_bytes: number} }
export interface Version { version_id: string | number; manifest_hash: string; artifact_hash: string; manifest: Record<string, unknown>; bundle_url: string; summary?: string; expires_at?: number }
export interface Attempt { attempt_id: string; task_id: string; version_id: string | number; status: "pending" | "unknown" | "succeeded" | "failed"; stage: string; receipt_url?: string; artifact_url?: string; start_transaction_hash?: string }
export interface Task { task_id: string; title: string; owner: string; proposer: string; reviewer: string; executor: string; receipt_signer: string; target: string; target_id: string; created_at: string | number; versions: Version[]; attempts: Attempt[]; chain_state?: Getter; chain_records?: {action:Action;version_id:number;transaction_hash:string;result:Verification}[] }
export interface PrepareRequest {action: Action; account: string; task_id?: string; version_id?: string | number; attempt_id?: string; expires_at?: number}
export interface Prepared {action: Action | "propose_version" | "consume"; chain_id?: number; transaction: BotTransaction; commitments: Record<string, unknown>; fees?: {gas_limit: string | null; gas_price_wei: string | null; estimated_max_fee_wei: string | null; balance_wei: string | null; sufficient_balance: boolean | null}; [key: string]: unknown}
export interface Getter {task?: Record<string, unknown> | null; version?: Record<string, unknown> | null; attempt?: Record<string, unknown> | null}
export interface Verification {status: "pending" | "confirming" | "verified" | "failed"; transaction_hash: string; confirmations?: number; contract_address?: string; getter_state?: Getter; onchain_ids?: {version_id?: string; attempt_id?: string}; reason?: string; gas_used?: string; effective_gas_price?: string; [key: string]: unknown}
export interface Receipt {receipt: Record<string, unknown>; receipt_hash: string; signature: string; signer: string; signature_scheme?:string; verification: Record<string, unknown>}
export type IntentStatus = "awaiting_wallet" | "unknown" | "pending" | "confirming" | "verified" | "failed" | "rejected" | "not_broadcast" | "mismatch";
export interface Intent {id: string; created_at: string; request: PrepareRequest; prepared: Prepared; status: IntentStatus; transaction_hash?: string; transaction_hashes?:string[]; verification?: Verification; message?: string}
export interface StartReference {schema:"cluetide.start-reference.v1"; task_id:string; version_id:number; transaction_hash:string}
export interface VerifiedStartGrant extends StartReference {
  attempt_id:string; executor:string; manifest_hash:string; artifact_hash:string; observed_at:string;
  snapshot:{block_number:string;block_hash:string}; transaction_block:{number:string;hash:string};
  confirmations:number; getter_state:Getter; receipt_recorded:boolean;
}
export const actionLabels: Record<Action, string> = {deploy: "Deploy witness contract", create_task: "Register release", submit_version: "Submit exact version", approve: "Approve this version", request_correction: "Request correction", revoke: "Revoke approval", start_execution: "Authorize one start", record_receipt: "Record receipt"};
export const decisionLabels: Record<string, string> = {"0":"Awaiting review", "1":"Approved", "2":"Approval revoked", "3":"Correction requested", "4":"Start consumed"};
export const record = (value: unknown): value is Record<string, unknown> => !!value && typeof value === "object" && !Array.isArray(value);
export const hash = (value: unknown): value is string => typeof value === "string" && /^0x[0-9a-fA-F]{64}$/.test(value);
export function safeText(value: unknown, fallback = "The request could not be completed. Refresh and try again."): string {
  const text = value instanceof Error ? value.message : typeof value === "string" ? value : "";
  if (!text || /[\u3400-\u9fff]/.test(text)) return fallback;
  return text.slice(0, 1200);
}
export function walletError(value: unknown): {message: string; rejected: boolean} {
  const code = record(value) ? value.code : undefined;
  if (code === 4001 || code === "4001" || code === "ACTION_REJECTED") return {message: "You declined the wallet request. No transaction hash was returned.", rejected: true};
  if (code === -32002) return {message:"A wallet request is already open. Complete it in your wallet.",rejected:false};
  return {message:safeText(value,"The wallet request ended without a transaction hash. Check wallet activity before trying again."),rejected:false};
}
export function validatePrepared(value: unknown, request: PrepareRequest, contract: string | null): Prepared {
  const chainAction = request.action === "submit_version" ? "propose_version" : request.action === "start_execution" ? "consume" : request.action;
  if (!record(value) || ![request.action,chainAction].includes(String(value.action)) || !record(value.commitments)) throw new Error("The preparation response does not match this action.");
  const tx = validateTransaction(value.transaction);
  if (tx.chainId !== "0x2a5" || tx.from.toLowerCase() !== request.account.toLowerCase() || (request.action === "deploy" ? tx.to !== undefined : !contract || tx.to?.toLowerCase() !== contract.toLowerCase())) throw new Error("The transaction account, contract, or network does not match this release.");
  if (request.task_id && value.commitments.task_id !== undefined && String(value.commitments.task_id).toLowerCase() !== request.task_id.toLowerCase()) throw new Error("The prepared release commitment does not match.");
  return value as unknown as Prepared;
}
export function canSend(prepared: Prepared): boolean {
  const tx = prepared.transaction, fees = prepared.fees;
  if (prepared.ready_to_send !== true || !tx.gas || !tx.gasPrice || !fees || fees.sufficient_balance !== true || !fees.estimated_max_fee_wei || !fees.balance_wei) return false;
  try { return BigInt(tx.gas) > 0n && BigInt(tx.gasPrice) > 0n && BigInt(fees.estimated_max_fee_wei) === BigInt(tx.gas) * BigInt(tx.gasPrice) && BigInt(fees.balance_wei) >= BigInt(fees.estimated_max_fee_wei); } catch { return false; }
}
export function parseVerification(value: unknown, transactionHash: string): Verification {
  if (!record(value) || !["pending", "confirming", "verified", "failed"].includes(String(value.status)) || typeof value.transaction_hash !== "string" || value.transaction_hash.toLowerCase() !== transactionHash.toLowerCase()) throw new Error("The transaction verification response failed validation.");
  if (value.onchain_ids !== undefined && (!record(value.onchain_ids) || Object.values(value.onchain_ids).some(id => !isDecimalId(id)))) throw new Error("The on-chain identifier failed validation.");
  return value as unknown as Verification;
}
export const short = (text: string | null | undefined): string => text ? `${text.slice(0, 8)}…${text.slice(-6)}` : "Unavailable";
export function safeUrl(value: unknown): string | undefined {
  if (typeof value !== "string") return;
  try { const url = new URL(value, typeof location === "undefined" ? "http://localhost" : location.origin); return ["http:","https:"].includes(url.protocol) && !url.username && !url.password ? url.href : undefined; } catch { return; }
}
export const storageKey = "cluetide.release.intents.v1";
export function loadIntents(raw: string | null): Intent[] {
  try {
    const parsed: unknown = JSON.parse(raw || "[]");
    if (!Array.isArray(parsed)) return [];
    return parsed.filter((item): item is Intent => record(item) && typeof item.id === "string" && typeof item.created_at === "string" && record(item.request) && typeof item.request.action === "string" && item.request.action in actionLabels && isAddress(item.request.account) && record(item.prepared) && record(item.prepared.transaction) && (item.transaction_hash === undefined || isTransactionHash(item.transaction_hash)) && ["awaiting_wallet","unknown","pending","confirming","verified","failed","rejected","not_broadcast","mismatch"].includes(String(item.status))).slice(-200).map(item => ({...item, status: item.status === "awaiting_wallet" ? "unknown" : item.status}));
  } catch { return []; }
}
export function validateRoles(roles: {owner: string; proposer: string; reviewer: string; executor: string}): string | null {
  if (![roles.owner,roles.proposer,roles.reviewer,roles.executor].every(isAddress)) return "Enter a valid nonzero wallet address for every role.";
  if (roles.proposer.toLowerCase() === roles.reviewer.toLowerCase()) return "Choose a reviewer wallet distinct from the proposer wallet.";
  return null;
}
export function authorizationState(version:Version|undefined,getter:Getter|null,now=Math.floor(Date.now()/1000)) {
  const chain=getter?.version;
  const exact=!!version&&!!chain&&String(chain.version_id)===String(version.version_id)&&String(chain.manifest_hash).toLowerCase()===version.manifest_hash.toLowerCase()&&String(chain.artifact_hash).toLowerCase()===version.artifact_hash.toLowerCase();
  const superseded=exact&&Number(getter?.task?.version_count)>Number(version?.version_id);
  const deadline=Number(chain?.deadline);
  const expired=exact&&Number.isFinite(deadline)&&deadline<=now;
  const decision=exact?String(chain?.decision):"";
  const label=exact&&decision==="4"?"start_consumed":superseded?"superseded":expired?"approval_expired":exact?decisionLabels[decision]?.toLowerCase().replaceAll(" ","_")||"unknown":"chain_state_unknown";
  const reviewable=exact&&!superseded&&!expired;
  return {exact,superseded,expired,label,canApprove:reviewable&&decision==="0",canRevoke:reviewable&&["0","1"].includes(decision),canRequestCorrection:reviewable&&["0","1"].includes(decision),canStart:reviewable&&decision==="1"};
}
