# Release Witness frontend

The English React and TypeScript application presents exact release packages, designated wallet approvals, one-time execution starts, signed receipts, and independent checks.

Use Node.js 24. Run `npm ci`, `npm test`, and `npm run build` here. Run `npm run dev` on `http://127.0.0.1:5177/` with the release API on port 8787. `/api` requests are proxied to that API. `npm run preview` uses port 4177. The backend serves the production build and API together on port 8787.

Both `index.html` and `bot.html` load the release application. Wallet actions use the browser extension after explicit user review. The configured product network is BOT Mainnet 677. Deployment and end-to-end mainnet wallet evidence for this new release workflow are pending.

The release sources depend on shared wallet and transaction types; their source dependencies are included for TypeScript checking. React, React DOM, and Scheduler license notices are preserved under [public/licenses](public/licenses).

See the [root README](../README.md) for installation, private state, deployment configuration, trust boundaries, and local EVM tests.
