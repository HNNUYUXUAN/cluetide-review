# Uniswap proposal 93 public snapshot

This directory contains a public Ethereum case snapshot captured on 2026-10-06 UTC. It supports the ClueTide offline investigation demo and can be reproduced with the included read-only PowerShell collectors and Python assembler.

## Scope

- Ethereum mainnet, chain ID `1`.
- Subject: treasury Timelock `0x1a9c8182c09f50c8318d769245bea52c32be35bc`.
- ERC-20 UNI: `0x1f9840a85d5af5bf1d1762f925bdaddc4201f984`.
- Inclusive block window: `24106368`–`24106388` (21 blocks).
- Execution transaction: `0x091f0083242a777d55821c1189e568d6d033d9da501b75087dc736fa143d2c1e`.

`rpc.json` contains the successful public cache responses used by the offline adapter. `raw/` retains exact request/response JSON bytes, plus short official-source excerpts. `capture*.json` records endpoints, UTC capture times, HTTP statuses and response hashes, including failed and empty provider responses. `fixture-manifest.json` hashes the retained evidence files, excluding itself and the reproduction scripts.

The bounded token query returned five UNI Transfer logs. Filtering those returned logs yields one outgoing Timelock transfer and zero incoming transfers. A successful query with no matching incoming log is an empty result. Earlier RPC transport failures and null historical receipts are separately recorded; they do not supply those empty subsets.

The successful transaction receipt has 15 logs. Its UNI Transfer at log index `11` sends exactly `100000000000000000000000000` minimum units (100 million UNI at 18 decimals) to `0x000000000000000000000000000000000000dead`. The outer caller of GovernorBravo is a different address. Historical `totalSupply()` reads at end of block `24106377` and `24106378` both return `1000000000000000000000000000` minimum units (one billion UNI).

The metadata displayed by the offline client has its own precise state anchor: end block `24106388`, hash `0xc8810025143c010a6daa496d0435688d58a3f213fd992fdc9cf4b3b5bab67e29`. Three successful Tenderly `eth_call` reads use EIP-1898 `blockHash` with `requireCanonical: true` and return `decimals = 18`, `symbol = UNI`, and `name = Uniswap`. `token_state` marks this metadata anchor; the earlier decimals read at `24106378` is retained separately as `decimals_case_block`. `token_state_queries` retains the exact metadata requests and results.

The captured provider-reported finalized anchor is block `26134786`, hash `0x2cbe680468e04cbe429b1619c94aab7de94b3791e1620740c2296c1489008efe`, timestamp `2026-10-06T17:20:23Z`. The requested historical window is below that height. The snapshot contains no independently verified header ancestry, cryptographic inclusion proof, or consensus proof. RPC providers remain trusted data sources.

## Reproduce and verify

In a Windows PowerShell session with normal HTTPS network access, run these files from the repository root:

```powershell
& .\data\cases\uniswap93\collect-public.ps1
& .\data\cases\uniswap93\collect-fallback.ps1
& .\data\cases\uniswap93\collect-state.ps1
& .\data\cases\uniswap93\collect-metadata.ps1
python .\data\cases\uniswap93\assemble-fixture.py
```

These collectors allow only public read methods and have no credential input. Recollection refreshes capture times and the finalized anchor, so new file hashes are expected. Existing historical response fields may differ across provider implementations. The assembler asserts the observed historical values, transaction/block consistency, unique bounded log identities and two-provider receipt-log equality before publishing its aggregate.

The documentation and short source excerpts are manually observed snapshots with source pointers. Their local SHA-256 values commit to the retained excerpt bytes, not the full web pages. File hashes detect alteration; they do not establish factual truth or independent certification. Full interpretation and limitations are recorded in `docs/case-uniswap93.md` at the repository root.
