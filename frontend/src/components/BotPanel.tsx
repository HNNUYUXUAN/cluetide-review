import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { ApiError, uiError } from "../api";
import { caseTitle } from "../format";
import type { Investigation } from "../types";
import type {
  BotAction, BotChainId, BotNetwork, BotPrepared, BotPrepareRequest,
  BotPublicRecord, BotReadResult, BotReviewPreflight, BotVerification,
} from "../bot-types";
import {
  addWalletNetwork, botApi, connectWallet, explorerLink, formatBot, isAddress,
  isBotAction, isDecimalId, isTransactionHash, parseAccounts, parseChainId,
  sendWalletTransaction, switchWalletNetwork, walletProvider, walletSnapshot,
} from "../bot-wallet";
import type { WalletProvider, WalletSnapshot } from "../bot-wallet";
import { getVerificationIntent, loadVerificationIntents, saveVerificationIntent, updateVerificationIntent } from "../bot-verification-store";
import type { VerificationIntent } from "../bot-verification-store";
import "../bot.css";

const historyKey = "cluetide:bot-public-transactions:v1";
const outcomeKey = "cluetide:bot-wallet-outcome:v1";
interface WalletOutcome { chain_id: BotChainId; action: BotAction; attempt_id?: string; status?: "attempting" | "unknown" }
function loadWalletOutcome(): WalletOutcome | null {
  try {
    const stored = localStorage.getItem(outcomeKey);
    if (!stored || stored.length > 500) return null;
    const value = JSON.parse(stored);
    if (value?.schema_version !== 1 || ![968, 677].includes(value.chain_id) || !isBotAction(value.action) ||
        (value.attempt_id !== undefined && (typeof value.attempt_id !== "string" || value.attempt_id.length > 100)) ||
        (value.status !== undefined && !["attempting", "unknown"].includes(value.status))) return null;
    return { chain_id: value.chain_id, action: value.action,
      ...(value.attempt_id ? { attempt_id: value.attempt_id } : {}), ...(value.status ? { status: value.status } : {}) };
  } catch { return null; }
}
const actionLabels: Record<BotAction, string> = {
  deploy: "1 · Deploy registry contract",
  create_case: "2 · Register case v1",
  add_review: "3 · Review selected version",
  append_version: "4 · Register corrected version",
};
const verificationLabels: Record<BotVerification["status"], string> = {
  pending: "Awaiting receipt or confirmations", failed: "On-chain execution failed", mismatch: "Transaction or contract state mismatch", verified: "Receipt and contract state verified",
};
const intentStatusLabel = (intent: VerificationIntent): string => {
  if (intent.status === "ready") return "Verification parameters saved";
  if (intent.status === "uncertain") return "Wallet outcome needs review";
  if (intent.status === "rejected") return "Wallet request cancelled";
  if (intent.status === "submitted") return "Transaction hash linked";
  return verificationLabels[intent.status];
};
const warningMessages: Record<string, string> = {
  mainnet_currency_metadata_inferred: "The mainnet currency name and 18 decimal places use inferred metadata. Check official documentation before adding the network.",
  public_evidence_uri_unavailable: "Public access to this evidence link has not been confirmed. Provide an accessible link before sharing it with reviewers.",
  gas_estimate_unavailable: "The gas limit is unavailable. Check RPC availability, then prepare again.",
  gas_price_unavailable: "The gas price is unavailable. Prepare the transaction again.",
  balance_unavailable: "The wallet balance is unavailable. Prepare the transaction again.",
  insufficient_balance: "The balance is insufficient for the estimated fee. Check your wallet.",
  wallet_controls_nonce_and_final_fees: "Your wallet confirms the transaction nonce and final fees.",
  hash_commitment_does_not_certify_facts: "The content hash verifies matching bytes. Case findings still require evidence review.",
};
const warningText = (code: string): string => Object.prototype.hasOwnProperty.call(warningMessages, code)
  ? warningMessages[code] : "An additional notice needs review. Open the transaction details.";
function loadHistory(): BotPublicRecord[] {
  try {
    const stored = localStorage.getItem(historyKey) ?? "null";
    if (stored.length > 100_000) return [];
    const value: unknown = JSON.parse(stored);
    if (!value || typeof value !== "object" || !("schema_version" in value) || value.schema_version !== 1 ||
        !("records" in value) || !Array.isArray(value.records)) return [];
    return value.records.slice(0, 50).filter((item): item is BotPublicRecord => {
      if (!item || typeof item !== "object" || ![968, 677].includes(item.chain_id) ||
          !isTransactionHash(item.transaction_hash) || !isBotAction(item.action) ||
          !["submitted", "pending", "failed", "mismatch", "verified"].includes(item.status)) return false;
      return (item.contract_address === undefined || isAddress(item.contract_address)) &&
        (item.case_id === undefined || (typeof item.case_id === "string" && /^[A-Za-z0-9_-]{1,64}$/.test(item.case_id))) &&
        (item.local_version_id === undefined || (Number.isSafeInteger(item.local_version_id) && item.local_version_id > 0)) &&
        (item.onchain_version_id === undefined || isDecimalId(item.onchain_version_id)) &&
        (item.onchain_review_id === undefined || isDecimalId(item.onchain_review_id));
    }).map((item) => ({
      chain_id: item.chain_id, transaction_hash: item.transaction_hash, action: item.action, status: item.status,
      ...(item.contract_address ? { contract_address: item.contract_address } : {}),
      ...(item.case_id ? { case_id: item.case_id } : {}),
      ...(item.local_version_id ? { local_version_id: item.local_version_id } : {}),
      ...(item.onchain_version_id ? { onchain_version_id: item.onchain_version_id } : {}),
      ...(item.onchain_review_id ? { onchain_review_id: item.onchain_review_id } : {}),
    }));
  } catch { return []; }
}
const botErrorMessages: Record<string, string> = {
  bot_rpc_unavailable: "The official RPC could not complete this read. Try again shortly.",
  read_block_changed: "The snapshot block was reorganized. Read the contract again to create a current block snapshot.",
  review_preflight_busy: "The review check is in progress. Continue shortly.",
  review_preflight_capacity: "The review check service is busy. Try again shortly.",
  rpc_error: "The official RPC could not complete this read. Check the service logs and retry.",
  case_exists: "This case is already registered in the selected contract. Read the contract and check its existing versions.",
  review_exists: "This wallet has already registered this review for the selected version. Read the contract to inspect the record.",
  unknown_case: "This case is not registered in the selected contract. Register its original v1 first.",
  initial_version_required: "Select the original local v1 to register this case.",
  stale_parent: "The selected parent is no longer the current on-chain version. Read the contract and select its current parent.",
  unauthorized_author: "This wallet is not the author allowed to append a version to this case.",
  onchain_version_mismatch: "The on-chain version does not match the selected case and content commitment.",
  onchain_review_mismatch: "The on-chain review does not match the selected version and review commitment.",
  contract_runtime_mismatch: "The contract code does not match the expected registry build. Check the contract address and network.",
  chain_id_mismatch: "The RPC chain ID does not match the selected network.",
  rate_limited: "The service is receiving too many requests. Wait briefly and retry.",
};
const verificationReason = (reason?: string): string => {
  if (!reason) return "";
  const labels: Record<string, string> = {
    receipt_pending: "The transaction receipt is not available yet.",
    transaction_pending: "The transaction is still pending.",
    confirmations_pending: "Waiting for the required block confirmations.",
    execution_reverted: "The transaction reverted on-chain.",
    transaction_sender_mismatch: "The transaction sender does not match the original verification intent.",
    transaction_target_mismatch: "The transaction target does not match the original contract.",
    transaction_input_mismatch: "The transaction calldata does not match the prepared action.",
    transaction_chain_mismatch: "The transaction belongs to a different network.",
    receipt_block_not_canonical: "The receipt block is no longer canonical. Read the receipt again.",
  };
  return labels[reason] ?? botErrorMessages[reason] ?? uiError(reason, "The verification result needs review.");
};
const message = (error: unknown) => error instanceof Error
  ? botErrorMessages[error.message] ?? uiError(error, "The action could not be completed. Check your wallet and local API.")
  : "The action could not be completed. Check your wallet and local API.";
