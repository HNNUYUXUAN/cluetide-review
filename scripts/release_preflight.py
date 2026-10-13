"""Read-only, anonymous readiness checks for a public ClueTide demonstration."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys
import zipfile
from urllib.parse import urlsplit

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from cluetide.release_chain import ReleaseArtifact, ReleaseChainError
from cluetide.release_exchange import MAX_PACKAGE_BYTES, strict_json, verify_package
from cluetide.releases import ReleaseError

PUBLIC_ORIGIN = "https://cluetide-release-review-r4xpxrwg7xvrh5xp5-8787.app.github.dev"
FRONTEND_ORIGIN = "https://hnnuyuxuan.github.io"
SITE_URL = FRONTEND_ORIGIN + "/cluetide-app/release/"
RPC_URL = "https://rpc.botchain.ai"
CONTRACT = "0xca71b77708674f5f2407c0ad4e599eaf5eb07281"
RECEIPT_SIGNER = "0xc0a54fc7daa998a2e8bacc015eeafd4591ac15a4"
EXAMPLE_TASK = "0xb34ed8325749ed1182bbad3f61e3490a3af78f1912bac49c1ccc1dc8c414690a"


class PreflightError(ValueError):
    """A public demonstration prerequisite could not be verified."""


def timestamp():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def checked_url(value, *, origin=False):
    parsed = urlsplit(value)
    if (parsed.scheme not in {"https", "http"} or not parsed.hostname or parsed.username or parsed.password
            or parsed.query or parsed.fragment or (origin and parsed.path not in {"", "/"})
            or (parsed.scheme == "http" and parsed.hostname not in {"127.0.0.1", "localhost", "::1"})):
        raise PreflightError("Use an HTTPS public URL or a local development address.")
    return value.rstrip("/") if origin else value


def bounded_request(client, method, url, *, limit=MAX_PACKAGE_BYTES, **kwargs):
    with client.stream(method, url, **kwargs) as response:
        if not 200 <= response.status_code < 300:
            raise PreflightError(f"Anonymous request returned HTTP {response.status_code}.")
        content = bytearray()
        for chunk in response.iter_bytes(64 * 1024):
            if len(content) + len(chunk) > limit:
                raise PreflightError("The public response exceeds its evidence limit.")
            content.extend(chunk)
        return response.headers, bytes(content)


def run_preflight(*, public_origin=PUBLIC_ORIGIN, frontend_origin=FRONTEND_ORIGIN,
                  site_url=SITE_URL, rpc_url=RPC_URL, task_id=EXAMPLE_TASK, version=2,
                  client=None, download_dir=None):
    public_origin = checked_url(public_origin, origin=True)
    frontend_origin = checked_url(frontend_origin, origin=True)
    site_url, rpc_url = checked_url(site_url), checked_url(rpc_url)
    if (not isinstance(task_id, str) or len(task_id) != 66 or not task_id.startswith("0x")
            or any(c not in "0123456789abcdef" for c in task_id[2:]) or int(task_id[2:], 16) == 0
            or type(version) is not int or not 1 <= version <= 50):
        raise PreflightError("Choose an exact public task and version.")
    own_client = client is None
    client = client or httpx.Client(timeout=10, trust_env=False, follow_redirects=False)
    checks, context = {}, {}

    def check(name, operation):
        try:
            detail = operation() or {}
            checks[name] = {"status": "passed", "checked_at": timestamp(), **detail}
        except (ValueError, KeyError, TypeError, AttributeError, OSError, httpx.HTTPError, ReleaseError, ReleaseChainError, zipfile.BadZipFile) as error:
            message = str(error) if isinstance(error, PreflightError) else "The expected public response could not be verified."
            checks[name] = {"status": "failed", "checked_at": timestamp(), "message": message}

    def public_port():
        _, body = bounded_request(client, "GET", public_origin + "/api/health", limit=2048)
        if strict_json(body) != {"status": "ok", "application": "agent_release_witness"}:
            raise PreflightError("The public port does not identify the release service.")
        return {"access": "anonymous", "path": "/api/health"}

    def frontend():
        headers, body = bounded_request(client, "GET", site_url, limit=2 * 1024 * 1024)
        if "text/html" not in headers.get("content-type", "") or b"<html" not in body.lower():
            raise PreflightError("The public application HTML is unavailable.")
        return {"url": site_url, "bytes": len(body), "sha256": hashlib.sha256(body).hexdigest()}

    def config():
        headers, body = bounded_request(client, "GET", public_origin + "/api/releases/config",
                                        headers={"Origin": frontend_origin}, limit=64 * 1024)
        value = strict_json(body)
        chain, executor = value["chain"], value["executor"]
        if (chain.get("status") != "configured" or chain.get("chain_id") != 677
                or str(chain.get("contract_address")).lower() != CONTRACT
                or str(executor.get("receipt_signer")).lower() != RECEIPT_SIGNER
                or executor.get("operation") != "publish_static" or executor.get("target") != "demo-site"
                or value["limits"].get("artifact_bytes") != 32768):
            raise PreflightError("The service network, fixed contract, signer or publication scope differs.")
        if headers.get("access-control-allow-origin") != frontend_origin:
            raise PreflightError("The service config is not readable from the public application origin.")
        return {"chain_id": 677, "contract": CONTRACT, "receipt_signer": RECEIPT_SIGNER, "artifact_bytes": 32768}

    def cors():
        headers, _ = bounded_request(client, "OPTIONS", public_origin + "/api/releases/imports", limit=4096,
            headers={"Origin": frontend_origin, "Access-Control-Request-Method": "POST",
                     "Access-Control-Request-Headers": "content-type"})
        if (headers.get("access-control-allow-origin") != frontend_origin
                or "POST" not in {method.strip() for method in headers.get("access-control-allow-methods", "").upper().split(",")}
                or "content-type" not in headers.get("access-control-allow-headers", "").lower()
                or headers.get("access-control-allow-credentials", "").lower() == "true"):
            raise PreflightError("The release import CORS preflight differs from the public application boundary.")
        return {"origin": frontend_origin, "method": "POST", "credentials": False}

    def rpc(method, params):
        _, body = bounded_request(client, "POST", rpc_url, limit=512 * 1024,
            json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params})
        value = strict_json(body)
        if value.get("jsonrpc") != "2.0" or value.get("id") != 1 or "error" in value or "result" not in value:
            raise PreflightError("The BOT RPC response failed validation.")
        return value["result"]

    def witness():
        if int(rpc("eth_chainId", []), 16) != 677:
            raise PreflightError("The RPC network differs from BOT Mainnet 677.")
        head = int(rpc("eth_blockNumber", []), 16)
        if head < 2:
            raise PreflightError("The RPC cannot provide a three-confirmation read block.")
        number = hex(head - 2)
        before = rpc("eth_getBlockByNumber", [number, False])
        code = rpc("eth_getCode", [CONTRACT, number])
        artifact = ReleaseArtifact.load(ROOT / "contracts/artifacts/ClueTideReleaseWitness.json",
                                         ROOT / "contracts/ClueTideReleaseWitness.sol")
        if not isinstance(code, str) or code.lower() != artifact.runtime:
            raise PreflightError("The deployed witness code differs from the pinned source artifact.")
        after = rpc("eth_getBlockByNumber", [number, False])
        if (not isinstance(before, dict) or before.get("number") != number or not before.get("hash")
                or before.get("hash") != after.get("hash") or after.get("number") != number):
            raise PreflightError("The confirmed RPC block changed during the preflight.")
        return {"chain_id": 677, "contract": CONTRACT, "read_block_number": head - 2,
                "read_block_hash": before["hash"], "minimum_confirmations": 3,
                "runtime_sha256": artifact.metadata["runtime_sha256"]}

    def evidence():
        path = f"/api/releases/tasks/{task_id}/bundle?version={version}"
        headers, body = bounded_request(client, "GET", public_origin + path, headers={"Origin": frontend_origin})
        if headers.get("access-control-allow-origin") != frontend_origin:
            raise PreflightError("The evidence package CORS response differs from the public application origin.")
        verified = verify_package(body)
        task, selected = verified.get("task"), verified.get("selected_version")
        if (not task or task["task_id"] != task_id or not selected or selected["version_id"] != version
                or task["receipt_signer"] != RECEIPT_SIGNER or not verified["complete_history"]):
            raise PreflightError("The fixed-version download lacks its exact task, signer or preceding originals.")
        context["task"], context["version"] = task, selected
        if download_dir is not None:
            destination = Path(download_dir)
            destination.mkdir(parents=True, exist_ok=True)
            (destination / f"cluetide-{task_id}-v{version}.zip").write_bytes(body)
        return {"path": path, "bytes": len(body), "sha256": hashlib.sha256(body).hexdigest(),
                "task_id": task_id, "version_id": version, "complete_history": True}

    def task_commitments():
        if "task" not in context:
            raise PreflightError("The original package must pass before comparing the chain commitments.")
        _, body = bounded_request(client, "POST", public_origin + "/api/releases/read", limit=512 * 1024,
            headers={"Origin": frontend_origin}, json={"task_id": task_id, "version_id": version})
        state = strict_json(body)["getter_state"]
        task, selected = context["task"], context["version"]
        if (any(str(state["task"].get(role)).lower() != task[role] for role in
                ("owner", "proposer", "reviewer", "executor", "receipt_signer"))
                or state["task"].get("target_hash") != task["target_id"]
                or str(state["version"].get("version_id")) != str(version)
                or state["version"].get("manifest_hash") != selected["manifest_hash"]
                or state["version"].get("artifact_hash") != selected["artifact_hash"]):
            raise PreflightError("The downloaded evidence differs from the service's chain read.")
        return {"task_id": task_id, "version_id": version, "manifest_hash": selected["manifest_hash"],
                "scope": "service_chain_read_matches_download"}

    try:
        for name, operation in (("public_port", public_port), ("frontend", frontend), ("api_configuration", config),
                                ("cors", cors), ("fixed_witness", witness), ("evidence_download", evidence),
                                ("task_commitments", task_commitments)):
            check(name, operation)
        return {"schema": "cluetide.demo-preflight.v1", "status": "ready" if all(c["status"] == "passed" for c in checks.values()) else "attention_required",
                "checked_at": timestamp(), "public_origin": public_origin, "checks": checks}
    finally:
        if own_client:
            client.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--public-origin", default=PUBLIC_ORIGIN)
    parser.add_argument("--frontend-origin", default=FRONTEND_ORIGIN)
    parser.add_argument("--site-url", default=SITE_URL)
    parser.add_argument("--rpc-url", default=RPC_URL)
    parser.add_argument("--task-id", default=EXAMPLE_TASK)
    parser.add_argument("--version", type=int, default=2)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--download-dir", type=Path)
    args = parser.parse_args()
    try:
        report = run_preflight(**{key: value for key, value in vars(args).items() if key != "output"})
    except (ValueError, OSError) as error:
        print(json.dumps({"status": "invalid_configuration", "message": str(error)}))
        return 1
    raw = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(raw, encoding="utf-8")
    print(raw, end="")
    return 0 if report["status"] == "ready" else 1


if __name__ == "__main__":
    raise SystemExit(main())
