"""Public release gateway boundaries and separation from investigation APIs."""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from cluetide.release_server import create_release_app
from cluetide.releases import ReleaseService


@pytest.fixture()
def service(tmp_path):
    return ReleaseService(tmp_path / "state", signing_key=bytes.fromhex("11" * 32))


def client_for(service, tmp_path: Path, origin=None):
    return TestClient(create_release_app(service=service, public_origin=origin,
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
