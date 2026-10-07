"""SQLite authority for case documents and local publication commitments.

Immutable archives are prepared first; document and registry then commit together.
registry.json is a recoverable compatibility mirror, never a second authority.
"""
from contextlib import contextmanager
import copy
import json
import os
from pathlib import Path
import sqlite3
import tempfile

from .registry import LocalRegistry, RegistryError


def _encode(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


class CaseStore:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self.registry_mirror_pending = False
        with self.connect() as db:
            db.execute("CREATE TABLE IF NOT EXISTS cases (id TEXT PRIMARY KEY, document TEXT NOT NULL)")
            db.execute("CREATE TABLE IF NOT EXISTS publication_registry "
                       "(singleton INTEGER PRIMARY KEY CHECK(singleton=1), state TEXT NOT NULL)")

    def connect(self):
        return sqlite3.connect(self.path, timeout=10)

    def _write_document(self, db, case_id, document):
        db.execute("INSERT INTO cases VALUES (?,?) ON CONFLICT(id) DO UPDATE SET document=excluded.document",
                   (case_id, _encode(document)))

    def _write_registry(self, db, state):
        db.execute("INSERT INTO publication_registry VALUES (1,?) "
                   "ON CONFLICT(singleton) DO UPDATE SET state=excluded.state", (_encode(state),))

    def _commit(self, db):
        db.commit()

    def put(self, case_id: str, document: dict):
        """Save runtime progress without replacing an already published result."""
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT document FROM cases WHERE id=?", (case_id,)).fetchone()
            previous = json.loads(row[0]) if row else None
            if previous and previous.get("versions"):
                if previous != document:
                    raise RegistryError("published_document_requires_transaction")
                return
            if document.get("versions"):
                raise RegistryError("published_document_requires_transaction")
            self._write_document(db, case_id, document)

    def get(self, case_id: str) -> dict | None:
        with self.connect() as db:
            row = db.execute("SELECT document FROM cases WHERE id=?", (case_id,)).fetchone()
        return json.loads(row[0]) if row else None

    def list(self) -> list[dict]:
        with self.connect() as db:
            rows = db.execute("SELECT document FROM cases ORDER BY rowid DESC").fetchall()
        return [json.loads(row[0]) for row in rows]

    @staticmethod
    def _validate_document(document, registry):
        case_id = document["id"]
        case = registry.get_case(case_id)
        versions, reviews = document.get("versions", []), document.get("reviews", [])
        if (document.get("registry") != case or
                [v["version_id"] for v in versions] != case["version_ids"] or
                [r["review_id"] for r in reviews] != case["review_ids"]):
            raise RegistryError("publication_state_mismatch")
        for version in versions:
            if version != registry.get_version(version["version_id"]):
                raise RegistryError("publication_state_mismatch")
            if not isinstance(document.get("bundle_files", {}).get(str(version["version_id"])), str):
                raise RegistryError("publication_state_mismatch")
        for review in reviews:
            expected = registry.get_review(review["review_id"])
            if {key: review.get(key) for key in expected} != expected:
                raise RegistryError("publication_state_mismatch")
            from .bundles import canonical_json_bytes
            import hashlib
            payload = {"case_id": case_id, "version_id": review["version_id"],
                       "content_hash": review["content_hash"], "comment": review.get("comment"),
                       "reviewer": review["reviewer"]}
            if not isinstance(review.get("comment"), str) or hashlib.sha256(canonical_json_bytes(payload)).hexdigest() != review["review_hash"]:
                raise RegistryError("publication_state_mismatch")
        if not versions or document.get("manifest_hash") != versions[-1]["content_hash"]:
            raise RegistryError("publication_state_mismatch")
        from .bundles import manifest_sha256
        if manifest_sha256(document["manifest"]) != document["manifest_hash"]:
            raise RegistryError("publication_state_mismatch")

    @staticmethod
    def _validate_archives(document):
        from .bundles import import_bundle, manifest_sha256
        for version in document["versions"]:
            verified = import_bundle(Path(document["bundle_files"][str(version["version_id"])]))
            if manifest_sha256(verified.manifest) != version["content_hash"]:
                raise RegistryError("publication_archive_mismatch")
            if version == document["versions"][-1] and (verified.report != document.get("report") or
                                                       verified.manifest != document.get("manifest")):
                raise RegistryError("publication_state_mismatch")

    def _registry_in_transaction(self, db, legacy):
        row = db.execute("SELECT state FROM publication_registry WHERE singleton=1").fetchone()
        if row:
            return LocalRegistry.from_state(json.loads(row[0]))
        candidate = LocalRegistry.from_state(legacy.export_state())
        documents = {row[0]: json.loads(row[1]) for row in db.execute("SELECT id,document FROM cases")}
        published = {key for key, value in documents.items() if value.get("versions")}
        if published != set(candidate.export_state()["cases"]):
            raise RegistryError("publication_state_mismatch")
        for key in published:
            self._validate_document(documents[key], candidate)
            self._validate_archives(documents[key])
        return candidate

    def _write_registry_mirror(self, path, state):
        temporary = None
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".registry-mirror-", suffix=".json",
                                             delete=False) as stream:
                temporary = Path(stream.name)
                stream.write((json.dumps(state, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    def _refresh_mirror(self, registry, committed):
        registry.adopt_state(committed)
        self.registry_mirror_pending = False
        if registry.path is None:
            return
        try:
            # Reacquire the SQLite writer lock and read the latest snapshot.
            # This prevents an older publisher overwriting a newer mirror.
            with self.connect() as db:
                db.execute("BEGIN IMMEDIATE")
                state = json.loads(db.execute("SELECT state FROM publication_registry WHERE singleton=1").fetchone()[0])
                self._write_registry_mirror(registry.path, state)
                registry.adopt_state(state)
        except (OSError, sqlite3.Error):
            self.registry_mirror_pending = True

    def initialize_registry(self, registry_or_path):
        path = registry_or_path.path if isinstance(registry_or_path, LocalRegistry) else Path(registry_or_path)
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT state FROM publication_registry WHERE singleton=1").fetchone()
            # Once migrated, even a damaged JSON mirror cannot block recovery.
            legacy = (LocalRegistry.from_state(json.loads(row[0])) if row else
                      registry_or_path if isinstance(registry_or_path, LocalRegistry) else LocalRegistry(path))
            candidate = self._registry_in_transaction(db, legacy)
            state = candidate.export_state()
            if not row:
                self._write_registry(db, state)
            self._commit(db)
        candidate.path = path
        self._refresh_mirror(candidate, state)
        return candidate

    def mutate_publication(self, registry, case_id: str, build) -> dict:
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            current = self._registry_in_transaction(db, registry)
            row = db.execute("SELECT document FROM cases WHERE id=?", (case_id,)).fetchone()
            target = build(copy.deepcopy(json.loads(row[0])) if row else None, current)
            if not isinstance(target, dict) or target.get("id") != case_id:
                raise RegistryError("publication_state_mismatch")
            self._validate_document(target, current)
            state = current.export_state()
            self._write_document(db, case_id, target)
            self._write_registry(db, state)
            try:
                self._commit(db)
            except sqlite3.Error:
                # An I/O failure can make commit acknowledgement ambiguous.
                # A fresh connection sees only committed bytes: accept an
                # exact target; otherwise propagate and roll back this writer.
                with self.connect() as check:
                    case_row = check.execute("SELECT document FROM cases WHERE id=?", (case_id,)).fetchone()
                    state_row = check.execute("SELECT state FROM publication_registry WHERE singleton=1").fetchone()
                if case_row != (_encode(target),) or state_row != (_encode(state),):
                    raise
        self._refresh_mirror(registry, state)
        return target

    def recover_interrupted(self) -> int:
        """Run once under the service lease before any investigation tasks exist."""
        count = 0
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            for case_id, encoded in db.execute("SELECT id,document FROM cases").fetchall():
                document = json.loads(encoded)
                if document.get("status") == "running":
                    document.update(status="partial", error="Previous local process ended before this investigation completed")
                    self._write_document(db, case_id, document)
                    count += 1
        return count

    @contextmanager
    def service_lease(self):
        """One task-owning service per database, including across different ports."""
        with self.path.with_suffix(".service.lock").open("a+b") as stream:
            stream.seek(0, os.SEEK_END)
            if stream.tell() == 0:
                stream.write(b"0")
                stream.flush()
            stream.seek(0)
            try:
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                raise RegistryError("local_service_already_running") from None
            try:
                yield
            finally:
                stream.seek(0)
                if os.name == "nt":
                    msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
