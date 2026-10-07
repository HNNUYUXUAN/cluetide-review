import { request } from "./api";
import type {
  BotAction, BotArtifact, BotNetwork, BotPrepared, BotPrepareRequest, BotPrepareSubmission, BotReviewPreflight,
  BotReadRequest, BotReadResult, BotTransaction, BotVerification,
} from "./bot-types";

export interface WalletProvider {
  request(args: { method: string; params?: unknown[] }): Promise<unknown>;
  on?: (event: string, listener: (value: unknown) => void) => void;
  removeListener?: (event: string, listener: (value: unknown) => void) => void;
}
export interface WalletSnapshot { account: string; chainId: number }
export const isAddress = (value: unknown): value is string =>
  typeof value === "string" && /^0x[0-9a-fA-F]{40}$/.test(value) && !/^0x0{40}$/.test(value);
export const isTransactionHash = (value: unknown): value is string =>
  typeof value === "string" && /^0x[0-9a-fA-F]{64}$/.test(value) && !/^0x0{64}$/.test(value);
export const isDecimalId = (value: unknown): value is string =>
  typeof value === "string" && /^[1-9][0-9]{0,77}$/.test(value) && BigInt(value) < 2n ** 256n;
const isQuantity = (value: unknown): value is string =>
  typeof value === "string" && /^0x(?:0|[1-9a-fA-F][0-9a-fA-F]{0,63})$/.test(value);
const isDecimal = (value: unknown): value is string =>
  typeof value === "string" && /^(?:0|[1-9][0-9]{0,77})$/.test(value) && BigInt(value) < 2n ** 256n;
const isHash = (value: unknown): value is string =>
  typeof value === "string" && /^(?:0x)?[0-9a-fA-F]{64}$/.test(value) && !/^(?:0x)?0{64}$/.test(value);
const record = (value: unknown): value is Record<string, unknown> =>
  !!value && typeof value === "object" && !Array.isArray(value);
const boundedText = (value: unknown, max = 500): value is string => typeof value === "string" && value.length <= max;
const strings = (value: unknown): value is string[] =>
  Array.isArray(value) && value.length <= 32 && value.every((item) => boundedText(item, 2000));
