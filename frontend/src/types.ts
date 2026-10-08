export type JsonRecord = Record<string, unknown>;
export interface Scope {
  address: string;
  token_address: string;
  from_block: number;
  to_block: number;
  mode: "offline" | "rpc";
  agent_mode: "offline" | "live";
}
export interface Preset extends Omit<Scope, "mode" | "agent_mode"> {
  title: string;
  case_id: string;
  tx_hash: string;
  data_status?: string;
  sources?: JsonRecord[];
}
export interface Coverage {
  status: string;
  requested_from_block: number;
  requested_to_block: number;
  planned_queries: number;
  completed_queries: number;
  issues: string[];
  queries: JsonRecord[];
}
export interface Transfer {
  evidence_id: string;
  block_number: number;
  transaction_hash: string;
  log_index: number;
  from_address: string;
  to_address: string;
  value_raw: string;
  directions: string[];
}
export interface Evidence {
  request: Scope;
  coverage: Coverage;
  transfers: Transfer[];
  metadata: {
    symbol?: string | null;
    decimals?: number | null;
    status?: string;
  };
  alerts: {
    alert_id: string;
    observed_value_raw: string;
    evidence_ids: string[];
    explanation: string;
    interpretation: string;
  }[];
  sources: JsonRecord[];
  raw?: JsonRecord;
  warnings: string[];
  finalized_anchor?: { number: number; block_hash: string } | null;
}
export interface ToolEvidence {
  evidence_id: string;
  kind: string;
  payload: unknown;
  status: string;
}
export interface Conclusion {
  summary?: string;
  classification?: string;
  claims?: { text: string; evidence_ids: string[]; interpretation?: boolean }[];
  limitations?: string[];
  assessments?: ExplanationAssessment[];
}
export interface ExplanationAssessment {
  explanation_id: string;
  explanation: string;
  status: "supported" | "refuted" | "unknown";
  support_evidence_ids: string[];
  counter_evidence_ids: string[];
  unknowns: string[];
  checks: string[];
}
export interface InvestigationScope {
  execution_status?: string;
  selected_transaction_hash: string | null;
  investigated_transaction_hash: string | null;
  uninvestigated_transaction_hashes: string[];
  observed_transaction_hashes: string[];
  alerted_transaction_hashes: string[];
  selected_alert_ids: string[];
  selected_evidence_ids: string[];
  selection_basis: string;
  selection_reason: string;
  statement: string;
}
export interface Report {
  quality_validation?: ReportQuality;
  case_id?: string;
  revision?: number;
  conclusion?: Conclusion | null;
  agent?: Agent | null;
  corrections?: { text: string; author: string; parent_version_id: number }[];
  execution_mode?: string;
  parent_manifest_hash?: string | null;
  investigation_scope?: InvestigationScope;
}
export interface ReportQuality {
  status: "needs_review" | "no_flags_detected";
  flags: { code: string; message: string; locations?: string[] }[];
  schema_version?: string;
  report_modified?: boolean;
  scope?: string;
}
export interface CitationValidation {
  status: "valid" | "needs_review" | "not_applicable";
  issues: { code: string; message: string; location?: string }[];
  scope: string;
  schema_version?: string;
  issue_count?: number;
  issues_truncated?: boolean;
  checked_counts?: Record<string, number>;
  report_modified?: boolean;
  factual_verification?: boolean;
  source_authenticity_verified?: boolean;
}
export interface TransferFact {
  evidence_id: string;
  token_address: string;
  raw_amount: string;
  decimals: number | null;
  formatted_amount: string | null;
  symbol: string | null;
  unit: "ERC-20 token units" | "ERC-20 base units";
  formatting_status: "formatted" | "raw_only";
  decimals_observation_ids: string[];
}
export interface Agent {
  status: string;
  report?: Conclusion | null;
  evidence: ToolEvidence[];
  trace: JsonRecord[];
  model_requests: number;
  tool_attempts: number;
  stop_reason?: string | null;
  model_names?: string[];
}
export interface Version {
  version_id: number;
  parent_version_id: number;
  author: string;
  content_hash: string;
  correction?: string;
  created_at?: string;
}
export interface Review {
  review_id?: number;
  reviewer: string;
  comment?: string;
  version_id?: number;
  decision?: string;
  created_at?: string;
}
export interface Investigation {
  created_at?: string;
  progress?: {stage: string; model_requests: number; tool_attempts: number; trace: JsonRecord[]; updated_at: string};
  id: string;
  title?: string;
  status: string;
  mode?: string;
  input?: Scope;
  error?: string;
  imported?: boolean;
  evidence: Evidence | null;
  agent: Agent | null;
  report: Report | null;
  registry?: JsonRecord | null;
  versions: Version[];
  reviews: Review[];
  manifest_hash?: string;
  investigation_scope?: InvestigationScope;
  validation?: {
    verified: boolean;
    statement?: string;
    files_verified?: string[];
  };
  manifest?: { files?: Record<string, { sha256: string; size: number }> };
}
export interface Health {
  status?: string;
  rpc_available?: boolean;
  live_available?: boolean;
  live_agent_available?: boolean;
  capabilities?: JsonRecord;
  [key: string]: unknown;
}
export interface VerifiedImport {
  status: string;
  validation: {
    verified: boolean;
    statement?: string;
    files_verified?: string[];
  };
  manifest: { files: Record<string, { sha256: string; size: number }> };
  manifest_hash: string;
  evidence: Evidence;
  report: Report;
  registry_verification: string;
  independence: string;
  report_quality?: ReportQuality;
  citation_validation?: CitationValidation;
  transfer_facts?: TransferFact[];
}
export interface HistoryItem {
  input?: Scope;
  agent?: Agent | null;
  id: string;
  title?: string;
  status?: string;
  created_at?: string;
}
export type Screen = "workbench" | "bundles" | "review";
