# Release Witness frontend

The root entry implements the public English wallet product. It uses the existing witness ABI, browser EIP-1193 wallets, RFC 8785 canonical manifests and SHA-256 artifact commitments. Publication uses the separately configured controlled service. The former server-backed workspace remains at `bot.html`.

Use Node.js 24. Run `npm ci`, `npm test`, then `npm run build`. Vite development runs at 5177 and static preview at 4177. Deploy the root production build under the Pages `/release/` directory; relative asset paths and hash routes support direct links and refresh.

The original MiMo-inspired concepts inform typography, warm-white color and horizontal entry rows. All interface controls and text are rendered in HTML/CSS; transaction states come from actual wallet and chain results.

See the [root README](../README.md) for workflow, configuration, private state and mainnet evidence scope.
