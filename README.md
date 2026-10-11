# ClueTide Agent Release Witness

Approve an exact release. Verify what shipped.

Source package version: **0.1.0**.

This package connects a coding agent's prebuilt static release to a designated human approval, a controlled publisher, and a signed execution receipt. The first workflow publishes one bounded UTF-8 artifact into an isolated demo target.

**New release-witness mainnet deployment and end-to-end wallet evidence are pending.** The included bytecode and local EVM tests establish local behavior. They are not evidence of a deployment or transaction on BOT Mainnet.

## Install and run

Use Python 3.12 and Node.js 24. From this directory, create an isolated environment and install the package:

```powershell
py -3.12 -m venv .venv
.venv/Scripts/python.exe -m pip install -e ".[dev]"
cd frontend
npm ci
npm test
npm run build
cd ..
./scripts/run-local.ps1
```

Open `http://127.0.0.1:8787/`. The service serves the built UI and API together. A source-only launch shows the API until the frontend is built. For frontend development, keep the API running and start `npm run dev` inside `frontend`; Vite uses port 5177 and proxies `/api` to port 8787. Static preview uses port 4177.

On other platforms, create `.venv` with Python 3.12, install the package, build the frontend, and run:

```sh
python -m uvicorn cluetide.release_server:app --host 127.0.0.1 --port 8787 --no-access-log
```

[requirements.txt](requirements.txt) contains the same pinned runtime dependencies as [pyproject.toml](pyproject.toml). [requirements-dev.txt](requirements-dev.txt) adds the local test tools. The Solidity compiler is an optional, separately supplied build tool. [frontend/README.md](frontend/README.md) describes the UI entry points.

## Workflow and wallet responsibility

1. Create a task with fixed owner, proposer, reviewer, executor, receipt signer, and demo target. The reviewer wallet must differ from the proposer wallet.
2. Add an already-built artifact with its source reference, test evidence, and exact Unix expiry. Download the package and inspect the exact bytes.
3. Register the version and approve it from the designated reviewer wallet.
4. Consume that approval from the designated executor wallet. After three canonical confirmations, invoke the controlled publisher with the verified attempt ID and start transaction hash.
5. Inspect the signed receipt and independently compare the available artifact bytes. Register the receipt commitment through a user-controlled wallet.

The browser prepares unsigned transactions and checks the selected account, BOT network, calldata, gas estimate, and balance before requesting a wallet action. The wallet holder confirms each network transaction and gas payment in the extension. The backend uses read-only RPC methods and never broadcasts a network transaction.

The operator's receipt-signing key is separate from wallet transaction signing. The service creates and stores that signing identity in its private runtime directory; task configuration fixes the resulting signer address. Preserve the key and runtime state together when recovering an executor.

The [reference Agent client](scripts/agent_release.py) performs package submission, transaction preparation, verification, and controlled execution through the API. It hands unsigned transactions to the wallet workflow. Run these examples with the Python interpreter from your activated `.venv`:

```sh
python scripts/agent_release.py config
python scripts/agent_release.py tasks
python scripts/agent_release.py --help
```

## Deployment configuration

The product gateway is configured for BOT Mainnet, chain ID **677**, using `https://rpc.botchain.ai` and explorer `https://scan.botchain.ai`. The standalone chain adapter also supports read-only verification and unsigned preparation for BOT Testnet **968**. RPC URLs are fixed in the source.

Start with an unconfigured release contract. Prepare deployment in the UI, review and confirm it in the wallet, and verify the actual transaction and runtime bytecode. Set `CLUETIDE_RELEASE_CONTRACT` to the resulting verified contract address before using release actions. No contract address is supplied as a completed deployment in this source package.

Set these environment variables before starting the process:

| Variable | Purpose |
| --- | --- |
| `CLUETIDE_DATA_DIR` | Private local state root; default is `local-data` in this package |
| `CLUETIDE_RELEASE_CONTRACT` | Verified `ClueTideReleaseWitness` deployment address |
| `CLUETIDE_PUBLIC_ORIGIN` | Exact HTTPS application origin when operating behind a configured public proxy |

