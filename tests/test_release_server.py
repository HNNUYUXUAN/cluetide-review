"""Public release gateway boundaries and separation from investigation APIs."""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from cluetide.release_server import create_release_app
from cluetide.releases import ReleaseService


@pytest.fixture()
def service(tmp_path):
    return ReleaseService(tmp_path / "state", signing_key=bytes.fromhex("11" * 32))


def client_for(service, tmp_path: Path, origin=None, *, codespaces_origin_rewrite=False):
    return TestClient(create_release_app(service=service, public_origin=origin,
                                        codespaces_origin_rewrite=codespaces_origin_rewrite,
                                        dist=tmp_path / "no-static-build"))


def test_health_and_release_only_surface(service, tmp_path):
    with client_for(service, tmp_path) as client:
        response = client.get("/api/health")
        assert response.json() == {"status": "ok", "application": "agent_release_witness"}
        assert response.headers["x-content-type-options"] == "nosniff"
        assert response.headers["x-frame-options"] == "DENY"
        assert "connect-src 'self'" in response.headers["content-security-policy"]
        assert client.get("/api/investigations").status_code == 404
        assert client.get("/api/bot/networks").status_code == 404


def test_unknown_host_and_cross_site_mutation_rejected(service, tmp_path):
    with client_for(service, tmp_path) as client:
        assert client.get("/api/health", headers={"Host": "other.example"}).status_code == 403
        assert client.post("/api/releases/tasks", json={},
                           headers={"Sec-Fetch-Site": "cross-site"}).status_code == 403


def test_explicit_public_origin_and_local_development_origin(service, tmp_path):
    with client_for(service, tmp_path, "https://release.example") as client:
        accepted = client.get("/api/health", headers={"Host": "release.example",
                                                     "Origin": "https://release.example"})
        assert accepted.status_code == 200
        assert client.get("/api/health", headers={"Origin": "https://other.example"}).status_code == 403
        assert client.get("/api/health", headers={"Origin": "http://127.0.0.1:5177"}).status_code == 200


def test_mutation_stream_has_actual_byte_limit(service, tmp_path):
    with client_for(service, tmp_path) as client:
        response = client.post("/api/releases/tasks", content=b"x" * (64 * 1024 + 1),
                               headers={"Content-Type": "application/json"})
        assert response.status_code == 413
        assert response.json()["detail"] == "API request body exceeds the size limit"


@pytest.mark.parametrize("origin", ["http://release.example", "https://user:pass@release.example",
                                   "https://release.example/path", "https://release.example#fragment",
                                   "https://release.example:444"])
def test_public_origin_must_be_a_single_https_origin(service, tmp_path, origin):
    with pytest.raises(ValueError):
        create_release_app(service=service, public_origin=origin, dist=tmp_path / "none")


CODESPACES_HOST = "review-demo-8787.app.github.dev"
CODESPACES_ORIGIN = "https://" + CODESPACES_HOST
CODESPACES_HEADERS = {"Host": CODESPACES_HOST, "Origin": "https://localhost:8787",
                      "Sec-Fetch-Site": "same-origin"}


@pytest.mark.parametrize("method", ["GET", "POST"])
def test_codespaces_rewritten_origin_requires_explicit_enable(service, tmp_path, method):
    with client_for(service, tmp_path, CODESPACES_ORIGIN) as client:
        assert client.request(method, "/api/health", headers=CODESPACES_HEADERS).status_code == 403


def test_codespaces_same_origin_browser_can_read_and_create_task(service, tmp_path):
    with client_for(service, tmp_path, CODESPACES_ORIGIN, codespaces_origin_rewrite=True) as client:
        assert client.get("/api/health", headers=CODESPACES_HEADERS).status_code == 200
        response = client.post("/api/releases/tasks", headers=CODESPACES_HEADERS, json={
            "title": "Browser release", "owner": "0x" + "11" * 20,
            "proposer": "0x" + "22" * 20, "reviewer": "0x" + "33" * 20,
            "executor": "0x" + "44" * 20,
        })
        assert response.status_code == 201
        assert response.json()["title"] == "Browser release"
        assert service.list_tasks()["tasks"][0]["task_id"] == response.json()["task_id"]


@pytest.mark.parametrize("site", [None, "cross-site", "same-site", "none", "Same-Origin"])
def test_codespaces_alias_requires_same_origin_fetch_metadata(service, tmp_path, site):
    headers = {**CODESPACES_HEADERS}
    if site is None:
        headers.pop("Sec-Fetch-Site")
    else:
        headers["Sec-Fetch-Site"] = site
    with client_for(service, tmp_path, CODESPACES_ORIGIN, codespaces_origin_rewrite=True) as client:
        assert client.get("/api/health", headers=headers).status_code == 403
        assert client.post("/api/releases/tasks", headers=headers, json={}).status_code == 403


@pytest.mark.parametrize("host", ["localhost:8787", "127.0.0.1:8787", "testserver",
                                  "other-demo-8787.app.github.dev"])
def test_codespaces_alias_requires_configured_public_host(service, tmp_path, host):
    with client_for(service, tmp_path, CODESPACES_ORIGIN, codespaces_origin_rewrite=True) as client:
        assert client.get("/api/health", headers={**CODESPACES_HEADERS, "Host": host}).status_code == 403


@pytest.mark.parametrize("origin", ["https://other.example", "https://localhost:5177",
                                    "https://127.0.0.1:8787", "https://localhost:8787/"])
def test_codespaces_alias_does_not_admit_other_origins(service, tmp_path, origin):
    with client_for(service, tmp_path, CODESPACES_ORIGIN, codespaces_origin_rewrite=True) as client:
        assert client.get("/api/health", headers={**CODESPACES_HEADERS, "Origin": origin}).status_code == 403


@pytest.mark.parametrize("origin", [None, "https://release.example", "https://review-demo-5177.app.github.dev",
                                    "https://review-demo-8787.app.github.dev.other.example",
                                    "https://-review-8787.app.github.dev", "https://review--8787.app.github.dev",
                                    "https://" + "a" * 59 + "-8787.app.github.dev"])
def test_codespaces_rewrite_configuration_requires_valid_forwarded_origin(service, tmp_path, origin):
    with pytest.raises(ValueError, match="Codespaces origin rewriting requires"):
        create_release_app(service=service, public_origin=origin, codespaces_origin_rewrite=True,
                           dist=tmp_path / "none")


def test_codespaces_keeps_exact_public_origin_and_cli_requests(service, tmp_path):
    with client_for(service, tmp_path, CODESPACES_ORIGIN, codespaces_origin_rewrite=True) as client:
        assert client.get("/api/health", headers={"Host": CODESPACES_HOST}).status_code == 200
        assert client.get("/api/health", headers={"Host": CODESPACES_HOST,
                                                  "Origin": CODESPACES_ORIGIN}).status_code == 200