const actions = new Set<BotAction>(["deploy", "create_case", "add_review", "append_version"]);
function boundedGetter(value: unknown): value is Record<string, unknown> {
  if (!record(value)) return false;
  let nodes = 0;
  const visit = (item: unknown, depth: number): boolean => {
    if (++nodes > 4096 || depth > 12) return false;
    if (item === null || typeof item === "boolean") return true;
    if (typeof item === "string") return item.length <= 8192;
    if (typeof item === "number") return Number.isSafeInteger(item);
    if (Array.isArray(item)) return item.length <= 512 && item.every((child) => visit(child, depth + 1));
    if (record(item)) return Object.keys(item).length <= 64 && Object.entries(item).every(([key, child]) => key.length <= 100 && visit(child, depth + 1));
    return false;
  };
  return visit(value, 0);
}
const safeHttps = (value: unknown): value is string => {
  if (!boundedText(value, 300)) return false;
  try {
    const url = new URL(value);
    return url.protocol === "https:" && !url.username && !url.password && !url.search && !url.hash;
  } catch { return false; }
};
export function isNetwork(value: unknown): value is BotNetwork {
  if (!record(value) || ![968, 677].includes(value.chain_id as number) ||
      value.chain_id_hex !== `0x${Number(value.chain_id).toString(16)}` || !boundedText(value.name, 100) ||
      !safeHttps(value.rpc_url) || !safeHttps(value.explorer_url) || !record(value.native_currency)) return false;
  const currency = value.native_currency;
  const endpoints = value.chain_id === 968
    ? ["https://rpc.bohr.life", "https://scan.bohr.life"]
    : ["https://rpc.botchain.ai", "https://scan.botchain.ai"];
  return currency.name === "BOT" && currency.symbol === "BOT" && currency.decimals === 18 &&
    value.rpc_url === endpoints[0] && value.explorer_url === endpoints[1] &&
    (value.native_currency_status === undefined || boundedText(value.native_currency_status, 150)) &&
    (value.warnings === undefined || strings(value.warnings));
}
function isArtifact(value: unknown): value is BotArtifact {
  return record(value) && boundedText(value.contract_name, 100) && boundedText(value.compiler, 100) &&
    boundedText(value.evm_version, 50) && isHash(value.source_sha256) && isHash(value.bytecode_sha256) && isHash(value.runtime_sha256);
}
export function validateTransaction(value: unknown): BotTransaction {
  if (!record(value) || Object.keys(value).some((key) => !["from", "to", "data", "value", "chainId", "gas", "gasPrice"].includes(key)) ||
      !isAddress(value.from) || (value.to !== undefined && !isAddress(value.to)) ||
      typeof value.data !== "string" || value.data.length > 2_000_002 || !/^0x(?:[0-9a-fA-F]{2})+$/.test(value.data) ||
      value.value !== "0x0" || !["0x3c8", "0x2a5"].includes(value.chainId as string) ||
      (value.gas !== undefined && (!isQuantity(value.gas) || BigInt(value.gas) === 0n)) ||
      (value.gasPrice !== undefined && !isQuantity(value.gasPrice))) throw new Error("Unsigned transaction parameters failed validation.");
  return value as unknown as BotTransaction;
}
function parsePrepared(value: unknown, expected: BotPrepareRequest): BotPrepared {
  if (!record(value) || !isNetwork(value.network) || value.network.chain_id !== expected.chain_id ||
      value.action !== expected.action || !record(value.commitments) || !record(value.fees) || !isArtifact(value.artifact) || !strings(value.warnings))
    throw new Error("The transaction preparation response failed validation.");
  const transaction = validateTransaction(value.transaction);
  if (transaction.chainId !== value.network.chain_id_hex || transaction.from.toLowerCase() !== expected.account.toLowerCase() ||
      (expected.action === "deploy" ? transaction.to !== undefined : transaction.to?.toLowerCase() !== expected.contract_address?.toLowerCase()))
    throw new Error("The prepared transaction does not match the account, network, or contract.");
  for (const key of ["gas_limit", "gas_price_wei", "estimated_max_fee_wei", "balance_wei"])
    if (value.fees[key] !== null && !isDecimal(value.fees[key])) throw new Error("Fee fields failed validation.");
  if (value.fees.sufficient_balance !== null && typeof value.fees.sufficient_balance !== "boolean") throw new Error("Balance status failed validation.");
  const fees = value.fees;
  if (transaction.gas && fees.gas_limit !== null && BigInt(transaction.gas) !== BigInt(fees.gas_limit as string)) throw new Error("The gas limit does not match the transaction parameters.");
  if (transaction.gasPrice && fees.gas_price_wei !== null && BigInt(transaction.gasPrice) !== BigInt(fees.gas_price_wei as string)) throw new Error("The gas price does not match the transaction parameters.");
  if (fees.gas_limit !== null && fees.gas_price_wei !== null && fees.estimated_max_fee_wei !== null &&
      BigInt(fees.gas_limit as string) * BigInt(fees.gas_price_wei as string) !== BigInt(fees.estimated_max_fee_wei as string))
    throw new Error("The estimated fee does not match the gas parameters.");
  for (const key of ["case_id_hex", "content_hash", "review_hash"])
    if (value.commitments[key] !== undefined && !isHash(value.commitments[key])) throw new Error("Content commitment fields failed validation.");
  if (expected.action !== "deploy" && (!isHash(value.commitments.case_id_hex) || !isHash(value.commitments.content_hash) || value.commitments.local_version_id !== expected.local_version_id))
    throw new Error("The case and version content commitments must match completely.");
  if (value.commitments.onchain_version_id !== undefined && (!isDecimalId(value.commitments.onchain_version_id) || value.commitments.onchain_version_id !== expected.onchain_version_id)) throw new Error("The on-chain version ID failed validation.");
  if (["append_version", "add_review"].includes(expected.action) && value.commitments.onchain_version_id !== expected.onchain_version_id)
    throw new Error("The on-chain version ID does not match the selected version.");
  if (expected.action === "add_review" && (!isHash(value.commitments.review_hash) || value.commitments.local_review_id !== expected.local_review_id))
    throw new Error("The review commitment does not match the selected record.");
  if (value.commitments.local_version_id !== undefined &&
      (!Number.isSafeInteger(value.commitments.local_version_id) || Number(value.commitments.local_version_id) <= 0 || value.commitments.local_version_id !== expected.local_version_id))
    throw new Error("The local version ID failed validation.");
  return value as unknown as BotPrepared;
}
function validInventory(value: unknown, items: unknown, offset: number): boolean {
  if (!record(value) || !Array.isArray(items) || items.length > 3 || !items.every(record) ||
      !isDecimal(value.offset) || !isDecimal(value.total_count) || typeof value.complete !== "boolean" ||
      (value.next_offset !== null && !isDecimal(value.next_offset)) || value.offset !== String(offset)) return false;
  const end = BigInt(value.offset) + BigInt(items.length), total = BigInt(value.total_count);
  return end <= total && value.complete === (offset === 0 && end === total) &&
    (end < total ? items.length > 0 && value.next_offset === String(end) : value.next_offset === null);
}
const post = (body: unknown): RequestInit => ({ method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body), signal: AbortSignal.timeout(60_000) });
export const botApi = {
  networks: async (): Promise<BotNetwork[]> => {
    const result: unknown = await request("/bot/networks", { signal: AbortSignal.timeout(15_000) });
    if (!record(result) || !Array.isArray(result.networks) || result.networks.length !== 2 || !result.networks.every(isNetwork) ||
        new Set(result.networks.map((network) => network.chain_id)).size !== 2) throw new Error("The BOT network configuration failed validation.");
    return result.networks;
  },
  prepare: async (body: BotPrepareSubmission): Promise<BotPrepared> => parsePrepared(await request("/bot/prepare", post(body)), body),
  reviewPreflight: async (prepared: BotPrepareRequest, cursor?: string): Promise<BotReviewPreflight> => {
    const value: unknown = await request("/bot/review-preflight", post({ prepared, ...(cursor ? { cursor } : {}) }));
    if (!record(value) || !boundedText(value.cursor, 512) || !value.cursor ||
        !["scanning", "ready", "duplicate"].includes(value.status as string) ||
        !isDecimal(value.scanned_count) || !isDecimal(value.total_count) ||
        BigInt(value.scanned_count) > BigInt(value.total_count) ||
        !isDecimal(value.read_block_number) || !isTransactionHash(value.read_block_hash) ||
        (value.existing_review_id !== null && !isDecimalId(value.existing_review_id)) ||
        (value.status === "duplicate" ? value.existing_review_id === null : value.existing_review_id !== null) ||
        (value.status === "ready" && value.scanned_count !== value.total_count))
      throw new Error("The review preflight response failed validation.");
    return value as unknown as BotReviewPreflight;
  },
  verify: async (transactionHash: string, prepared: BotPrepareRequest, minimumConfirmations: number): Promise<BotVerification> => {
    const value: unknown = await request("/bot/verify", post({ chain_id: prepared.chain_id, transaction_hash: transactionHash, prepared, minimum_confirmations: minimumConfirmations }));
    if (!record(value) || !["pending", "failed", "mismatch", "verified"].includes(value.status as string) ||
        !isTransactionHash(value.transaction_hash) || value.transaction_hash.toLowerCase() !== transactionHash.toLowerCase() ||
        !Number.isSafeInteger(value.confirmations) || Number(value.confirmations) < 0 || !Number.isSafeInteger(value.minimum_confirmations) ||
        value.minimum_confirmations !== minimumConfirmations || (value.contract_address !== undefined && value.contract_address !== null && !isAddress(value.contract_address)) ||
        (value.status === "verified" && (!isAddress(value.contract_address) || Number(value.confirmations) < minimumConfirmations)) ||
        (value.reason !== undefined && !boundedText(value.reason, 4000)) || (value.getter_state !== undefined && !boundedGetter(value.getter_state)) ||
        (value.onchain_ids !== undefined && (!record(value.onchain_ids) || Object.values(value.onchain_ids).some((item) => !isDecimalId(item)))))
      throw new Error("The transaction verification response failed validation.");
    return value as unknown as BotVerification;
  },
  read: async (body: BotReadRequest): Promise<BotReadResult> => {
    const value: unknown = await request("/bot/read", post(body));
    if (!record(value) || !isNetwork(value.network) || value.network.chain_id !== body.chain_id || !isAddress(value.contract_address) ||
        value.contract_address.toLowerCase() !== body.contract_address.toLowerCase() || !isHash(value.case_id_hex) || !boundedGetter(value.getter_state) || !isArtifact(value.artifact) ||
        (value.case_status !== undefined && !["registered", "unregistered"].includes(value.case_status as string)) ||
        (value.case_status === "unregistered" && value.getter_state.case !== null) ||
        (value.case_status === "registered" && !record(value.getter_state.case)) ||
        !isDecimal(value.read_block_number) || !isTransactionHash(value.read_block_hash) ||
        (body.read_block_number !== undefined && value.read_block_number !== body.read_block_number) ||
        (body.read_block_hash !== undefined && value.read_block_hash.toLowerCase() !== body.read_block_hash.toLowerCase()) ||
        !record(value.getter_state.inventory) ||
        !validInventory(value.getter_state.inventory.versions, value.getter_state.versions, body.version_offset ?? 0) ||
        !validInventory(value.getter_state.inventory.reviews, value.getter_state.reviews, body.review_offset ?? 0))
      throw new Error("The contract read response failed validation.");
    return value as unknown as BotReadResult;
  },
};
export function walletProvider(): WalletProvider | null {
  const candidate = (window as Window & { ethereum?: WalletProvider }).ethereum;
  return candidate && typeof candidate.request === "function" ? candidate : null;
}
export function parseChainId(value: unknown): number {
  if (!isQuantity(value) || BigInt(value) > BigInt(Number.MAX_SAFE_INTEGER)) throw new Error("Invalid wallet chain ID.");
  return Number(BigInt(value));
}
export function parseAccounts(value: unknown): string[] {
  if (!Array.isArray(value) || value.length > 20 || !value.every(isAddress)) throw new Error("Invalid wallet accounts response.");
  return value;
}
export async function walletSnapshot(provider: WalletProvider): Promise<WalletSnapshot> {
  const [accounts, chainId] = await Promise.all([
    provider.request({ method: "eth_accounts" }), provider.request({ method: "eth_chainId" }),
  ]);
  const parsed = parseAccounts(accounts);
  if (!parsed.length) throw new Error("Connect your wallet account first.");
  return { account: parsed[0], chainId: parseChainId(chainId) };
}
export async function connectWallet(provider: WalletProvider): Promise<WalletSnapshot> {
  parseAccounts(await provider.request({ method: "eth_requestAccounts" }));
  return walletSnapshot(provider);
}
export async function switchWalletNetwork(provider: WalletProvider, network: BotNetwork): Promise<WalletSnapshot> {
  await provider.request({ method: "wallet_switchEthereumChain", params: [{ chainId: network.chain_id_hex }] });
  return walletSnapshot(provider);
}
export async function addWalletNetwork(provider: WalletProvider, network: BotNetwork): Promise<WalletSnapshot> {
  await provider.request({ method: "wallet_addEthereumChain", params: [{ chainId: network.chain_id_hex, chainName: network.name,
    nativeCurrency: network.native_currency, rpcUrls: [network.rpc_url], blockExplorerUrls: [network.explorer_url] }] });
  return walletSnapshot(provider);
}
export async function sendWalletTransaction(
  provider: WalletProvider, prepared: BotPrepared, isCurrent: () => boolean,
  onTransactionHash?: (transactionHash: string) => Promise<void>,
): Promise<{ transactionHash: string; walletChanged: boolean }> {
  const transaction = validateTransaction(prepared.transaction);
  if (!actions.has(prepared.action) || !isNetwork(prepared.network) || transaction.chainId !== prepared.network.chain_id_hex ||
      (prepared.action === "deploy" ? transaction.to !== undefined : !isAddress(transaction.to))) throw new Error("Invalid action, network, or contract parameters for signing.");
  if (!transaction.gas || !transaction.gasPrice || BigInt(transaction.gasPrice) === 0n || prepared.fees.estimated_max_fee_wei === null ||
      prepared.fees.balance_wei === null || prepared.fees.sufficient_balance !== true)
    throw new Error("Obtain a complete fee estimate and sufficient balance before reviewing a signature.");
  const before = await walletSnapshot(provider);
  if (!isCurrent() || before.chainId !== prepared.network.chain_id || before.account.toLowerCase() !== transaction.from.toLowerCase())
    throw new Error("The wallet account or network changed. Prepare the transaction again.");
  const result = await provider.request({ method: "eth_sendTransaction", params: [{ ...transaction }] });
  if (!isTransactionHash(result)) throw new Error("The wallet did not return a valid transaction hash. Check wallet activity, then enter the transaction hash.");
  if (onTransactionHash) await onTransactionHash(result);
  let walletChanged = !isCurrent();
  try {
    const after = await walletSnapshot(provider);
    walletChanged ||= after.chainId !== before.chainId || after.account.toLowerCase() !== before.account.toLowerCase();
  } catch { walletChanged = true; }
  walletChanged ||= !isCurrent();
  return { transactionHash: result, walletChanged };
}
export function formatBot(wei: string | null): string {
  if (wei === null || !isDecimal(wei)) return "Pending";
  const raw = BigInt(wei);
  const fraction = (raw % 10n ** 18n).toString().padStart(18, "0").replace(/0+$/, "");
  return `${raw / 10n ** 18n}${fraction ? `.${fraction}` : ""} BOT`;
}
export function explorerLink(network: BotNetwork, kind: "tx" | "address", identifier: string): string | undefined {
  if (kind === "tx" ? !isTransactionHash(identifier) : !isAddress(identifier)) return undefined;
  return `${network.explorer_url.replace(/\/$/, "")}/${kind}/${identifier}`;
}
export function isBotAction(value: string): value is BotAction { return actions.has(value as BotAction); }
