# Source provenance

This BOT review release derives from the ClueTide BOT maintenance baseline `f88009336bb548d782b70124e1740535020caefd`, which follows the common research baseline `cf19b51af64fa289de19ccd70ab26425c09584d3`.

The English product implementation is recorded in maintenance commit `8a688ada3a2376280e7d60d47f5a92254c5e6590`. The mainnet product and its verified receipt archive derive from maintenance commit `31c7cd7912ca22eb6ccabd159c0f4076d9dfd41d`. The release carries the product runtime, tests, Solidity contract and artifact, public Ethereum case snapshots, selected immutable evidence bundles, verified BOT Mainnet 677 receipts, historical BOT Testnet 968 receipts, and upstream license notices.

Release adaptations include the English product shell and route model, English workspace controls, static demo assets, portable setup documentation, and a release-specific package definition. Compatibility and quality tests use the original reports extracted into `tests/fixtures/legacy-conclusion.json` and `tests/fixtures/quality-regression.json`; their hashes are checked directly by the tests. The dependency lock limits pywin32 to Windows. Windows was tested; the POSIX commands are provided for reproduction.

`source-manifest.json` records the final release files and exact byte hashes. Run `python scripts/verify_sources.py` to check the checkout. The manifest is an integrity index, not an independent certification.

The two adaptive UNI evidence archives are the exact bytes associated with the recorded version commitments. Their narrative and original model output retain their source language. English UI summaries explain those artifacts while keeping observation, interpretation, and unresolved questions distinct.

Original third-party notices and public source references remain applicable. The project MIT license covers ClueTide's own code. Generated hero imagery is a product illustration; on-chain evidence is represented by the linked data and transactions.

## Bilingual product documentation · 2026-10-08

The current README offers separate Chinese and English product narratives with language links, generated concept artwork, editable diagrams and actual product screenshots. [Visual provenance](docs/readme-assets.md) and its [asset manifest](docs/readme-assets.json) record the production briefs, image origins and exact bytes. These documentation assets are release additions; the source baseline and original evidence artifacts above retain their recorded identities. The release integrity manifest includes the final documentation and image files.
