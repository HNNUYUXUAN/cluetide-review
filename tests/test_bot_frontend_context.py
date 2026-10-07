"""Exercise BOT pagination, review preflight and receipt recovery with local HTTP fixtures."""

from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import shutil
import subprocess
from threading import Thread

import pytest


ROOT = Path(__file__).resolve().parents[1]
CONTRACT = "0x" + "2" * 40
OTHER_CONTRACT = "0x" + "3" * 40


@pytest.fixture(scope="module")
def bot_browser(tmp_path_factory):
    playwright = pytest.importorskip("playwright.sync_api")
    node = shutil.which("node")
    bundled_node = ROOT / ".tools/node-v24.14.0-win-x64/node.exe"
    if node is None and bundled_node.exists():
        node = str(bundled_node)
    esbuild = ROOT / "frontend/node_modules/esbuild/bin/esbuild"
    if node is None or not esbuild.exists():
        pytest.skip("BOT browser regression requires installed frontend dependencies and Node")
    directory = tmp_path_factory.mktemp("bot-context-browser")
    imports = {
        "REACT": str(ROOT / "frontend/node_modules/react/index.js"),
        "REACT_DOM": str(ROOT / "frontend/node_modules/react-dom/client.js"),
        "PANEL": str(ROOT / "frontend/src/components/BotPanel.tsx"),
        "WALLET": str(ROOT / "frontend/src/bot-wallet.ts"),
        "STORE": str(ROOT / "frontend/src/bot-verification-store.ts"),
    }
    source = r'''
import React, { useState } from REACT;
import { createRoot } from REACT_DOM;
import BotPanel from PANEL;
import { botApi } from WALLET;
import { saveVerificationIntent, updateVerificationIntent } from STORE;
const account = "0x" + "1".repeat(40), contract = "0x" + "2".repeat(40);
const listeners = new Map();
window.ethereum = {
  request: async ({ method }) => {
    if (method === "eth_accounts" || method === "eth_requestAccounts") return [account];
    if (method === "eth_chainId") return "0x3c8";
    throw new Error("Unexpected wallet request: " + method);
  },
  on: (name, listener) => listeners.set(name, listener),
  removeListener: (name) => listeners.delete(name),
};
const networks = [968, 677].map(chain_id => ({
  chain_id, chain_id_hex: "0x" + chain_id.toString(16), name: "BOT " + chain_id,
  rpc_url: chain_id === 968 ? "https://rpc.bohr.life" : "https://rpc.botchain.ai",
  explorer_url: chain_id === 968 ? "https://scan.bohr.life" : "https://scan.botchain.ai",
  native_currency: { name: "BOT", symbol: "BOT", decimals: 18 },
}));
const investigation = id => ({ id, title: id, status: "completed", evidence: null, agent: null, report: null,
  versions: [1, 2].map(version_id => ({ version_id, parent_version_id: version_id - 1,
    author: "author", content_hash: String(version_id).repeat(64) })),
  reviews: [1, 2].map(review_id => ({ review_id, version_id: 1, reviewer: "reviewer",
    comment: "核读证据后提交复核", review_hash: "f".repeat(64) })) });
const artifact = { contract_name: "Registry", compiler: "0.8.24", evm_version: "paris",
  source_sha256: "a".repeat(64), bytecode_sha256: "b".repeat(64), runtime_sha256: "c".repeat(64) };
const blockHash = "0x" + "c".repeat(64);
let complete, completeRead, completePreflight, completePrepare;
const calls = [];
const config = { deferRead: false, deferPreflight: false, deferPrepare: false, preflightTotal: 7, scanned: 0,
  readError: null, prepareError: null, preflightError: null, duplicate: false, verifyFailures: 0, transportFailure: false };
const json = (value, status = 200) => new Response(JSON.stringify(value), { status,
  headers: { "Content-Type": "application/json" } });
const inventoryPage = (offset, total, kind) => {
  const end = Math.min(offset + 3, total);
  return { items: Array.from({ length: end - offset }, (_, index) => ({ [kind + "_id"]: String(offset + index + 1) })),
    page: { offset: String(offset), total_count: String(total), next_offset: end < total ? String(end) : null,
      complete: offset === 0 && end === total } };
};
window.fetch = async (url, options) => {
  const body = options?.body ? JSON.parse(options.body) : {};
  if (url === "/api/bot/networks") return json({ networks });
  if (url === "/api/bot/verify") {
    calls.push({ kind: "verify", hash: body.transaction_hash, request: body.prepared });
    if (config.transportFailure) throw new TypeError("Failed to fetch");
    if (config.verifyFailures > 0) { config.verifyFailures--; return json({ detail: "bot_rpc_unavailable" }, 503); }
    return new Promise(resolve => { complete = result => resolve(json({ status: "verified", transaction_hash: body.transaction_hash,
      confirmations: 3, minimum_confirmations: 3, contract_address: contract, getter_state: {}, ...result })); });
  }
  if (url === "/api/bot/read") {
    calls.push({ kind: "read", request: body });
    const versions = inventoryPage(body.version_offset ?? 0, 7, "version");
    const reviews = inventoryPage(body.review_offset ?? 0, 5, "review");
    const result = { network: networks.find(n => n.chain_id === body.chain_id), contract_address: body.contract_address,
      case_id_hex: "0x" + "a".repeat(64), case_status: "registered", artifact,
      read_block_number: body.read_block_number ?? "120", read_block_hash: body.read_block_hash ?? blockHash,
      getter_state: { case: {}, versions: versions.items, reviews: reviews.items,
        inventory: { versions: versions.page, reviews: reviews.page } } };
    const error = config.readError; config.readError = null;
    const response = () => error ? json({ detail: error }, 409) : json(result);
    if (config.deferRead) return new Promise(resolve => { completeRead = () => resolve(response()); });
    return response();
  }
  if (url === "/api/bot/review-preflight") {
    calls.push({ kind: "preflight", request: body });
    if (config.preflightError) {
      const detail = config.preflightError; config.preflightError = null;
      return json({ detail }, config.preflightHttpStatus ?? 409);
    }
    config.scanned = Math.min(body.cursor ? config.scanned + 3 : 3, config.preflightTotal);
    const result = { cursor: "opaque-cursor", status: config.duplicate ? "duplicate" : config.scanned === config.preflightTotal ? "ready" : "scanning",
      scanned_count: String(config.scanned), total_count: String(config.preflightTotal), read_block_number: "120",
      read_block_hash: blockHash, existing_review_id: config.duplicate ? "5" : null };
    if (config.deferPreflight) return new Promise(resolve => { completePreflight = () => resolve(json(result)); });
    return json(result);
  }
  if (url === "/api/bot/prepare") {
    calls.push({ kind: "prepare", request: body });
    if (config.prepareError) {
      const detail = config.prepareError; config.prepareError = null;
      if (detail === "review_preflight_changed") config.preflightTotal += 3;
      return json({ detail }, 409);
    }
    const result = { network: networks.find(n => n.chain_id === body.chain_id), action: body.action,
      transaction: { from: body.account, to: body.contract_address, chainId: "0x3c8", data: "0x1234", value: "0x0", gas: "0x5208", gasPrice: "0x1" },
      commitments: { case_id_hex: "0x" + "a".repeat(64), content_hash: String(body.local_version_id).repeat(64),
        local_version_id: body.local_version_id, onchain_version_id: body.onchain_version_id,
        ...(body.action === "add_review" ? { local_review_id: body.local_review_id, review_hash: "f".repeat(64) } : {}) },
      fees: { gas_limit: "21000", gas_price_wei: "1", estimated_max_fee_wei: "21000", balance_wei: "50000000", sufficient_balance: true }, artifact, warnings: [] };
    if (config.deferPrepare) return new Promise(resolve => { completePrepare = () => resolve(json(result)); });
    return json(result);
  }
  throw new Error("Unexpected API request: " + url);
};
window.botFixture = {
  calls,
  configure: values => Object.assign(config, values),
  get pending() { return typeof complete === "function"; },
  get pendingRead() { return typeof completeRead === "function"; },
  get pendingPreflight() { return typeof completePreflight === "function"; },
  get pendingPrepare() { return typeof completePrepare === "function"; },
  complete: result => { complete(result); complete = undefined; },
  completeRead: () => { completeRead(); completeRead = undefined; },
  completePreflight: () => { completePreflight(); completePreflight = undefined; },
  completePrepare: () => { completePrepare(); completePrepare = undefined; },
  walletChanged: () => listeners.get("accountsChanged")?.(["0x" + "4".repeat(40)]),
  walletNetworkChanged: () => listeners.get("chainChanged")?.("0x2a5"),
  seed: async action => {
    const request = { chain_id: 968, action, account, contract_address: contract, case_id: "original-case",
      local_version_id: action === "append_version" ? 2 : 1,
      ...(action === "create_case" ? {} : { onchain_version_id: "1" }),
      ...(action === "add_review" ? { local_review_id: 1 } : {}) };
    const intent = await saveVerificationIntent(request);
    await updateVerificationIntent(intent.intent_id, { transaction_hash: "0x" + (action === "add_review" ? "a" : "b").repeat(64), status: "submitted" });
    return intent.intent_id;
  },
};
function Fixture() {
  const [current, setCurrent] = useState("original-case");
  return React.createElement(React.Fragment, null,
    React.createElement("button", { "data-testid": "fixture-other-case", onClick: () => setCurrent("other-case") }, "Open another case"),
    React.createElement(BotPanel, { investigation: investigation(current) }));
}
createRoot(document.getElementById("root")).render(React.createElement(Fixture));
'''
    for key, value in imports.items():
        source = source.replace("from " + key + ";", "from " + json.dumps(value) + ";")
    entry = directory / "fixture.ts"
    entry.write_text(source, encoding="utf-8")
    subprocess.run([node, str(esbuild), str(entry), "--bundle", "--jsx=automatic",
                    "--outfile=" + str(directory / "fixture.js")],
                   check=True, capture_output=True, text=True, timeout=30)
    (directory / "styles.css").write_text((ROOT / "frontend/src/styles.css").read_text(encoding="utf-8"), encoding="utf-8")
    (directory / "index.html").write_text(
        '<!doctype html><html lang="zh"><head><meta charset="utf-8"><title>BOT context regression</title><link rel="icon" href="data:,"><link rel="stylesheet" href="styles.css"><link rel="stylesheet" href="fixture.css"></head>'
        '<body><main id="root"></main><script src="fixture.js"></script></body></html>', encoding="utf-8")

    class Handler(SimpleHTTPRequestHandler):
        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), partial(Handler, directory=str(directory)))
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    with playwright.sync_playwright() as runtime:
        try:
            browser = runtime.chromium.launch(channel="chrome", headless=True)
        except playwright.Error:
            server.shutdown()
            server.server_close()
            pytest.skip("BOT browser regression requires installed Chrome")
        try:
            yield browser, f"http://127.0.0.1:{server.server_port}/"
        finally:
            browser.close()
            server.shutdown()
            server.server_close()


