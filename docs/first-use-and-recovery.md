# Your first release and recovery guide

1. Ask your Agent to create the task and export its review ZIP. Keep the Agent's private access file with its creator. On ClueTide choose **Import Agent package**. Read the task purpose, exact changes, preview, tests and assigned roles. This inspection needs no wallet and uploads nothing.
2. Follow **Your next step**. It names the required role and full address, BOT Mainnet 677, and whether the operation pays Gas. Connect MetaMask and select that address. **Switch to reviewer / executor** opens account selection; if unsupported, use MetaMask's account menu and reconnect the site.
3. The proposer registers the task and exact version. The reviewer approves or chooses **Request correction**, enters a reason and acceptance conditions, confirms the correction transaction, then signs **Sign and sync feedback**. The feedback signature costs no Gas and its content becomes public.
4. The Agent reads the signed feedback and exports the revised package. Import it, inspect the actual differences and obtain a new approval. A changed version cannot reuse the old approval.
5. The executor chooses **Authorize one start**, checks the estimate and confirms in MetaMask. Wait for three verified confirmations. Publish the artifact, check its signed receipt and actual download, then register that receipt on chain.
6. Verify the three separate results: original bytes and signature; current confirmed chain commitment; actual downloaded artifact. Export the acceptance ZIP. Another browser can inspect it offline; current chain and fresh-download checks require an explicit online check. File equality and the executor statement do not establish business quality.

| What you see | Your next action |
|---|---|
| Wallet confirmation is open | Finish or decline that prompt before preparing another operation. |
| Account or network changed | Select the full assigned address on BOT 677. A sent request remains saved and continues verification. |
| No transaction hash returned | In Transaction history choose Recover request, then Find the original transaction. Check MetaMask activity and paste the exact hash when needed. Reconciliation is bounded to 4,000 blocks; it does not automatically resend. |
| Waiting for chain data to synchronize | Keep the original request. Retry verification; three canonical confirmations and exact parameters remain required. |
| Service is restoring or unavailable | Reads retry at 2, 5 and 10 seconds within 30 seconds. Save local evidence and retry later. Authorized package uploads and signed feedback resume with their saved content. |
| Publication timed out | Check the existing attempt. Recover its start hash and original package; use the same attempt after an expired lease. |
| New browser or device | Import the exact package and choose Recover start transaction. A consumed grant retains its original attempt even after a later revision or elapsed deadline. |
| Browser storage failed | Download the transaction journal, task backup, signed feedback or acceptance bundle immediately. Preserve the wallet hash. Complete storage recovery before signing another operation. |

**Publish review package & copy link** makes the selected version's original materials publicly downloadable. Share that fixed-version link. A newer version is offered separately; collaborators choose when to inspect it.
