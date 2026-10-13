# Web3 collaboration and portable acceptance

A Web3 project commissions a launch page from an Agent. The proposer retains the package. The reviewer can inspect a public fixed-version link without connecting a wallet; their assigned wallet is needed for approval and correction. Changes return to review. The executor gets a single confirmed attempt, and every participant can retain the delivered evidence.

## Review and corrections

Register the task and package before publishing its review link. Sharing explicitly uploads all originals through the selected version, so inspect title, source reference, tests and HTML for intended public disclosure first. The link remains pinned when a newer version exists. Review the actual line diff; the structural preview removes executable content, forms, external resources and source styling from a display copy.

Correction requires a reason and supports acceptance conditions. Confirm the on-chain correction first, then choose **Sign and sync feedback**. This second operation is an EIP-712 message signature with no Gas. It binds the exact task, version, manifest, transaction and text. A rejected message signature or interrupted upload leaves a draft; retry uses the same correction transaction. The service stores one immutable feedback record per correction transaction.

## Agent commands

```sh
python scripts/agent_release.py --url https://SERVICE export --task-id TASK --version-id 2 --output review-v2.zip
python scripts/agent_release.py verify-package --package review-v2.zip
python scripts/agent_release.py --url https://SERVICE feedback --task-id TASK --version-id 2
python scripts/agent_release.py --url https://SERVICE wait --task-id TASK --version-id 2 --interval 5 --timeout 300
```

Run `--help` for exact command fields. `wait` returns machine-readable decision, expiry, replacement, execution and receipt states. Public writes require `--public`; the Agent prepares unsigned transactions for wallet handoff. The task capability file stays private.

## Device recovery and acceptance

Import the original package on another device and choose **Recover start transaction**. Supply the hash of `consume`, not approval or submission. ClueTide verifies BOT 677, the fixed contract bytecode, executor, zero transfer, exact calldata, event, successful receipt, three confirmations and canonical block hashes. The same attempt survives later versions and deadline expiry. An attempt with a recorded receipt opens result verification.

After publication, **Verify receipt and artifact** checks the executor's EIP-712 signature, current confirmed chain commitment and independently downloaded bytes. **Export acceptance bundle** retains raw HTTP receipt bytes and the actual artifact with original version materials. It remains available after refresh through IndexedDB. Storage errors are explicit and require exporting the current in-memory evidence before leaving.

Open **Verify**, choose an acceptance ZIP and select **Verify bundle offline**. This operation performs no network requests. It reports file/signature consistency and leaves current chain and live artifact checks marked unperformed. **Open this exact version for online verification** starts those separate checks.

## Operator preflight

Run `python scripts/release_preflight.py --help` and the configured service check before a demonstration. Verify public port access, exact CORS origin, API identity, BOT chain, witness runtime code, signer and a downloaded evidence bundle. Resume the existing Codespace if necessary and restore its existing public 8787 port. The startup helper adopts a resumed listener only after its user, directory, interpreter, command, environment and service identity match.

Retain the existing free-resource budget and 30-minute idle limit. Back up the private runtime state before upgrading. A sleeping service affects public package downloads, feedback synchronization and publication; chain evidence and local package inspection remain usable.
