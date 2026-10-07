"""Restore the public saved DS v1 case while the loopback service is stopped.

The source archive, report and Agent outcome are preserved. This creates a new
local simulation record, without model, RPC, signing or broadcast operations.
"""
from __future__ import annotations

import argparse
import copy
from datetime import UTC, datetime
import hashlib
import json
import os
from pathlib import Path
import re
import socket
import sqlite3
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
# These modules do not import settings or load environment files.
from cluetide.bundles import import_bundle, manifest_sha256
from cluetide.registry import LocalRegistry, RegistryError, normalize_hash
from cluetide.store import CaseStore


V1_SOURCE = "data/demo/bundles/real-acceptance-v1.zip"
V2_REFERENCE = "data/demo/bundles/real-acceptance-v2.zip"
MAX_ARCHIVE_BYTES = 32 * 1024 * 1024


class SeedError(ValueError):
    pass


def ensure_service_stopped(port: int) -> None:
    if type(port) is not int or not 1 <= port <= 65535:
        raise SeedError("The local service port must be between 1 and 65535.")
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as connection:
        connection.settimeout(0.5)
        if connection.connect_ex(("127.0.0.1", port)) == 0:
            raise SeedError(f"Stop the loopback service on port {port} before restoring the demo.")


def safe_path(path: Path, root: Path) -> None:
    for candidate in (path, *path.parents):
        if candidate == root.parent:
            break
        if candidate.is_symlink() or candidate.is_junction():
            raise SeedError("Demo paths must not contain symlinks or directory junctions.")
    if not path.resolve().is_relative_to(root.resolve()) or path.name.casefold().startswith(".env"):
        raise SeedError("A demo path is outside the selected project or is an environment file.")


def frozen_bundle(path: Path, root: Path):
    safe_path(path, root)
    if path.suffix.casefold() != ".zip" or not path.is_file() or path.stat().st_size > MAX_ARCHIVE_BYTES:
        raise SeedError("A bounded public source ZIP is required.")
    with path.open("rb") as stream:
        payload = stream.read(MAX_ARCHIVE_BYTES + 1)
    if len(payload) > MAX_ARCHIVE_BYTES:
        raise SeedError("The source ZIP exceeds the size limit.")
    # Validate the exact frozen bytes that will be copied, not a second path read.
    with tempfile.TemporaryDirectory(prefix="cluetide-seed-verify-") as temporary:
        frozen_path = Path(temporary) / "source.zip"
        frozen_path.write_bytes(payload)
        verified = import_bundle(frozen_path)
    return payload, verified


def read_existing(store_path: Path, case_id: str) -> dict | None:
    if not store_path.exists():
        return None
    # Read-only inspection permits a true no-op without creating a DB/table.
    with sqlite3.connect(store_path.resolve().as_uri() + "?mode=ro", uri=True) as connection:
        row = connection.execute("SELECT document FROM cases WHERE id=?", (case_id,)).fetchone()
    return json.loads(row[0]) if row else None


def read_publication(store_path: Path, registry_path: Path, case_id: str):
    """Read one SQL snapshot without creating a database, table or mirror."""
    document = None
    state = None
    if store_path.exists():
        with sqlite3.connect(store_path.resolve().as_uri() + "?mode=ro", uri=True) as connection:
            connection.execute("BEGIN")
            row = connection.execute("SELECT document FROM cases WHERE id=?", (case_id,)).fetchone()
            document = json.loads(row[0]) if row else None
            has_registry = connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='publication_registry'"
            ).fetchone()
            if has_registry:
                row = connection.execute("SELECT state FROM publication_registry WHERE singleton=1").fetchone()
                state = json.loads(row[0]) if row else None
    registry = LocalRegistry.from_state(state) if state is not None else LocalRegistry(registry_path)
    registry.path = registry_path
    return document, registry


def registry_or_none(registry: LocalRegistry, case_id: str) -> dict | None:
    try:
        return registry.get_case(case_id)
    except RegistryError as exc:
        if exc.code == "unknown_case":
            return None
        raise


