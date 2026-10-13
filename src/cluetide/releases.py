"""Exact release packages and a fixed-directory, wallet-authorized executor."""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import secrets
import threading
import time
from typing import Any, Literal
import zipfile
import io

from eth_account import Account
from eth_account.messages import encode_typed_data
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .bundles import canonical_json_bytes


MAX_ARTIFACT_BYTES = 32768
MAX_REQUEST_BYTES = 60 * 1024
POLICY = "cluetide.demo-release.v1"
_HASH = re.compile(r"0x[0-9a-fA-F]{64}\Z")
_ADDRESS = re.compile(r"0x[0-9a-fA-F]{40}\Z")
_INTEGER = re.compile(r"[1-9][0-9]{0,77}\Z")


class ReleaseError(Exception):
    def __init__(self, message: str, status: int = 409):
        self.message, self.status = message, status
        super().__init__(message)


def digest(payload: bytes) -> str:
    return "0x" + hashlib.sha256(payload).hexdigest()


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def has_text(value: str) -> bool:
    """Use the same whitespace set as the browser's Unicode trim operation."""
    return bool(value.strip("\u0009\u000a\u000b\u000c\u000d\u0020\u00a0\u1680\u2000\u2001\u2002\u2003\u2004\u2005\u2006\u2007\u2008\u2009\u200a\u2028\u2029\u202f\u205f\u3000\ufeff"))


def identifier(value: str) -> str:
    if not isinstance(value, str) or not _HASH.fullmatch(value) or int(value, 16) == 0:
        raise ReleaseError("A valid release identifier is required.", 422)
    return value.lower()


def attempt_identifier(value: str | int) -> str:
    if isinstance(value, int) and not isinstance(value, bool):
        value = str(value)
    if not isinstance(value, str) or not _INTEGER.fullmatch(value) or int(value) >= 2**256:
        raise ReleaseError("A valid execution attempt identifier is required.", 422)
    return value


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class CreateTask(StrictModel):
    title: str = Field(min_length=1, max_length=160)
    owner: str
    proposer: str
    reviewer: str
    executor: str

    @field_validator("title")
    @classmethod
    def title_text(cls, value):
        if not has_text(value):
            raise ValueError("A task title is required")
        value.encode("utf-8")
        return value

    @field_validator("owner", "proposer", "reviewer", "executor")
    @classmethod
    def address(cls, value):
        if not _ADDRESS.fullmatch(value) or int(value, 16) == 0:
            raise ValueError("Invalid address")
        return value.lower()

    @model_validator(mode="after")
    def roles(self):
        if self.proposer == self.reviewer:
            raise ValueError("The proposer and reviewer must use different wallets")
        return self


class CreateVersion(StrictModel):
    summary: str = Field(min_length=1, max_length=1000)
    source_reference: str = Field(min_length=1, max_length=300)
    artifact_text: str = Field(min_length=1, max_length=MAX_ARTIFACT_BYTES)
    test_evidence: list[str] = Field(min_length=1, max_length=12)
    expires_at: int = Field(gt=0, le=2**53 - 1)

    @field_validator("summary", "source_reference")
    @classmethod
    def required_text(cls, value):
        if not has_text(value):
            raise ValueError("Release text is required")
        value.encode("utf-8")
        return value

    @field_validator("artifact_text")
    @classmethod
    def bounded_artifact(cls, value):
        if len(value.encode("utf-8")) > MAX_ARTIFACT_BYTES or "\x00" in value or not has_text(value):
            raise ValueError("Invalid artifact")
        return value

    @field_validator("test_evidence")
    @classmethod
    def bounded_evidence(cls, value):
        if any(not has_text(item) or len(item) > 1000 for item in value):
            raise ValueError("Invalid test evidence")
        for item in value:
            item.encode("utf-8")
        return value


class ChainRequest(StrictModel):
    action: Literal["deploy", "create_task", "submit_version", "approve", "request_correction", "revoke", "start_execution", "record_receipt"]
    account: str
    task_id: str | None = None
    version_id: int | None = Field(default=None, gt=0, le=1000)
    attempt_id: str | None = None
    expires_at: int | None = Field(default=None, gt=0, le=2**53 - 1)

    @field_validator("account")
    @classmethod
    def address(cls, value):
        return CreateTask.address(value)

    @field_validator("task_id")
    @classmethod
    def task(cls, value):
        return identifier(value) if value is not None else None

    @field_validator("attempt_id")
    @classmethod
    def attempt(cls, value):
        return attempt_identifier(value) if value is not None else None

    @model_validator(mode="after")
    def scope(self):
        if self.action != "deploy" and self.task_id is None:
            raise ValueError("A task is required")
        if self.action not in {"deploy", "create_task"} and self.version_id is None:
            raise ValueError("A version is required")
        if self.action == "record_receipt" and self.attempt_id is None:
            raise ValueError("An attempt is required")
        return self


class VerifyRequest(ChainRequest):
    transaction_hash: str

    @field_validator("transaction_hash")
    @classmethod
    def transaction(cls, value):
        return identifier(value)


class ExecuteRequest(StrictModel):
    version_id: int = Field(gt=0, le=1000)
    attempt_id: str
    start_transaction_hash: str

    @field_validator("attempt_id")
    @classmethod
    def attempt(cls, value):
        return attempt_identifier(value)

    @field_validator("start_transaction_hash")
    @classmethod
    def transaction(cls, value):
        return identifier(value)