@pytest.fixture
def bot_page(bot_browser):
    browser, url = bot_browser
    context = browser.new_context(viewport={"width": 1440, "height": 1000})
    page = context.new_page()
    errors = []
    page.on("console", lambda entry: errors.append(entry.text) if entry.type in ("error", "warning") else None)
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.goto(url)
    page.get_by_test_id("bot-panel").wait_for()
    yield page
    context.close()
    assert errors == []


def seed(page, action):
    identifier = page.evaluate("action => window.botFixture.seed(action)", action)
    page.reload()
    page.get_by_test_id("bot-intent-select").locator(f'option[value="{identifier}"]').wait_for(state="attached")
    return identifier


def restore(page, identifier):
    page.get_by_test_id("bot-intent-select").select_option(identifier)
    page.get_by_test_id("bot-intent-restore").click()
    page.get_by_test_id("bot-verification-intent").wait_for()


def complete(page, ids):
    page.wait_for_function("window.botFixture.pending")
    page.evaluate("ids => window.botFixture.complete({ onchain_ids: ids })", ids)
    page.get_by_test_id("bot-verification").wait_for()
    page.get_by_test_id("bot-verify").wait_for(state="visible")
    page.wait_for_function("!document.querySelector('[data-testid=bot-verify]').disabled")


