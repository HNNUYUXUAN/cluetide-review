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