class ReadRequest(StrictModel):
    task_id: str
    version_id: int | None = Field(default=None, gt=0, le=1000)
    attempt_id: str | None = None

    @field_validator("task_id")
    @classmethod
    def task(cls, value):
        return identifier(value)

    @field_validator("attempt_id")
    @classmethod
    def attempt(cls, value):
        return attempt_identifier(value) if value is not None else None


class ChainGateway:
    """Bind each public request to server-owned role, package and chain settings."""

    def __init__(self, root, *, service=None):
        from .release_chain import ReleaseChainService
        self.root = root
        self.service = service or ReleaseChainService()

    def config(self):
        contract = os.environ.get("CLUETIDE_RELEASE_CONTRACT")
        path = self.root / "chain.json"
        if path.is_symlink() or path.is_junction():
            raise ReleaseError("The release chain configuration is unavailable.", 503)
        if not contract and path.exists():
            try:
                if path.stat().st_size > 4096:
                    raise ValueError()
                value = json.loads(path.read_text("utf-8"))
                if value.get("chain_id") != 677 or set(value) != {"chain_id", "contract_address"}:
                    raise ValueError()
                contract = value["contract_address"]
            except (ValueError, OSError, KeyError, TypeError):
                raise ReleaseError("The release chain configuration is unavailable.", 503) from None
        if contract is not None and (not isinstance(contract, str) or not _ADDRESS.fullmatch(contract) or int(contract, 16) == 0):
            raise ReleaseError("The release chain configuration is unavailable.", 503)
        from .bot import NETWORKS
        return {"status": "configured" if contract else "unconfigured", "chain_id": 677,
                "contract_address": contract.lower() if contract else None,
                "minimum_confirmations": 3, "network": NETWORKS[677]}

    def _base(self, *, deploy=False):
        config = self.config()
        if not deploy and config["status"] != "configured":
            raise ReleaseError("The BOT Mainnet release contract is not configured.", 503)
        return {"chain_id": 677, **({} if deploy else {"contract_address": config["contract_address"]})}

    @staticmethod
    def _call(operation, payload):
        from .release_chain import ReleaseChainError
        try:
            return operation(payload)
        except ReleaseChainError:
            raise ReleaseError("The BOT Chain request could not be verified. Check the network and transaction parameters.", 503) from None

    def _intent(self, action, *, task, version, account, attempt_id=None, receipt=None):
        mapped = {"submit_version": "propose_version", "start_execution": "consume"}.get(action, action)
        payload = {**self._base(deploy=action == "deploy"), "action": mapped, "account": account}
        if action == "deploy":
            return payload
        payload["task_id"] = task["task_id"]
        if action == "create_task":
            if account != task["owner"]:
                raise ReleaseError("Use the task owner's wallet for this action.")
            payload.update({key: task[key] for key in ("proposer", "reviewer", "executor", "receipt_signer")})
            payload["target_hash"] = task["target_id"]
        elif action == "submit_version":
            if account != task["proposer"]:
                raise ReleaseError("Use the designated proposer's wallet for this action.")
            payload.update(parent_version_id=str(version["parent_version_id"]), manifest_hash=version["manifest_hash"],
                           artifact_hash=version["artifact_hash"], deadline=version["expires_at"])
        elif action in {"approve", "request_correction", "revoke"}:
            allowed = {task["reviewer"], task["owner"]} if action == "revoke" else {task["reviewer"]}
            if account not in allowed:
                raise ReleaseError("Use an authorized review wallet for this action.")
            payload["version_id"] = str(version["version_id"])
            if action == "approve":
                payload.update(manifest_hash=version["manifest_hash"], artifact_hash=version["artifact_hash"], deadline=version["expires_at"])
        elif action == "start_execution":
            if account != task["executor"]:
                raise ReleaseError("Use the designated executor's wallet for this action.")
            payload.update(version_id=str(version["version_id"]), manifest_hash=version["manifest_hash"], artifact_hash=version["artifact_hash"])
        elif action == "record_receipt":
            if not receipt["verification"]["signature_valid"]:
                raise ReleaseError("The execution receipt signature could not be verified.")
            if receipt["receipt"]["status"] == "succeeded" and not receipt["verification"]["artifact_matches"]:
                raise ReleaseError("The execution artifact could not be verified.")
            payload.update(attempt_id=attempt_id, receipt_hash=receipt["receipt_hash"],
                           result={"succeeded": 1, "failed": 2}[receipt["receipt"]["status"]], signature=receipt["signature"])
        return payload

    def prepare(self, action, **context):
        return self._call(self.service.prepare, self._intent(action, **context))

    def verify(self, action, *, transaction_hash, **context):
        return self._call(self.service.verify, {"prepared": self._intent(action, **context),
                                               "transaction_hash": transaction_hash, "min_confirmations": 3})

    def read(self, *, task, version_id=None, attempt_id=None):
        payload = {**self._base(), "task_id": task["task_id"]}
        if version_id is not None:
            payload["version_id"] = str(version_id)
        if attempt_id is not None:
            payload["attempt_id"] = attempt_id
        result = self._call(self.service.read, payload)
        chain_task = result.get("getter_state", {}).get("task")
        if chain_task is not None and any(chain_task.get(key) != task[key] for key in ("owner", "proposer", "reviewer", "executor", "receipt_signer")):
            raise ReleaseError("The chain task roles do not match this release task.")
        if chain_task is not None and chain_task.get("target_hash") != task["target_id"]:
            raise ReleaseError("The chain task target does not match this release task.")
        return result

    def read_import(self, *, task, version_id):
        return self._call(self.service.read, {**self._base(), "task_id": task["task_id"],
                         "version_id": str(version_id), "confirmations": 3})

    def authorize_execution(self, *, task, version, attempt_id, start_transaction_hash):
        base = self._base()
        result = self._call(self.service.authorize_execution, {**base, "task_id": task["task_id"],
            "version_id": str(version["version_id"]), "attempt_id": attempt_id, "start_transaction_hash": start_transaction_hash,
            "account": task["executor"], "manifest_hash": version["manifest_hash"], "artifact_hash": version["artifact_hash"],
            "target_hash": task["target_id"], "minimum_confirmations": 3})
        if result.get("status") != "verified":
            raise ReleaseError("The execution start transaction is awaiting canonical confirmation.")
        chain_task = result["getter_state"]["task"]
        chain_version = result["getter_state"]["version"]
        if (any(chain_task.get(key) != task[key] for key in ("owner", "proposer", "reviewer", "executor", "receipt_signer"))
                or chain_version.get("deadline") != str(version["expires_at"])):
            raise ReleaseError("The chain authorization roles or deadline do not match the release manifest.")
        return {"status": "verified", **base, "task_id": task["task_id"], "version_id": int(result["version_id"]),
                "attempt_id": result["attempt_id"], "manifest_hash": chain_version["manifest_hash"],
                "artifact_hash": chain_version["artifact_hash"], "target_id": chain_task["target_hash"],
                "executor": chain_task["executor"], "receipt_signer": chain_task["receipt_signer"],
                "authorization_tx": result["transaction_hash"], "block_number": int(result["block_number"]),
                "block_hash": result["block_hash"], "confirmations": int(result["confirmations"]),
                "read_block_number": int(result["read_block_number"]), "read_block_hash": result["read_block_hash"],
                "observed_at": now()}


