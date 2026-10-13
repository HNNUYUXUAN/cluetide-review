"""Service migration adds only verified domain bindings to private access files."""
import json
import httpx
import pytest
from scripts.agent_release import migrate_service_access, read_access
from cluetide.release_readiness import CONTRACT, SIGNER

TASK = "0x" + "ab" * 32


def test_service_binding_preserves_original_access_and_does_not_transmit_capability(tmp_path):
    path = tmp_path / "access.json"
    original = {"https://old.example|" + TASK: "private-test-capability"}
    path.write_text(json.dumps(original), "utf-8")
    def handle(request):
        assert request.method == "GET"
        assert b"private-test-capability" not in request.content
        assert "X-ClueTide-Task-Key" not in request.headers
        if request.url.path.endswith("readiness"):
            return httpx.Response(200, json={"schema": "cluetide.readiness.v1", "chain_id": 677, "contract_address": CONTRACT, "receipt_signer": SIGNER, "contract": "matched", "signer": "matched"})
        return httpx.Response(200, json={"task_id": TASK, "receipt_signer": SIGNER})
    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        result = migrate_service_access(client, "https://new.example", "https://old.example", path)
    assert result["tasks"] == 1
    assert read_access(path) == {**original, "https://new.example|" + TASK: "private-test-capability"}


def test_wrong_service_identity_cannot_change_private_access(tmp_path):
    path = tmp_path / "access.json"
    original = json.dumps({"https://old.example|" + TASK: "private-test-capability"})
    path.write_text(original, "utf-8")
    with httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(200, json={"chain_id": 1}))) as client:
        with pytest.raises(ValueError):
            migrate_service_access(client, "https://new.example", "https://old.example", path)
    assert path.read_text("utf-8") == original
