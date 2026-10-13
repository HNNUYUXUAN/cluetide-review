"""Byte-preserving records shared by file migration and PostgreSQL execution."""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from fnmatch import fnmatchcase
import hashlib
from pathlib import Path, PurePosixPath
import re
from types import SimpleNamespace

from .releases import ReleaseError


def record_key(value: str) -> str:
    path = PurePosixPath(value)
    if value and (path.is_absolute() or ".." in path.parts or "\\" in value
                  or str(path) != value or not re.fullmatch(r"[a-zA-Z0-9_./-]+", value)):
        raise ReleaseError("The release storage boundary could not be verified.")
    return value


class FileRecordStore:
    """Read and write original records under an explicit local boundary."""
    def __init__(self, root: Path):
        self.root = root.absolute()

    def path(self, key):
        path = self.root / record_key(key)
        for candidate in (path, *path.parents):
            if candidate.is_symlink() or candidate.is_junction():
                raise ReleaseError("The release storage boundary could not be verified.")
            if candidate == self.root:
                break
        if not path.resolve().is_relative_to(self.root.resolve()):
            raise ReleaseError("The release storage boundary could not be verified.")
        return path

    def read(self, key):
        return self.path(key).read_bytes()

    def keys(self, prefix=""):
        return sorted(p.relative_to(self.root).as_posix() for p in self.path(prefix).rglob("*")
                      if p.is_file() and not p.is_symlink())


@dataclass(frozen=True)
class RecordPath:
    """A validated record address; no database record becomes an OS pathname."""
    store: object
    key: str = ""

    def __post_init__(self):
        record_key(self.key)

    def __truediv__(self, child):
        return RecordPath(self.store, (self.key + "/" if self.key else "") + str(child))

    @property
    def parent(self):
        value = PurePosixPath(self.key).parent.as_posix()
        return RecordPath(self.store, "" if value == "." else value)

    @property
    def name(self):
        return PurePosixPath(self.key).name

    @property
    def stem(self):
        return PurePosixPath(self.key).stem

    def exists(self):
        return self.store.exists(self.key)

    def read_bytes(self):
        return self.store.read(self.key)

    def read_text(self, encoding="utf-8"):
        return self.read_bytes().decode(encoding)

    def stat(self):
        return SimpleNamespace(st_size=len(self.read_bytes()))

    def is_symlink(self):
        return False

    def is_junction(self):
        return False

    def glob(self, pattern):
        # Match each component independently: '*' cannot consume '/'.
        parts = pattern.split("/")
        prefix = self.key + "/" if self.key else ""
        for key in self.store.keys(self.key):
            suffix = key[len(prefix):].split("/")
            if len(parts) == len(suffix) and all(fnmatchcase(a, b) for a, b in zip(suffix, parts)):
                yield RecordPath(self.store, key)

    def __lt__(self, other):
        return self.key < other.key


SCHEMA = """
CREATE TABLE IF NOT EXISTS cluetide_records (
    namespace text NOT NULL,
    path text NOT NULL CHECK (path !~ '(^/|\\.\\.|\\\\)' AND length(path) <= 512),
    payload bytea NOT NULL CHECK (octet_length(payload) <= 1048576),
    sha256 text NOT NULL,
    PRIMARY KEY (namespace, path)
);
CREATE TABLE IF NOT EXISTS cluetide_task_leases (
    namespace text NOT NULL,
    task_id text NOT NULL,
    attempt_id text NOT NULL,
    generation bigint NOT NULL CHECK (generation > 0),
    expires_at timestamptz NOT NULL,
    PRIMARY KEY (namespace, task_id)
);
CREATE TABLE IF NOT EXISTS cluetide_writer_control (
    namespace text PRIMARY KEY,
    enabled boolean NOT NULL DEFAULT false
);
"""


