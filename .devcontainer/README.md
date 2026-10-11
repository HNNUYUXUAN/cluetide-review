# Codespaces review environment

This container runs the English Agent Release Witness interface and its release API at one origin. It uses Python 3.12, Node.js 24, and the pinned runtime dependencies in [requirements.txt](requirements.txt). The installation script builds the frontend with `npm ci` and `npm run build`. The same entry points work with the full source tree and the public release source package.

Create a Codespace from the branch containing this directory. Initial setup runs [codespaces-install.sh](../scripts/codespaces-install.sh); each container start runs [codespaces-start.sh](../scripts/codespaces-start.sh). The API binds `0.0.0.0:8787` and serves the built frontend. A startup hook succeeds only after the local `/api/health` endpoint identifies the release application.

The external origin is derived from GitHub's `CODESPACE_NAME` and `GITHUB_CODESPACES_PORT_FORWARDING_DOMAIN` environment variables:

```text
https://<codespace-name>-8787.app.github.dev
```

The default forwarding domain is `app.github.dev`. The launcher sets `CLUETIDE_PUBLIC_ORIGIN` to the exact forwarded HTTPS origin before importing the application. GitHub terminates HTTPS; the container serves HTTP internally.

## Sharing the review

Port 8787 is forwarded automatically and remains private until its visibility is changed explicitly. An authorized operator can run:

```bash
gh codespace ports visibility 8787:public --codespace "$CODESPACE_NAME"
```

Public visibility permits unauthenticated internet access to the entire release application and API. Use a dedicated review Codespace with synthetic data and its generated receipt signer. GitHub organization policy may prevent public forwarding. GitHub resets public visibility to private after a Codespace restart, so verify the port setting before sharing the URL.

The release application prepares unsigned transactions and reads chain results. Wallet holders review, sign, and pay for each transaction in their browser wallet. The new release witness mainnet deployment is pending until its deployment and runtime verification are completed and configured through the release application.

## State, logs, and recovery

`CLUETIDE_DATA_DIR` is fixed to the workspace's `local-data` directory. Release state and the local receipt signer stay in `local-data/release-witness`; launcher metadata and logs stay in `local-data/codespaces`. The repository ignores `local-data`, `.venv`, and built frontend output. Treat backups of `local-data` as private signing material. The launcher creates its state directory with owner-only permissions and starts the application with a private file creation mask.

The launcher uses a nonblocking process lock, a PID record with Linux process start time and command, and a local health check. Running the start script again reuses a matching healthy process. An occupied port, unhealthy existing process, build omission, or failed server startup returns a nonzero status with the log path. It never terminates an unrelated process. Inspect failures with:

```bash
cat local-data/codespaces/server.json
tail -n 60 local-data/codespaces/server.log
bash scripts/codespaces-start.sh
```

After editing dependencies or UI source, rerun the installation script and restart the Codespace to load the rebuilt application. Workspace files under `/workspaces` survive a stop/start and container rebuild. Deleting the Codespace deletes its workspace state; keep any required private backup outside the Codespace before deletion.

If startup times out while its process remains alive, the PID record stays available for inspection. After correcting the reported problem, stop and start the Codespace to replace that process and rerun the startup hook. An unhealthy process is never reported as ready.

Codespaces provides a temporary review environment. Idle timeout and manual stopping suspend the server and forwarded URL. GitHub stops each running session after at most 12 hours, even with activity. The review configuration uses a 30-minute idle timeout when provisioned through the CLI. Active compute and retained storage consume the account's Codespaces allowance. Check the account's current billing and timeout settings before keeping a review environment running.

Creating a task returns its package-write capability once. The browser keeps it on the creating device; the Agent CLI keeps it in its ignored private access file. Package writes require `X-ClueTide-Task-Key`. Chain actions separately require the fixed operator's actual wallet transaction. The local service operator can rotate a task capability with `ReleaseService.issue_task_write_key(task_id)` when recovering access; rotating invalidates the previous key. Preserve private state and capabilities outside public exports.

## Local validation

The Python launcher checks can run with Python 3.12 on a development host:

```bash
python -m unittest discover -s .devcontainer -p 'test_codespaces*.py' -v
```

These checks validate startup decisions and actual local process readiness. Build the frontend first to include the same-origin UI and static asset check. Linux process identity is simulated when these tests run on a Windows host. They do not provision a Codespace or establish that a public URL is reachable.

Official references: [GitHub environment variables](https://docs.github.com/en/codespaces/developing-in-a-codespace/default-environment-variables-for-your-codespace), [port forwarding](https://docs.github.com/en/codespaces/developing-in-a-codespace/forwarding-ports-in-your-codespace), [port security](https://docs.github.com/en/codespaces/reference/security-in-github-codespaces#port-forwarding), [workspace persistence](https://docs.github.com/en/codespaces/developing-in-a-codespace/persisting-environment-variables-and-temporary-files), [lifecycle properties](https://github.com/devcontainers/spec/blob/main/docs/specs/devcontainerjson-reference.md#lifecycle-scripts), and [CLI port visibility](https://cli.github.com/manual/gh_codespace_ports_visibility).