def receipt_typed_data(receipt: dict, receipt_hash: str) -> dict:
    return {
        "types": {
            "EIP712Domain": [
                {"name": "name", "type": "string"}, {"name": "version", "type": "string"},
                {"name": "chainId", "type": "uint256"}, {"name": "verifyingContract", "type": "address"}],
            "Receipt": [
                {"name": "taskId", "type": "bytes32"}, {"name": "versionId", "type": "uint256"},
                {"name": "attemptId", "type": "uint256"}, {"name": "receiptHash", "type": "bytes32"},
                {"name": "result", "type": "uint8"}],
        },
        "primaryType": "Receipt",
        "domain": {"name": "ClueTideReleaseWitness", "version": "1", "chainId": receipt["chain_id"],
                   "verifyingContract": receipt["contract_address"]},
        "message": {"taskId": receipt["task_id"], "versionId": receipt["version_id"],
                    "attemptId": int(receipt["attempt_id"]), "receiptHash": receipt_hash,
                    "result": {"succeeded": 1, "failed": 2, "unknown": 3}[receipt["status"]]},
    }


class ReleaseService:
    """Only this executor selects paths and publishes prebuilt UTF-8 bytes."""

    def __init__(self, root: Path, *, chain=None, signing_key: bytes | None = None, clock=time.time):
        self.root = Path(root).absolute()
        self._clock = clock
        self.chain = chain
        self._thread_lock = threading.Lock()
        self._key = signing_key
        self._safe(self.root)
        self.root.mkdir(parents=True, exist_ok=True)

    def _safe(self, path: Path) -> Path:
        path = Path(path).absolute()
        if not path.is_relative_to(self.root):
            raise ReleaseError("The release storage boundary could not be verified.")
        # Check lexical components before resolving; junctions also change the boundary.
        for candidate in (path, *path.parents):
            if candidate.is_symlink() or candidate.is_junction():
                raise ReleaseError("The release storage boundary could not be verified.")
            if candidate == self.root:
                break
        if not path.resolve().is_relative_to(self.root.resolve()):
            raise ReleaseError("The release storage boundary could not be verified.")
        return path

    @contextmanager
    def _locked(self):
        if not self._thread_lock.acquire(timeout=5):
            raise ReleaseError("Another release operation is in progress.")
        stream = None
        acquired = False
        try:
            stream = self._safe(self.root / "executor.lock").open("a+b")
            stream.seek(0)
            if os.name == "nt":
                import msvcrt
                if os.fstat(stream.fileno()).st_size == 0:
                    stream.write(b"0")
                    stream.flush()
                stream.seek(0)
                try:
                    msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                except OSError:
                    raise ReleaseError("Another release operation is in progress.") from None
            else:
                import fcntl
                try:
                    fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except OSError:
                    raise ReleaseError("Another release operation is in progress.") from None
            acquired = True
            yield
        finally:
            if stream:
                if acquired:
                    if os.name == "nt":
                        import msvcrt
                        stream.seek(0)
                        msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
                    else:
                        import fcntl
                        fcntl.flock(stream, fcntl.LOCK_UN)
                stream.close()
            self._thread_lock.release()

    def _write(self, path: Path, payload: bytes, *, immutable=False):
        path = self._safe(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        if immutable:
            try:
                with path.open("xb") as stream:
                    stream.write(payload)
                    stream.flush()
                    os.fsync(stream.fileno())
            except FileExistsError:
                if path.read_bytes() != payload:
                    raise ReleaseError("An immutable release record has different bytes.") from None
            return
        temporary = self._safe(path.parent / (".p-" + secrets.token_hex(12)))
        try:
            with temporary.open("xb") as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            self._safe(path)
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)

    def _json(self, path: Path) -> dict:
        path = self._safe(path)
        try:
            if path.stat().st_size > 1024 * 1024:
                raise ValueError()
            from .release_exchange import strict_json
            value = strict_json(path.read_bytes())
            if not isinstance(value, dict):
                raise ValueError()
            return value
        except FileNotFoundError:
            raise ReleaseError("The release record was not found.", 404) from None
        except (ValueError, UnicodeError, RecursionError):
            raise ReleaseError("The release record failed its integrity check.") from None

    def _save(self, path: Path, value: dict, *, immutable=False):
        self._write(path, canonical_json_bytes(value), immutable=immutable)

    def _signer(self):
        if self._key is None:
            path = self._safe(self.root / "executor-signing.key")
            try:
                descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            except FileExistsError:
                pass
            else:
                with os.fdopen(descriptor, "wb") as stream:
                    stream.write(secrets.token_bytes(32))
                    stream.flush()
                    os.fsync(stream.fileno())
            if path.stat().st_size != 32:
                raise ReleaseError("The executor signing identity is unavailable.", 503)
            self._key = path.read_bytes()
        try:
            return Account.from_key(self._key)
        except ValueError:
            raise ReleaseError("The executor signing identity is unavailable.", 503) from None

    def _adapter(self):
        if self.chain is None:
            self.chain = ChainGateway(self.root)
        return self.chain

    def config(self):
        from .release_exchange import MAX_PACKAGE_BYTES, MAX_VERSION_REQUEST_BYTES
        with self._locked():
            signer = self._signer().address.lower()
            chain = self._adapter().config()
        return {"schema_version": 1, "chain": chain,
                "executor": {"receipt_signer": signer, "operation": "publish_static", "target": "demo-site",
                             "scope": "Isolated demonstration release directory"},
                "limits": {"artifact_bytes": MAX_ARTIFACT_BYTES, "request_bytes": MAX_REQUEST_BYTES,
                           "version_request_bytes": MAX_VERSION_REQUEST_BYTES, "package_bytes": MAX_PACKAGE_BYTES}}

    def _task_path(self, task_id):
        return self._safe(self.root / "tasks" / identifier(task_id) / "task.json")

    def _task(self, task_id):
        task_id = identifier(task_id)
        task = self._json(self._task_path(task_id))
        if task.get("task_id") != identifier(task_id):
            raise ReleaseError("The task failed its integrity check.")
        try:
            from .release_exchange import bounded_text
            bounded_text(task["title"], 160)
            roles = {key: CreateTask.address(task[key]) for key in ("owner", "proposer", "reviewer", "executor")}
            if (any(task[key] != roles[key] for key in roles) or roles["proposer"] == roles["reviewer"]
                    or task["target"] != "demo-site"
                    or task["target_id"] != digest((POLICY + "/" + task_id).encode())
                    or not _ADDRESS.fullmatch(task["receipt_signer"])):
                raise ValueError()
        except (ValueError, KeyError, TypeError):
            raise ReleaseError("The task failed its integrity check.") from None
        return task

    def create_task(self, payload):
        payload = CreateTask.model_validate(payload) if not isinstance(payload, CreateTask) else payload
        with self._locked():
            if len(list((self.root / "tasks").glob("*/task.json"))) >= 100:
                raise ReleaseError("The demonstration task limit has been reached.")
            task_id = "0x" + secrets.token_hex(32)
            task = {"schema": "cluetide.release-task.v1", "task_id": task_id, **payload.model_dump(),
                    "receipt_signer": self._signer().address.lower(), "target": "demo-site",
                    "target_id": digest((POLICY + "/" + task_id).encode()), "created_at": now(),
                    "version_count": 0, "chain_records": []}
            self._save(self._task_path(task_id), task, immutable=True)
            return self._detail(task)

    def import_version(self, payload):
        from .release_imports import import_version
        return import_version(self, payload)

    def issue_task_write_key(self, task_id):
        """Create or rotate an off-chain package capability for a local operator."""
        task_id = identifier(task_id)
        with self._locked():
            self._task(task_id)
            key = secrets.token_urlsafe(32)
            self._save(self.root / "task-capabilities" / (task_id + ".json"), {
                "task_id": task_id, "write_key_sha256": digest(key.encode("ascii")),
            })
            return key

    def authorize_task_write_key(self, task_id, key):
        """Require the task-scoped capability before an untrusted package write."""
        task_id = identifier(task_id)
        with self._locked():
            rejected = "A valid task write key is required to add a release package."
            if not isinstance(key, str) or not re.fullmatch(r"[A-Za-z0-9_-]{43}", key):
                raise ReleaseError(rejected, 403)
            try:
                capability = self._json(self.root / "task-capabilities" / (task_id + ".json"))
            except ReleaseError as error:
                if error.status == 404:
                    raise ReleaseError(rejected, 403) from None
                raise
            expected = capability.get("write_key_sha256")
            if (set(capability) != {"task_id", "write_key_sha256"}
                    or capability.get("task_id") != task_id
                    or not isinstance(expected, str) or not _HASH.fullmatch(expected)):
                raise ReleaseError("The task write capability failed its integrity check.", 503)
            if not hmac.compare_digest(digest(key.encode("ascii")), expected):
                raise ReleaseError(rejected, 403)

    def list_tasks(self):
        with self._locked():
            return {"tasks": [self._detail(self._task(path.parent.name))
                              for path in sorted((self.root / "tasks").glob("*/task.json"))[:100]]}

    def _version_path(self, task_id, version_id):
        if type(version_id) is not int or not 1 <= version_id <= 1000:
            raise ReleaseError("A valid release version is required.", 422)
        return self._safe(self._task_path(task_id).parent / "versions" / str(version_id))

    def _version(self, task, version_id):
        directory = self._version_path(task["task_id"], version_id)
        version = self._json(directory / "version.json")
        manifest_bytes = self._safe(directory / "manifest.json").read_bytes()
        manifest = self._json(directory / "manifest.json")
        artifact_path = self._safe(directory / "artifact.html")
        if artifact_path.stat().st_size > MAX_ARTIFACT_BYTES:
            raise ReleaseError("The release package failed its integrity check.")
        artifact = artifact_path.read_bytes()
        try:
            artifact.decode("utf-8")
            if (digest(manifest_bytes) != version["manifest_hash"]
                    or digest(artifact) != version["artifact_hash"]
                    or manifest["artifact"] != {"filename": "artifact.html", "sha256": version["artifact_hash"], "bytes": len(artifact)}
                    or manifest["task_id"] != task["task_id"] or manifest["version_id"] != version_id
                    or manifest["parent_version_id"] != version_id - 1 or version["parent_version_id"] != manifest["parent_version_id"]
                    or manifest["target_id"] != task["target_id"] or manifest["operation"] != "publish_static"
                    or manifest["target"] != "demo-site"
                    or manifest["policy_version"] != POLICY or version["version_id"] != version_id
                    or version["expires_at"] != manifest["expires_at"]):
                raise ValueError()
        except (ValueError, KeyError, TypeError, UnicodeError):
            raise ReleaseError("The release package failed its integrity check.") from None
        return {**version, "manifest": manifest, "manifest_text": manifest_bytes.decode("utf-8")}

    def create_version(self, task_id, payload):
        task_id = identifier(task_id)
        payload = CreateVersion.model_validate(payload) if not isinstance(payload, CreateVersion) else payload
        if not self._clock() < payload.expires_at <= self._clock() + 30 * 86400:
            raise ReleaseError("The release deadline must be within the next 30 days.", 422)
        with self._locked():
            task = self._task(task_id)
            if task["version_count"] >= 50:
                raise ReleaseError("The demonstration version limit has been reached.")
            version_id = task["version_count"] + 1
            artifact = payload.artifact_text.encode("utf-8")
            canonical_json_bytes({"artifact_text": payload.artifact_text})
            artifact_hash = digest(artifact)
            manifest = {"schema": "cluetide.release-manifest.v1", "task_id": task["task_id"],
                        "version_id": version_id, "parent_version_id": version_id - 1,
                        "summary": payload.summary, "source_reference": payload.source_reference,
                        "artifact": {"filename": "artifact.html", "sha256": artifact_hash, "bytes": len(artifact)},
                        "operation": "publish_static", "target": "demo-site", "target_id": task["target_id"],
                        "policy_version": POLICY, "expires_at": payload.expires_at, "test_evidence": payload.test_evidence}
            manifest_bytes = canonical_json_bytes(manifest)
            version = {"version_id": version_id, "parent_version_id": version_id - 1, "manifest_hash": digest(manifest_bytes),
                       "artifact_hash": artifact_hash, "expires_at": payload.expires_at, "created_at": now(),
                       "bundle_url": f"/api/releases/tasks/{task_id}/bundle?version={version_id}"}
            directory = self._version_path(task_id, version_id)
            self._write(directory / "artifact.html", artifact, immutable=True)
            self._write(directory / "manifest.json", manifest_bytes, immutable=True)
            self._save(directory / "version.json", version, immutable=True)
            task["version_count"] = version_id
            self._save(self._task_path(task_id), task)
            return {**version, "manifest": manifest}

    def _attempts(self, task_id):
        return [self._json(path) for path in sorted((self.root / "attempts" / task_id).glob("*/state.json"))[:200]]

    def _detail(self, task):
        return {**task, "versions": [self._version(task, i) for i in range(1, task["version_count"] + 1)],
                "attempts": self._attempts(task["task_id"])}

    def get_task(self, task_id):
        with self._locked():
            return self._detail(self._task(task_id))

    def bundle(self, task_id, version_id=None):
        from .release_exchange import MAX_PACKAGE_BYTES, verify_package
        with self._locked():
            task = self._task(task_id)
            version = self._version(task, version_id or task["version_count"])
            directory = self._version_path(task_id, version["version_id"])
            public_task = {key: task[key] for key in ("task_id", "title", "created_at", "owner", "proposer",
                           "reviewer", "executor", "receipt_signer", "target", "target_id")}
            versions = []
            for number in range(1, version["version_id"] + 1):
                original = self._version(task, number)
                versions.append({**original, "summary": original["manifest"]["summary"],
                    "artifact_text": self._safe(self._version_path(task_id, number) / "artifact.html").read_bytes().decode("utf-8")})
            public_task.update(version_count=version["version_id"], versions=versions, attempts=[], chain_records=[])
            entries = {"task.json": canonical_json_bytes(public_task)}
            for name in ("manifest.json", "artifact.html"):
                payload = self._safe(directory / name).read_bytes()
                expected = version["manifest_hash"] if name == "manifest.json" else version["artifact_hash"]
                if digest(payload) != expected:
                    raise ReleaseError("The release package changed during download preparation.")
                entries[name] = payload
            if sum(map(len, entries.values())) > MAX_PACKAGE_BYTES:
                raise ReleaseError("This complete package exceeds the 4 MiB exchange limit.", 413)
            output = io.BytesIO()
            with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                for name, payload in entries.items():
                    info = zipfile.ZipInfo(name, date_time=(2026, 1, 1, 0, 0, 0))
                    info.compress_type = zipfile.ZIP_DEFLATED
                    archive.writestr(info, payload)
            payload = output.getvalue()
            verify_package(payload)
            return payload

    def submit_feedback(self, task_id, payload):
        from .release_feedback import submit_feedback
        return submit_feedback(self, task_id, payload)

    def feedback(self, task_id, version_id=None):
        from .release_feedback import get_feedback
        return get_feedback(self, task_id, version_id)

    def _chain_context(self, request):
        if request.action == "deploy":
            return None, None, None
        task = self._task(request.task_id)
        version = self._version(task, request.version_id) if request.version_id else None
        receipt = None
        if request.expires_at is not None and (version is None or request.expires_at != version["expires_at"]):
            raise ReleaseError("The approval deadline must match the exact release manifest.")
        if request.action == "record_receipt":
            receipt = self._receipt(task, request.attempt_id)
            if receipt["receipt"]["version_id"] != request.version_id:
                raise ReleaseError("The receipt belongs to another release version.")
        return task, version, receipt

    def prepare(self, payload):
        request = ChainRequest.model_validate(payload) if not isinstance(payload, ChainRequest) else payload
        with self._locked():
            task, version, receipt = self._chain_context(request)
            return self._adapter().prepare(request.action, task=task, version=version, account=request.account,
                                           attempt_id=request.attempt_id, receipt=receipt)

    def verify(self, payload):
        request = VerifyRequest.model_validate(payload) if not isinstance(payload, VerifyRequest) else payload
        with self._locked():
            task, version, receipt = self._chain_context(request)
            result = self._adapter().verify(request.action, task=task, version=version, account=request.account,
                                            transaction_hash=request.transaction_hash, attempt_id=request.attempt_id, receipt=receipt)
            if task is not None and result.get("status") == "verified":
                record = {"action": request.action, "version_id": request.version_id,
                          "transaction_hash": request.transaction_hash, "observed_at": now(), "result": result}
                previous = [r for r in task["chain_records"] if r["transaction_hash"] != request.transaction_hash]
                task["chain_records"] = (previous + [record])[-100:]
                self._save(self._task_path(task["task_id"]), task)
            return result

    def read(self, payload):
        request = ReadRequest.model_validate(payload) if not isinstance(payload, ReadRequest) else payload
        with self._locked():
            task = self._task(request.task_id)
            if request.version_id is not None:
                self._version(task, request.version_id)
            return self._adapter().read(task=task, version_id=request.version_id, attempt_id=request.attempt_id)

    def _attempt_path(self, task_id, attempt_id):
        return self._safe(self.root / "attempts" / identifier(task_id) / attempt_identifier(attempt_id))

    def _target(self, task):
        return self._safe(self.root / "targets" / task["task_id"])

    def _expected_marker(self, task, version, attempt):
        return {"task_id": task["task_id"], "version_id": version["version_id"], "attempt_id": attempt["attempt_id"],
                "manifest_hash": version["manifest_hash"], "artifact_hash": version["artifact_hash"], "target_id": task["target_id"]}

    def _read_target(self, task, version, attempt):
        marker = self._target(task) / "current.json"
        if not marker.exists():
            return False
        expected = self._expected_marker(task, version, attempt)
        if self._json(marker) != expected:
            return False
        published = self._target(task) / "releases" / attempt["attempt_id"]
        if self._json(published / "marker.json") != expected:
            return False
        artifact = self._safe(published / "artifact.html")
        return artifact.stat().st_size <= MAX_ARTIFACT_BYTES and digest(artifact.read_bytes()) == version["artifact_hash"]

    def _check_authorization(self, task, version, attempt_id, tx_hash):
        authorization = self._adapter().authorize_execution(task=task, version=version, attempt_id=attempt_id,
                                                            start_transaction_hash=tx_hash)
        expected = {"chain_id": 677, "task_id": task["task_id"], "version_id": version["version_id"],
                    "manifest_hash": version["manifest_hash"], "artifact_hash": version["artifact_hash"],
                    "target_id": task["target_id"], "executor": task["executor"], "receipt_signer": task["receipt_signer"],
                    "attempt_id": attempt_id, "authorization_tx": tx_hash}
        if (authorization.get("status") != "verified" or any(authorization.get(k) != v for k, v in expected.items())
                or not _ADDRESS.fullmatch(authorization.get("contract_address", ""))
                or not _HASH.fullmatch(authorization.get("block_hash", ""))
                or type(authorization.get("block_number")) is not int or authorization["block_number"] < 0
                or type(authorization.get("confirmations")) is not int or authorization["confirmations"] < 3):
            raise ReleaseError("The confirmed execution authorization does not match this release.")
        if self._signer().address.lower() != task["receipt_signer"]:
            raise ReleaseError("The configured executor signing identity does not match the task.")
        return authorization

    def _switch_target(self, task, marker):
        self._save(self._target(task) / "current.json", marker)

    def _finish(self, task, version, attempt, status):
        directory = self._attempt_path(task["task_id"], attempt["attempt_id"])
        receipt_path = directory / "receipt.json"
        if receipt_path.exists():
            envelope = self._json(receipt_path)
            if not self._verify_signature(task, version, attempt, envelope):
                raise ReleaseError("The saved execution receipt failed verification.")
        else:
            auth = attempt["chain_grant"]
            receipt = {"schema": "cluetide.execution-receipt.v1", "chain_id": auth["chain_id"],
                       "contract_address": auth["contract_address"], "task_id": task["task_id"],
                       "version_id": version["version_id"], "attempt_id": attempt["attempt_id"],
                       "manifest_hash": version["manifest_hash"], "artifact_hash": version["artifact_hash"],
                       "output_hash": version["artifact_hash"] if status == "succeeded" else None,
                       "target_id": task["target_id"], "operation": "publish_static", "environment": "isolated-demo",
                       "executor": task["executor"], "receipt_signer": task["receipt_signer"],
                       "authorization_tx": attempt["start_transaction_hash"], "chain_grant": auth,
                       "status": status, "stage": attempt["stage"], "started_at": attempt["started_at"], "completed_at": now(),
                       "evidence": {"bundle_url": version["bundle_url"],
                                    "artifact_url": f"/api/releases/attempts/{attempt['attempt_id']}/artifact?task_id={task['task_id']}"}}
            receipt_hash = digest(canonical_json_bytes(receipt))
            message = encode_typed_data(full_message=receipt_typed_data(receipt, receipt_hash))
            signature = "0x" + Account.sign_message(message, self._key).signature.hex().removeprefix("0x")
            envelope = {"receipt": receipt, "receipt_hash": receipt_hash, "signature": signature,
                        "signer": task["receipt_signer"], "signature_scheme": "EIP-712"}
            self._save(receipt_path, envelope, immutable=True)
        attempt.update(status=envelope["receipt"]["status"], stage="complete", completed_at=envelope["receipt"]["completed_at"],
                       receipt_url=f"/api/releases/attempts/{attempt['attempt_id']}/receipt?task_id={task['task_id']}",
                       artifact_url=envelope["receipt"]["evidence"]["artifact_url"])
        self._save(directory / "state.json", attempt)
        return attempt

    def execute(self, task_id, payload):
        task_id = identifier(task_id)
        request = ExecuteRequest.model_validate(payload) if not isinstance(payload, ExecuteRequest) else payload
        with self._locked():
            task = self._task(task_id)
            version = self._version(task, request.version_id)
            path = self._attempt_path(task_id, request.attempt_id) / "state.json"
            if path.exists():
                attempt = self._json(path)
                if (attempt.get("task_id") != task_id or attempt.get("version_id") != request.version_id
                        or attempt.get("manifest_hash") != version["manifest_hash"]
                        or attempt.get("start_transaction_hash") != request.start_transaction_hash):
                    raise ReleaseError("This execution attempt is bound to different release parameters.")
                if attempt["status"] in {"succeeded", "failed"}:
                    return {**attempt, "verification": self._receipt(task, request.attempt_id)["verification"]}
                # A completed signed receipt can survive a crash before the state update.
                if (path.parent / "receipt.json").exists():
                    saved = self._receipt(task, request.attempt_id)
                    if saved["verification"]["signature_valid"] and (saved["receipt"]["status"] == "failed" or saved["verification"]["artifact_matches"]):
                        return self._finish(task, version, attempt, saved["receipt"]["status"])
                # Recovery can observe an existing target, but cannot issue a second publication.
                self._check_authorization(task, version, request.attempt_id, request.start_transaction_hash)
                if self._read_target(task, version, attempt):
                    attempt["stage"] = "output_verified"
                    return self._finish(task, version, attempt, "succeeded")
                attempt.update(status="unknown", stage="reconciliation_required")
                self._save(path, attempt)
                return attempt
            if len(self._attempts(task_id)) >= 200:
                raise ReleaseError("The demonstration execution attempt limit has been reached.")
            authorization = self._check_authorization(task, version, request.attempt_id, request.start_transaction_hash)
            attempt = {"schema": "cluetide.execution-attempt.v1", "task_id": task_id, "version_id": request.version_id,
                       "attempt_id": request.attempt_id, "manifest_hash": version["manifest_hash"],
                       "artifact_hash": version["artifact_hash"], "start_transaction_hash": request.start_transaction_hash,
                       "chain_grant": authorization, "status": "pending", "stage": "authorized", "started_at": now()}
            self._save(path, attempt, immutable=True)
            try:
                destination = self._target(task) / "releases" / request.attempt_id
                source = self._version_path(task_id, request.version_id) / "artifact.html"
                artifact = self._safe(source).read_bytes()
                if digest(artifact) != version["artifact_hash"]:
                    raise ReleaseError("The release input changed before staging.")
                marker = self._expected_marker(task, version, attempt)
                self._write(destination / "artifact.html", artifact, immutable=True)
                self._save(destination / "marker.json", marker, immutable=True)
                staged = self._safe(destination / "artifact.html").read_bytes()
                if digest(staged) != version["artifact_hash"]:
                    raise ReleaseError("The staged artifact failed its integrity check.")
                attempt["stage"] = "switching_target"
                self._save(path, attempt)
                self._switch_target(task, marker)
                if not self._read_target(task, version, attempt):
                    raise ReleaseError("The published artifact could not be verified.")
                attempt["stage"] = "output_verified"
                return self._finish(task, version, attempt, "succeeded")
            except Exception:
                if attempt["stage"] == "authorized":
                    # The release pointer has not been touched at this durable stage.
                    attempt["stage"] = "staging_failed"
                    try:
                        return self._finish(task, version, attempt, "failed")
                    except Exception:
                        pass
                # A durable observation is required before deciding to resume a publication.
                attempt.update(status="unknown", stage="reconciliation_required")
                self._save(path, attempt)
                return attempt

    def _verify_signature(self, task, version, attempt, envelope):
        try:
            receipt = envelope["receipt"]
            receipt_hash = digest(canonical_json_bytes(receipt))
            expected = {"schema": "cluetide.execution-receipt.v1", "task_id": task["task_id"],
                        "version_id": version["version_id"], "attempt_id": attempt["attempt_id"],
                        "manifest_hash": version["manifest_hash"], "artifact_hash": version["artifact_hash"],
                        "target_id": task["target_id"], "executor": task["executor"], "receipt_signer": task["receipt_signer"],
                        "authorization_tx": attempt["start_transaction_hash"], "chain_grant": attempt["chain_grant"],
                        "chain_id": 677, "contract_address": attempt["chain_grant"]["contract_address"],
                        "operation": "publish_static", "environment": "isolated-demo"}
            if (any(receipt.get(k) != v for k, v in expected.items()) or envelope["receipt_hash"] != receipt_hash
                    or envelope["signer"].lower() != task["receipt_signer"] or envelope["signature_scheme"] != "EIP-712"
                    or receipt["status"] not in {"succeeded", "failed"}
                    or receipt["output_hash"] != (version["artifact_hash"] if receipt["status"] == "succeeded" else None)):
                return False
            from .release_chain import recover_receipt_signer
            signer = recover_receipt_signer(receipt["chain_id"], receipt["contract_address"], receipt["task_id"],
                                           receipt["version_id"], receipt["attempt_id"], receipt_hash,
                                           {"succeeded": 1, "failed": 2}[receipt["status"]], envelope["signature"])
            return signer == task["receipt_signer"]
        except Exception:
            return False

    def _receipt(self, task, attempt_id):
        directory = self._attempt_path(task["task_id"], attempt_id)
        attempt = self._json(directory / "state.json")
        version = self._version(task, attempt["version_id"])
        envelope = self._json(directory / "receipt.json")
        signature_valid = self._verify_signature(task, version, attempt, envelope)
        artifact_matches = False
        artifact = self._target(task) / "releases" / attempt_identifier(attempt_id) / "artifact.html"
        if artifact.exists():
            artifact = self._safe(artifact)
            artifact_matches = artifact.stat().st_size <= MAX_ARTIFACT_BYTES and digest(artifact.read_bytes()) == version["artifact_hash"]
        return {**envelope, "verification": {"signature_valid": signature_valid, "artifact_matches": artifact_matches,
                                             "current_target_matches": self._read_target(task, version, attempt),
                                             "scope": "Executor signature and available local artifact bytes"}}

    def _find_task(self, attempt_id, task_id=None):
        attempt_id = attempt_identifier(attempt_id)
        if task_id:
            return self._task(task_id)
        matches = list((self.root / "attempts").glob("*/" + attempt_id + "/state.json"))
        if len(matches) != 1:
            raise ReleaseError("Specify the task for this execution attempt.", 404)
        return self._task(matches[0].parent.parent.name)

    def receipt(self, attempt_id, task_id=None):
        with self._locked():
            return self._receipt(self._find_task(attempt_id, task_id), attempt_id)

    def verify_receipt(self, task_id, attempt_id, envelope):
        with self._locked():
            task = self._task(task_id)
            attempt = self._json(self._attempt_path(task_id, attempt_id) / "state.json")
            version = self._version(task, attempt["version_id"])
            return {"signature_valid": self._verify_signature(task, version, attempt, envelope)}

    def artifact(self, task_id=None, attempt_id=None):
        with self._locked():
            task = self._find_task(attempt_id, task_id) if attempt_id else self._task(task_id)
            if attempt_id is None:
                marker = self._json(self._target(task) / "current.json")
                attempt_id = attempt_identifier(marker["attempt_id"])
            attempt = self._json(self._attempt_path(task["task_id"], attempt_id) / "state.json")
            version = self._version(task, attempt["version_id"])
            path = self._safe(self._target(task) / "releases" / attempt_identifier(attempt_id) / "artifact.html")
            if path.stat().st_size > MAX_ARTIFACT_BYTES or digest(path.read_bytes()) != version["artifact_hash"]:
                raise ReleaseError("The published artifact failed its integrity check.")
            return path.read_bytes()