def test_revision_receipt_keeps_getter_version_and_review_consistent(bot_page):
    page = bot_page
    review = seed(page, "add_review")
    revision = seed(page, "append_version")
    restore(page, review)
    page.get_by_test_id("bot-verify").click()
    complete(page, {"version_id": "1", "review_id": "1"})
    assert page.get_by_label("On-chain review ID", exact=True).input_value() == "1"
    restore(page, revision)
    page.get_by_test_id("bot-verify").click()
    complete(page, {"version_id": "2"})
    assert page.get_by_test_id("bot-onchain-version").input_value() == "2"
    assert page.get_by_label("On-chain review ID", exact=True).input_value() == ""
    page.get_by_test_id("bot-read").click()
    page.get_by_test_id("bot-read-result").wait_for()
    request = page.evaluate("window.botFixture.calls.find(call => call.kind === 'read').request")
    assert request["version_id"] == "2"
    assert "review_id" not in request


@pytest.mark.parametrize("change", ["case", "wallet"])
def test_late_receipt_preserves_current_form_context(bot_page, change):
    page = bot_page
    identifier = seed(page, "create_case")
    restore(page, identifier)
    page.get_by_test_id("bot-contract").fill(OTHER_CONTRACT)
    page.get_by_test_id("bot-verify").click()
    page.wait_for_function("window.botFixture.calls.some(call => call.kind === 'verify')")
    if change == "case":
        page.get_by_test_id("fixture-other-case").click()
    else:
        page.evaluate("window.botFixture.walletChanged()")
    complete(page, {"version_id": "1"})
    assert page.get_by_test_id("bot-contract").input_value() == OTHER_CONTRACT
    assert page.get_by_test_id("bot-onchain-version").input_value() == ""
    assert "Receipt and contract state verified" in page.get_by_test_id("bot-verification").inner_text()
    assert "original-case" in page.get_by_test_id("bot-verification-intent").inner_text()


