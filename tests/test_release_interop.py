"""Execute the Python service, browser package module and Agent against the same originals."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

import pytest

from cluetide.release_exchange import verify_package
from cluetide.releases import ReleaseService, digest

ROOT = Path(__file__).resolve().parents[1]


class PackageChain:
    def __init__(self, task, versions):
        self.task, self.versions = task, versions

    def read_import(self, *, task, version_id):
        version = self.versions[version_id - 1]
        return {"getter_state": {
            "task": {**{role: self.task[role] for role in ("owner", "proposer", "reviewer", "executor", "receipt_signer")},
                     "target_hash": self.task["target_id"], "version_count": str(len(self.versions))},
            "version": {"version_id": str(version_id), "parent_version_id": str(version_id - 1),
                        "manifest_hash": version["manifest_hash"], "artifact_hash": version["artifact_hash"],
                        "deadline": str(version["expires_at"])}}, "read_block_number": "123"}


@pytest.mark.parametrize("noncanonical_manifest", [False, True])
def test_service_browser_agent_service_preserve_original_bytes(tmp_path, noncanonical_manifest):
    node = os.environ.get("CLUETIDE_NODE_BINARY") or shutil.which("node")
    if not node or not (ROOT / "frontend/node_modules/fflate").exists():
        pytest.skip("Install Node and frontend dependencies to run the cross-language package check")
    key = bytes.fromhex("07" * 32)
    source = ReleaseService(tmp_path / "source", signing_key=key)
    task = source.create_task({"title": "Shared review 协作 😀", "owner": "0x" + "11" * 20,
                              "proposer": "0x" + "11" * 20, "reviewer": "0x" + "22" * 20,
                              "executor": "0x" + "11" * 20})
    originals = [b"\xef\xbb\xbf<!doctype html>\r\n<h1>Approved release</h1>\r\n",
                 "\ufeff<!doctype html>\r\n<h1>协作 😀</h1>\r\n".encode("utf-8")]
    versions = []
    for number, original in enumerate(originals, 1):
        versions.append(source.create_version(task["task_id"], {
            "summary": "Exact version " + str(number), "source_reference": "git:interop",
            "artifact_text": original.decode("utf-8"), "test_evidence": ["BOM and CRLF retained"],
            "expires_at": int(time.time()) + 3600}))
    if noncanonical_manifest:
        # This fixture models an existing commitment whose original JSON used CRLF formatting.
        for version in versions:
            raw = ("\ufeff" + json.dumps(version["manifest"], ensure_ascii=False, indent=2).replace("\n", "\r\n") + "\r\n").encode("utf-8")
            directory = source._version_path(task["task_id"], version["version_id"])
            (directory / "manifest.json").write_bytes(raw)
            metadata = json.loads((directory / "version.json").read_text("utf-8"))
            metadata["manifest_hash"] = version["manifest_hash"] = digest(raw)
            (directory / "version.json").write_bytes(json.dumps(metadata).encode("utf-8"))
    source_bytes = source.bundle(task["task_id"], 2)
    (tmp_path / "service.zip").write_bytes(source_bytes)
    module = (ROOT / "frontend/src/release/package.ts").as_uri()
    program = f"""
import {{readFileSync,writeFileSync}} from 'node:fs';
import {{importPackage,packageZip,backupBytes}} from {json.dumps(module)};
globalThis.fetch=()=>{{throw new Error('Network is unavailable during package verification')}};
const base={json.dumps(str(tmp_path))};
const task=await importPackage(new Uint8Array(readFileSync(base+'/service.zip')));
const bytes=packageZip(task,task.versions.at(-1));
await importPackage(bytes);
writeFileSync(base+'/browser.zip',bytes);
writeFileSync(base+'/browser.json',backupBytes(task));
console.log(JSON.stringify({{status:'verified',versions:task.versions.map(v=>({{version_id:v.version_id,manifest_hash:v.manifest_hash,artifact_hash:v.artifact_hash}}))}}));
"""
    result = subprocess.run([node, "--input-type=module", "-"], input=program, text=True,
                            encoding="utf-8", capture_output=True, timeout=30, check=True)
    browser = json.loads(result.stdout)
    assert browser["status"] == "verified"
    checked = verify_package((tmp_path / "browser.zip").read_bytes())
    assert checked["complete_history"] and checked["selected_version"]["version_id"] == 2
    imported = checked["task"]
    assert [v["artifact_text"].encode("utf-8") for v in imported["versions"]] == originals
    for index, version in enumerate(imported["versions"]):
        assert version["manifest_text"].encode("utf-8") == (source._version_path(task["task_id"], index + 1) / "manifest.json").read_bytes()
    agent = subprocess.run([sys.executable, str(ROOT / "scripts/agent_release.py"), "verify-package", "--package", str(tmp_path / "browser.zip")],
                           text=True, encoding="utf-8", capture_output=True, timeout=30, check=True)
    agent_report = json.loads(agent.stdout)
    assert agent_report["status"] == "verified" and agent_report["current_chain_verified"] is False
    destination = ReleaseService(tmp_path / "destination", chain=PackageChain(task, versions), signing_key=key)
    fields = ("task_id", "title", "owner", "proposer", "reviewer", "executor", "receipt_signer", "target_id", "created_at")
    for version in imported["versions"]:
        payload = {"task": {field: imported[field] for field in fields}, "manifest": version["manifest"],
                   "manifest_text": version["manifest_text"], "artifact_text": version["artifact_text"]}
        destination.import_version(payload)
        assert destination.import_version(payload)["manifest_hash"] == version["manifest_hash"]
    roundtrip = verify_package(destination.bundle(task["task_id"], 2))["task"]
    assert [v["manifest_hash"] for v in roundtrip["versions"]] == [v["manifest_hash"] for v in versions]
    assert [v["artifact_text"].encode("utf-8") for v in roundtrip["versions"]] == originals