Release state is stored under `CLUETIDE_DATA_DIR/release-witness`. Keep this directory out of public hosting, source control, and source exports. Set a persistent, access-controlled location for an operated service. The PowerShell launcher selects this package's `local-data` directory and binds the API to loopback. Launch Uvicorn directly when using a custom state directory.

For public operation, serve the built UI and API at the same HTTPS origin and forward the configured host to the loopback service. Configure operator access at the hosting boundary. Host, origin, and request-size checks do not authenticate users or replace access control. Only the fixed demo publishing operation is supported; artifacts are offered as downloads rather than executed as application code.

## Authorization, recovery, and trust

The [release witness contract](contracts/ClueTideReleaseWitness.sol) fixes task roles and target commitment. A different target or role configuration requires a new task. Review identity is the designated wallet account; operator policy establishes who controls it. Approval binds the current version, manifest hash, artifact hash, and deadline. Consumption atomically marks the approval used and creates one task-scoped attempt. A later version or revocation cannot cancel an attempt whose consumption has already reached the required confirmation policy.

The publisher pins the approved inputs, writes immutable attempt material, checks staged and published bytes, and records its durable stage. Repeating a completed local attempt returns its existing receipt. An uncertain attempt is reconciled before further action; a retry requires a fresh version, approval, and attempt. Chain consumption and filesystem publication are separate commits, so the system does not promise physical exactly-once execution.

Receipt EIP-712 signatures bind chain ID, contract, task, version, attempt, receipt hash, and result. The contract accepts the preset EOA signer's statement, including relayed submissions. Identical receipts are idempotent; conflicting commitments are rejected. A valid signature authenticates the statement's signer. It does not prove that an external service actually shipped or that the software is correct. Artifact comparison and access to original evidence remain separate checks.

RPC verification checks the actual transaction intent, successful receipt, canonical block at observation time, confirmation count, exact runtime code, and matching getters. Three confirmations are an explicit policy, not a claim of irreversible finality. Publication authority still depends on the operator controlling the actual host credentials and protecting the executor process.

## Test and verify the artifact

In the activated `.venv`, run:

```sh
python -m pytest -q
```

All included Python tests concern the release product. [The integration test](tests/test_release_integration_evm.py) runs the real Solidity bytecode, chain adapter, gateway, signatures, and filesystem publisher on an in-process Py-EVM chain. Mock HTTP transport terminates every RPC request locally. Test account signatures and transactions stay in that process.

The [saved artifact](contracts/artifacts/ClueTideReleaseWitness.json) was compiled with Solidity `0.8.30+commit.73712a01`, optimizer runs 200, and EVM target `paris`. To reproduce it on Windows, provide `solc-0.8.30.exe` and run:

```powershell
.venv/Scripts/python.exe contracts/compile_release.py --compiler <path-to-solc-0.8.30.exe> --check
```

The compiler's required SHA-256 is `ccbd3ed44d5fbd26fe039702d403421f1212d2e8752e3cbe3bfd074986911586`. The compiler script verifies this checksum, recompiles the source, and compares the saved ABI and bytecode. Omit `--check` to regenerate the artifact. The script does not download a compiler or connect to a chain.

## Source map

| Path | Responsibility |
| --- | --- |
| [frontend/src/release](frontend/src/release) | English release UI, wallet handoff, local transaction recovery |
| [src/cluetide/release_server.py](src/cluetide/release_server.py) | Release application and deployment boundaries |
| [src/cluetide/releases_api.py](src/cluetide/releases_api.py) | Bounded release API |
| [src/cluetide/releases.py](src/cluetide/releases.py) | Exact packages, controlled publisher, signed receipts |
| [src/cluetide/release_chain.py](src/cluetide/release_chain.py) | Unsigned transactions and read-only verification |
| [contracts](contracts) | Release witness source, artifact, and pinned compilation |
| [tests](tests) | Release unit, API, bytecode, and integration checks |

Shared Python modules and frontend type dependencies retain their source bytes. Third-party frontend notices are preserved in [frontend/public/licenses](frontend/public/licenses). Dependency licenses remain subject to their original terms.