def existing_result(document: dict | None, registry: LocalRegistry, case_id: str, digest: str, local_data: Path, root: Path):
    registry_case = registry_or_none(registry, case_id)
    if document is None:
        if registry_case is not None:
            raise SeedError("The case already has a local registry record without a matching case document; no state was changed.")
        return None
    if registry_case is None or document.get("id") != case_id:
        raise SeedError("The existing local case is inconsistent; no state was changed.")
    CaseStore._validate_document(document, registry)
    for candidate in document.get("versions", []):
        if normalize_hash(candidate.get("content_hash", "")) != digest:
            continue
        version = registry.get_version(candidate["version_id"])
        if version["case_id"] != case_id or version["content_hash"] != digest:
            raise SeedError("The existing case and registry commitment disagree; no state was changed.")
        bundle_value = document.get("bundle_files", {}).get(str(version["version_id"]))
        if not isinstance(bundle_value, str):
            raise SeedError("The existing matching version has no local public archive.")
        bundle_path = Path(bundle_value)
        safe_path(bundle_path, root)
        if not bundle_path.resolve().is_relative_to((local_data / "bundles").resolve()):
            raise SeedError("The existing bundle path is outside the local bundle directory.")
        _, existing = frozen_bundle(bundle_path, root)
        if manifest_sha256(existing.manifest) != digest:
            raise SeedError("The existing public archive does not match its registry commitment.")
        return {"status": "already_present", "case_id": case_id, "manifest_hash": digest,
                "local_version_id": version["version_id"], "write_count": 0}
    raise SeedError("The case ID already exists with a conflicting manifest; no state was changed.")


def source_reference(root: Path, case_id: str, digest: str) -> tuple[dict | None, int | None]:
    path = root / V2_REFERENCE
    if not path.exists():
        return None, None
    payload, verified = frozen_bundle(path, root)
    report = verified.report
    if report.get("case_id") != case_id or report.get("revision") != 2 or report.get("parent_manifest_hash") != digest:
        raise SeedError("The read-only v2 reference does not extend the selected v1 manifest.")
    source_ids = set()
    for item in report.get("review_records", []):
        if item.get("case_id") == case_id and item.get("content_hash") == digest:
            source_ids.add(item.get("version_id"))
    for item in report.get("corrections", []):
        source_ids.add(item.get("parent_version_id"))
    if any(type(value) is not int or value < 1 for value in source_ids) or len(source_ids) > 1:
        raise SeedError("The v2 reference contains inconsistent source parent version identifiers.")
    source_id = next(iter(source_ids)) if source_ids else None
    return {
        "path": V2_REFERENCE, "archive_sha256": hashlib.sha256(payload).hexdigest(),
        "manifest_hash": manifest_sha256(verified.manifest), "revision": 2,
        "relationship": "Verified public v2 parent manifest; retained as read-only reference.",
        "registered_in_local_registry": False,
    }, source_id


def publish_frozen_bundle(payload: bytes, destination: Path, root: Path) -> None:
    """Publish the original ZIP bytes exclusively; retain complete crash orphans."""
    safe_path(destination, root)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = None
    try:
        with tempfile.NamedTemporaryFile(
            dir=destination.parent, prefix=".seed-bundle-", suffix=".zip", delete=False
        ) as stream:
            temporary_path = Path(stream.name)
            safe_path(temporary_path, root)
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.link(temporary_path, destination)
        except FileExistsError:
            if destination.is_symlink() or not destination.is_file() or destination.stat().st_size != len(payload):
                raise SeedError("The restored bundle filename already contains different bytes; no state was changed.") from None
            with destination.open("rb") as existing:
                if existing.read(MAX_ARCHIVE_BYTES + 1) != payload:
                    raise SeedError("The restored bundle filename already contains different bytes; no state was changed.") from None
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def restored_document(verified, source_bytes, reference, source_version_id, digest,
                      case_id, bundle_path, registry):
    """Build metadata inside the authority transaction using its fresh version ID."""
    report = verified.report
    agent = report["agent"]
    version = registry.create_case(case_id, digest, author="local-author",
        evidence_uri=f"/api/investigations/{case_id}/bundle?revision=1")
    restoration = {
        "mode": "local_replay_of_public_v1", "restored_at_utc": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "source_path": V1_SOURCE, "source_archive_sha256": hashlib.sha256(source_bytes).hexdigest(),
        "source_manifest_hash": digest, "source_case_id": case_id, "source_revision": 1,
        "local_case_id": case_id, "local_version_id": version["version_id"],
        "version_id_mapping": [{"source_version_id": source_version_id, "local_version_id": version["version_id"], "manifest_hash": digest}],
        "source_version_id_origin": "Public v2 parent/review references" if source_version_id is not None else "The public v1 archive does not record an original local global version ID.",
        "reference_v2": reference, "source_report_and_agent_preserved": True,
        "state_scope": "A new local state simulation record; original EVM state and original review records are not replayed.",
        "independence": "Local role labels remain controlled demo roles; this restoration does not establish independent review.",
        "model_requests_in_this_script": 0, "chain_reads_in_this_script": 0,
    }
    return {
        "id": case_id, "title": "Uniswap 93 · saved DS acceptance", "status": agent["status"],
        "input": {**copy.deepcopy(verified.evidence.get("request", {})), "mode": "offline", "agent_mode": "live"},
        "evidence": {**copy.deepcopy(verified.evidence), "raw": copy.deepcopy(verified.raw)},
        "agent": copy.deepcopy(agent), "report": copy.deepcopy(report),
        "registry": registry.get_case(case_id), "versions": [version], "reviews": [],
        "manifest_hash": digest, "manifest": copy.deepcopy(verified.manifest),
        "bundle_files": {str(version["version_id"]): str(bundle_path)}, "metadata": {"restoration": restoration},
    }


