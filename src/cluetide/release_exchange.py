"""Bounded, byte-preserving release exchange shared by the service and Agent."""
from __future__ import annotations

from datetime import datetime
import io
import json
import math
from pathlib import PurePosixPath
import zipfile

from .bundles import canonical_json_bytes
from .releases import MAX_ARTIFACT_BYTES, POLICY, digest, has_text, identifier, CreateTask

MAX_PACKAGE_BYTES = 4 * 1024 * 1024
MAX_VERSION_REQUEST_BYTES = 512 * 1024
MAX_MEMBERS = 160
PUBLIC_CONTRACT = "0xca71b77708674f5f2407c0ad4e599eaf5eb07281"


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON key")
        result[key] = value
    return result


def strict_json(payload):
    def finite(_):
        raise ValueError("Non-finite JSON value")
    if isinstance(payload, bytes):
        payload = payload.decode("utf-8")
    value = json.loads(payload.removeprefix("\ufeff"), object_pairs_hook=unique_object, parse_constant=finite)
    def validate(item, depth=0):
        if depth > 64:
            raise ValueError("JSON nesting exceeds the exchange limit")
        if isinstance(item, str):
            item.encode("utf-8")
        elif isinstance(item, float) and not math.isfinite(item):
            raise ValueError("Non-finite JSON value")
        elif isinstance(item, list):
            for child in item:
                validate(child, depth + 1)
        elif isinstance(item, dict):
            for key, child in item.items():
                key.encode("utf-8")
                validate(child, depth + 1)
    validate(value)
    return value


def bounded_text(value, maximum, *, nonblank=False):
    if not isinstance(value, str) or not 1 <= len(value) <= maximum or (nonblank and not has_text(value)):
        raise ValueError("Invalid text")
    value.encode("utf-8")  # Reject unpaired surrogate code points.
    return value


def read_zip(payload: bytes) -> dict[str, bytes]:
    if not payload or len(payload) > MAX_PACKAGE_BYTES:
        raise ValueError("The package exceeds 4 MiB")
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        members = archive.infolist()
        if not 1 <= len(members) <= MAX_MEMBERS:
            raise ValueError("The package has too many members")
        names, size = set(), 0
        for member in members:
            name = member.filename
            path = PurePosixPath(name)
            if (name != member.orig_filename or name in names or not name or "\\" in name or ":" in name or "\x00" in name
                    or path.is_absolute() or any(part in {"", ".", ".."} for part in name.split("/"))
                    or member.is_dir() or member.flag_bits & 1 or (member.external_attr >> 16) & 0o170000 == 0o120000):
                raise ValueError("The package contains an invalid member path")
            names.add(name)
            size += member.file_size
            if member.file_size < 0 or size > MAX_PACKAGE_BYTES:
                raise ValueError("The package expands beyond 4 MiB")
        result = {member.filename: archive.read(member) for member in members}
        if sum(map(len, result.values())) != size:
            raise ValueError("The package member sizes differ")
        return result


def validate_manifest(manifest, artifact_text, task_id, target_id, manifest_text=None):
    if not isinstance(manifest, dict):
        raise ValueError("Invalid release manifest")
    version, parent = manifest.get("version_id"), manifest.get("parent_version_id")
    if type(version) is not int or not 1 <= version <= 50 or type(parent) is not int or parent != version - 1:
        raise ValueError("Invalid version sequence")
    bounded_text(manifest.get("summary"), 1000)
    bounded_text(manifest.get("source_reference"), 300)
    evidence = manifest.get("test_evidence")
    if not isinstance(evidence, list) or not 1 <= len(evidence) <= 12:
        raise ValueError("Invalid test evidence")
    for item in evidence:
        bounded_text(item, 1000)
    deadline = manifest.get("expires_at")
    if type(deadline) is not int or not 1 <= deadline <= 2**53 - 1:
        raise ValueError("Invalid manifest deadline")
    if not isinstance(artifact_text, str):
        raise ValueError("Invalid UTF-8 artifact")
    artifact = artifact_text.encode("utf-8")
    if not 1 <= len(artifact) <= MAX_ARTIFACT_BYTES or b"\0" in artifact:
        raise ValueError("Invalid UTF-8 artifact")
    expected = {"schema": "cluetide.release-manifest.v1", "task_id": task_id,
        "version_id": version, "parent_version_id": parent, "summary": manifest["summary"],
        "source_reference": manifest["source_reference"],
        "artifact": {"filename": "artifact.html", "sha256": digest(artifact), "bytes": len(artifact)},
        "operation": "publish_static", "target": "demo-site", "target_id": target_id,
        "policy_version": POLICY, "expires_at": deadline, "test_evidence": evidence}
    if canonical_json_bytes(expected) != canonical_json_bytes(manifest):
        raise ValueError("The artifact differs from its release manifest")
    if manifest_text is not None and not isinstance(manifest_text, str):
        raise ValueError("Invalid original manifest text")
    raw = canonical_json_bytes(manifest) if manifest_text is None else manifest_text.encode("utf-8")
    if len(raw) > MAX_VERSION_REQUEST_BYTES or strict_json(raw) != manifest:
        raise ValueError("The original manifest differs from its representation")
    return raw, artifact


