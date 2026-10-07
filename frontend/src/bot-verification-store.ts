import type { BotPrepareRequest } from "./bot-types";
import { isAddress, isBotAction, isDecimalId, isTransactionHash } from "./bot-wallet";

export type VerificationIntentStatus =
  | "ready" | "submitted" | "uncertain" | "rejected"
  | "pending" | "failed" | "mismatch" | "verified";

export interface VerificationIntent {
  intent_id: string;
  request: BotPrepareRequest;
  transaction_hash?: string;
  status: VerificationIntentStatus;
  binding_sha256: string;
}

const STORAGE_KEY = "cluetide.bot.verification-intents.v1";
const MAX_ENTRIES = 50;
const MAX_BYTES = 250_000;
const encoder = new TextEncoder();
const statuses = new Set<VerificationIntentStatus>([
  "ready", "submitted", "uncertain", "rejected", "pending", "failed", "mismatch", "verified",
]);
const requestKeys = new Set([
  "chain_id", "action", "account", "contract_address", "case_id", "local_version_id",
  "onchain_version_id", "local_review_id", "evidence_uri",
]);
const entryKeys = new Set(["intent_id", "request", "transaction_hash", "status", "binding_sha256"]);
const malformed = "The local verification record failed its integrity check. Preserve browser storage and inspect the record.";
const conflict = "Another operation updated the local verification record. Read it again before continuing.";
const record = (value: unknown): value is Record<string, unknown> =>
  value !== null && typeof value === "object" && !Array.isArray(value);
const owns = (value: Record<string, unknown>, key: string): boolean => Object.hasOwn(value, key);
const positiveInteger = (value: unknown): value is number =>
  typeof value === "number" && Number.isSafeInteger(value) && value > 0;
const validUnicode = (value: string): boolean =>
  !/[\uD800-\uDBFF](?![\uDC00-\uDFFF])|(?<![\uD800-\uDBFF])[\uDC00-\uDFFF]/.test(value);
function fail(message = malformed): never { throw new Error(message); }

function storage(): Storage {
  try {
    if (!globalThis.localStorage) fail("Local verification storage is unavailable in this browser. Check storage permissions.");
    return globalThis.localStorage;
  } catch { return fail("Local verification storage is unavailable in this browser. Check storage permissions."); }
}

function readRaw(target: Storage): string | null {
  try { return target.getItem(STORAGE_KEY); }
  catch { return fail("Could not read local verification records. Check browser storage permissions."); }
}

// Parse bounded JSON with unique object keys so every persisted field has one meaning.
function parseEnvelope(raw: string): unknown {
  if (!validUnicode(raw) || encoder.encode(raw).length > MAX_BYTES) fail();
  let cursor = 0;
  const whitespace = (): void => { while (/[\t\n\r ]/.test(raw[cursor] ?? "!") && cursor < raw.length) cursor++; };
  const string = (): string => {
    const start = cursor++;
    while (cursor < raw.length) {
      const character = raw[cursor++];
      if (character === "\\") cursor++;
      else if (character === '"') {
        try {
          const value: unknown = JSON.parse(raw.slice(start, cursor));
          if (typeof value !== "string" || !validUnicode(value)) fail();
          return value;
        } catch { return fail(); }
      }
    }
    return fail();
  };
  const value = (depth: number): unknown => {
    if (depth > 8) fail();
    whitespace();
    const character = raw[cursor];
    if (character === '"') return string();
    if (character === "{" || character === "[") {
      const object = character === "{";
      const result: Record<string, unknown> | unknown[] = object ? Object.create(null) as Record<string, unknown> : [];
      const closing = object ? "}" : "]";
      cursor++;
      whitespace();
      if (raw[cursor] === closing) { cursor++; return result; }
      let count = 0;
      while (cursor < raw.length) {
        if (++count > (object ? 16 : MAX_ENTRIES)) fail();
        if (object) {
          whitespace();
          if (raw[cursor] !== '"') fail();
          const key = string();
          if (owns(result as Record<string, unknown>, key)) fail();
          whitespace();
          if (raw[cursor++] !== ":") fail();
          (result as Record<string, unknown>)[key] = value(depth + 1);
        } else (result as unknown[]).push(value(depth + 1));
        whitespace();
        if (raw[cursor] === closing) { cursor++; return result; }
        if (raw[cursor++] !== ",") fail();
      }
      return fail();
    }
    for (const [token, primitive] of [["true", true], ["false", false], ["null", null]] as const) {
      if (raw.startsWith(token, cursor)) { cursor += token.length; return primitive; }
    }
    const token = /^-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?/.exec(raw.slice(cursor))?.[0];
    if (!token || !Number.isFinite(Number(token))) return fail();
    cursor += token.length;
    return Number(token);
  };
  const result = value(0);
  whitespace();
  if (cursor !== raw.length) fail();
  return result;
}

