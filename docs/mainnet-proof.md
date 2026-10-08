# BOT mainnet proof and reproduction

This reference accompanies the [English product README](../README.md) and [中文产品说明](../README.zh-CN.md). The current product uses the BOT Mainnet 677 archive observed on **8 October 2026**. Archived verification describes the network at its recorded observation time; the application presents a new readback separately.

## Recorded deployment and workflow

Registry: [`0x951f7b5c68adba4cefd7fa851cb8e030426e81aa`](https://scan.botchain.ai/address/0x951f7b5c68adba4cefd7fa851cb8e030426e81aa). Official RPC: `https://rpc.botchain.ai`.

The author signed four transactions. Public RPC verification checked successful receipts, canonical blocks, deployment runtime, exact content commitments, and complete version/review getters. The final getter snapshot at block **25917270** contains two versions and one review, with version 2 as the head and version 1 as its parent. Review 1 remains bound to version 1 and that version's content hash.

| Step | Relationship | Transaction |
| --- | --- | --- |
| Registry deployment | Creation and runtime match the included artifact | [Deployment](https://scan.botchain.ai/tx/0xd7d4d91b0b4158adb4da9cdccb0d71739fc670ba7801f382eb08742d3274fe8c) |
| v1 registration | Version 1, parent 0 | [v1](https://scan.botchain.ai/tx/0x936c81539bfeafbc14cdd83ee27059d9979262267b74d3062aaef1e37804d398) |
| Exact-version review | Review 1 binds to version 1 | [Review](https://scan.botchain.ai/tx/0x51af7682149dd9f28e6ceffe8e8044028d3ed437e2cfcfdc5f1dfa3facf7aabd) |
| Corrected version | Version 2, parent 1 | [v2](https://scan.botchain.ai/tx/0xb1a45ace22fee58c97a0c218cb67deeea07cc5b778fa13371e730c56bda72328) |

The three business transactions consumed **0.02207558 BOT**. Including deployment, the total was **0.04501682 BOT**. The author and reviewer used the same wallet. This demonstrates exact-version review; assessing reviewer independence requires additional evidence.

The [workflow archive](../data/demo/bot-mainnet-workflow-20261008.json), also [available in the hosted demo](https://hnnuyuxuan.github.io/cluetide-app/bot/mainnet/bot-mainnet-workflow-20261008.json), contains all four transaction hashes, fees, receipt-file hashes, and complete final getters. Original receipts are in [data/demo](../data/demo); the product consumes [bot-mainnet-story-data.json](../frontend/src/bot-mainnet-story-data.json).

The [Solidity source](../contracts/ClueTideRegistry.sol) and [compiled artifact](../contracts/artifacts/ClueTideRegistry.json) are included. Deployment verification matched creation and runtime bytecode to this artifact. Compiler settings: **Solidity 0.8.30**, **Paris EVM**, **optimizer enabled, 200 runs**.

Ethereum is the investigation data source. BOT Mainnet 677 and BOT Testnet 968 are distinct registry networks. The [historical testnet snapshot](../frontend/src/bot-story-data.json) and its original receipts preserve their original network identity.

## Evidence and transaction semantics

Each public evidence ZIP includes `evidence.json`, `raw.json`, `report.json`, `report.md`, and `manifest.json`. The manifest records the SHA-256 and byte length of the four content files. Its RFC 8785 canonical JSON bytes produce the manifest SHA-256 registered as `contentHash`. The archive's own SHA-256 identifies the downloaded ZIP bytes and is a separate value.

The registry restricts version appends to the case author, requires the current head as the parent, and binds that parent to the same case. A review names a case, version ID, exact version content hash, decision, review hash, and review URI. v2 has a new content commitment; review 1 continues to reference v1.

The backend rebuilds a proposed call from saved evidence and version identity. Wallet confirmation authorizes signing. Readback checks the transaction, canonical block, receipt, runtime code, emitted events, content commitments, and getters against the saved public intent. Saved transaction hashes and public context support readback after a refresh; an account or network change invalidates prepared signing context. Chain reads use a fixed block snapshot, and review preflight uses bounded batches with resumable server cursors.

Content commitments establish exact bytes and their version relationships. The UNI reports retain two `missing_agent_rpc_observation` citation issues: receipt or transaction evidence lacks a matching raw RPC observation. Governance authorization needs further investigation, and supply change remains unknown within this investigation. Original ZIPs preserve their source report bytes and language; English UI copy is an explanatory adaptation.

## Product routes and deployment

| Route | Purpose |
| --- | --- |
| `#/` | Product and evidence lifecycle |
| `#/cases/uniswap93` | v1, its review, and v2 with source and transaction links |
| `#/verify` | Evidence-file integrity and recorded commitments |
| `#/developers` | Architecture, source, network details, and setup |
| `#/workspace` | Local investigation and wallet workflow |

Hash routes support direct GitHub Pages links. Both `index.html` and `bot.html` enter the English product. Existing local investigation query links and saved transaction intents remain supported.

| Capability | Hosted demo | Local application |
| --- | --- | --- |
| Product and UNI version story | Available | Available |
| Evidence downloads and file checks | Available | Available |
| Recorded mainnet explorer links | Available | Available |
| Investigation API and persistent history | Uses local service | Available |
| Transaction preparation and full readback | Uses local service | Available |
| Wallet signing | Through local workflow | Explicit wallet confirmation |

The hosted demo serves bundled public records. A new local application creates a separate case store. Archived transaction contexts refer to their archived local version identities; a fresh local case receives its own identities. Keep the original browser origin when continuing saved transaction intents.

## Local development

The [README](../README.md#run-your-own-workspace) provides the Windows quickstart. Use Python 3.12 and Node.js 24. The repository's `requirements-lock.txt` and `frontend/package-lock.json` fix dependency versions.

For Linux or macOS, after cloning the BOT branch and entering the repository:

```bash
python3.12 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements-lock.txt
(cd frontend && npm ci && npm run build)
CLUETIDE_PREVIEW_ONLY=1 uvicorn cluetide.app:app --host 127.0.0.1 --port 8765
```

Open **http://127.0.0.1:8765/**. The offline preview uses public snapshots and a deterministic model. Investigations, evidence export/import, local reviews, and corrections work without a model API key. Windows is the recorded validation platform; the POSIX commands are provided for reproduction.

For frontend development, start the API and run `npm run dev` in `frontend`. Vite proxies `/api` to port 8765. Production serves frontend and API from the same origin.

## Verification commands

From the repository root after the Windows quickstart:

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m pip check
.\.venv\Scripts\python.exe scripts/build_bot_story_data.py --check
.\.venv\Scripts\python.exe scripts/build_bot_story_data.py --network mainnet --check
cd frontend
npm test
npm run build
cd ..
.\.venv\Scripts\python.exe scripts/package_demo.py --dry-run --include-demo
```

Tests cover archive integrity, publication consistency, exact-version review, transaction preparation/readback, wallet context, bounded RPC, and recovery. [Release validation](validation.md) records each execution's actual scope; [source provenance](../SOURCE.md) records the upstream baseline and selected release files. Run `python scripts/verify_sources.py` to compare the checkout with its source manifest.

ClueTide's own source uses the [MIT License](../LICENSE). Original third-party notices remain in [data/attribution](../data/attribution). Explore the [source and setup](https://github.com/HNNUYUXUAN/cluetide-review/tree/BOT) or the [hosted application](https://hnnuyuxuan.github.io/cluetide-app/bot/).
