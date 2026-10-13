# ClueTide Agent Release Witness

**Approve an exact release. Verify what shipped.**

[Open the public product](https://hnnuyuxuan.github.io/cluetide-app/release/).

ClueTide helps Web3 project partners ship an Agent-prepared project page together. The proposer preserves the exact HTML, source reference and tests; the reviewer approves that version or signs correction requirements; the executor consumes one authorization and publishes; collaborators retain independent delivery evidence.

## Public collaboration

1. Connect MetaMask on BOT Mainnet 677. Create a release with a distinct designated reviewer, then register the task and exact package.
2. Choose **Publish review package & copy link**. The original materials become publicly downloadable. A fixed-version link opens that version directly, while newer versions require an explicit switch.
3. Review the purpose, actual line changes, inert preview and test evidence. **Request correction** records the decision, followed by a Gas-free EIP-712 feedback signature. Interrupted signing or upload resumes against that same transaction.
4. Use the Agent's `feedback` or `wait` command to retrieve exact revision requirements. A revised package needs its own approval.
5. Authorize one start, publish, and inspect the receipt. Another device can recover the consumed attempt from its exact package and start transaction hash.
6. Record the receipt and export the acceptance bundle. Its offline verifier checks original bytes and signatures; current confirmed chain records and a freshly downloaded artifact are checked separately online.

The witness contract is [0xca71b77708674f5f2407c0ad4e599eaf5eb07281](https://scan.botchain.ai/address/0xca71b77708674f5f2407c0ad4e599eaf5eb07281). Every business transaction needs the holder's wallet confirmation and BOT Gas. Revisions require renewed approval; a confirmed start grants one attempt even if a deadline later passes or another version is registered. The controlled publisher attests to its execution, file equality proves content integrity, and tests and human review establish business quality.

The temporary Codespace service may sleep. Chain reads, wallet review and file imports remain available. Check the timestamped service status before sharing or publishing. See the [collaboration guide](docs/collaboration-guide.md) and [formal recording runbook](docs/collaboration-recording.md).

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

On Windows use `.venv/Scripts/python.exe` and `./scripts/run-local.ps1`. The browser entry is `/`; `bot.html` retains the service-backed workspace. Development uses port 5177 and proxies the API to 8787. See [Codespaces setup](.devcontainer/README.md).

## Configuration and evidence

- `CLUETIDE_DATA_DIR`: private persistent runtime root, default `local-data`.
- `CLUETIDE_RELEASE_CONTRACT`: verified deployed witness contract.
- `CLUETIDE_PUBLIC_ORIGIN`: exact HTTPS service origin.
- `CLUETIDE_FRONTEND_ORIGINS`: allowed browser origins; the review service permits `https://hnnuyuxuan.github.io`.

The browser pins the BOT chain, witness bytecode, contract, target and receipt signer. The operator retains the private signer and backups across upgrades. Source exports contain no private runtime state.

Package writes on the service-created workflow require its creator's task capability. Public `POST /api/releases/imports` independently checks confirmed chain roles and commitments for each immutable version. The Agent requires `--public` for writes to a remote service. Original artifacts retain UTF-8 BOM and CRLF; strict package checks reject duplicate JSON keys, unsafe ZIP members, inconsistent copies and excessive expansion. Artifacts are at most 32 KiB; complete packages are at most 4 MiB, and version/import requests at most 512 KiB.

`cluetide.acceptance-bundle.v1` carries original versions, raw receipt bytes, downloaded artifact bytes, transaction references and timestamped observations. Imported historical confirmations are observations, not a new chain check. Browser evidence persists in a separate IndexedDB store; export it before clearing local storage.

## Validation and mainnet scope

Run `python -m pytest -q`, `python .devcontainer/test_codespaces.py`, and the frontend test/build commands. Tests use isolated state and local EVM transactions. The source manifest identifies every exported file by SHA-256. Solidity 0.8.30 and the Paris EVM artifact remain fixed.

The [2026-10-12 public-wallet acceptance](docs/public-wallet-acceptance.md) completed seven mainnet transactions, revised-version approval, actual publication and receipt recording for 0.01629482 BOT. Both role wallets were controlled by one participant. The current collaboration upgrade's formal three-version recording is tracked separately in its runbook; engineering checks do not substitute for those new wallet transactions or a continuous recording.