function publicIpv4(parts: number[]): boolean {
  const [a, b, c, d] = parts;
  return parts.length === 4 && parts.every((part) => Number.isInteger(part) && part >= 0 && part <= 255) &&
    a !== 0 && a !== 10 && a !== 127 && a < 224 &&
    !(a === 100 && b >= 64 && b <= 127) && !(a === 169 && b === 254) &&
    !(a === 172 && b >= 16 && b <= 31) && !(a === 192 && b === 168) &&
    !(a === 192 && b === 0 && c === 0 && d !== 9 && d !== 10) &&
    !(a === 192 && b === 0 && c === 2) && !(a === 192 && b === 88 && c === 99) &&
    !(a === 198 && (b === 18 || b === 19)) && !(a === 198 && b === 51 && c === 100) &&
    !(a === 203 && b === 0 && c === 113);
}

function publicHttpsHost(host: string): boolean {
  if (!host || host === "localhost" || host.endsWith(".localhost") || host.endsWith(".local") || host.endsWith(".internal")) return false;
  if (/^[0-9]+(?:\.[0-9]+){3}$/.test(host)) return publicIpv4(host.split(".").map(Number));
  if (host.startsWith("[") && host.endsWith("]")) {
    const halves = host.slice(1, -1).split("::");
    const left = halves[0] ? halves[0].split(":") : [];
    const right = halves.length === 2 && halves[1] ? halves[1].split(":") : [];
    const parts = [...left, ...Array(8 - left.length - right.length).fill("0"), ...right].map((part) => Number.parseInt(part, 16));
    if (parts.length !== 8 || parts.some((part) => !Number.isInteger(part) || part < 0 || part > 65535)) return false;
    if (parts.slice(0, 5).every((part) => part === 0) && parts[5] === 65535)
      return publicIpv4([parts[6] >> 8, parts[6] & 255, parts[7] >> 8, parts[7] & 255]);
    if (parts[0] === 0x0064 && parts[1] === 0xff9b && parts.slice(2, 6).every((part) => part === 0)) return true;
    return parts[0] >= 0x2000 && parts[0] <= 0x3fff &&
      !(parts[0] === 0x2001 && (parts[1] <= 0x01ff || parts[1] === 0x0db8)) && parts[0] !== 0x2002;
  }
  return host.includes(".");
}

