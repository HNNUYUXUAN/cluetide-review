"""Operator-only migration, inventory and writer control; never exports keys."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from cluetide.release_storage import FileRecordStore, PostgresRecordStore
from cluetide.release_persistent import PersistentReleaseService
from cluetide.releases import ReleaseError

PATTERNS = [
    r"tasks/0x[0-9a-f]{64}/task\.json",
    r"tasks/0x[0-9a-f]{64}/versions/[1-9][0-9]*/(?:version\.json|manifest\.json|artifact\.html)",
    r"task-capabilities/0x[0-9a-f]{64}\.json",
    r"feedback/0x[0-9a-f]{64}/0x[0-9a-f]{64}\.json",
    r"attempts/0x[0-9a-f]{64}/[1-9][0-9]*/(?:state\.json|receipt\.json)",
    r"targets/0x[0-9a-f]{64}/current\.json",
    r"targets/0x[0-9a-f]{64}/releases/[1-9][0-9]*/(?:artifact\.html|marker\.json)",
]


def transferable(key):
    return any(re.fullmatch(pattern, key) for pattern in PATTERNS)


def inventory(store):
    return [{"path": key, "bytes": len(payload), "sha256": hashlib.sha256(payload).hexdigest()}
            for key in store.keys() if transferable(key) for payload in [store.read(key)]]


def validate_records(service):
    """Revalidate original manifests, roles, feedback signatures and receipts."""
    with service._locked():
        count = {"tasks": 0, "versions": 0, "feedback": 0, "receipts": 0}
        for path in (service.root / "tasks").glob("*/task.json"):
            task = service._task(path.parent.name)
            count["tasks"] += 1
            for version in range(1, task["version_count"] + 1):
                service._version(task, version)
                count["versions"] += 1
            count["feedback"] += len(service.feedback(task["task_id"])["feedback"])
            for attempt in service._attempts(task["task_id"]):
                directory = service._attempt_path(task["task_id"], attempt["attempt_id"])
                if (directory / "receipt.json").exists():
                    verification = service._receipt(task, attempt["attempt_id"])["verification"]
                    if not verification["signature_valid"] or attempt["status"] == "succeeded" and not verification["artifact_matches"]:
                        raise ReleaseError("A migrated receipt failed signature or byte verification.")
                    count["receipts"] += 1
        return count


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--namespace", required=True)
    parser.add_argument("--manifest", type=Path)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("init")
    commands.add_parser("verify")
    migrate = commands.add_parser("import-files")
    migrate.add_argument("--source", type=Path, required=True)
    migrate.add_argument("--old-writer-paused", action="store_true", required=True)
    export = commands.add_parser("export-files")
    export.add_argument("--destination", type=Path, required=True)
    writer = commands.add_parser("writer")
    writer.add_argument("--state", choices=["paused", "enabled"], required=True)
    args = parser.parse_args()
    try:
        store = PostgresRecordStore(os.environ.get("DATABASE_URL", ""), args.namespace)
        if args.command == "init":
            store.initialize()
            print(json.dumps({"status": "initialized", "writer": "paused"}))
            return 0
        with store.transaction("migration"):
            if args.command == "writer":
                if args.state == "enabled":
                    service = PersistentReleaseService(store, signing_key=os.environ.get("CLUETIDE_RELEASE_SIGNING_KEY"),
                        expected_signer="0xc0a54fc7daa998a2e8bacc015eeafd4591ac15a4")
                    service._signer()
                    validate_records(service)
                store.set_writer(args.state == "enabled")
                print(json.dumps({"status": "configured", "writer": args.state}))
                return 0
            if args.command in {"import-files", "export-files"} and store.writer_enabled():
                raise ReleaseError("Pause the database writer before a state migration.")
            if args.command == "import-files":
                source = FileRecordStore(args.source)
                expected = inventory(source)
                for item in expected:
                    key = item["path"]
                    # Mutable pointers/state can advance in the final frozen delta;
                    # originals, feedback and signed receipts must remain identical.
                    immutable = not key.endswith(("/task.json", "/state.json", "/current.json")) and not key.startswith("task-capabilities/")
                    store.write(key, source.read(key), immutable=immutable)
                actual = inventory(store)
                if expected != actual:
                    raise ReleaseError("The migration inventory differs. Resolve missing or extra records before cutover.")
            elif args.command == "export-files":
                destination = FileRecordStore(args.destination)
                for item in inventory(store):
                    path = destination.path(item["path"])
                    payload = store.read(item["path"])
                    if path.exists() and path.read_bytes() != payload:
                        raise ReleaseError("Use a fresh rollback export directory to preserve original records.")
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_bytes(payload)
                if inventory(destination) != inventory(store):
                    raise ReleaseError("The rollback export did not preserve original bytes.")
            service = PersistentReleaseService(store)
            validated = validate_records(service)
            manifest = {"schema": "cluetide.storage-inventory.v1", "files": inventory(store), "validated": validated}
        if args.manifest:
            args.manifest.parent.mkdir(parents=True, exist_ok=True)
            args.manifest.write_text(json.dumps(manifest, indent=2) + "\n", "utf-8")
        print(json.dumps({"status": "verified", "records": len(manifest["files"]), "validated": validated}))
        return 0
    except (ReleaseError, OSError, ValueError, KeyError, TypeError):
        print(json.dumps({"status": "unavailable", "message": "Storage administration did not complete. Keep both writers paused and inspect configuration privately."}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