def validate_task(value):
    if not isinstance(value, dict):
        raise ValueError("Invalid release task")
    task_id = identifier(value.get("task_id"))
    bounded_text(value.get("title"), 160)
    bounded_text(value.get("created_at"), 64)
    datetime.fromisoformat(value["created_at"].replace("Z", "+00:00"))
    roles = {key: CreateTask.address(value[key]) for key in ("owner", "proposer", "reviewer", "executor", "receipt_signer")}
    target = digest((POLICY + "/" + task_id).encode())
    if roles["proposer"] == roles["reviewer"] or value.get("target") != "demo-site" or value.get("target_id") != target:
        raise ValueError("The task roles or target differ")
    versions = value.get("versions")
    if not isinstance(versions, list) or len(versions) > 50:
        raise ValueError("Invalid release versions")
    seen, validated = set(), []
    for version in versions:
        if not isinstance(version, dict):
            raise ValueError("Invalid version record")
        raw, artifact = validate_manifest(version["manifest"], version["artifact_text"], task_id, target, version.get("manifest_text"))
        number = version["manifest"]["version_id"]
        if (number in seen or version.get("version_id") != number or digest(raw) != version.get("manifest_hash")
                or digest(artifact) != version.get("artifact_hash")
                or version.get("summary", version["manifest"]["summary"]) != version["manifest"]["summary"]
                or version.get("expires_at", version["manifest"]["expires_at"]) != version["manifest"]["expires_at"]):
            raise ValueError("The version commitments differ")
        seen.add(number)
        validated.append({"version_id": number, "parent_version_id": number - 1,
            "manifest": version["manifest"], "manifest_text": raw.decode("utf-8"),
            "manifest_hash": digest(raw), "artifact_hash": digest(artifact), "artifact_text": version["artifact_text"],
            "expires_at": version["manifest"]["expires_at"], "summary": version["manifest"]["summary"],
            "bundle_url": version.get("bundle_url", "")})
    validated.sort(key=lambda item: item["version_id"])
    return {"task_id": task_id, "title": value["title"], "created_at": value["created_at"], **roles,
        "target": "demo-site", "target_id": target, "version_count": max(seen, default=0), "versions": validated,
        "attempts": [], "chain_records": []}


def verify_package(payload: bytes):
    """Verify local evidence without accessing RPC or the release service."""
    if not payload or len(payload) > MAX_PACKAGE_BYTES:
        raise ValueError("The package exceeds 4 MiB")
    entries = read_zip(payload) if payload.startswith(b"PK") else {"task.json": payload}
    if "acceptance.json" in entries:
        return verify_acceptance(entries)
    if set(entries) - {"task.json", "manifest.json", "artifact.html"}:
        raise ValueError("Unexpected release package members")
    task = validate_task(strict_json(entries["task.json"])) if "task.json" in entries else None
    if ("manifest.json" in entries) != ("artifact.html" in entries):
        raise ValueError("The release package requires both originals")
    selected = None
    if "manifest.json" in entries:
        manifest = strict_json(entries["manifest.json"])
        if not isinstance(manifest, dict):
            raise ValueError("Invalid release manifest")
        task_id = identifier(manifest.get("task_id"))
        target = digest((POLICY + "/" + task_id).encode())
        raw, artifact = validate_manifest(manifest, entries["artifact.html"].decode("utf-8"), task_id, target,
                                          entries["manifest.json"].decode("utf-8"))
        selected = {"version_id": manifest["version_id"], "manifest_hash": digest(raw), "artifact_hash": digest(artifact)}
        if task is not None:
            versions = [item for item in task["versions"] if item["version_id"] == manifest["version_id"]]
            if (task["task_id"] != task_id or len(versions) != 1
                    or versions[0]["manifest_text"].encode("utf-8") != raw
                    or versions[0]["artifact_text"].encode("utf-8") != artifact):
                raise ValueError("Duplicate package representations differ")
    elif task is None:
        raise ValueError("No release evidence is present")
    return {"status": "verified", "scope": "offline_package_integrity", "task": task, "selected_version": selected,
        "complete_history": bool(task and [v["version_id"] for v in task["versions"]] == list(range(1, task["version_count"] + 1))),
        "chain_roles_required": task is None, "current_chain_verified": False,
        "files": {name: {"bytes": len(data), "sha256": digest(data)} for name, data in entries.items()}}