interface PreparedContext { intent_id: string; request: BotPrepareRequest; response: BotPrepared; generation: number }

export default function BotPanel({ investigation }: { investigation: Investigation | null }) {
  const [networks, setNetworks] = useState<BotNetwork[]>([]);
  const [chainId, setChainId] = useState<BotChainId>(968);
  const [provider, setProvider] = useState<WalletProvider | null>(walletProvider);
  const providerRef = useRef(provider);
  const [account, setAccount] = useState("");
  const [walletChain, setWalletChain] = useState<number | null>(null);
  const [action, setAction] = useState<BotAction>("deploy");
  const [contractAddress, setContractAddress] = useState("");
  const [localVersion, setLocalVersion] = useState(0);
  const [localReview, setLocalReview] = useState(0);
  const [onchainVersion, setOnchainVersion] = useState("");
  const [onchainReview, setOnchainReview] = useState("");
  const [evidenceUri, setEvidenceUri] = useState("");
  const [prepared, setPrepared] = useState<PreparedContext | null>(null);
  const [accepted, setAccepted] = useState(false);
  const [transactionHash, setTransactionHash] = useState("");
  const [verification, setVerification] = useState<BotVerification | null>(null);
  const [readResult, setReadResult] = useState<BotReadResult | null>(null);
  const [readPages, setReadPages] = useState({ versions: [0], reviews: [0] });
  const [preflight, setPreflight] = useState<{ fingerprint: string; result: BotReviewPreflight } | null>(null);
  const [verificationError, setVerificationError] = useState("");
  const [intents, setIntents] = useState<VerificationIntent[]>([]);
  const [selectedIntentId, setSelectedIntentId] = useState("");
  const [verificationIntent, setVerificationIntent] = useState<VerificationIntent | null>(null);
  const verificationIntentRef = useRef<VerificationIntent | null>(null);
  const verificationEpoch = useRef(0);
  const [intentStoreError, setIntentStoreError] = useState("");
  const [records, setRecords] = useState<BotPublicRecord[]>(loadHistory);
  const [walletOutcome, setWalletOutcome] = useState<WalletOutcome | null>(loadWalletOutcome);
  const recordsRef = useRef(records);
  const [busy, setBusy] = useState("");
  const [notice, setNotice] = useState("");
  const [error, setError] = useState("");
  const busyRef = useRef(false);
  const generation = useRef(0);
  const walletEpoch = useRef(0);
  const mounted = useRef(true);
  const contextRef = useRef<PreparedContext | null>(null);
  const submittedContext = useRef<PreparedContext | null>(null);
  const network = networks.find((item) => item.chain_id === chainId);
  const verificationNetwork = networks.find((item) => item.chain_id === verificationIntent?.request.chain_id);
  const versions = investigation?.versions ?? [];
  const reviews = investigation?.reviews ?? [];
  const selectedVersion = versions.find((item) => item.version_id === localVersion);
  const selectedReview = reviews.find((item) => item.review_id === localReview);
  const investigationSignature = `${investigation?.id ?? ""}:${versions.map((item) => `${item.version_id}:${item.content_hash}`).join(",")}:${JSON.stringify(reviews)}`;
  const invalidate = () => {
    generation.current += 1;
    contextRef.current = null;
    setPrepared(null);
    setAccepted(false);
    setReadResult(null);
    setReadPages({ versions: [0], reviews: [0] });
    setPreflight(null);
    setError("");
    setNotice("");
  };
  useEffect(() => {
    mounted.current = true;
    let cancelled = false;
    void botApi.networks().then((result) => { if (!cancelled) setNetworks(result); }).catch((failure) => { if (!cancelled) setError(message(failure)); });
    void loadVerificationIntents().then((result) => { if (!cancelled) { setIntents(result); setSelectedIntentId(result[0]?.intent_id ?? ""); } })
      .catch((failure) => { if (!cancelled) setIntentStoreError(message(failure)); });
    return () => { cancelled = true; mounted.current = false; generation.current += 1; contextRef.current = null; };
  }, []);
  useLayoutEffect(() => {
    invalidate();
    setLocalVersion(versions[0]?.version_id ?? 0);
    setLocalReview(reviews.find((item) => item.review_id && item.version_id === versions[0]?.version_id)?.review_id ?? 0);
    setOnchainVersion("");
    setOnchainReview("");
  }, [investigationSignature]);
  useEffect(() => {
    providerRef.current = provider;
    if (!provider?.on) return;
    const accountsChanged = (value: unknown) => {
      walletEpoch.current += 1;
      invalidate();
      try { setAccount(parseAccounts(value)[0] ?? ""); } catch { setAccount(""); setError("Invalid wallet account update. Reconnect your wallet."); }
    };
    const chainChanged = (value: unknown) => {
      walletEpoch.current += 1;
      invalidate();
      try { setWalletChain(parseChainId(value)); } catch { setWalletChain(null); setError("Invalid wallet network update. Reconnect your wallet."); }
    };
    const disconnected = () => { walletEpoch.current += 1; invalidate(); setAccount(""); setWalletChain(null); };
    provider.on("accountsChanged", accountsChanged);
    provider.on("chainChanged", chainChanged);
    provider.on("disconnect", disconnected);
    return () => {
      provider.removeListener?.("accountsChanged", accountsChanged);
      provider.removeListener?.("chainChanged", chainChanged);
      provider.removeListener?.("disconnect", disconnected);
    };
  }, [provider]);
  const saveRecord = (entry: BotPublicRecord) => {
    const previous = [...loadHistory(), ...recordsRef.current];
    const next = [entry];
    const seen = new Set([`${entry.chain_id}:${entry.transaction_hash.toLowerCase()}`]);
    for (const item of previous) {
      const key = `${item.chain_id}:${item.transaction_hash.toLowerCase()}`;
      if (!seen.has(key)) { next.push(item); seen.add(key); }
      if (next.length === 50) break;
    }
    recordsRef.current = next;
    try { localStorage.setItem(historyKey, JSON.stringify({ schema_version: 1, records: next })); } catch { /* Current session records remain visible. */ }
    if (mounted.current) setRecords(next);
  };
  const refreshIntents = async () => {
    const result = await loadVerificationIntents();
    if (mounted.current) { setIntents(result); setIntentStoreError(""); }
    return result;
  };
  const selectVerificationIntent = (intent: VerificationIntent) => {
    verificationEpoch.current += 1;
    verificationIntentRef.current = intent;
    setVerificationIntent(intent);
    setSelectedIntentId(intent.intent_id);
    setTransactionHash(intent.transaction_hash ?? "");
    setVerification(null);
    setVerificationError("");
  };
  const syncVerificationIntent = (intent: VerificationIntent) => {
    if (mounted.current && verificationIntentRef.current?.intent_id === intent.intent_id) {
      verificationIntentRef.current = intent;
      setVerificationIntent(intent);
    }
  };
  const run = async (label: string, operation: () => Promise<void>, scope: "session" | "form" = "session") => {
    if (busyRef.current) return;
    busyRef.current = true;
    const started = generation.current;
    setBusy(label);
    setError("");
    try { await operation(); } catch (failure) { if (mounted.current && (scope === "session" || started === generation.current)) setError(message(failure)); }
    finally { busyRef.current = false; if (mounted.current) setBusy(""); }
  };
  const stableSnapshot = async (available: WalletProvider): Promise<WalletSnapshot> => {
    const epoch = walletEpoch.current;
    const snapshot = await walletSnapshot(available);
    if (epoch !== walletEpoch.current || providerRef.current !== available || !mounted.current)
      throw new Error("The wallet account or network changed. Read the connection status again.");
    return snapshot;
  };
  const applySnapshot = (snapshot: WalletSnapshot) => { setAccount(snapshot.account); setWalletChain(snapshot.chainId); };
  const connect = () => run("Connect wallet", async () => {
    const available = walletProvider();
    if (!available) throw new Error("Enable an EIP-1193 wallet in this browser, then connect.");
    invalidate();
    providerRef.current = available;
    setProvider(available);
    await connectWallet(available);
    applySnapshot(await stableSnapshot(available));
    setNotice("Wallet connected. Check the selected network before preparing a transaction.");
  });
  const networkAction = (kind: "switch" | "add") => run(kind === "switch" ? "Switch network" : "Add network", async () => {
    if (!provider || !network) throw new Error("Connect your wallet and load the BOT network configuration first.");
    invalidate();
    if (kind === "switch") await switchWalletNetwork(provider, network);
    else await addWalletNetwork(provider, network);
    applySnapshot(await stableSnapshot(provider));
    setNotice("Wallet network loaded. Check that its chain ID matches the selected network.");
  });
  const prepare = () => run("Prepare transaction", async () => {
    if (!provider || !network || !account) throw new Error("Connect your wallet and select a BOT network first.");
    contextRef.current = null;
    setPrepared(null);
    setAccepted(false);
    setNotice("");
    const current = generation.current;
    const isCurrent = () => generation.current === current && mounted.current;
    const snapshot = await stableSnapshot(provider);
    if (!isCurrent()) return;
    if (snapshot.chainId !== chainId || snapshot.account.toLowerCase() !== account.toLowerCase()) throw new Error("The wallet account or network does not match this form. Reconnect or switch networks.");
    const body: BotPrepareRequest = { chain_id: chainId, action, account: snapshot.account };
    if (action !== "deploy") {
      if (!isAddress(contractAddress)) throw new Error("Enter a valid registry contract address on the current network.");
      if (!investigation || !/^[A-Za-z0-9_-]{1,64}$/.test(investigation.id)) throw new Error("Open a saved case first.");
      if (!selectedVersion) throw new Error("Select a saved local version.");
      body.contract_address = contractAddress;
      body.case_id = investigation.id;
      body.local_version_id = selectedVersion.version_id;
      if (action !== "create_case") {
        if (!isDecimalId(onchainVersion)) throw new Error("Enter the on-chain version ID verified by the contract getter.");
        body.onchain_version_id = onchainVersion;
      }
      if (action === "add_review") {
        if (!selectedReview?.review_id || selectedReview.version_id !== selectedVersion.version_id) throw new Error("Select a saved review bound to this local version.");
        body.local_review_id = selectedReview.review_id;
      }
      if (evidenceUri.trim()) body.evidence_uri = evidenceUri.trim();
    }
    const fingerprint = JSON.stringify(body);
    let checked = preflight?.fingerprint === fingerprint ? preflight.result : null;
    let response: BotPrepared;
    try {
      if (action === "add_review") {
        checked = await botApi.reviewPreflight(body, checked?.cursor);
        if (!isCurrent()) return;
        setPreflight({ fingerprint, result: checked });
        if (checked.status !== "ready") return;
      }
      response = await botApi.prepare({ ...body, ...(checked ? { review_preflight_cursor: checked.cursor } : {}) });
    } catch (failure) {
      if (!isCurrent()) return;
      const code = failure instanceof Error ? failure.message : "";
      if (code === "review_preflight_changed" && checked) {
        setPreflight({ fingerprint, result: { ...checked, status: "scanning" } });
        setNotice("New reviews were registered on-chain. Continue checking the new records before preparing the transaction.");
        return;
      }
      if (["review_preflight_required", "review_preflight_expired", "review_preflight_invalid", "review_preflight_mismatch"].includes(code)) {
        setPreflight(null);
        setNotice("The review snapshot changed or expired. Prepare again to check from the first page.");
        return;
      }
      throw failure;
    }
    if (!isCurrent()) return;
    if (selectedVersion && action !== "deploy" && response.commitments.content_hash?.replace(/^0x/, "").toLowerCase() !== selectedVersion.content_hash.replace(/^0x/, "").toLowerCase())
      throw new Error("The content hash to sign does not match the selected local version.");
    if (selectedReview && action === "add_review" && "review_hash" in selectedReview && typeof selectedReview.review_hash === "string" &&
        response.commitments.review_hash?.replace(/^0x/, "").toLowerCase() !== selectedReview.review_hash.replace(/^0x/, "").toLowerCase())
      throw new Error("The review hash to sign does not match the selected local record.");
    if (generation.current !== current || !mounted.current) throw new Error("The account, network, or case changed during preparation. Prepare again.");
    const intent = await saveVerificationIntent(body);
    await refreshIntents();
    if (generation.current !== current || !mounted.current) throw new Error("The account, network, or case changed while saving. The read-only verification intent was saved. Prepare fresh signing parameters.");
    const context = { intent_id: intent.intent_id, request: intent.request, response, generation: current };
    submittedContext.current = null;
    contextRef.current = context;
    setPrepared(context);
    selectVerificationIntent(intent);
    setNotice("Transaction prepared. Check the parameters, then review and sign in your wallet.");
  }, "form");
  const sign = () => run("Awaiting wallet review", async () => {
    const context = contextRef.current;
    if (!provider || !context || !accepted) throw new Error("Check and confirm the current transaction parameters first.");
    const storedOutcome = loadWalletOutcome();
    if (walletOutcome || storedOutcome) {
      if (storedOutcome) setWalletOutcome(storedOutcome);
      throw new Error("Check the previous wallet activity and clear the pending-outcome notice before reviewing a new signature.");
    }
    if (submittedContext.current === context) throw new Error("A transaction hash already exists for these parameters. Read that transaction first.");
    const durableIntent = await getVerificationIntent(context.intent_id);
    if (JSON.stringify(durableIntent.request) !== JSON.stringify(context.request) || durableIntent.transaction_hash || durableIntent.status !== "ready")
      throw new Error("This verification intent has a linked outcome or changed content. Use read-only receipt verification.");
    const currentProvider = provider;
    submittedContext.current = context;
    setAccepted(false);
    const outcome: WalletOutcome = { chain_id: context.request.chain_id, action: context.request.action,
      attempt_id: crypto.randomUUID(), status: "attempting" };
    try {
      localStorage.setItem(outcomeKey, JSON.stringify({ schema_version: 1, ...outcome }));
      if (loadWalletOutcome()?.attempt_id !== outcome.attempt_id) throw new Error("Wallet outcome persistence unavailable");
    } catch { throw new Error("This browser could not save the wallet submission state. Check local storage availability, then prepare again."); }
    setWalletOutcome(outcome);
    syncVerificationIntent(await updateVerificationIntent(context.intent_id, { status: "uncertain" }));
    await refreshIntents();
    const clearAttempt = () => {
      try { if (loadWalletOutcome()?.attempt_id === outcome.attempt_id) localStorage.removeItem(outcomeKey); } catch { /* Current session state is retained. */ }
      if (mounted.current) setWalletOutcome(loadWalletOutcome());
    };
    let result;
    let knownHash = "";
    try {
      result = await sendWalletTransaction(currentProvider, context.response,
        () => mounted.current && providerRef.current === currentProvider && contextRef.current === context && generation.current === context.generation,
        async (hash) => {
          knownHash = hash;
          saveRecord({ chain_id: context.request.chain_id, transaction_hash: hash, action: context.request.action,
            status: "submitted", contract_address: context.request.contract_address, case_id: context.request.case_id,
            local_version_id: context.request.local_version_id, onchain_version_id: context.request.onchain_version_id });
          if (mounted.current && verificationIntentRef.current?.intent_id === context.intent_id) setTransactionHash(hash);
          const saved = await updateVerificationIntent(context.intent_id, { transaction_hash: hash, status: "submitted" });
          syncVerificationIntent(saved);
          await refreshIntents();
        });
    } catch (failure) {
      const rejected = !!failure && typeof failure === "object" && "code" in failure && failure.code === 4001;
      if (rejected) {
        syncVerificationIntent(await updateVerificationIntent(context.intent_id, { status: "rejected" }));
        await refreshIntents();
        clearAttempt();
        throw new Error("Wallet request cancelled. Prepare a new transaction when you are ready to review it.");
      }
      const unknown: WalletOutcome = { ...outcome, status: "unknown" };
      try { if (loadWalletOutcome()?.attempt_id === outcome.attempt_id) localStorage.setItem(outcomeKey, JSON.stringify({ schema_version: 1, ...unknown })); } catch { /* Keep the warning in the active session. */ }
      if (mounted.current) setWalletOutcome(unknown);
      try { syncVerificationIntent(await updateVerificationIntent(context.intent_id, { status: "uncertain" })); await refreshIntents(); }
      catch (storeFailure) { if (mounted.current) setIntentStoreError(message(storeFailure)); }
      if (knownHash) throw new Error("The wallet returned a transaction hash, but its verification link could not be saved. Save the hash, check local storage, then restore the original verification intent.");
      throw new Error("The wallet did not return a verifiable transaction hash. Check wallet activity on the relevant network, then enter the hash and read the receipt. These parameters are locked.");
    }
    clearAttempt();
    saveRecord({ chain_id: context.request.chain_id, transaction_hash: result.transactionHash, action: context.request.action,
      status: "submitted", contract_address: context.request.contract_address, case_id: context.request.case_id,
      local_version_id: context.request.local_version_id, onchain_version_id: context.request.onchain_version_id });
    if (!mounted.current) return;
    setTransactionHash(result.transactionHash);
    setAccepted(false);
    setNotice(result.walletChanged
      ? "The wallet returned a transaction hash and the account or network changed. Save the hash and read it on the original network. The signing parameters have been cleared."
      : "The wallet returned a transaction hash. Select Read receipt and verify to confirm the result.");
  });
  const restoreIntent = () => run("Restore read-only verification", async () => {
    if (!selectedIntentId) throw new Error("Select a locally saved verification intent.");
    const intent = await getVerificationIntent(selectedIntentId);
    invalidate();
    selectVerificationIntent(intent);
    setNotice("Original verification intent restored. Verification uses its recorded network and public sender. Wallet connection and transaction preparation are not required.");
  });
  const copyIntent = () => run("Copy verification intent", async () => {
    const selected = verificationIntentRef.current;
    if (!selected) throw new Error("Restore the verification intent to copy first.");
    const original = await getVerificationIntent(selected.intent_id);
    const copied = await saveVerificationIntent(original.request);
    await refreshIntents();
    invalidate();
    selectVerificationIntent(copied);
    setNotice("A separate read-only verification intent was created. Enter another transaction hash to inspect it. The original record is preserved.");
  });
  const bindHash = () => run("Save read-only hash link", async () => {
    const selected = verificationIntentRef.current;
    if (!selected || !isTransactionHash(transactionHash)) throw new Error("Restore a verification intent and enter a valid transaction hash first.");
    const saved = await updateVerificationIntent(selected.intent_id, { transaction_hash: transactionHash,
      status: selected.status === "ready" ? "submitted" : selected.status });
    await refreshIntents();
    selectVerificationIntent(saved);
    if (contextRef.current?.intent_id === selected.intent_id) { submittedContext.current = contextRef.current; setAccepted(false); }
    setNotice("The hash is linked to the original public verification intent. Read the receipt to verify the on-chain match.");
  });
  const verify = () => run("Read receipt", async () => {
    const selected = verificationIntentRef.current;
    if (!selected || !isTransactionHash(transactionHash)) throw new Error("Restore the original verification intent and enter a transaction hash.");
    const hash = transactionHash.toLowerCase();
    const current = verificationEpoch.current;
    const formGeneration = generation.current;
    const isVerificationCurrent = () => current === verificationEpoch.current &&
      verificationIntentRef.current?.intent_id === selected.intent_id && mounted.current;
    const intent = await getVerificationIntent(selected.intent_id);
    if (intent.binding_sha256 !== selected.binding_sha256 || (intent.transaction_hash && intent.transaction_hash.toLowerCase() !== hash))
      throw new Error("The verification intent or its linked hash changed. Restore the corresponding record again.");
    setVerificationError("");
    setNotice("");
    let result: BotVerification;
    try {
      result = await botApi.verify(hash, intent.request, 3);
    } catch (failure) {
      if (!isVerificationCurrent()) return;
      const retryable = (failure instanceof ApiError && failure.status === 503) ||
        failure instanceof TypeError || (failure instanceof Error && ["TimeoutError", "AbortError"].includes(failure.name));
      setVerificationError(retryable
        ? "This receipt read could not be completed. The transaction hash, verification intent, and saved state are preserved. Retry the receipt read."
        : message(failure));
      return;
    }
    if (!isVerificationCurrent()) return;
    setVerification(result);
    const matched = result.status === "verified" || result.status === "failed" ||
      (result.status === "pending" && result.reason === "confirmations_pending");
    if (matched || intent.transaction_hash) {
      const saved = await updateVerificationIntent(intent.intent_id, { transaction_hash: hash, status: result.status });
      if (!isVerificationCurrent()) return;
      verificationIntentRef.current = saved;
      setVerificationIntent(saved);
      await refreshIntents();
    }
    if (!isVerificationCurrent()) return;
    if (matched && contextRef.current?.intent_id === intent.intent_id) {
      submittedContext.current = contextRef.current;
      setAccepted(false);
    }
    const entry: BotPublicRecord = { chain_id: intent.request.chain_id, transaction_hash: result.transaction_hash, action: intent.request.action,
      status: result.status, contract_address: result.status === "verified" ? result.contract_address ?? intent.request.contract_address : intent.request.contract_address,
      case_id: intent.request.case_id, local_version_id: intent.request.local_version_id, onchain_version_id: result.onchain_ids?.version_id ?? intent.request.onchain_version_id,
      onchain_review_id: result.onchain_ids?.review_id };
    saveRecord(entry);
    if (result.status === "verified" && formGeneration === generation.current && chainId === intent.request.chain_id &&
        (intent.request.action === "deploy" || investigation?.id === intent.request.case_id)) {
      const nextContract = result.contract_address ?? intent.request.contract_address ?? contractAddress;
      const contractChanged = nextContract.toLowerCase() !== contractAddress.toLowerCase();
      const nextVersion = result.onchain_ids?.version_id ?? (contractChanged ? "" : onchainVersion);
      const nextReview = result.onchain_ids?.review_id ??
        (contractChanged || nextVersion !== onchainVersion ? "" : onchainReview);
      if (contractChanged || nextVersion !== onchainVersion || nextReview !== onchainReview) {
        invalidate();
        setContractAddress(nextContract);
        setOnchainVersion(nextVersion);
        setOnchainReview(nextReview);
      }
    }
    setNotice(`${verificationLabels[result.status]} · ${result.confirmations}/${result.minimum_confirmations} confirmations.`);
  });
  const read = (page?: { kind: "versions" | "reviews"; direction: "previous" | "next" }) => run("Read contract", async () => {
    if (!isAddress(contractAddress) || !investigation) throw new Error("Open a case and enter its registry contract address.");
    if (onchainVersion && !isDecimalId(onchainVersion)) throw new Error("The on-chain version ID must be a positive integer.");
    if (onchainReview && !isDecimalId(onchainReview)) throw new Error("The on-chain review ID must be a positive integer.");
    const current = generation.current;
    const paths = page ? { ...readPages } : { versions: [0], reviews: [0] };
    if (page) {
      if (!readResult) return;
      const path = paths[page.kind];
      if (page.direction === "previous") {
        if (path.length < 2) return;
        paths[page.kind] = path.slice(0, -1);
      } else {
        const next = readResult.getter_state.inventory[page.kind].next_offset;
        if (next === null || !Number.isSafeInteger(Number(next))) return;
        paths[page.kind] = [...path, Number(next)];
      }
    } else {
      setReadResult(null);
      setReadPages(paths);
    }
    let result: BotReadResult;
    try {
      result = await botApi.read({ chain_id: chainId, contract_address: contractAddress, case_id: investigation.id,
        version_offset: paths.versions[paths.versions.length - 1], review_offset: paths.reviews[paths.reviews.length - 1],
        ...(page && readResult ? { read_block_number: readResult.read_block_number, read_block_hash: readResult.read_block_hash } : {}),
        ...(onchainVersion ? { version_id: onchainVersion } : {}), ...(onchainReview ? { review_id: onchainReview } : {}) });
    } catch (failure) {
      if (current !== generation.current || !mounted.current) return;
      if (failure instanceof Error && failure.message === "read_block_changed") {
        setReadResult(null);
        setReadPages({ versions: [0], reviews: [0] });
      }
      throw failure;
    }
    if (current !== generation.current || !mounted.current) return;
    setReadResult(result);
    setReadPages(paths);
    setNotice(result.case_status === "unregistered"
      ? "The contract getter confirms that this case is unregistered. Select the original v1 to prepare registration."
      : "The case and selected records were read from the contract. Check their content hashes and version relationships.");
  }, "form");
  const feesReady = !!prepared?.response.transaction.gas && !!prepared.response.transaction.gasPrice && prepared.response.fees.estimated_max_fee_wei !== null;
  const signDisabled = !!busy || !!walletOutcome || !!intentStoreError || !accepted || !feesReady || prepared?.response.fees.sufficient_balance !== true || prepared.response.fees.balance_wei === null || walletChain !== chainId || !account || submittedContext.current === prepared ||
    (!!verificationIntent?.transaction_hash && verificationIntent.intent_id === prepared?.intent_id);
  const change = (operation: () => void) => { invalidate(); operation(); };
  return <section className="panel bot-panel" data-testid="bot-panel" aria-labelledby="bot-heading">
    <div className="bot-heading-row"><div><h2 id="bot-heading">BOT on-chain registry</h2><p className="muted">Your wallet signs · Contract getters verify · Content commitments and version history</p></div>
      <span className="bot-network-badge">{chainId === 968 ? "Testnet · 968" : "Mainnet · 677"}</span></div>
    <p className="bot-boundary">Complete deployment, v1, review, and v2 on testnet and save accessible transaction links before preparing a mainnet gas support application. Review and sign each real transaction in your own wallet.</p>
    <div className="bot-grid">
      <div className="subpanel bot-form"><h3>Network and wallet</h3>
        <label>Target network<select data-testid="bot-network" value={chainId} disabled={!!busy} onChange={(event) => change(() => { setChainId(Number(event.target.value) as BotChainId); setContractAddress(""); setOnchainVersion(""); setOnchainReview(""); })}>
          <option value={968}>BOT Testnet · 968</option><option value={677}>BOT Mainnet · 677</option></select></label>
        <p className="bot-wallet-status">Account: <span className="mono">{account || "Not connected"}</span><br />Wallet network: {walletChain ?? "Not loaded"}{walletChain !== null && walletChain !== chainId ? " · Switch to the target network" : ""}</p>
        <div className="bot-actions"><button type="button" className="button outline" data-testid="bot-connect" disabled={!!busy} onClick={() => void connect()}>Connect wallet</button>
          <button type="button" className="button outline" data-testid="bot-switch-network" disabled={!!busy || !account || !network} onClick={() => void networkAction("switch")}>Switch to selected network</button>
          <button type="button" className="button outline" data-testid="bot-add-network" disabled={!!busy || !account || !network} onClick={() => void networkAction("add")}>Add network to wallet</button></div>
        {network ? <dl className="bot-parameters"><dt>RPC</dt><dd className="mono">{network.rpc_url}</dd><dt>Explorer</dt><dd><a href={network.explorer_url} target="_blank" rel="noopener noreferrer">{network.explorer_url}</a></dd><dt>Native currency</dt><dd>BOT · 18 decimals</dd></dl> : <p className="muted">Waiting for the local API network configuration.</p>}
        {chainId === 677 ? <p className="bot-warning">Official documentation confirms the mainnet symbol BOT. The currency name is a display label; 18 decimals are inferred from official testnet configuration and the official client definition of Ether as 10¹⁸ units. Check these details before adding the network.</p> : null}
        <p className="muted">Mainnet records are read through contract getters. Receipt verification requires at least 3 block confirmations. Reorganizations can change the confirmation count.</p>
      </div>
      <div className="subpanel bot-form"><h3>Registration details</h3>
        <label>Action<select data-testid="bot-action" value={action} disabled={!!busy} onChange={(event) => change(() => { if (isBotAction(event.target.value)) setAction(event.target.value); })}>{Object.entries(actionLabels).map(([value, label]) => <option value={value} key={value}>{label}</option>)}</select></label>
        <label>Registry contract<input data-testid="bot-contract" className="mono" placeholder="Filled after verified deployment, or enter an existing contract" value={contractAddress} disabled={!!busy} maxLength={42} onChange={(event) => change(() => { setContractAddress(event.target.value.trim()); setOnchainVersion(""); setOnchainReview(""); })} /></label>
        <p className="bot-case-name">Current case: <strong>{investigation ? caseTitle(investigation.title, investigation.id) : "Open a saved investigation first"}</strong></p>
        <label>Local version<select data-testid="bot-local-version" value={localVersion} disabled={!!busy || !versions.length} onChange={(event) => change(() => { setLocalVersion(Number(event.target.value)); setLocalReview(0); })}>
          {!versions.length ? <option value={0}>No saved versions</option> : versions.map((version, index) => <option key={version.version_id} value={version.version_id}>v{index + 1} · Local ID  {version.version_id}</option>)}</select></label>
        <label>On-chain version ID<input data-testid="bot-onchain-version" value={onchainVersion} disabled={!!busy} inputMode="numeric" maxLength={78} placeholder={action === "append_version" ? "On-chain ID of this case’s current parent version" : "ID from a verified receipt or contract getter"} onChange={(event) => change(() => { setOnchainVersion(event.target.value.trim()); setOnchainReview(""); })} /></label>
        <p className="muted">Local version IDs and global contract version IDs are recorded separately. Reviews bind to a selected version. Corrections extend the current parent version of the same case.</p>
        {action === "add_review" ? <label>Local review<select data-testid="bot-local-review" value={localReview} disabled={!!busy} onChange={(event) => change(() => setLocalReview(Number(event.target.value)))}>
          <option value={0}>Select a review bound to this version</option>{reviews.filter((review) => review.review_id && review.version_id === localVersion).map((review) => <option key={review.review_id} value={review.review_id}>{review.review_id} · {review.reviewer} · Original review: {(review.comment ?? review.decision ?? "").slice(0, 50)}</option>)}</select></label> : null}
        <label>On-chain review ID<input value={onchainReview} disabled={!!busy} maxLength={78} inputMode="numeric" placeholder="Enter an ID to read a review" onChange={(event) => change(() => setOnchainReview(event.target.value.trim()))} /></label>
        <label>Public evidence URI<input value={evidenceUri} disabled={!!busy} maxLength={512} placeholder="Optional: a shareable evidence location" onChange={(event) => change(() => setEvidenceUri(event.target.value))} /></label>
        <div className="bot-actions"><button type="button" data-testid="bot-prepare" className="button primary" disabled={!!busy || !network || !account || walletChain !== chainId || preflight?.result.status === "duplicate"} onClick={() => void prepare()}>{preflight?.result.status === "scanning" ? "Continue check" : "Prepare unsigned transaction"}</button>
          <button type="button" data-testid="bot-read" className="button outline" disabled={!!busy || !network || !investigation || !isAddress(contractAddress)} onClick={() => void read()}>Read contract getters</button></div>
      </div>
    </div>
    {preflight ? <div className="subpanel" data-testid="bot-preflight" role="status">
      <strong>{preflight.result.status === "duplicate" ? "Review already exists; transaction preparation stopped" : preflight.result.status === "ready" ? "Review check complete" : "Checking existing reviews page by page"}</strong>
      <p>Checked  {preflight.result.scanned_count} / {preflight.result.total_count} records · Block  {preflight.result.read_block_number}</p>
      {preflight.result.status === "duplicate" ? <p>On-chain review ID {preflight.result.existing_review_id}. Read the contract getter to inspect this review.</p> : null}
      {preflight.result.status === "scanning" ? <p>Select Continue check to read the next page. The transaction is prepared once the check completes.</p> : null}
      <button type="button" className="button outline" data-testid="bot-preflight-restart" disabled={!!busy} onClick={() => change(() => undefined)}>Restart check</button>
    </div> : null}
    {prepared ? <div className="subpanel bot-prepared" data-testid="bot-prepared"><h3>Review transaction</h3>
      <dl className="bot-parameters"><dt>Action / Network</dt><dd>{actionLabels[prepared.request.action]} · {prepared.response.network.name} ({prepared.request.chain_id})</dd><dt>Estimated maximum fee</dt><dd>{formatBot(prepared.response.fees.estimated_max_fee_wei)}</dd><dt>Balance</dt><dd>{formatBot(prepared.response.fees.balance_wei)}{prepared.response.fees.sufficient_balance === false ? " · Insufficient balance" : ""}</dd><dt>Gas limit / Price</dt><dd>{prepared.response.fees.gas_limit ?? "Pending"} / {prepared.response.fees.gas_price_wei ?? "Pending"} wei</dd></dl>
      <p className="muted">The maximum fee uses current gas estimates. Final transaction parameters and costs are confirmed in your wallet and the on-chain receipt.</p>
      {prepared.request.chain_id === 677 ? <p className="bot-warning">This is a real mainnet transaction and will spend BOT. Check the mainnet contract, wallet account, and estimated fee.</p> : null}
      {prepared.response.fees.balance_wei === null ? <p className="bot-warning">The balance read is incomplete. Check RPC availability and prepare again.</p> : null}
      {!feesReady ? <p className="bot-warning">The fee estimate is incomplete. Check RPC availability and prepare again.</p> : null}
      {prepared.response.warnings.length ? <ul className="bot-warnings">{[...new Set(prepared.response.warnings.map(warningText))].map((warning) => <li key={warning}>{warning}</li>)}</ul> : null}
      <label className="bot-json-label">Full unsigned transaction parameters<textarea className="mono" aria-label="Full unsigned transaction parameters" readOnly rows={8} value={JSON.stringify(prepared.response.transaction, null, 2)} /></label>
      <details><summary>Content commitments, contract build, and notices</summary><pre className="mono">{JSON.stringify({ commitments: prepared.response.commitments, artifact: prepared.response.artifact, warning_codes: prepared.response.warnings }, null, 2)}</pre></details>
      <label className="bot-consent"><input type="checkbox" checked={accepted} disabled={!!busy} onChange={(event) => setAccepted(event.target.checked)} /><span className="bot-consent-text">I have checked the network, account, contract, content commitments, and estimated fee.</span></label>
      <button type="button" className="button primary" data-testid="bot-sign" disabled={signDisabled} onClick={() => void sign()}>Review and sign in wallet</button>
    </div> : null}
    <div className="subpanel bot-receipt"><h3>Transaction verification</h3>
      <label className="bot-json-label">Local verification intent<select data-testid="bot-intent-select" value={selectedIntentId} disabled={!!busy || !intents.length} onChange={(event) => setSelectedIntentId(event.target.value)}>
        <option value="">Select saved public verification parameters</option>{intents.map((intent) => <option value={intent.intent_id} key={intent.intent_id}>{actionLabels[intent.request.action]} · {intent.request.chain_id} · {intentStatusLabel(intent)} · {intent.transaction_hash?.slice(0, 12) ?? intent.intent_id.slice(0, 8)}</option>)}
      </select></label>
      <button type="button" className="button outline" data-testid="bot-intent-restore" disabled={!!busy || !selectedIntentId} onClick={() => void restoreIntent()}>Restore verification intent</button>
      {verificationIntent ? <div data-testid="bot-verification-intent"><p className="muted">Read-only verification uses the original public parameters below. They are shown separately from your current wallet account and target network. Restore the record to read its receipt directly.</p>
        <dl className="bot-parameters"><dt>Original network / Action</dt><dd>{verificationIntent.request.chain_id} · {actionLabels[verificationIntent.request.action]}</dd><dt>Original sender</dt><dd className="mono">{verificationIntent.request.account}</dd><dt>Original contract</dt><dd className="mono">{verificationIntent.request.contract_address ?? "Create a new contract"}</dd><dt>Case / Local version</dt><dd>{verificationIntent.request.case_id ?? "—"} / {verificationIntent.request.local_version_id ?? "—"}</dd><dt>On-chain version / Review</dt><dd>{verificationIntent.request.onchain_version_id ?? "—"} / Local review  {verificationIntent.request.local_review_id ?? "—"}</dd><dt>Linked transaction hash</dt><dd className="mono">{verificationIntent.transaction_hash ?? "Not linked"}</dd></dl>
        <details><summary>Verification intent details</summary><pre className="mono">{JSON.stringify(verificationIntent, null, 2)}</pre></details>
        <button type="button" className="button outline" data-testid="bot-intent-copy" disabled={!!busy} onClick={() => void copyIntent()}>Create a separate verification intent</button>
      </div> : <p className="muted">After a refresh, select the original intent to read its receipt without preparing the executed transaction again.</p>}
      <label className="bot-json-label">Transaction hash<input data-testid="bot-transaction-hash" className="mono" value={transactionHash} disabled={!!busy} readOnly={!!verificationIntent?.transaction_hash} maxLength={66} placeholder="0x… 32-byte hash returned by your wallet" onChange={(event) => { verificationEpoch.current += 1; setTransactionHash(event.target.value.trim()); setVerification(null); setVerificationError(""); }} /></label>
      <button type="button" className="button outline" data-testid="bot-bind-hash" disabled={!!busy || !verificationIntent || !!verificationIntent.transaction_hash || !isTransactionHash(transactionHash)} onClick={() => void bindHash()}>Save read-only link to this hash</button>
      {walletOutcome ? <div className="bot-warning" data-testid="bot-wallet-outcome-warning" role="alert">
        <p>{walletOutcome.status === "attempting" && busy === "Awaiting wallet review"
          ? `Wallet review in progress: ${actionLabels[walletOutcome.action]} · Network  ${walletOutcome.chain_id}. Collapsing this panel preserves the submission state. Wait for the wallet result.`
          : `The previous wallet outcome needs review: ${actionLabels[walletOutcome.action]} · Network  ${walletOutcome.chain_id}. Check wallet activity and the transaction history on that network. Read the receipt once you have the hash before preparing again. Preparing new parameters preserves this notice.`}</p>
        <button type="button" className="button outline" disabled={!!busy} data-testid="bot-wallet-outcome-ack" onClick={() => {
          try { localStorage.removeItem(outcomeKey); } catch { /* Current session acknowledgement still applies. */ }
          setWalletOutcome(null);
        }}>I checked wallet activity; clear notice</button>
      </div> : null}
      <button type="button" className="button outline" data-testid="bot-verify" disabled={!!busy || !verificationIntent || !isTransactionHash(transactionHash)} onClick={() => void verify()}>Read receipt and verify</button>
      {transactionHash && verificationNetwork && isTransactionHash(transactionHash) ? <a className="text-button bot-explorer-link" href={explorerLink(verificationNetwork, "tx", transactionHash)} target="_blank" rel="noopener noreferrer">View transaction on its original network</a> : null}
      {verificationError ? <p className="error-banner" role="alert" data-testid="bot-verification-error">{verificationError}</p> : null}
      {verification ? <div className={`bot-verification bot-${verification.status}`} data-testid="bot-verification"><strong>{verificationLabels[verification.status]}</strong><p>{verificationReason(verification.reason) || `${verification.confirmations}/${verification.minimum_confirmations} confirmations`}</p><pre className="mono">{JSON.stringify({ contract_address: verification.contract_address, onchain_ids: verification.onchain_ids, getter_state: verification.getter_state }, null, 2)}</pre></div> : null}
    </div>
    {readResult ? <details className="subpanel bot-read-result" open data-testid="bot-read-result"><summary>Read-only contract results</summary>
      {readResult.case_status === "unregistered" ? <p role="status">This case is not registered in the selected network and contract. Select the original v1 to prepare registration.</p> : null}
      <p className="muted" data-testid="bot-read-block">Pinned read block  {readResult.read_block_number} · <span className="mono">{readResult.read_block_hash}</span></p>
      {(["versions", "reviews"] as const).map((kind) => {
        const inventory = readResult.getter_state.inventory[kind];
        const count = readResult.getter_state[kind].length;
        const label = kind === "versions" ? "Versions" : "Reviews";
        return <div key={kind} data-testid={`bot-${kind}-pagination`}>
          <p>{label} list: {count ? `${BigInt(inventory.offset) + 1n}–${BigInt(inventory.offset) + BigInt(count)}` : "0"} / {inventory.total_count} records</p>
          <div className="bot-actions">
            <button type="button" className="button outline" data-testid={`bot-${kind}-previous`} disabled={!!busy || readPages[kind].length < 2} onClick={() => void read({ kind, direction: "previous" })}>{label} previous page</button>
            <button type="button" className="button outline" data-testid={`bot-${kind}-next`} disabled={!!busy || inventory.next_offset === null || !Number.isSafeInteger(Number(inventory.next_offset))} onClick={() => void read({ kind, direction: "next" })}>{label} next page</button>
          </div>
        </div>;
      })}
      <pre className="mono">{JSON.stringify(readResult, null, 2)}</pre></details> : null}
    {busy ? <p className="inline-notice" role="status">{busy}…</p> : null}
    {notice ? <p className="inline-notice" role="status">{notice}</p> : null}
    {error ? <p className="error-banner" role="alert">{error}</p> : null}
    {intentStoreError ? <p className="error-banner" role="alert" data-testid="bot-intent-store-error">{intentStoreError}</p> : null}
    {records.length ? <details className="bot-records"><summary>Local public transaction records ({records.length})</summary><ul>{records.map((entry) => {
      const entryNetwork = networks.find((item) => item.chain_id === entry.chain_id);
      return <li key={`${entry.chain_id}:${entry.transaction_hash}`}><strong>{actionLabels[entry.action]} · {entry.chain_id} · {entry.status === "submitted" ? "Wallet returned a hash" : verificationLabels[entry.status]}</strong><br />{entryNetwork ? <a className="mono" href={explorerLink(entryNetwork, "tx", entry.transaction_hash)} target="_blank" rel="noopener noreferrer">{entry.transaction_hash}</a> : <span className="mono">{entry.transaction_hash}</span>}<br />Local version {entry.local_version_id ?? "—"} → On-chain version  {entry.onchain_version_id ?? "—"}{entry.onchain_review_id ? ` · On-chain review  ${entry.onchain_review_id}` : ""}</li>;
    })}</ul></details> : null}
    <p className="page-note">On-chain hashes and version relationships trace content. Findings still require evidence review. Public verification parameters, account addresses, and hash links are saved locally; signing parameters and fee estimates stay in page memory. Disclose when one operator controls multiple wallet roles.</p>
  </section>;
}