function validEvidenceUri(value: unknown): value is string {
  if (typeof value !== "string" || !validUnicode(value) || encoder.encode(value).length > 512) return false;
  if (value === "") return true;
  try {
    const relative = value.startsWith("/api/");
    const url = new URL(value, relative ? "https://evidence.example" : undefined);
    if (url.username || url.password || url.search || url.hash) return false;
    if (relative) return true;
    if (!["https:", "ipfs:", "ar:", "urn:"].includes(url.protocol)) return false;
    if (url.protocol === "https:" && (!/^https:\/\/[^/\\?#]+/i.test(value) || value.includes("\\"))) return false;
    return url.protocol !== "https:" || publicHttpsHost(url.hostname);
  } catch { return false; }
}

function canonicalRequest(value: unknown, normalize: boolean): BotPrepareRequest {
  if (!record(value) || Object.keys(value).some((key) => !requestKeys.has(key)) ||
      !owns(value, "chain_id") || !owns(value, "action") || !owns(value, "account") ||
      (value.chain_id !== 968 && value.chain_id !== 677) || typeof value.action !== "string" ||
      !isBotAction(value.action) || !isAddress(value.account) ||
      (!normalize && value.account !== value.account.toLowerCase())) return fail();
  const request: BotPrepareRequest = { chain_id: value.chain_id, action: value.action, account: value.account.toLowerCase() };
  const optional = (key: string): boolean => owns(value, key) && value[key] !== undefined;
  if (!normalize && Object.values(value).some((field) => field === undefined)) fail();
  if (optional("contract_address")) {
    if (!isAddress(value.contract_address) || (!normalize && value.contract_address !== value.contract_address.toLowerCase())) fail();
    request.contract_address = value.contract_address.toLowerCase();
  }
  if (optional("case_id")) {
    if (typeof value.case_id !== "string" || !/^[A-Za-z0-9_-]{1,64}$/.test(value.case_id)) fail();
    request.case_id = value.case_id;
  }
  if (optional("local_version_id")) {
    if (!positiveInteger(value.local_version_id)) fail();
    request.local_version_id = value.local_version_id;
  }
  if (optional("onchain_version_id")) {
    if (!isDecimalId(value.onchain_version_id)) fail();
    request.onchain_version_id = value.onchain_version_id;
  }
  if (optional("local_review_id")) {
    if (!positiveInteger(value.local_review_id)) fail();
    request.local_review_id = value.local_review_id;
  }
  if (optional("evidence_uri")) {
    if (!validEvidenceUri(value.evidence_uri)) fail();
    request.evidence_uri = value.evidence_uri;
  }
  if (request.action === "deploy") {
    if (Object.keys(request).length !== 3) fail();
  } else {
    if (request.contract_address === undefined || request.case_id === undefined || request.local_version_id === undefined) fail();
    if (request.action === "create_case" && request.onchain_version_id !== undefined) fail();
    if ((request.action === "append_version" || request.action === "add_review") && request.onchain_version_id === undefined) fail();
    if ((request.action === "add_review") !== (request.local_review_id !== undefined)) fail();
  }
  return Object.freeze(request);
}

function canonicalEntry(value: unknown): VerificationIntent {
  if (!record(value) || Object.keys(value).some((key) => !entryKeys.has(key)) ||
      !["intent_id", "request", "status", "binding_sha256"].every((key) => owns(value, key)) ||
      typeof value.intent_id !== "string" || !/^(?:[0-9a-f]{64}|[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})$/.test(value.intent_id) ||
      typeof value.status !== "string" || !statuses.has(value.status as VerificationIntentStatus) ||
      typeof value.binding_sha256 !== "string" || !/^[0-9a-f]{64}$/.test(value.binding_sha256)) fail();
  const result: VerificationIntent = {
    intent_id: value.intent_id, request: canonicalRequest(value.request, false),
    status: value.status as VerificationIntentStatus, binding_sha256: value.binding_sha256,
  };
  if (owns(value, "transaction_hash")) {
    if (!isTransactionHash(value.transaction_hash) || value.transaction_hash !== value.transaction_hash.toLowerCase()) fail();
    result.transaction_hash = value.transaction_hash;
  }
  if (["submitted", "pending", "failed", "mismatch", "verified"].includes(result.status) && result.transaction_hash === undefined) fail();
  return Object.freeze(result);
}

// The digest detects local record changes; transaction binding is checked by the backend.
async function bindingDigest(intent: Omit<VerificationIntent, "binding_sha256">): Promise<string> {
  const binding = {
    intent_id: intent.intent_id, request: intent.request,
    ...(intent.transaction_hash === undefined ? {} : { transaction_hash: intent.transaction_hash }), status: intent.status,
  };
  try {
    const digest = await globalThis.crypto.subtle.digest("SHA-256", encoder.encode(JSON.stringify(binding)));
    return Array.from(new Uint8Array(digest), (byte) => byte.toString(16).padStart(2, "0")).join("");
  } catch { return fail("The verification digest could not be computed. Use a browser with a secure context."); }
}

async function validateRaw(raw: string | null): Promise<VerificationIntent[]> {
  if (raw === null) return [];
  const envelope = parseEnvelope(raw);
  if (!record(envelope) || Object.keys(envelope).length !== 2 || !owns(envelope, "schema") || !owns(envelope, "intents") ||
      envelope.schema !== 1 || !Array.isArray(envelope.intents) || envelope.intents.length > MAX_ENTRIES) fail();
  const intents = envelope.intents.map(canonicalEntry);
  if (new Set(intents.map((intent) => intent.intent_id)).size !== intents.length) fail();
  const digests = await Promise.all(intents.map(bindingDigest));
  if (intents.some((intent, index) => intent.binding_sha256 !== digests[index])) fail();
  return intents;
}

async function readSnapshot(): Promise<{ target: Storage; raw: string | null; intents: VerificationIntent[] }> {
  const target = storage();
  const raw = readRaw(target);
  const intents = await validateRaw(raw);
  if (readRaw(target) !== raw) fail(conflict);
  return { target, raw, intents };
}

async function writeSnapshot(
  snapshot: Awaited<ReturnType<typeof readSnapshot>>, intents: VerificationIntent[],
): Promise<VerificationIntent[]> {
  const raw = JSON.stringify({ schema: 1, intents });
  if (intents.length > MAX_ENTRIES || encoder.encode(raw).length > MAX_BYTES)
    fail("Verification storage is full. Preserve existing records and continue with the current verification intent.");
  if (readRaw(snapshot.target) !== snapshot.raw) fail(conflict);
  try { snapshot.target.setItem(STORAGE_KEY, raw); }
  catch { return fail("Could not save the verification record. Check browser storage permissions and available space."); }
  if (readRaw(snapshot.target) !== raw) fail("The saved verification record did not match its read-back. Reload and check the record.");
  const saved = await validateRaw(raw);
  if (readRaw(snapshot.target) !== raw) fail(conflict);
  return saved;
}

export async function loadVerificationIntents(): Promise<VerificationIntent[]> {
  return (await readSnapshot()).intents;
}

export async function saveVerificationIntent(request: BotPrepareRequest): Promise<VerificationIntent> {
  const validated = canonicalRequest(request, true);
  const snapshot = await readSnapshot();
  if (snapshot.intents.length >= MAX_ENTRIES)
    fail("The 50-record verification limit has been reached. Preserve existing records and continue with the current intent.");
  let intentId: string;
  try { intentId = globalThis.crypto.randomUUID(); }
  catch { return fail("A verification intent could not be created. Use a browser with a secure context."); }
  if (snapshot.intents.some((intent) => intent.intent_id === intentId)) fail("This verification intent ID already exists. Create another verification intent.");
  const intent: VerificationIntent = {
    intent_id: intentId, request: validated, status: "ready", binding_sha256: "",
  };
  intent.binding_sha256 = await bindingDigest(intent);
  const saved = await writeSnapshot(snapshot, [...snapshot.intents, intent]);
  return saved[saved.length - 1];
}

export async function updateVerificationIntent(
  intentId: string, patch: { transaction_hash?: string; status?: VerificationIntentStatus },
): Promise<VerificationIntent> {
  if (!record(patch) || Object.keys(patch).some((key) => !["transaction_hash", "status"].includes(key)) ||
      (owns(patch, "transaction_hash") && !isTransactionHash(patch.transaction_hash)) ||
      (owns(patch, "status") && (typeof patch.status !== "string" || !statuses.has(patch.status as VerificationIntentStatus)))) fail();
  const snapshot = await readSnapshot();
  const index = snapshot.intents.findIndex((intent) => intent.intent_id === intentId);
  if (index === -1) fail("The selected verification intent was not found. Reload the verification records.");
  const previous = snapshot.intents[index];
  const transactionHash = patch.transaction_hash?.toLowerCase();
  if (previous.transaction_hash !== undefined && transactionHash !== undefined && previous.transaction_hash !== transactionHash)
    fail("This intent already has a transaction hash. Continue verifying the original transaction or create a new intent.");
  const next: VerificationIntent = {
    ...previous, ...(transactionHash === undefined ? {} : { transaction_hash: transactionHash }),
    status: patch.status ?? previous.status,
  };
  if (["submitted", "pending", "failed", "mismatch", "verified"].includes(next.status) && next.transaction_hash === undefined) fail();
  next.binding_sha256 = await bindingDigest(next);
  const intents = [...snapshot.intents];
  intents[index] = next;
  return (await writeSnapshot(snapshot, intents))[index];
}

export async function getVerificationIntent(intentId: string): Promise<VerificationIntent> {
  const intent = (await readSnapshot()).intents.find((entry) => entry.intent_id === intentId);
  return intent ?? fail("The selected verification intent was not found. Reload the verification records.");
}
