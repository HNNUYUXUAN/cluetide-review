# Persistent Vercel deployment

This runbook maps the organizer's October 2026 deployment tutorial to the release application. Deployment evidence must record actual URLs, commits and observations before production is announced.

| Tutorial section | Project configuration | Evidence required |
|---|---|---|
| 22–25: account and GitHub import | Existing personal Hobby account; existing `HNNUYUXUAN/cluetide-review`; API branch `workshop-codex/agent-release-witness`; frontend branch `vercel-production` | Limited repository authorization; actual project names and connected branch |
| 26–28, 41: frontend build | Vite; root `frontend`; Node 24; `npm ci`; `npm run build`; output `dist` | Fresh install, build logs, anonymous HTML and asset readback |
| 29, 43–44: backend and database | Separate FastAPI project at repository root; Python 3.12; `app.py`; 60 seconds; region `iad1`; Neon Free PostgreSQL nearby | API deployment, persistent database and secret configuration |
| 32–34: navigation and resources | Hash routing; same-origin `/api/*` proxy; generated `frontend/vercel.json` | Fixed-version URL, refresh, API and desktop/390-pixel checks |
| 45: Web3 | BOT Mainnet 677; fixed witness and receipt signer; three confirmations | Wallet and actual mainnet receipt evidence |
| 52: anonymous access | Use the actual production platform domains | Independent unauthenticated HTTP and browser readback |
| 58–61: source and delivery | Explicit source allowlist; signing key and database URL stay in backend secrets | Source manifest; acceptance bundle; inspected recording; submission status |

## Observed deployment state (13 October 2026)

- Frontend: https://cluetide.vercel.app/ ; project `cluetide`, root `frontend`, Vite. The first successful production deployment uses public source `624f2b8fc4840d13088db2a43b958650e08cb2c5`.
- API: https://cluetide-release-api.vercel.app/ ; project `cluetide-release-api`, repository root and FastAPI.
- Both the anonymous frontend and its same-origin `/api/health` return HTTP 200. Anonymous package import, refresh recovery and a 390-pixel viewport pass before the publication service is ready.
- Neon Free resources `cluetide-release-production` and `cluetide-release-preview` are separate databases in `iad1`, connected only to their corresponding Vercel environments. App authentication is disabled because this product uses designated wallet roles.
- Both database schemas are initialized with writers paused. The production readiness endpoint confirms PostgreSQL, the existing receipt signer and fixed contract. Migrated-record verification, cold starts and production writer cutover remain deployment gates. The existing Pages entry is retained until cutover validation passes.

## Configure the API

Import the existing GitHub repository into the API project and select the production branch explicitly. The root `vercel.json` configures FastAPI, `iad1` and `app.py` with a 60-second maximum. Python is constrained to 3.12 by `pyproject.toml`. The backend is installed from the public package's pinned dependencies.

Create a Neon Free database using Vercel Marketplace. Keep Production and Preview connections separate. Do not upgrade the plan or enable paid add-ons. The holder completes any new integration permissions and enters secrets. Configure:

- `DATABASE_URL`: the relevant Neon connection string, backend only.
- `CLUETIDE_STORAGE_NAMESPACE`: `production` in Production; a separate `preview` namespace in the Preview database.
- `CLUETIDE_RELEASE_SIGNING_KEY`: the existing signer's key, Production only. Its derived address must be `0xc0a54fc7daa998a2e8bacc015eeafd4591ac15a4`.
- `CLUETIDE_PUBLIC_ORIGIN`: the actual API HTTPS origin.
- `CLUETIDE_FRONTEND_ORIGINS`: the exact actual frontend HTTPS origin; comma-separated explicit preview origins where required.
- `CLUETIDE_RELEASE_WRITES_ENABLED`: `0` during setup and migration; `1` only in Production after cutover verification.

Vercel supplies `VERCEL`, `VERCEL_ENV` and deployment metadata. Preview cannot enable real mainnet writes through the application flag. Missing secrets fail closed. No production signing key is generated in the function filesystem.

Initialize the schema from a protected operator environment with `DATABASE_URL` already set:

```sh
python scripts/release_storage_admin.py --namespace production init
```