def test_refresh_restores_original_network_for_readonly_verification(bot_page):
    page = bot_page
    identifier = seed(page, "append_version")
    page.get_by_test_id("bot-network").select_option("677")
    restore(page, identifier)
    page.get_by_test_id("bot-verify").click()
    complete(page, {"version_id": "2"})
    assert page.evaluate("window.botFixture.calls[0].request.chain_id") == 968
    assert page.get_by_test_id("bot-network").input_value() == "677"
    assert page.get_by_test_id("bot-contract").input_value() == ""
    assert page.get_by_test_id("bot-onchain-version").input_value() == ""
    assert page.get_by_role("link", name="View transaction on its original network").get_attribute("href").startswith("https://scan.bohr.life/tx/")


def test_manual_contract_and_version_changes_reset_dependent_ids(bot_page):
    page = bot_page
    page.get_by_test_id("bot-contract").fill(CONTRACT)
    page.get_by_test_id("bot-onchain-version").fill("1")
    page.get_by_label("On-chain review ID", exact=True).fill("7")
    page.get_by_test_id("bot-onchain-version").fill("2")
    assert page.get_by_label("On-chain review ID", exact=True).input_value() == ""
    page.get_by_label("On-chain review ID", exact=True).fill("8")
    page.get_by_test_id("bot-contract").fill(OTHER_CONTRACT)
    assert page.get_by_test_id("bot-onchain-version").input_value() == ""
    assert page.get_by_label("On-chain review ID", exact=True).input_value() == ""


def idle(page):
    page.wait_for_function("!document.querySelector('[data-testid=bot-read]').disabled")


def setup_review(page, **config):
    if config:
        page.evaluate("values => window.botFixture.configure(values)", config)
    page.get_by_test_id("bot-connect").click()
    page.get_by_test_id("bot-action").select_option("add_review")
    page.get_by_test_id("bot-contract").fill(CONTRACT)
    page.get_by_test_id("bot-onchain-version").fill("1")
    page.get_by_test_id("bot-local-review").select_option("1")


