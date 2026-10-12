# ClueTide Agent Release Witness

Approve an exact release. Verify what shipped.

[Open the public product](https://hnnuyuxuan.github.io/cluetide-app/release/)

The public website connects an EVM wallet directly to BOT Mainnet 677. Create a task, prepare a built HTML artifact and evidence, register its exact version, and invite the designated reviewer. A controlled publisher checks the one-time authorization, publishes the approved artifact and signs an execution receipt.

## Public workflow

1. Click **Connect Wallet**, choose your wallet and switch to BOT Mainnet 677.
2. Choose **Create release**. Your connected account is the owner, proposer and executor; select another reviewer address. The prefilled address is a demonstration reviewer, whose holder decides whether to approve.
3. **Register release** is the first mainnet transaction. Add the artifact, source reference and test evidence, then **Submit exact version**.
4. Export the package and share it with the task link. The reviewer imports the exact bytes and approves or requests a correction using their assigned wallet.
5. The executor checks the publication service, imports the confirmed package and authorizes one start. Publication uses an isolated demo directory. Verify the receipt signature, chain commitment and downloaded output separately.

Task registration, version submission and review continue to work when the temporary publication service sleeps. Actual publication needs that service online. Every transaction requires the wallet holder's confirmation and BOT gas. Original files are stored on the current device until explicitly shared or imported to the service; download a task backup before clearing browser storage.

## Mainnet evidence

Contract: [0xca71b77708674f5f2407c0ad4e599eaf5eb07281](https://scan.botchain.ai/address/0xca71b77708674f5f2407c0ad4e599eaf5eb07281).

On 12 October 2026 the release workflow completed six real mainnet transactions, including deployment, at a total cost of 0.03896024 BOT. Its signed receipt and archived artifact are available under **Explore verified release**; the application checks the current chain separately. Both demonstration role wallets were controlled by the same participant. Public-browser acceptance of this source revision and recording coverage are reported in the accompanying release evidence.

## Install and run

Use Python 3.12 and Node.js 24:

```sh
python -m venv .venv
.venv/bin/python -m pip install -e ".[dev]"
cd frontend
npm ci
npm test
npm run build
cd ..
.venv/bin/python -m uvicorn cluetide.release_server:app --host 127.0.0.1 --port 8787
```

On Windows use `.venv/Scripts/python.exe` and `./scripts/run-local.ps1`. Open `http://127.0.0.1:8787/`. Vite development uses port 5177 with an API proxy; static preview uses port 4177. The root frontend is the public direct-wallet product. `bot.html` retains the server-backed release workspace.

The included [Codespaces configuration](.devcontainer/README.md) installs dependencies and starts the review service. Its idle and session limits require checking availability before a later publication session.

## Configuration and boundaries

- `CLUETIDE_DATA_DIR`: persistent private state root; default `local-data`.
- `CLUETIDE_RELEASE_CONTRACT`: verified witness address.
- `CLUETIDE_PUBLIC_ORIGIN`: exact HTTPS origin serving the API.
- `CLUETIDE_FRONTEND_ORIGINS`: comma-separated HTTPS frontend origins allowed to call the API. The configured review startup permits `https://hnnuyuxuan.github.io`.

Browser network, contract, reviewer example and publisher endpoint are public configuration in `frontend/src/release/public-config.ts`. No private signing material is included. The service preserves its private execution signer across upgrades and independently validates the on-chain task before importing bytes.

`POST /api/releases/imports` accepts one task descriptor, exact manifest and HTML artifact per request. It reads a canonical block with three confirmations, verifies roles, signer, target, manifest and artifact, and imports versions in order. Matching imports are idempotent. Imports do not issue task write keys. Existing task and version APIs retain their capability checks.

Approval binds the current version, target and expiry. Consuming it grants one execution attempt; later changes cannot recall that grant. Recovery reconciles the recorded attempt with actual files before another execution. Signatures authenticate the configured executor's statement; artifact comparison verifies available bytes. Software quality remains subject to tests and human review.

## Verification and source

Run `python -m pytest -q` in the package and `npm test` / `npm run build` in `frontend`. Tests include Python/JavaScript JCS and ABI fixtures, recorded EIP-712 receipt verification, real compiled bytecode on a local EVM, browser-package import, publication and recovery. Local test transactions stay in the test chain.

`source-manifest.json` binds exported files to their exact bytes. Solidity source and compiled artifact are under `contracts/`; the artifact pins Solidity 0.8.30 and EVM paris. Frontend dependency notices are retained under `frontend/public/licenses`.
