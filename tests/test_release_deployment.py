"""Deployment configuration and readiness preserve the fixed production identity."""
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from cluetide.release_deployment import production_service
from cluetide.release_server import create_release_app
from cluetide.release_readiness import readiness, attempt_status
from cluetide.releases import ReleaseError
from test_releases import release


def test_preview_disables_mainnet_writes_and_keeps_secrets_out_of_configuration(monkeypatch):
    monkeypatch.setenv("VERCEL_ENV", "preview")
    monkeypatch.setenv("CLUETIDE_RELEASE_WRITES_ENABLED", "1")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("CLUETIDE_RELEASE_SIGNING_KEY", raising=False)
    service = production_service()
    assert not service.writes_enabled
    with pytest.raises(ReleaseError):
        service._signer()


def test_attempt_status_before_and_after_actual_local_publication(release):
    service, _, task, _, request = release
    assert attempt_status(service, task["task_id"], "1")["status"] == "not_started"
    service.execute(task["task_id"], request)
    observed = attempt_status(service, task["task_id"], "1")
    assert observed["status"] == "succeeded"
    assert observed["receipt_available"]
    assert not observed["can_resume"]


def test_readiness_never_claims_a_different_signer_is_ready(release):
    service, _, _, _, _ = release
    observed = readiness(service, check_chain=False)
    assert observed["storage"] == "local-files"
    assert observed["signer"] == "mismatch"
    assert not observed["writes_enabled"]


def test_pause_stops_public_writes_but_preserves_task_reads(release, monkeypatch, tmp_path):
    service, _, task, _, _ = release
    monkeypatch.setenv("CLUETIDE_RELEASE_WRITES_ENABLED", "0")
    with TestClient(create_release_app(service=service, dist=tmp_path / "none")) as client:
        assert client.get(f"/api/releases/tasks/{task['task_id']}").status_code == 200
        response = client.post(f"/api/releases/tasks/{task['task_id']}/execute", json={"version_id": 1, "attempt_id": "1", "start_transaction_hash": "0x" + "55" * 32})
        assert response.status_code == 503


def test_api_config_is_sixty_seconds_and_frontend_proxy_is_generated_only_for_actual_origin():
    root = Path(__file__).resolve().parents[1]
    config = json.loads((root / "vercel.json").read_text("utf-8"))
    assert config["functions"]["app.py"]["maxDuration"] == 60
    assert config["regions"] == ["iad1"]
    assert config["framework"] == "fastapi"


@pytest.mark.parametrize("invalid", [{"VERCEL_ENV": "production"},
    {"CLUETIDE_STORAGE_NAMESPACE": "production"}, {"CLUETIDE_RELEASE_SIGNING_KEY": "test-only"},
    {"DATABASE_URL": ""}])
def test_database_build_validation_refuses_unisolated_configuration(invalid, monkeypatch):
    import os
    import runpy
    from unittest.mock import patch
    root = Path(__file__).resolve().parents[1]
    hook = runpy.run_path(str(root / "scripts/vercel_build.py"))["main"]
    environment = {"CLUETIDE_RUN_DATABASE_TESTS": "1", "VERCEL_ENV": "preview",
                   "CLUETIDE_STORAGE_NAMESPACE": "preview", "DATABASE_URL": "test-only", **invalid}
    with patch.dict(os.environ, environment, clear=True), patch("subprocess.run") as run:
        with pytest.raises(SystemExit):
            hook()
        run.assert_not_called()