def stored_state(page):
    return page.evaluate("Object.fromEntries(Object.keys(localStorage).sort().map(key => [key, localStorage.getItem(key)]))")


def test_read_paginates_each_inventory_at_the_same_block(bot_page):
    page = bot_page
    page.get_by_test_id("bot-contract").fill(CONTRACT)
    page.get_by_test_id("bot-read").click()
    page.get_by_test_id("bot-read-result").wait_for()
    assert "1–3 / 7" in page.get_by_test_id("bot-versions-pagination").inner_text()
    assert page.get_by_test_id("bot-versions-previous").is_disabled()
    for kind, offsets in [("versions", (3, 0)), ("reviews", (3, 3)), ("versions", (6, 3))]:
        page.get_by_test_id(f"bot-{kind}-next").click()
        idle(page)
        request = page.evaluate("window.botFixture.calls.filter(call => call.kind === 'read').at(-1).request")
        assert (request["version_offset"], request["review_offset"]) == offsets
        assert request["read_block_number"] == "120"
        assert request["read_block_hash"] == "0x" + "c" * 64
    assert page.get_by_test_id("bot-versions-next").is_disabled()
    assert page.get_by_test_id("bot-reviews-next").is_disabled()
    assert "7–7 / 7" in page.get_by_test_id("bot-versions-pagination").inner_text()
    for _ in range(2):
        page.get_by_test_id("bot-versions-previous").click()
        idle(page)
    assert "1–3 / 7" in page.get_by_test_id("bot-versions-pagination").inner_text()
    assert "4–5 / 5" in page.get_by_test_id("bot-reviews-pagination").inner_text()
    page.get_by_test_id("bot-read").click()
    idle(page)
    request = page.evaluate("window.botFixture.calls.filter(call => call.kind === 'read').at(-1).request")
    assert request["version_offset"] == request["review_offset"] == 0
    assert "read_block_hash" not in request and "read_block_number" not in request


@pytest.mark.parametrize("change", ["network", "contract", "case"])
def test_read_context_changes_clear_page_and_anchor(bot_page, change):
    page = bot_page
    page.get_by_test_id("bot-contract").fill(CONTRACT)
    page.get_by_test_id("bot-read").click()
    page.get_by_test_id("bot-read-result").wait_for()
    if change == "network":
        page.get_by_test_id("bot-network").select_option("677")
        page.get_by_test_id("bot-contract").fill(CONTRACT)
    elif change == "contract":
        page.get_by_test_id("bot-contract").fill(OTHER_CONTRACT)
    else:
        page.get_by_test_id("fixture-other-case").click()
    assert page.get_by_test_id("bot-read-result").count() == 0
    page.get_by_test_id("bot-read").click()
    idle(page)
    request = page.evaluate("window.botFixture.calls.filter(call => call.kind === 'read').at(-1).request")
    assert "read_block_hash" not in request
    assert request["version_offset"] == request["review_offset"] == 0


@pytest.mark.parametrize("failure", [None, "read_block_changed"])
@pytest.mark.parametrize("change", ["case", "wallet"])
def test_late_read_response_cannot_repopulate_changed_context(bot_page, change, failure):
    page = bot_page
    page.get_by_test_id("bot-contract").fill(CONTRACT)
    page.evaluate("config => window.botFixture.configure(config)", {"deferRead": True, "readError": failure})
    page.get_by_test_id("bot-read").click()
    page.wait_for_function("window.botFixture.pendingRead")
    if change == "case":
        page.get_by_test_id("fixture-other-case").click()
    else:
        page.evaluate("window.botFixture.walletChanged()")
    page.evaluate("window.botFixture.completeRead()")
    idle(page)
    assert page.get_by_test_id("bot-read-result").count() == 0
    assert page.get_by_role("alert").count() == 0


