# ClueTide BOT English product

The accepted home and case concepts are stored alongside this specification. The implementation uses a shared quiet header, near-black `#050b0a`, mint `#19e8b5`, white headings, muted blue-gray body text, 1px green-gray rules and open, generous spacing. The sans-serif stack is Inter, Arial and system sans. Desktop gutters are 64px, reducing to 22px on mobile. Buttons have 10px corners, panels 12px, with 44px minimum interactive targets.

Home composition: brand/navigation/action, two-column headline and standalone evidence-lineage artwork, three-part testnet evidence strip, then the capture/review/correct horizontal sequence. Hero text and actions are native HTML. The standalone generated artwork has no UI overlay. It is blended at its outside edges without a color wash.

Case composition: breadcrumb and title, three exact-version steps, wide report panel and narrower on-chain panel. Report facts, interpretation and unknowns are explicit. The archive date is 8 Oct 2026. Header, buttons, rules, typography and responsive stacking continue across Verify, Developers and Workspace routes.

The implemented design uses the approved standalone lineage artwork, the verified archive date 8 Oct 2026, and one quiet header across all routes. Original Chinese evidence bytes stay in downloadable source archives. Display text is a separately labeled English adaptation. Archived status and current readback results have separate labels.

Routes: `#/`, `#/cases/uniswap93?step=v1|review|v2`, `#/verify`, `#/developers`, `#/workspace`. Legacy investigation/client/view query parameters and the old `#workspace` link resolve to Workspace. Static assets use a relative base for the public `/cluetide-app/bot/` path. The local workspace remains on origin 127.0.0.1:8765 to retain saved public transaction intents.

Core verification checks: independent browser ZIP validation and exact archive/version digest matching; case step Back/Forward; offline/static operation; local backend detection; live readback errors and retries; mobile navigation and keyboard focus. Workspace and the ZIP verifier are lazy-loaded.

## Implementation review

The accepted concepts and latest browser screenshots were opened with `view_image`. Desktop captures use 1536×1024, matching the concepts. Mobile captures use 390×844. The root review also used IAB; the implementation agent's IAB returned “Browser is not available: iab”, so its automated browser acceptance used installed Chrome through Playwright.

| Comparison | Implemented result |
| --- | --- |
| First viewport and section order | Large left-aligned headline, right-side standalone lineage artwork, proof strip and capture/review/correct continuation |
| Palette | Near-black background, mint primary actions, green-gray panel borders and subdued blue-gray body text |
| Typography | White sans-serif headings, 84px desktop hero maximum, responsive mobile line breaks and deliberate control sizes |
| Containers | Open landing layout; three-step case rail with report and chain panels; matching native controls |
| Native copy | Home headline, navigation and primary actions match the accepted wording; archive date and interpretation labels reflect actual evidence |
| Asset treatment | Approved standalone lineage artwork; edge-only blending preserves its colors |
| Responsive layout | Mobile navigation toggle, stacked case panels, wrapped hash fields, and no horizontal overflow at 390px |
| Workspace | Existing functional forms are embedded in the product layout, with a contained sidebar and matching palette |

The scoped workspace layout keeps the sidebar within its product surface and gives forms, tables, notices and evidence panels readable dark surfaces. Home and case were checked against their concepts in the final state. The first-viewport copy follows the approved product wording and factual archive labels. Additional explanatory detail appears within the functional verification and source-inspection surfaces.

Browser acceptance covers case selection, refresh/back/forward, both original ZIPs, damaged ZIP error recovery, a fresh local store without the original archived case, legacy investigation deep links, and mobile navigation. Original v1/v2 archive SHA-256 and parent commitments match their immutable sources. Two archived citation gaps remain visible after byte verification. Current chain readback requires the matching saved case, local version and review commitment; a fresh installation is directed to bundle verification and the block explorer.

The result faithfully follows the accepted design and verified archive facts. Local captures and detailed runtime results are retained in `local-only/product-qa`; release captures are retained in `local-only/release-qa`.

## Asset and source provenance

`home-concept.png` and `case-concept.png` are the ImageGen concepts generated for this product rebuild. The standalone `frontend/public/bot-demo/evidence-lineage.png` was generated in the same session and is used as the production hero artwork. Source generation filenames are `exec-4e799c0a-4e7c-4d75-8f5e-adc3a984a6f2.png`, `exec-801b3ace-638f-47a4-8d3c-06c8de16f914.png`, and `exec-565eae8f-9dd0-4c16-9a35-5ac1278257a2.png` respectively. The two archive downloads are byte-preserving copies of `data/demo/bundles/gcc-uniswap93-adaptive-v1.zip` and `gcc-uniswap93-adaptive-v2.zip`.

The authoritative source and setup destination is the private review repository's [BOT branch](https://github.com/HNNUYUXUAN/cluetide-review/tree/BOT). The public deployment target is [ClueTide BOT](https://hnnuyuxuan.github.io/cluetide-app/bot/). Publication verification is recorded by the release coordinator after deployment.