class PostgresRecordStore:
    """Short-lived connections allow Neon to sleep between actual requests."""
    def __init__(self, dsn: str, namespace: str):
        if not dsn or not re.fullmatch(r"[a-zA-Z0-9_-]{1,80}", namespace):
            raise ReleaseError("The persistent release storage is not configured.", 503)
        self._dsn, self.namespace = dsn, namespace
        self._connection = ContextVar(f"release_connection_{id(self)}", default=None)

    @contextmanager
    def connect(self):
        import psycopg
        # Never include the connection string or driver error in public output.
        try:
            with psycopg.connect(self._dsn, connect_timeout=6, autocommit=False) as connection:
                connection.execute("SET LOCAL statement_timeout='12s'")
                connection.execute("SET LOCAL lock_timeout='5s'")
                yield connection
        except psycopg.Error:
            raise ReleaseError("Persistent storage is unavailable. Retry the same request after checking its status.", 503) from None

    def initialize(self):
        with self.connect() as connection:
            connection.execute(SCHEMA)
            connection.execute("INSERT INTO cluetide_writer_control(namespace) VALUES (%s) ON CONFLICT DO NOTHING", (self.namespace,))

    @contextmanager
    def transaction(self, scope="catalog"):
        existing = self._connection.get()
        if existing is not None:
            yield existing
            return
        with self.connect() as connection:
            lock = int.from_bytes(hashlib.sha256(f"{self.namespace}/{scope}".encode()).digest()[:8], "big", signed=True)
            # Transaction locks work with Neon's transaction-mode pooler.
            connection.execute("SELECT pg_advisory_xact_lock(%s)", (lock,))
            token = self._connection.set(connection)
            try:
                yield connection
            finally:
                self._connection.reset(token)

    def connection(self):
        connection = self._connection.get()
        if connection is None:
            raise RuntimeError("Release records require a transaction")
        return connection

    def exists(self, key):
        return self.connection().execute("SELECT 1 FROM cluetide_records WHERE namespace=%s AND path=%s",
                                         (self.namespace, record_key(key))).fetchone() is not None

    def read(self, key):
        row = self.connection().execute("SELECT payload,sha256 FROM cluetide_records WHERE namespace=%s AND path=%s",
                                        (self.namespace, record_key(key))).fetchone()
        if row is None:
            raise FileNotFoundError(key)
        payload = bytes(row[0])
        if hashlib.sha256(payload).hexdigest() != row[1]:
            raise ReleaseError("The persistent release record failed its integrity check.")
        return payload

    def keys(self, prefix=""):
        record_key(prefix)
        return [row[0] for row in self.connection().execute(
            "SELECT path FROM cluetide_records WHERE namespace=%s AND starts_with(path,%s) ORDER BY path",
            (self.namespace, prefix + "/" if prefix else "")).fetchall()]

    def write(self, key, payload, *, immutable=False):
        record_key(key)
        if len(payload) > 1048576:
            raise ReleaseError("The release record exceeds the storage limit.", 413)
        connection = self.connection()
        if immutable:
            connection.execute("INSERT INTO cluetide_records VALUES (%s,%s,%s,%s) ON CONFLICT DO NOTHING",
                               (self.namespace, key, payload, hashlib.sha256(payload).hexdigest()))
            if self.read(key) != payload:
                raise ReleaseError("An immutable release record has different bytes.")
        else:
            connection.execute("""INSERT INTO cluetide_records VALUES (%s,%s,%s,%s)
                ON CONFLICT(namespace,path) DO UPDATE SET payload=EXCLUDED.payload,sha256=EXCLUDED.sha256""",
                               (self.namespace, key, payload, hashlib.sha256(payload).hexdigest()))

    def writer_enabled(self, *, lock=False):
        row = self.connection().execute("SELECT enabled FROM cluetide_writer_control WHERE namespace=%s" + (" FOR SHARE" if lock else ""), (self.namespace,)).fetchone()
        return bool(row and row[0])

    def set_writer(self, enabled):
        self.connection().execute("UPDATE cluetide_writer_control SET enabled=%s WHERE namespace=%s", (enabled, self.namespace))

    def claim(self, task_id, attempt_id):
        row = self.connection().execute("""INSERT INTO cluetide_task_leases VALUES (%s,%s,%s,1,clock_timestamp()+interval '90 seconds')
            ON CONFLICT(namespace,task_id) DO UPDATE
            SET attempt_id=EXCLUDED.attempt_id,generation=cluetide_task_leases.generation+1,expires_at=clock_timestamp()+interval '90 seconds'
            WHERE cluetide_task_leases.expires_at <= clock_timestamp()
            RETURNING generation,expires_at""", (self.namespace, task_id, attempt_id)).fetchone()
        return (row[0], row[1].isoformat()) if row else None

    def assert_lease(self, task_id, attempt_id, generation):
        row = self.connection().execute("""SELECT 1 FROM cluetide_task_leases
            WHERE namespace=%s AND task_id=%s AND attempt_id=%s AND generation=%s
            AND expires_at>clock_timestamp() FOR UPDATE""", (self.namespace, task_id, attempt_id, generation)).fetchone()
        if not row:
            raise ReleaseError("The execution lease expired. Check and recover the same attempt.")

    def lease(self, task_id, attempt_id):
        row = self.connection().execute("""SELECT generation,expires_at,expires_at>clock_timestamp(),attempt_id
            FROM cluetide_task_leases WHERE namespace=%s AND task_id=%s""",
                                       (self.namespace, task_id)).fetchone()
        return {"lease_attempt_id": row[3], "generation": row[0], "lease_expires_at": row[1].isoformat(), "lease_active": row[2]} if row else {"lease_active": False}

    def release_lease(self, task_id, attempt_id, generation):
        self.connection().execute("""UPDATE cluetide_task_leases SET expires_at=clock_timestamp()
            WHERE namespace=%s AND task_id=%s AND attempt_id=%s AND generation=%s""",
            (self.namespace, task_id, attempt_id, generation))