def test_read_reorg_requires_a_new_snapshot(bot_page):
    page = bot_page
    page.get_by_test_id("bot-contract").fill(CONTRACT)
    page.get_by_test_id("bot-read").click()
    page.get_by_test_id("bot-read-result").wait_for()
    page.evaluate("window.botFixture.configure({ readError: 'read_block_changed' })")
    page.get_by_test_id("bot-versions-next").click()
    page.get_by_role("alert").wait_for()
    assert "Read the contract again" in page.get_by_role("alert").inner_text()
    assert page.get_by_test_id("bot-read-result").count() == 0
    page.get_by_test_id("bot-read").click()
    idle(page)
    request = page.evaluate("window.botFixture.calls.filter(call => call.kind === 'read').at(-1).request")
    assert "read_block_hash" not in request
    assert request["version_offset"] == request["review_offset"] == 0


def test_review_preflight_pages_then_prepares_and_refresh_restores_plain_intent(bot_page):
    page = bot_page
    setup_review(page)
    for count in (3, 6):
        page.get_by_test_id("bot-prepare").click()
        idle(page)
        assert f"Checked {count} / 7" in page.get_by_test_id("bot-preflight").inner_text()
        assert page.get_by_test_id("bot-prepare").inner_text() == "Continue check"
        assert page.evaluate("window.botFixture.calls.filter(call => call.kind === 'prepare').length") == 0
        assert stored_state(page) == {}
    page.get_by_test_id("bot-prepare").click()
    page.get_by_test_id("bot-prepared").wait_for()
    calls = page.evaluate("window.botFixture.calls")
    preflights = [call["request"] for call in calls if call["kind"] == "preflight"]
    prepared = next(call["request"] for call in calls if call["kind"] == "prepare")
    assert len(preflights) == 3
    assert "cursor" not in preflights[0]
    assert preflights[1]["cursor"] == preflights[2]["cursor"] == "opaque-cursor"
    assert prepared["review_preflight_cursor"] == "opaque-cursor"
    assert all("review_preflight_cursor" not in call["prepared"] for call in preflights)
    identifier = page.get_by_test_id("bot-intent-select").input_value()
    state = stored_state(page)
    assert "opaque-cursor" not in json.dumps(state)
    page.reload()
    page.get_by_test_id("bot-panel").wait_for()
    assert page.get_by_test_id("bot-preflight").count() == 0
    restore(page, identifier)
    assert "Local review 1" in page.get_by_test_id("bot-verification-intent").inner_text()
    page.get_by_test_id("bot-transaction-hash").fill("0x" + "b" * 64)
    page.get_by_test_id("bot-verify").click()
    complete(page, {"version_id": "1", "review_id": "9"})
    original = {key: value for key, value in prepared.items() if key != "review_preflight_cursor"}
    assert page.evaluate("window.botFixture.calls.find(call => call.kind === 'verify').request") == original


def test_review_preflight_changed_continues_new_tail(bot_page):
    page = bot_page
    setup_review(page, preflightTotal=4, prepareError="review_preflight_changed")
    for _ in range(2):
        page.get_by_test_id("bot-prepare").click()
        idle(page)
    assert "New reviews were registered on-chain" in page.inner_text("body")
    assert page.get_by_test_id("bot-prepared").count() == 0
    assert page.get_by_test_id("bot-prepare").inner_text() == "Continue check"
    page.get_by_test_id("bot-prepare").click()
    page.get_by_test_id("bot-prepared").wait_for()
    assert "Checked 7 / 7" in page.get_by_test_id("bot-preflight").inner_text()
    calls = page.evaluate("window.botFixture.calls.filter(call => call.kind === 'preflight')")
    assert len(calls) == 3
    assert calls[-1]["request"]["cursor"] == calls[-2]["request"]["cursor"]


@pytest.mark.parametrize("code", ["review_preflight_expired", "review_preflight_invalid", "review_preflight_mismatch"])
def test_review_preflight_restarts_from_first_page_after_snapshot_error(bot_page, code):
    page = bot_page
    setup_review(page)
    page.get_by_test_id("bot-prepare").click()
    idle(page)
    page.evaluate("code => window.botFixture.configure({ preflightError: code })", code)
    page.get_by_test_id("bot-prepare").click()
    idle(page)
    assert page.get_by_test_id("bot-preflight").count() == 0
    assert "check from the first page" in page.inner_text("body")
    page.get_by_test_id("bot-prepare").click()
    idle(page)
    assert "Checked 3 / 7" in page.get_by_test_id("bot-preflight").inner_text()
    assert "cursor" not in page.evaluate("window.botFixture.calls.filter(call => call.kind === 'preflight').at(-1).request")


