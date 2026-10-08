# Release validation

## Mainnet update — 8 October 2026

The current release presents **BOT Mainnet 677**. Four user-signed transactions were independently verified through the official RPC: registry deployment, v1, a review bound to v1, and v2 with parent v1. The complete final getter snapshot at block 25917270 contains two versions and one review, with version 2 as the head. The deployment creation and runtime bytecode match the included artifact.

| Current update check | Result |
| --- | --- |
| Release story and package regression tests | 28 passed |
| Frontend Node tests | 8 passed |
| TypeScript and Vite production build | Passed; 63 modules |
| Original testnet snapshot regeneration | Exact byte match |
| Mainnet snapshot and immutable archive check | Passed; four distinct successful transactions |
| Public-content package dry run | 518 files; no missing optional files |
| Product browser flows | 9 passed; zero page errors |
| Production-origin live UI readback | v1, review and v2 passed on chain 677 |

The readback checks observed 1,254 confirmations for v1, 859 for the review, and 470 for v2. Confirmation counts record their respective observation times. Mainnet transaction costs total 0.04501682 BOT, including deployment. The Chrome explorer also showed v2 as successful at block 25917190 with a fee of 0.00643662 BOT.

Current screenshots were captured from the mainnet product and visually inspected on desktop and mobile. The historical testnet snapshot, original evidence ZIPs, and previous validation scope below retain their original identities. The full 1,078-test suite below belongs to the preceding release; this update reran the affected story, package and frontend tests.

## Previous English product release

Validated on 8 October 2026 on Windows, Python 3.12 and Node.js 24.14.0, using this release's independent virtual environment and dependency installation.

| Check | Result |
| --- | --- |
| Locked Python installation and `pip check` | Passed |
| Complete `pytest -q` suite | 1,078 passed in 95.50 seconds |
| Locked frontend install and production TypeScript/Vite build | Passed; 63 modules |
| `npm test` | 8 passed |
| Story source and original archive commitments | Passed; three recorded steps |
| Product package allowlist and public-content validation | Passed; no missing optional files |
| Desktop and mobile browser flow checks | 7 passed; zero page errors |
| Independent verifier and archived-context review | 12 archive checks and four route/context checks passed |

The full Python run includes 45 browser-context regression tests for wallet preparation, saved intents, account/network changes, pagination, recovery and English controls. The independent review checked both original evidence ZIPs, malformed metadata, duplicate identifiers, corruption, size limits, fresh-store availability, and exact review-decision matching. It sent no verification or signing transactions.

The root visual review used the Codex in-app browser at 1536×1024 and 390×844. It exercised product navigation, case versions, the two bundled ZIP checks, developer links, mobile navigation and static-service guidance. At 390px, the home and case pages had a 390px document width. The two citation gaps remain visible after successful byte verification. GCC's separate static browser demo was also exercised through download and re-import.

The GitHub Pages deployment was opened at its public HTTPS address and the original v2 ZIP was verified there. The publication uses `docs/bot` in `HNNUYUXUAN/cluetide-app`; Pages reported `built` for commit `399ad610c096cc3da469600fe79767cb40c5475d`.

## Design verification

The home and case concepts and final screenshots were opened with `view_image` in the same review. Checks covered section order, headline hierarchy, palette, panel geometry, native copy, artwork treatment and responsive stacking. The case uses the actual archive date, 8 October 2026, and labels the English interpretation separately from its original evidence. See the [design and fidelity ledger](../design/release-20261008/DESIGN.md), [desktop product](images/bot-product.jpg), [case](images/bot-case.jpg), and [mobile product](images/bot-mobile.jpg).

## Reproduction

Run the backend test suite with the branch's Python 3.12 environment, then run `npm test` and `npm run build` in `frontend`, and validate the story source with `scripts/build_bot_story_data.py --check`. `frontend/tests/product_browser_check.py` runs the seven browser scenarios against the Vite preview at port 5173 with installed Chrome.

Browser acceptance covers the product, all three case steps, downloads, evidence verification, developer links, and the local workspace at desktop and mobile sizes. Static deployment is checked separately from the local API. Real-wallet broadcasts require the user's wallet confirmation and are outside this release's read-only acceptance.
