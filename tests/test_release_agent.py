"""Agent package-write access stays private and bound to one service and task."""
import importlib.util
import json
from pathlib import Path
import sys

import httpx
import pytest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("release_agent_client", ROOT / "scripts" / "agent_release.py")
agent = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(agent)
TASK = "0x" + "11" * 32
KEY = "test-package-capability-" + "a" * 32


def configure(monkeypatch, tmp_path, arguments, handler):
    original = httpx.Client
    monkeypatch.setattr(agent.httpx, "Client", lambda **kwargs: original(transport=httpx.MockTransport(handler), **kwargs))
    access = tmp_path / "private" / "access.json"
    monkeypatch.setattr(sys, "argv", ["agent_release.py", "--access-file", str(access), *arguments])
    monkeypatch.delenv("CLUETIDE_TASK_WRITE_KEY", raising=False)
    return access


def test_creation_saves_access_without_printing_it(monkeypatch, tmp_path, capsys):
    access = configure(monkeypatch, tmp_path, ["create-task", "--title", "Release", *sum((["--" + field, "0x" + "22" * 20] for field in ("owner", "proposer", "reviewer", "executor")), [])],
        lambda request: httpx.Response(201, json={"task_id": TASK, "write_key": KEY}))
    assert agent.main() == 0
    captured = capsys.readouterr()
    assert KEY not in captured.out + captured.err
    assert "write_key" not in captured.out
    assert json.loads(access.read_text("utf-8")) == {"http://127.0.0.1:8787|" + TASK: KEY}


def test_package_uses_private_key_for_matching_service_and_task(monkeypatch, tmp_path, capsys):
    artifact = tmp_path / "artifact.html"
    artifact.write_text("<h1>Release</h1>", "utf-8")
    calls = []
    def handle(request):
        calls.append(request)
        return httpx.Response(201, json={"version_id": 1})
    access = configure(monkeypatch, tmp_path, ["submit-version", "--task-id", TASK, "--artifact", str(artifact), "--summary", "Release", "--source-reference", "git:build", "--expires-at", "2000000000", "--test-evidence", "Passed"], handle)
    agent.save_access(access, "http://127.0.0.1:8787|" + TASK, KEY)
    assert agent.main() == 0
    assert calls[0].headers["X-ClueTide-Task-Key"] == KEY
    assert KEY.encode() not in calls[0].content
    assert KEY not in capsys.readouterr().out


@pytest.mark.parametrize("identity", ["https://other.example|" + TASK, "http://127.0.0.1:8787|0x" + "33" * 32])
def test_access_for_another_origin_or_task_does_not_send(monkeypatch, tmp_path, identity, capsys):
    calls = []
    access = configure(monkeypatch, tmp_path, ["submit-version", "--task-id", TASK, "--artifact", str(tmp_path / "unused"), "--summary", "Release", "--source-reference", "git:build", "--expires-at", "2000000000", "--test-evidence", "Passed"], lambda request: calls.append(request))
    agent.save_access(access, identity, KEY)
    assert agent.main() == 1
    assert not calls
    assert KEY not in capsys.readouterr().err


def test_public_writes_require_explicit_public_flag(monkeypatch, tmp_path, capsys):
    calls = []
    args = ["--url", "https://release.example", "create-task", "--title", "Public release",
            *sum((["--" + field, "0x" + "22" * 20] for field in ("owner", "proposer", "reviewer", "executor")), [])]
    configure(monkeypatch, tmp_path, args, lambda request: calls.append(request))
    assert agent.main() == 1 and not calls
    assert json.loads(capsys.readouterr().out)["status"] == "public_confirmation_required"
    sys.argv.append("--public")
    monkeypatch.setattr(agent.httpx, "Client", lambda **kwargs: httpx_mock(kwargs))
    def httpx_mock(kwargs):
        class Client:
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def request(self, *args, **kwargs):
                calls.append(args)
                return httpx.Response(201, json={"task_id": TASK, "write_key": KEY})
        return Client()
    assert agent.main() == 0 and len(calls) == 1


def test_artifact_submission_preserves_bom_crlf_and_unicode(monkeypatch, tmp_path):
    artifact = tmp_path / "artifact.html"
    raw = "\ufeff<h1>中文😀</h1>\r\n".encode("utf-8")
    artifact.write_bytes(raw)
    calls = []
    access = configure(monkeypatch, tmp_path, ["submit-version", "--task-id", TASK, "--artifact", str(artifact),
        "--summary", "Release", "--source-reference", "git:bytes", "--expires-at", "2000000000", "--test-evidence", "Passed"],
        lambda request: calls.append(request) or httpx.Response(201, json={"version_id": 1}))
    agent.save_access(access, "http://127.0.0.1:8787|" + TASK, KEY)
    assert agent.main() == 0
    assert json.loads(calls[0].content)["artifact_text"].encode("utf-8") == raw