def test_review_duplicate_stops_preparation(bot_page):
    page = bot_page
    setup_review(page)
    page.get_by_test_id("bot-prepare").click()
    idle(page)
    page.evaluate("window.botFixture.configure({ duplicate: true })")
    page.get_by_test_id("bot-prepare").click()
    idle(page)
    assert "Review already exists; transaction preparation stopped" in page.get_by_test_id("bot-preflight").inner_text()
    assert "On-chain review ID 5" in page.get_by_test_id("bot-preflight").inner_text()
    assert page.get_by_test_id("bot-prepare").is_disabled()
    assert page.get_by_test_id("bot-prepared").count() == 0
    assert page.evaluate("window.botFixture.calls.filter(call => call.kind === 'prepare').length") == 0
    page.get_by_test_id("bot-preflight-restart").click()
    assert page.get_by_test_id("bot-prepare").is_enabled()


@pytest.mark.parametrize("change", ["case", "wallet", "wallet_network", "network", "contract", "action", "version", "onchain_version", "review", "uri"])
def test_review_preflight_context_changes_clear_temporary_cursor(bot_page, change):
    page = bot_page
    setup_review(page)
    page.get_by_test_id("bot-prepare").click()
    idle(page)
    if change == "case":
        page.get_by_test_id("fixture-other-case").click()
    elif change == "wallet":
        page.evaluate("window.botFixture.walletChanged()")
    elif change == "wallet_network":
        page.evaluate("window.botFixture.walletNetworkChanged()")
    elif change == "network":
        page.get_by_test_id("bot-network").select_option("677")
    elif change == "contract":
        page.get_by_test_id("bot-contract").fill(OTHER_CONTRACT)
    elif change == "action":
        page.get_by_test_id("bot-action").select_option("append_version")
    elif change == "version":
        page.get_by_test_id("bot-local-version").select_option("2")
    elif change == "onchain_version":
        page.get_by_test_id("bot-onchain-version").fill("2")
    elif change == "review":
        page.get_by_test_id("bot-local-review").select_option("2")
    else:
        page.get_by_label("Public evidence URI", exact=True).fill("https://example.org/evidence")
    assert page.get_by_test_id("bot-preflight").count() == 0
    assert page.get_by_test_id("bot-prepared").count() == 0
    assert stored_state(page) == {}


@pytest.mark.parametrize("stage", ["Preflight", "Prepare"])
@pytest.mark.parametrize("change", ["case", "wallet"])
def test_late_review_response_cannot_prepare_or_persist_old_context(bot_page, change, stage):
    page = bot_page
    setup_review(page, preflightTotal=1, **{f"defer{stage}": True})
    page.get_by_test_id("bot-prepare").click()
    page.wait_for_function(f"window.botFixture.pending{stage}")
    if change == "case":
        page.get_by_test_id("fixture-other-case").click()
    else:
        page.evaluate("window.botFixture.walletChanged()")
    page.evaluate(f"window.botFixture.complete{stage}()")
    idle(page)
    assert page.get_by_test_id("bot-preflight").count() == 0
    assert page.get_by_test_id("bot-prepared").count() == 0
    assert page.get_by_role("alert").count() == 0
    assert stored_state(page) == {}
    if stage == "Preflight":
        assert page.evaluate("window.botFixture.calls.filter(call => call.kind === 'prepare').length") == 0


