"""Persistent release service with task transactions and fenced publication."""
from contextlib import contextmanager
from contextvars import ContextVar
from functools import wraps
from pathlib import Path
import tempfile
import time

from .releases import ReleaseService, ReleaseError, ExecuteRequest, identifier, attempt_identifier, digest, now
from .release_storage import RecordPath


class PersistentReleaseService(ReleaseService):
    def __init__(self, store, *, signing_key=None, expected_signer=None, chain=None, writes_enabled=False, clock=time.time):
        self.store, self.root = store, RecordPath(store)
        self._key, self.expected_signer, self.chain = signing_key, expected_signer, chain
        self._clock, self.writes_enabled = clock, writes_enabled
        self.scope = ContextVar(f"release_task_{id(self)}", default="catalog")

    def _safe(self, path):
        if not isinstance(path, RecordPath) or path.store is not self.store:
            raise ReleaseError("The release storage boundary could not be verified.")
        return path

    @contextmanager
    def _locked(self):
        with self.store.transaction(self.scope.get()):
            yield

    def _write(self, path, payload, *, immutable=False):
        self.store.write(self._safe(path).key, payload, immutable=immutable)

    def _signer(self):
        from eth_account import Account
        if self._key is None:
            raise ReleaseError("The existing executor signing identity must be configured before writing.", 503)
        try:
            signer = Account.from_key(self._key)
            if self.expected_signer and signer.address.lower() != self.expected_signer.lower():
                raise ValueError()
            return signer
        except (ValueError, TypeError):
            raise ReleaseError("The executor signing identity does not match the configured signer.", 503) from None

    def require_writer(self):
        with self._locked():
            if not self.writes_enabled or not self.store.writer_enabled(lock=True):
                raise ReleaseError("Production writes are paused. Preserve this request and check readiness before retrying.", 503)
            self._signer()

    def attempt_status(self, task_id, attempt_id):
        task_id, attempt_id = identifier(task_id), attempt_identifier(attempt_id)
        with self.store.transaction(task_id):
            self._task(task_id)
            path = self._attempt_path(task_id, attempt_id)
            lease = self.store.lease(task_id, attempt_id)
            state = self._json(path / "state.json") if (path / "state.json").exists() else None
            return {"task_id": task_id, "attempt_id": attempt_id, "checked_at": now(),
                    "status": state["status"] if state else "not_started", "stage": state["stage"] if state else "awaiting_execution",
                    "receipt_available": (path / "receipt.json").exists(),
                    "can_resume": self.writes_enabled and self.store.writer_enabled() and not lease["lease_active"] and (not state or state["status"] not in {"succeeded", "failed"}),
                    **lease}

    def _bound_attempt(self, task, version, request):
        path = self._attempt_path(task["task_id"], request.attempt_id) / "state.json"
        if not path.exists():
            return None
        attempt = self._json(path)
        if any(attempt.get(key) != value for key, value in {
            "task_id": task["task_id"], "version_id": request.version_id,
            "manifest_hash": version["manifest_hash"], "artifact_hash": version["artifact_hash"],
            "start_transaction_hash": request.start_transaction_hash}.items()):
            raise ReleaseError("This execution attempt is bound to different release parameters.")
        return attempt

    def _stage_artifact(self, artifact, expected):
        with tempfile.TemporaryDirectory(prefix="cluetide-release-") as temporary:
            path = Path(temporary) / "artifact.html"
            path.write_bytes(artifact)
            staged = path.read_bytes()
            if digest(staged) != expected:
                raise ReleaseError("The staged artifact failed its integrity check.")
            return staged

    def execute(self, task_id, payload):
        task_id = identifier(task_id)
        request = payload if isinstance(payload, ExecuteRequest) else ExecuteRequest.model_validate(payload)
        with self.store.transaction(task_id):
            self.require_writer()
            task, version = self._task(task_id), None
            version = self._version(task, request.version_id)
            attempt = self._bound_attempt(task, version, request)
            if attempt and attempt["status"] in {"succeeded", "failed"}:
                return {**attempt, "verification": self._receipt(task, request.attempt_id)["verification"]}
            authorization = self._check_authorization(task, version, request.attempt_id, request.start_transaction_hash)
            lease = self.store.claim(task_id, request.attempt_id)
            if lease is None:
                return attempt or {"task_id": task_id, "attempt_id": request.attempt_id, "status": "pending", "stage": "leased"}
            generation, expiry = lease
            attempt = attempt or {"schema": "cluetide.execution-attempt.v1", "task_id": task_id, "version_id": request.version_id,
                "attempt_id": request.attempt_id, "manifest_hash": version["manifest_hash"], "artifact_hash": version["artifact_hash"],
                "start_transaction_hash": request.start_transaction_hash, "chain_grant": authorization, "started_at": now()}
            attempt.update(status="pending", stage="staging", execution_generation=generation, lease_expires_at=expiry)
            self._save(self._attempt_path(task_id, request.attempt_id) / "state.json", attempt)
            artifact = (self._version_path(task_id, request.version_id) / "artifact.html").read_bytes()
        # No database transaction spans filesystem work. A killed function leaves
        # a durable claim; recovery waits for its 90-second lease to expire.
        try:
            staged = self._stage_artifact(artifact, version["artifact_hash"])
        except (OSError, ReleaseError):
            with self.store.transaction(task_id):
                self.require_writer()
                self.store.assert_lease(task_id, request.attempt_id, generation)
                attempt["stage"] = "staging_failed"
                result = self._finish(task, version, attempt, "failed")
                self.store.assert_lease(task_id, request.attempt_id, generation)
                self.store.release_lease(task_id, request.attempt_id, generation)
                return result
        with self.store.transaction(task_id):
            self.require_writer()
            self.store.assert_lease(task_id, request.attempt_id, generation)
            current = self._bound_attempt(task, version, request)
            if current.get("execution_generation") != generation:
                raise ReleaseError("A newer execution instance owns this attempt.")
            destination = self._target(task) / "releases" / request.attempt_id
            marker = self._expected_marker(task, version, attempt)
            self._write(destination / "artifact.html", staged, immutable=True)
            self._save(destination / "marker.json", marker, immutable=True)
            self._switch_target(task, marker)
            if not self._read_target(task, version, attempt):
                raise ReleaseError("The published artifact could not be verified.")
            attempt["stage"] = "output_verified"
            result = self._finish(task, version, attempt, "succeeded")
            self.store.assert_lease(task_id, request.attempt_id, generation)
            self.store.release_lease(task_id, request.attempt_id, generation)
            # Bytes, target pointer, signature and completed state commit together.
            return result