def verify_acceptance(entries):
    """Independently check an exported acceptance observation, entirely offline."""
    from .release_chain import recover_receipt_signer
    from .releases import attempt_identifier
    metadata = strict_json(entries["acceptance.json"])
    if (not isinstance(metadata, dict) or metadata.get("schema") != "cluetide.acceptance-bundle.v1" or metadata.get("chain_id") != 677
            or metadata.get("contract_address") != PUBLIC_CONTRACT or type(metadata.get("version_id")) is not int
            or not isinstance(metadata.get("observations"), dict) or not isinstance(metadata.get("transactions"), list)):
        raise ValueError("Invalid acceptance schema or network")
    contract = CreateTask.address(metadata["contract_address"])
    task = validate_task(strict_json(entries["task.json"]))
    if metadata["task_id"] != task["task_id"]:
        raise ValueError("The acceptance task differs")
    attempt_id = attempt_identifier(metadata["attempt_id"])
    versions = [v for v in task["versions"] if v["version_id"] == metadata["version_id"]]
    if len(versions) != 1:
        raise ValueError("The acceptance version is missing")
    version = versions[0]
    required = {"task.json", "receipt.json"}
    for original in task["versions"]:
        prefix = "versions/" + str(original["version_id"]) + "/"
        manifest_path, artifact_path = prefix + "manifest.json", prefix + "artifact.html"
        required.update((manifest_path, artifact_path))
        if (entries.get(manifest_path) != original["manifest_text"].encode("utf-8")
                or entries.get(artifact_path) != original["artifact_text"].encode("utf-8")):
            raise ValueError("Acceptance originals differ from task representations")
    files = metadata.get("files")
    if not isinstance(files, list) or len(files) != len(entries) - 1:
        raise ValueError("Incomplete acceptance file inventory")
    covered = set()
    for file in files:
        if not isinstance(file, dict):
            raise ValueError("Invalid acceptance file record")
        path = file["path"]
        if (path in covered or path == "acceptance.json" or path not in entries
                or file.get("bytes") != len(entries[path]) or file.get("sha256") != digest(entries[path])):
            raise ValueError("Acceptance file digest differs")
        covered.add(path)
    if covered != set(entries) - {"acceptance.json"}:
        raise ValueError("Incomplete acceptance file inventory")
    reference = metadata["start_reference"]
    if (not isinstance(reference, dict) or reference.get("schema") != "cluetide.start-reference.v1" or reference.get("task_id") != task["task_id"]
            or type(reference.get("version_id")) is not int
            or reference.get("version_id") != version["version_id"]):
        raise ValueError("The start reference differs")
    start_hash = identifier(reference.get("transaction_hash"))
    envelope = strict_json(entries["receipt.json"])
    if not isinstance(envelope, dict) or not isinstance(envelope.get("receipt"), dict):
        raise ValueError("Invalid acceptance receipt")
    receipt = envelope["receipt"]
    expected = {"schema": "cluetide.execution-receipt.v1", "chain_id": 677, "contract_address": contract,
        "task_id": task["task_id"], "version_id": version["version_id"], "attempt_id": attempt_id,
        "manifest_hash": version["manifest_hash"], "artifact_hash": version["artifact_hash"], "target_id": task["target_id"],
        "executor": task["executor"], "receipt_signer": task["receipt_signer"], "authorization_tx": start_hash,
        "operation": "publish_static", "environment": "isolated-demo"}
    if any(receipt.get(key) != value for key, value in expected.items()):
        raise ValueError("The execution receipt differs from the acceptance context")
    status = receipt.get("status")
    receipt_hash = digest(canonical_json_bytes(receipt))
    if (status not in {"succeeded", "failed"} or envelope.get("receipt_hash") != receipt_hash
            or envelope.get("signature_scheme") != "EIP-712" or envelope.get("signer", "").lower() != task["receipt_signer"]
            or receipt.get("output_hash") != (version["artifact_hash"] if status == "succeeded" else None)):
        raise ValueError("The receipt commitments differ")
    signer = recover_receipt_signer(677, contract, task["task_id"], version["version_id"], attempt_id, receipt_hash,
                                   1 if status == "succeeded" else 2, envelope["signature"])
    if signer != task["receipt_signer"]:
        raise ValueError("The execution receipt signer differs")
    if status == "succeeded":
        required.add("published/artifact.html")
        if entries.get("published/artifact.html") != version["artifact_text"].encode("utf-8"):
            raise ValueError("The downloaded artifact differs from the approved bytes")
    if set(entries) - {"acceptance.json"} != required:
        raise ValueError("Unexpected acceptance members")
    datetime.fromisoformat(metadata["observed_at"].replace("Z", "+00:00"))
    transactions = metadata["transactions"]
    if (len(transactions) > 100 or any(not isinstance(item, dict) for item in transactions)
            or not any(item.get("action") == "start_execution" and item.get("transaction_hash") == start_hash for item in transactions)):
        raise ValueError("The acceptance start transaction is missing")
    for transaction in transactions:
        if not isinstance(transaction.get("action"), str):
            raise ValueError("Invalid acceptance transaction action")
        identifier(transaction["transaction_hash"])
    return {"status": "verified", "scope": "offline_acceptance_integrity", "task_id": task["task_id"],
        "version_id": version["version_id"], "attempt_id": attempt_id, "receipt_signature_valid": True,
        "stored_artifact_matches": status == "succeeded", "current_chain_verified": False,
        "live_artifact_verified": False, "observed_at": metadata["observed_at"], "files": files}
