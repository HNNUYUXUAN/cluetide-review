"""Anonymous readiness checks reject inaccessible services and inconsistent evidence."""
import copy
import importlib.util
import io
import json
from pathlib import Path
import zipfile

import httpx
import pytest

from cluetide.bundles import canonical_json_bytes

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("release_demo_preflight", ROOT / "scripts/release_preflight.py")
preflight = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(preflight)
FIXTURE = json.loads((ROOT / "frontend/tests/fixtures/release-public.json").read_text("utf-8"))
RUNTIME = json.loads((ROOT / "contracts/artifacts/ClueTideReleaseWitness.json").read_text("utf-8"))["deployedBytecode"]


def package(task):
    version = task["versions"][0]
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("task.json", canonical_json_bytes(task))
        archive.writestr("manifest.json", canonical_json_bytes(version["manifest"]))
        archive.writestr("artifact.html", version["artifact_text"].encode())
    return stream.getvalue()


@pytest.fixture
def transport():
    task = copy.deepcopy(FIXTURE["task"])
    version = task["versions"][0]
    changes, requests = {}, []
    cors = {"access-control-allow-origin": preflight.FRONTEND_ORIGIN}
    block = {"number": "0x62", "hash": "0x" + "aa" * 32}
    reads = 0

    def handle(request):
        nonlocal reads
        requests.append(request)
        assert "cookie" not in request.headers and "authorization" not in request.headers
        if request.url.host == "rpc.botchain.ai":
            payload = json.loads(request.content)
            method = payload["method"]
            result = {"eth_chainId": "0x2a5", "eth_blockNumber": "0x64", "eth_getCode": RUNTIME,
                      "eth_getBlockByNumber": dict(block)}[method]
            if method == "eth_getBlockByNumber":
                reads += 1
                if changes.get("reorg") and reads > 1:
                    result["hash"] = "0x" + "bb" * 32
            return httpx.Response(200, json={"jsonrpc": "2.0", "id": 1, "result": changes.get(method, result)})
        if request.url.host == httpx.URL(preflight.SITE_URL).host:
            return httpx.Response(200, content=b"<!doctype html><html lang='en'></html>", headers={"content-type": "text/html"})
        if changes.get(request.url.path):
            return changes[request.url.path]
        if request.url.path == "/api/health":
            return httpx.Response(200, json={"status": "ok", "application": "agent_release_witness"})
        if request.url.path == "/api/releases/config":
            value = {"chain": {"status": "configured", "chain_id": 677, "contract_address": preflight.CONTRACT},
                     "executor": {"receipt_signer": preflight.RECEIPT_SIGNER, "operation": "publish_static", "target": "demo-site"},
                     "limits": {"artifact_bytes": 32768}}
            if changes.get("signer"):
                value["executor"]["receipt_signer"] = changes["signer"]
            return httpx.Response(200, json=value, headers=cors)
        if request.url.path == "/api/releases/imports":
            assert request.method == "OPTIONS"
            headers = {**cors, "access-control-allow-methods": "GET,POST,OPTIONS", "access-control-allow-headers": "content-type"}
            headers.update(changes.get("cors", {}))
            return httpx.Response(200, headers=headers)
        if request.url.path.endswith("/bundle"):
            assert request.url.params["version"] == "1"
            return httpx.Response(200, content=changes.get("bundle", package(task)), headers=cors)
        if request.url.path == "/api/releases/read":
            assert json.loads(request.content) == {"task_id": task["task_id"], "version_id": 1}
            state = {"task": {**{role: task[role] for role in ("owner", "proposer", "reviewer", "executor", "receipt_signer")},
                               "target_hash": task["target_id"]}, "version": {"version_id": "1", "manifest_hash": version["manifest_hash"],
                                                                                "artifact_hash": version["artifact_hash"]}}
            state["version"].update(changes.get("commitments", {}))
            return httpx.Response(200, json={"getter_state": state}, headers=cors)
        raise AssertionError(f"Unexpected request: {request.method} {request.url.path}")

    client = httpx.Client(transport=httpx.MockTransport(handle), follow_redirects=False, trust_env=False)
    yield client, changes, requests, task
    client.close()


def run(transport, **kwargs):
    client, _, _, task = transport
    return preflight.run_preflight(client=client, task_id=task["task_id"], version=1, **kwargs)


def test_complete_preflight_uses_anonymous_read_only_requests_and_persists_exact_bundle(transport, tmp_path):
    report = run(transport, download_dir=tmp_path)
    assert report["status"] == "ready"
    assert len(report["checks"]) == 7
    assert all(check["status"] == "passed" and check["checked_at"] for check in report["checks"].values())
    client, _, requests, task = transport
    assert not client.is_closed
    assert (tmp_path / f"cluetide-{task['task_id']}-v1.zip").read_bytes() == package(task)
    assert all(request.method in {"GET", "OPTIONS"} or request.url.host == "rpc.botchain.ai"
               or request.url.path == "/api/releases/read" for request in requests)
    witness = report["checks"]["fixed_witness"]
    assert witness["read_block_number"] == 98 and witness["minimum_confirmations"] == 3


def test_private_port_redirect_is_reported_and_other_checks_continue(transport):
    transport[1]["/api/health"] = httpx.Response(302, headers={"location": "https://github.com/login"})
    report = run(transport)
    assert report["status"] == "attention_required"
    assert "HTTP 302" in report["checks"]["public_port"]["message"]
    assert report["checks"]["fixed_witness"]["status"] == "passed"
    assert report["checks"]["evidence_download"]["status"] == "passed"


@pytest.mark.parametrize("change,check", [
    ({"signer": "0x" + "11" * 20}, "api_configuration"),
    ({"eth_chainId": "0x1"}, "fixed_witness"),
    ({"eth_getCode": "0x6000"}, "fixed_witness"),
    ({"reorg": True}, "fixed_witness"),
    ({"cors": {"access-control-allow-origin": "*"}}, "cors"),
    ({"cors": {"access-control-allow-credentials": "true"}}, "cors"),
    ({"cors": {"access-control-allow-methods": "GET"}}, "cors"),
    ({"commitments": {"manifest_hash": "0x" + "ee" * 32}}, "task_commitments"),
])
def test_preflight_rejects_different_network_contract_signer_cors_or_commitments(transport, change, check):
    transport[1].update(change)
    report = run(transport)
    assert report["status"] == "attention_required"
    assert report["checks"][check]["status"] == "failed"


def test_tampered_package_fails_before_chain_comparison(transport):
    task = copy.deepcopy(transport[3])
    task["versions"][0]["artifact_text"] += "changed"
    transport[1]["bundle"] = package(task)
    report = run(transport)
    assert report["checks"]["evidence_download"]["status"] == "failed"
    assert report["checks"]["task_commitments"]["status"] == "failed"
    assert all(request.url.path != "/api/releases/read" for request in transport[2])


def test_large_and_invalid_public_responses_produce_specific_failed_checks(transport):
    transport[1]["/api/health"] = httpx.Response(200, content=b"x" * 2049)
    transport[1]["bundle"] = b"PK invalid zip"
    report = run(transport)
    assert "evidence limit" in report["checks"]["public_port"]["message"]
    assert report["checks"]["evidence_download"]["status"] == "failed"


@pytest.mark.parametrize("value", ["https://user:secret@example.com", "http://example.com", "https://example.com/?key=private"])
def test_public_origin_configuration_rejects_credentials_and_ambiguous_urls(value):
    with pytest.raises(preflight.PreflightError):
        preflight.checked_url(value, origin=True)