def _scoped(name, *, writes=False):
    operation = getattr(ReleaseService, name)
    @wraps(operation)
    def scoped(self, *args, **kwargs):
        task_id = args[0] if args and isinstance(args[0], str) and args[0].startswith("0x") else None
        if args and hasattr(args[0], "task_id"):
            task_id = args[0].task_id
        if name == "import_version" and args:
            value = args[0]
            task_id = value.task.task_id if hasattr(value, "task") else value["task"]["task_id"]
        if name in {"receipt", "artifact"}:
            task_id = kwargs.get("task_id") or (args[1] if name == "receipt" and len(args) > 1 else task_id)
        token = self.scope.set(task_id or "catalog")
        try:
            with self._locked():
                if writes:
                    self.require_writer()
                return operation(self, *args, **kwargs)
        finally:
            self.scope.reset(token)
    return scoped


for _name in ("create_task", "create_version", "issue_task_write_key", "import_version", "submit_feedback", "prepare", "verify"):
    setattr(PersistentReleaseService, _name, _scoped(_name, writes=True))
for _name in ("get_task", "bundle", "read", "feedback", "authorize_task_write_key", "receipt", "verify_receipt", "artifact", "list_tasks", "config"):
    setattr(PersistentReleaseService, _name, _scoped(_name))
