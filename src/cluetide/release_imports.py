"""Import exact browser packages using independently confirmed chain commitments."""
from __future__ import annotations

from datetime import datetime

from pydantic import Field, field_validator

from .bundles import canonical_json_bytes
from .releases import (CreateTask, CreateVersion, StrictModel, ReleaseError, POLICY,
                       digest, identifier, now)


class ImportTask(CreateTask):
    task_id: str
    receipt_signer: str
    target_id: str
    created_at: str = Field(max_length=64)

    @field_validator("task_id", "target_id")
    @classmethod
    def commitment(cls, value):
        return identifier(value)

    @field_validator("receipt_signer")
    @classmethod
    def signer(cls, value):
        return CreateTask.address(value)

    @field_validator("created_at")
    @classmethod
    def timestamp(cls, value):
        datetime.fromisoformat(value.replace("Z", "+00:00"))
        return value


class ImportPackage(StrictModel):
    task: ImportTask
    manifest: dict
    artifact_text: str


def import_version(service, payload):
    payload = ImportPackage.model_validate(payload) if not isinstance(payload, ImportPackage) else payload
    task_data, manifest = payload.task.model_dump(), payload.manifest
    task_id = task_data["task_id"]
    version_id, parent = manifest.get("version_id"), manifest.get("parent_version_id")
    if type(version_id) is not int or not 1 <= version_id <= 50 or type(parent) is not int or parent != version_id - 1:
        raise ReleaseError("The imported version sequence is invalid.", 422)
    data = CreateVersion.model_validate({"summary": manifest.get("summary"), "source_reference": manifest.get("source_reference"),
        "test_evidence": manifest.get("test_evidence"), "expires_at": manifest.get("expires_at"),
        "artifact_text": payload.artifact_text})
    artifact = data.artifact_text.encode("utf-8")
    target_id = digest((POLICY + "/" + task_id).encode())
    artifact_hash = digest(artifact)
    expected = {"schema": "cluetide.release-manifest.v1", "task_id": task_id,
        "version_id": version_id, "parent_version_id": parent, "summary": data.summary,
        "source_reference": data.source_reference,
        "artifact": {"filename": "artifact.html", "sha256": artifact_hash, "bytes": len(artifact)},
        "operation": "publish_static", "target": "demo-site", "target_id": target_id,
        "policy_version": POLICY, "expires_at": data.expires_at, "test_evidence": data.test_evidence}
    if canonical_json_bytes(manifest) != canonical_json_bytes(expected) or task_data["target_id"] != target_id:
        raise ReleaseError("The imported files differ from their release manifest.", 422)
    manifest_bytes = canonical_json_bytes(manifest)
    manifest_hash = digest(manifest_bytes)
    with service._locked():
        if task_data["receipt_signer"] != service._signer().address.lower():
            raise ReleaseError("This task requires a different publication signer.", 409)
        gateway = service._adapter()
        state = gateway.read_import(task=task_data, version_id=version_id)
        chain_task, chain_version = state.get("getter_state", {}).get("task"), state.get("getter_state", {}).get("version")
        if not chain_task or not chain_version:
            raise ReleaseError("Register this task and version, then wait for three confirmations before importing.")
        if any(str(chain_task.get(key)).lower() != task_data[key] for key in ("owner", "proposer", "reviewer", "executor", "receipt_signer")) or chain_task.get("target_hash") != target_id:
            raise ReleaseError("The imported task differs from the confirmed chain roles or target.")
        required = {"version_id": str(version_id), "parent_version_id": str(parent), "manifest_hash": manifest_hash,
                    "artifact_hash": artifact_hash, "deadline": str(data.expires_at)}
        if any(chain_version.get(key) != value for key, value in required.items()):
            raise ReleaseError("The imported version differs from the confirmed chain commitments.")
        path = service._task_path(task_id)
        if path.exists():
            task = service._task(task_id)
            if any(task[key] != task_data[key] for key in ("owner", "proposer", "reviewer", "executor", "receipt_signer", "target_id")):
                raise ReleaseError("An existing task has different immutable roles.")
        else:
            if len(list((service.root / "tasks").glob("*/task.json"))) >= 100:
                raise ReleaseError("The demonstration task limit has been reached.")
            task = {"schema": "cluetide.release-task.v1", **task_data, "target": "demo-site", "version_count": 0, "chain_records": []}
        if parent > task["version_count"]:
            raise ReleaseError("Import the preceding package versions first.")
        directory = service._version_path(task_id, version_id)
        version_path = directory / "version.json"
        if version_path.exists():
            stored = service._json(version_path)
            if stored["manifest_hash"] != manifest_hash or stored["artifact_hash"] != artifact_hash:
                raise ReleaseError("An immutable version already has different commitments.")
            if (service._safe(directory / "manifest.json").read_bytes() != manifest_bytes
                    or service._safe(directory / "artifact.html").read_bytes() != artifact):
                raise ReleaseError("The saved package failed its byte verification.")
        else:
            if task["version_count"] >= version_id:
                raise ReleaseError("An earlier imported package is missing. Restore its evidence first.")
            version = {"version_id": version_id, "parent_version_id": parent, "manifest_hash": manifest_hash,
                       "artifact_hash": artifact_hash, "expires_at": data.expires_at, "created_at": now(),
                       "bundle_url": f"/api/releases/tasks/{task_id}/bundle?version={version_id}"}
            service._write(directory / "manifest.json", manifest_bytes, immutable=True)
            service._write(directory / "artifact.html", artifact, immutable=True)
            service._save(version_path, version, immutable=True)
        task["version_count"] = max(task["version_count"], version_id)
        service._save(path, task)
        return {"status": "imported", "task_id": task_id, "version_id": version_id,
                "manifest_hash": manifest_hash, "artifact_hash": artifact_hash,
                "read_block_number": state.get("read_block_number")}