@pytest.mark.parametrize("status", ["submitted", "pending", "mismatch", "failed", "verified"])
def test_verify_503_preserves_exact_saved_state_and_retries(bot_page, status):
    page = bot_page
    identifier = seed(page, "append_version")
    restore(page, identifier)
    if status != "submitted":
        page.get_by_test_id("bot-verify").click()
        page.wait_for_function("window.botFixture.pending")
        page.evaluate("status => window.botFixture.complete({ status, onchain_ids: { version_id: '2' } })", status)
        idle_verify = "!document.querySelector('[data-testid=bot-verify]').disabled"
        page.wait_for_function(idle_verify)
    before = stored_state(page)
    hash_value = page.get_by_test_id("bot-transaction-hash").input_value()
    original = page.get_by_test_id("bot-verification-intent").inner_text()
    page.evaluate("window.botFixture.configure({ verifyFailures: 1 })")
    page.get_by_test_id("bot-verify").click()
    page.get_by_test_id("bot-verification-error").wait_for()
    assert "Retry the receipt read" in page.get_by_test_id("bot-verification-error").inner_text()
    assert page.get_by_test_id("bot-transaction-hash").input_value() == hash_value
    assert page.get_by_test_id("bot-verification-intent").inner_text() == original
    assert stored_state(page) == before
    page.get_by_test_id("bot-verify").click()
    complete(page, {"version_id": "2"})
    assert page.get_by_test_id("bot-verification-error").count() == 0
    calls = page.evaluate("window.botFixture.calls.filter(call => call.kind === 'verify')")
    assert calls[-1] == calls[-2]


def test_verify_network_failure_keeps_bound_hash_and_intent(bot_page):
    page = bot_page
    identifier = seed(page, "add_review")
    restore(page, identifier)
    before = stored_state(page)
    page.evaluate("window.botFixture.configure({ transportFailure: true })")
    page.get_by_test_id("bot-verify").click()
    page.get_by_test_id("bot-verification-error").wait_for()
    assert stored_state(page) == before
    assert "The transaction hash, verification intent, and saved state are preserved" in page.get_by_test_id("bot-verification-error").inner_text()
    page.evaluate("window.botFixture.configure({ transportFailure: false })")
    page.get_by_test_id("bot-verify").click()
    complete(page, {"version_id": "1", "review_id": "1"})


def test_pagination_layout_and_page_identity(bot_page, tmp_path):
    page = bot_page
    assert page.title() == "BOT context regression"
    assert page.url.startswith("http://127.0.0.1:")
    assert page.get_by_role("heading", name="BOT on-chain registry").is_visible()
    assert page.locator("vite-error-overlay").count() == 0
    page.get_by_test_id("bot-contract").fill(CONTRACT)
    page.get_by_test_id("bot-read").click()
    page.get_by_test_id("bot-read-result").wait_for()
    page.get_by_test_id("bot-versions-next").click()
    idle(page)
    page.get_by_test_id("bot-read-result").scroll_into_view_if_needed()
    for width, height, name in [(1440, 1000, "desktop"), (390, 844, "mobile")]:
        page.set_viewport_size({"width": width, "height": height})
        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
        assert "4–6 / 7" in page.get_by_test_id("bot-versions-pagination").inner_text()
        path = tmp_path / f"bot-pagination-{name}.png"
        page.screenshot(path=str(path), full_page=True)
        print(f"Screenshot: {path}")


@pytest.mark.parametrize(("code", "http_status"), [("review_preflight_busy", 409), ("review_preflight_capacity", 409), ("rate_limited", 429), ("bot_rpc_unavailable", 503)])
def test_review_preflight_transient_error_retains_cursor_for_retry(bot_page, code, http_status):
    page = bot_page
    setup_review(page)
    page.get_by_test_id("bot-prepare").click()
    idle(page)
    page.evaluate("config => window.botFixture.configure(config)", {"preflightError": code, "preflightHttpStatus": http_status})
    page.get_by_test_id("bot-prepare").click()
    page.get_by_role("alert").wait_for()
    assert "Checked 3 / 7" in page.get_by_test_id("bot-preflight").inner_text()
    page.get_by_test_id("bot-prepare").click()
    idle(page)
    assert "Checked 6 / 7" in page.get_by_test_id("bot-preflight").inner_text()
    request = page.evaluate("window.botFixture.calls.filter(call => call.kind === 'preflight').at(-1).request")
    assert request["cursor"] == "opaque-cursor"
    assert page.get_by_role("alert").count() == 0
