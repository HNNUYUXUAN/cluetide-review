# Public wallet acceptance

Verified on 12 October 2026, BOT Mainnet 677. [Open the product](https://hnnuyuxuan.github.io/cluetide-app/release/) and [view the task](https://hnnuyuxuan.github.io/cluetide-app/release/#/task/0xb34ed8325749ed1182bbad3f61e3490a3af78f1912bac49c1ccc1dc8c414690a).

A fresh Chrome session connected MetaMask, registered a new task and v1, obtained the designated approval, registered changed v2, obtained a separate approval, consumed one start, published the exact artifact and recorded the signed receipt. Seven transactions passed independent parameter, receipt, canonical-block, bytecode and three-confirmation checks. Total gas: **814741**. Total fee: **0.01629482 BOT**.

| Action | Version | Transaction | Actual fee (BOT) |
| --- | --- | --- | --- |
| createTask | task | [0x7393f780b9…](https://scan.botchain.ai/tx/0x7393f780b90d266bbce2a94fb108604b359694685e699fe5218b41b8019e4c23) | 0.00373454 |
| proposeVersion | 1 | [0x46e70e4963…](https://scan.botchain.ai/tx/0x46e70e49638682b6e26732e62983a596c1c2194c460ec0db7dc077c19788122a) | 0.00293332 |
| approve | 1 | [0xfddbe74b8a…](https://scan.botchain.ai/tx/0xfddbe74b8add899aa2dc8b8fb3ae5f2031fa4135f0f02dd4b69723f37c03883c) | 0.000879 |
| proposeVersion | 2 | [0x664980a580…](https://scan.botchain.ai/tx/0x664980a580d8ac6f5836f8a2046bdcddb527fbfdaadccfca2e4c0223176cc03b) | 0.00298956 |
| approve | 2 | [0x23cfb6e9f7…](https://scan.botchain.ai/tx/0x23cfb6e9f7f6c86043f9c2a8caa8ccd74ce3a07bd08d12beb2510aab4fe4e554) | 0.000879 |
| consume | 2 | [0x9cfe79ce61…](https://scan.botchain.ai/tx/0x9cfe79ce6188edec299c59463732a7cd1cc507808288052d2a6a11e922e31321) | 0.0031919 |
| recordReceipt | 2 | [0x2ab663e8b0…](https://scan.botchain.ai/tx/0x2ab663e8b04edeacf962963c4d3bdbcf9022101c80014667872a08224742bae4) | 0.0016875 |

The [revision check](../data/demo/public-revision-check-20261012.json) used a canonical block with three confirmations before v2 approval. `consume(v1)` reverted with `StaleVersion`; `consume(v2)` reverted with `InvalidDecision`. Both checks were read-only and cost zero gas.

The [evidence directory](../data/demo/public-wallet-20261012/) contains both original manifests and artifacts, the exported task, signed receipt, independently downloaded output and byte manifest. The published v2 contains 194 bytes and SHA-256 `d90118354c28a4f730e628303f8984c795066e6bcfb7075f4118fd1032f77881`. The receipt signer is `0xc0a54fc7daa998a2e8bacc015eeafd4591ac15a4`. Receipt hash: `0x1c374c3a8b7bcc9f2741475b62db65c938a6936903e00c5c841cffd9ba254a9e`.

The witness contract is `0xca71b77708674f5f2407c0ad4e599eaf5eb07281`. Both demonstration wallet roles were controlled by the same participant. The agreed wallet test scope is MetaMask. This functional run was not continuously recorded; use the [recording guide](public-wallet-recording.md) for a fresh recorded sequence. Submission to the organizer remains a separate step.

Pages serves wallet and chain interactions directly. Actual publication uses a temporary Codespace service and requires it online. Current enforcement covers roles, exact versions, expiry and one-time starts. Financial penalties require a separate mechanism; software quality depends on review and tests.
