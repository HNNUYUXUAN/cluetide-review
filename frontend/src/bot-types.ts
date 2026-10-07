export type BotChainId = 968 | 677;
export type BotAction = "deploy" | "create_case" | "add_review" | "append_version";
export interface BotNetwork {
  chain_id: BotChainId;
  chain_id_hex: string;
  name: string;
  rpc_url: string;
  explorer_url: string;
  native_currency: { name: string; symbol: string; decimals: number };
  native_currency_status?: string;
  warnings?: string[];
}
export interface BotArtifact {
  contract_name: string;
  compiler: string;
  evm_version: string;
  source_sha256: string;
  bytecode_sha256: string;
  runtime_sha256: string;
}
export interface BotPrepareRequest {
  chain_id: BotChainId;
  action: BotAction;
  account: string;
  contract_address?: string;
  case_id?: string;
  local_version_id?: number;
  onchain_version_id?: string;
  local_review_id?: number;
  evidence_uri?: string;
}
export interface BotPrepareSubmission extends BotPrepareRequest {
  review_preflight_cursor?: string;
}
export interface BotReviewPreflight {
  cursor: string;
  status: "scanning" | "ready" | "duplicate";
  scanned_count: string;
  total_count: string;
  read_block_number: string;
  read_block_hash: string;
  existing_review_id: string | null;
}
export interface BotInventoryPage {
  offset: string;
  total_count: string;
  next_offset: string | null;
  complete: boolean;
}
export interface BotTransaction {
  from: string;
  to?: string;
  data: string;
  value: string;
  chainId: string;
  gas?: string;
  gasPrice?: string;
}
export interface BotPrepared {
  network: BotNetwork;
  action: BotAction;
  transaction: BotTransaction;
  commitments: {
    case_id_hex?: string;
    content_hash?: string;
    review_hash?: string;
    local_version_id?: number;
    local_review_id?: number;
    onchain_version_id?: string;
  };
  fees: {
    gas_limit: string | null;
    gas_price_wei: string | null;
    estimated_max_fee_wei: string | null;
    balance_wei: string | null;
    sufficient_balance: boolean | null;
  };
  artifact: BotArtifact;
  warnings: string[];
}
export interface BotVerification {
  status: "pending" | "failed" | "mismatch" | "verified";
  reason?: string;
  contract_address?: string | null;
  transaction_hash: string;
  tx_link?: string;
  confirmations: number;
  minimum_confirmations: number;
  getter_state?: Record<string, unknown>;
  onchain_ids?: { version_id?: string; review_id?: string };
}
export interface BotReadRequest {
  chain_id: BotChainId;
  contract_address: string;
  case_id: string;
  version_id?: string;
  review_id?: string;
  version_offset?: number;
  review_offset?: number;
  read_block_number?: string;
  read_block_hash?: string;
}
export interface BotReadResult {
  network: BotNetwork;
  contract_address: string;
  case_id_hex: string;
  case_status?: "registered" | "unregistered";
  read_block_number: string;
  read_block_hash: string;
  getter_state: Record<string, unknown> & {
    versions: Record<string, unknown>[];
    reviews: Record<string, unknown>[];
    inventory: { versions: BotInventoryPage; reviews: BotInventoryPage };
  };
  artifact: BotArtifact;
}
export interface BotPublicRecord {
  chain_id: BotChainId;
  transaction_hash: string;
  action: BotAction;
  contract_address?: string;
  case_id?: string;
  local_version_id?: number;
  onchain_version_id?: string;
  onchain_review_id?: string;
  status: "submitted" | BotVerification["status"];
}