def restore_v1(root: Path = ROOT, *, port: int = 5186) -> dict:
    ensure_service_stopped(port)
    root = root.resolve()
    source_bytes, verified = frozen_bundle(root / V1_SOURCE, root)
    report = verified.report
    case_id = report.get("case_id")
    if not isinstance(case_id, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", case_id):
        raise SeedError("The public v1 report requires a portable case ID.")
    if report.get("revision") != 1 or report.get("parent_manifest_hash") is not None:
        raise SeedError("Only the original public v1 report can be restored.")
    agent = report.get("agent")
    acceptance = verified.raw.get("saved_model_acceptance") if isinstance(verified.raw, dict) else None
    if (not isinstance(agent, dict) or agent.get("status") != "completed" or agent.get("saved_acceptance") is not True
            or not isinstance(acceptance, dict) or acceptance.get("acceptance_pass") is not True
            or acceptance.get("network_transactions_signed_or_broadcast") != 0
            or "deepseek-v3.2" not in acceptance.get("model_names", [])):
        raise SeedError("The source must be the sanitized completed saved DS acceptance archive.")
    digest = manifest_sha256(verified.manifest)
    reference, source_version_id = source_reference(root, case_id, digest)
    local_data = root / "local-data"
    store_path = local_data / "cases.sqlite3"
    registry_path = local_data / "registry.json"
    bundle_path = local_data / "bundles" / f"{case_id}-restored-v1.zip"
    for path in (local_data, store_path, registry_path, bundle_path, store_path.with_suffix(".service.lock")):
        safe_path(path, root)
    previous, registry = read_publication(store_path, registry_path, case_id)
    already = existing_result(previous, registry, case_id, digest, local_data, root)
    if already is not None:
        return already
    store = CaseStore(store_path)
    with store.service_lease():
        ensure_service_stopped(port)

        def build(current_document, current_registry):
            if current_document is not None:
                raise SeedError("The case ID appeared during restoration; no case was overwritten.")
            existing_result(None, current_registry, case_id, digest, local_data, root)
            document = restored_document(verified, source_bytes, reference, source_version_id,
                                         digest, case_id, bundle_path, current_registry)
            publish_frozen_bundle(source_bytes, bundle_path, root)
            return document

        document = store.mutate_publication(registry, case_id, build)
    restoration = document["metadata"]["restoration"]
    return {"status": "restored_v1", "case_id": case_id, "manifest_hash": digest,
            "source_archive_sha256": restoration["source_archive_sha256"], "local_version_id": restoration["local_version_id"],
            "source_version_id": source_version_id, "reference_v2_registered": False,
            "model_requests_in_this_script": 0, "chain_reads_in_this_script": 0}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=5186, help="Stopped loopback service port (default 5186).")
    args = parser.parse_args()
    try:
        result = restore_v1(port=args.port)
    except (ValueError, OSError, sqlite3.Error, TypeError, KeyError):
        print(json.dumps({"status": "blocked", "reason": "Restoration was refused: stop the service and verify the source archives and existing local state. No conflicting case is overwritten."}), file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