The database writer starts paused. `/api/releases/readiness` reports checked time, deployment commit, storage, signer, fixed contract and effective write readiness. `/api/releases/tasks/{id}/attempts/{attempt}/status` reports the original attempt, active task lease and resumability.

## Configure the frontend

After the API project's actual platform domain exists, generate and commit the proxy configuration:

```sh
python scripts/configure_vercel_frontend.py --api-origin https://ACTUAL-API-PROJECT.vercel.app
```

Import the same GitHub repository as the frontend project. Set root `frontend`, Vite, Node 24, install `npm ci`, build `npm run build`, output `dist`, and the explicit `vercel-production` deployment branch. Keep that branch at the same verified commit as `workshop-codex/agent-release-witness`. The import UI accepts the deployment branch URL and exposes the actual `frontend` directory before deployment. Only `/api/:path*` is rewritten to the API. Keep hash routes and asset paths intact. Set the exact frontend origin on the backend before mutation tests.

## Migrate and cut over

1. Take a protected backup of the existing runtime and generate a SHA-256 inventory. Preserve original bytes and the existing signer; signing keys are excluded from the migration inventory and public source.
2. Create `OLD_STATE/writer-paused` while holding the old release-store lock, then restart with `CLUETIDE_RELEASE_WRITES_ENABLED=0`. Both API requests and the Codespace startup path honor the persisted marker, so a future process or Codespace restore stays read-only. Verify reads still work and mutation requests are refused. Keep the database writer paused.
3. Import the final frozen state using `release_storage_admin.py --namespace production --manifest PRIVATE_PATH import-files --source OLD_STATE --old-writer-paused`. It copies tasks, versions, hashed task capabilities, feedback, attempts, target markers, artifacts and receipts. Immutable conflicts and extra records stop cutover. Manifest hashes, feedback signatures and receipt signatures are independently revalidated.
4. Run `verify`, inspect readiness and confirm the fixed signer. Enable the database writer with `writer --state enabled`, then enable the Production application flag. Old writes remain paused.
5. Add the actual service domain to private Agent access with `agent_release.py --url NEW_ORIGIN --access-file PRIVATE_PATH migrate-service --from-url OLD_ORIGIN`. The old binding remains; capabilities are never transmitted by this command.
6. Verify anonymous frontend/API reads, imports, fixed links and original downloads. Update Pages to a redirect preserving `location.hash` only after these pass. Browser-local material crosses origins through explicit export/import.

Rollback starts by pausing the new database writer. Export to a fresh protected directory with `export-files --destination FRESH_DIRECTORY`, compare the full inventory, restore the old file store including all new state, then remove the old `writer-paused` marker and enable the old writer only after all state checks pass. Never run two production writers.

## Execution and recovery guarantees

PostgreSQL records hold raw bytes with SHA-256 and unique validated paths. Task transactions use PostgreSQL advisory transaction locks. A task-wide 90-second execution lease has a monotonically increasing generation and identifies its current attempt. A superseded or expired generation cannot commit. The function writes and reads back the HTML in a temporary isolated directory. Published bytes, release marker, signed receipt and completed state commit atomically; repeated requests return the original attempt's result.

An interrupted function leaves a durable lease. Query attempt status; wait for its lease to expire and resume the same start grant. A staging I/O failure produces a signed failure receipt. A completed attempt remains available after process recreation. Neon connections close after actual requests; no keepalive is configured.

## Evidence gates

Run the complete Python/frontend/startup suites and real PostgreSQL tests with `CLUETIDE_TEST_DATABASE_URL` pointing only to isolated test storage. The CI workflow provisions PostgreSQL independently. Offline acceptance tests replace all network calls with immediate failures.

Record three real cold-start checks, including one after Neon has actually idled, each within 30 seconds. Verify original manifests, signed feedback and receipts after redeployment. Then reconcile the existing mainnet task and complete a new clean-browser task with the holder signing each wallet operation. Record transaction hashes, actual fees and a continuous English video containing both the webpage and MetaMask. Two role addresses controlled by one holder must be disclosed. Inspect the exported video before marking recording complete.

Official references: [Vite](https://vercel.com/docs/frameworks/frontend/vite), [FastAPI](https://vercel.com/docs/frameworks/backend/fastapi), [Neon integration](https://vercel.com/marketplace/neon), [Neon automatic recovery](https://neon.com/faster).