def test_feedback_reads_exact_version_and_uploads_signed_file(monkeypatch, tmp_path, capsys):
    calls = []
    configure(monkeypatch, tmp_path, ["feedback", "--task-id", TASK, "--version-id", "2"],
        lambda request: calls.append(request) or httpx.Response(200, json={"task_id": TASK, "feedback": []}))
    assert agent.main() == 0
    assert calls[0].url.params["version"] == "2"
    path = tmp_path / "signed.json"
    path.write_text('{"reason":"Use the approved name","signature":"wallet-signed"}', "utf-8")
    sys.argv = ["agent_release.py", "feedback", "--task-id", TASK, "--file", str(path)]
    assert agent.main() == 0
    assert calls[-1].method == "POST"
    assert json.loads(calls[-1].content)["signature"] == "wallet-signed"


def test_feedback_file_supports_full_unicode_budget(monkeypatch, tmp_path):
    path = tmp_path / "signed-unicode.json"
    data = {"reason": "😀" * 4000, "acceptance_conditions": ["😀" * 1000] * 12, "signature": "wallet-signed"}
    path.write_bytes(json.dumps(data, ensure_ascii=True).encode("ascii"))
    assert path.stat().st_size > 128 * 1024
    calls = []
    configure(monkeypatch, tmp_path, ["feedback", "--task-id", TASK, "--file", str(path)],
        lambda request: calls.append(request) or httpx.Response(200, json={"status": "stored"}))
    assert agent.main() == 0
    assert json.loads(calls[0].content) == data
    path.write_bytes(b" " * (agent.MAX_FEEDBACK_REQUEST_BYTES + 1))
    assert agent.main() == 1
    assert len(calls) == 1


@pytest.mark.parametrize("decision,latest,deadline,result,expected", [
    (0, 1, 2000000000, 0, "awaiting_approval"), (1, 1, 2000000000, 0, "approved"),
    (3, 1, 2000000000, 0, "correction_required"), (1, 2, 2000000000, 0, "superseded"),
    (1, 1, 1, 0, "expired"), (4, 2, 1, 0, "consumed"), (4, 2, 1, 1, "receipt_recorded"), (4, 2, 1, 2, "failed")])
def test_agent_states_retain_consumed_rights_after_revision_or_expiry(decision, latest, deadline, result, expected):
    state = {"getter_state": {"task": {"version_count": str(latest)},
        "version": {"decision": str(decision), "deadline": str(deadline)}, "attempt": {"result": str(result)}}}
    assert agent.workflow_status(state, 1, 100) == expected


def test_wait_returns_signed_correction_or_bounded_timeout(monkeypatch, tmp_path, capsys):
    status = {"getter_state": {"task": {"version_count": "1"}, "version": {"decision": "3", "deadline": "2000000000"}}}
    def handle(request):
        if request.url.path.endswith("/feedback"):
            return httpx.Response(200, json={"feedback": [{"reason": "Exact correction", "version_id": 1}]})
        return httpx.Response(200, json=status)
    configure(monkeypatch, tmp_path, ["wait", "--task-id", TASK, "--version-id", "1", "--timeout", "0"], handle)
    assert agent.main() == 0
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "correction_required" and result["feedback"][0]["reason"] == "Exact correction"
    status["getter_state"]["version"]["decision"] = "0"
    assert agent.main() == 2
    assert json.loads(capsys.readouterr().out)["last_status"] == "awaiting_approval"


def test_export_validates_before_writing_and_verify_package_never_connects(monkeypatch, tmp_path, capsys):
    from test_releases import ConfirmedChain, OWNER, REVIEWER, EXECUTOR
    from cluetide.releases import ReleaseService
    import time
    source = ReleaseService(tmp_path / "source", chain=ConfirmedChain(), signing_key=bytes.fromhex("07" * 32))
    task = source.create_task({"title": "Agent export", "owner": OWNER, "proposer": OWNER, "reviewer": REVIEWER, "executor": EXECUTOR})
    source.create_version(task["task_id"], {"summary": "Export", "source_reference": "ref", "artifact_text": "<h1>HTML</h1>",
        "test_evidence": ["Passed"], "expires_at": int(time.time()) + 3600})
    output = tmp_path / "export.zip"
    configure(monkeypatch, tmp_path, ["export", "--task-id", task["task_id"], "--version-id", "1", "--output", str(output)],
        lambda request: httpx.Response(200, content=source.bundle(task["task_id"], 1)))
    assert agent.main() == 0 and output.exists()
    capsys.readouterr()
    def no_client(**kwargs):
        raise AssertionError("Offline package verification attempted HTTP")
    monkeypatch.setattr(agent.httpx, "Client", no_client)
    sys.argv = ["agent_release.py", "verify-package", "--package", str(output)]
    assert agent.main() == 0
    assert json.loads(capsys.readouterr().out)["scope"] == "offline_package_integrity"
