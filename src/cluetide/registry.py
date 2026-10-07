"""Local evidence commitments with deterministic author, version, and review rules."""

from __future__ import annotations

import copy
import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path


DECISIONS = {"supported": 1, "correction_requested": 2, "unresolved": 3}
_HASH = re.compile(r"^(?:0x)?[0-9a-fA-F]{64}$")


class RegistryError(ValueError):
    """Invalid registry operation, with a stable machine-readable error code."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def normalize_hash(value: str) -> str:
    if not isinstance(value, str) or not _HASH.fullmatch(value):
        raise RegistryError("invalid_content_hash")
    normalized = value.removeprefix("0x").lower()
    if normalized == "0" * 64:
        raise RegistryError("invalid_content_hash")
    return normalized


def case_id_bytes(case_id: str) -> bytes:
    if not isinstance(case_id, str) or not case_id.strip() or len(case_id.encode("utf-8")) > 512:
        raise RegistryError("invalid_identifier")
    return hashlib.sha256(case_id.encode("utf-8")).digest()


def _metadata(content_hash: str, schema_version: str, evidence_uri: str) -> str:
    content_hash = normalize_hash(content_hash)
    if not isinstance(schema_version, str) or not 1 <= len(schema_version.encode("utf-8")) <= 64:
        raise RegistryError("invalid_metadata")
    if not isinstance(evidence_uri, str) or len(evidence_uri.encode("utf-8")) > 512:
        raise RegistryError("invalid_metadata")
    return content_hash


def _identity(value: str) -> str:
    if not isinstance(value, str) or not value.strip() or len(value.encode("utf-8")) > 512:
        raise RegistryError("invalid_identity")
    return value


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class LocalRegistry:
    """Deterministic permission/version rules for a local demo; not an EVM chain.

    Role labels are user-controlled demo identities. They do not authenticate a
    person, and a second label is not evidence of independent review.
    """

    mode = "local_state_simulation"

    def __init__(self, path: str | Path | None = None):
        self.path = Path(path) if path is not None else None
        self._state: dict = {"mode": self.mode, "cases": {}, "versions": {}, "reviews": {}}
        if self.path is not None and self.path.exists():
            saved = json.loads(self.path.read_text(encoding="utf-8"))
            self._loading = True
            try:
                self._load_verified(saved)
            finally:
                self._loading = False

    @classmethod
    def from_state(cls, state: dict) -> "LocalRegistry":
        """Validate a snapshot in memory without touching a mirror file."""
        instance = cls()
        instance._loading = True
        try:
            instance._load_verified(state)
        finally:
            instance._loading = False
        return instance

    def adopt_state(self, state: dict) -> None:
        self._state = self.from_state(state).export_state()

    def _load_verified(self, saved: dict) -> None:
        """Replay the saved records to reject inconsistent parent/hash links."""
        if not isinstance(saved, dict) or saved.get("mode") != self.mode:
            raise RegistryError("invalid_simulation_state")
        for field in ("cases", "versions", "reviews"):
            if not isinstance(saved.get(field), dict):
                raise RegistryError("invalid_simulation_state")
            if any(not isinstance(record, dict) for record in saved[field].values()):
                raise RegistryError("invalid_simulation_state")
        try:
            for key, version in sorted(saved["versions"].items(), key=lambda item: int(item[0])):
                if type(version["version_id"]) is not int or type(version["parent_version_id"]) is not int:
                    raise RegistryError("invalid_simulation_state")
                if int(key) != version["version_id"]:
                    raise RegistryError("invalid_simulation_state")
                args = dict(case_id=version["case_id"], content_hash=version["content_hash"],
                            schema_version=version["schema_version"], evidence_uri=version["evidence_uri"],
                            author=version["author"])
                if version["parent_version_id"] == 0:
                    actual = self.create_case(**args)
                else:
                    actual = self.append_version(parent_version_id=version["parent_version_id"], **args)
                for field, value in actual.items():
                    if field != "created_at" and version.get(field) != value:
                        raise RegistryError("invalid_simulation_state")
            for key, review in sorted(saved["reviews"].items(), key=lambda item: int(item[0])):
                if type(review["review_id"]) is not int or type(review["version_id"]) is not int:
                    raise RegistryError("invalid_simulation_state")
                if int(key) != review["review_id"]:
                    raise RegistryError("invalid_simulation_state")
                actual = self.add_review(case_id=review["case_id"], version_id=review["version_id"],
                                         content_hash=review["content_hash"], review_hash=review["review_hash"],
                                         decision=review["decision"], reviewer=review["reviewer"],
                                         review_uri=review["review_uri"])
                for field, value in actual.items():
                    if field != "created_at" and review.get(field) != value:
                        raise RegistryError("invalid_simulation_state")
            for case_id, case in self._state["cases"].items():
                expected = saved["cases"][case_id]
                for field in ("author", "head_version_id", "version_ids", "review_ids", "case_id_hex"):
                    if case[field] != expected[field]:
                        raise RegistryError("invalid_simulation_state")
            if set(saved["cases"]) != set(self._state["cases"]):
                raise RegistryError("invalid_simulation_state")
            self._state = copy.deepcopy(saved)
        except (KeyError, TypeError, ValueError) as exc:
            raise RegistryError("invalid_simulation_state") from exc

    def _save(self) -> None:
        # A replay during load must not write incomplete state back to disk.
        if self.path is not None and not getattr(self, "_loading", False):
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.path.with_name(self.path.name + ".tmp")
            temporary.write_text(json.dumps(self._state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            temporary.replace(self.path)

    def create_case(self, case_id: str, content_hash: str, schema_version: str = "cluetide.evidence.v1",
                    evidence_uri: str = "", author: str = "local-author") -> dict:
        case_hex = "0x" + case_id_bytes(case_id).hex()
        if case_id in self._state["cases"]:
            raise RegistryError("case_exists")
        content_hash = _metadata(content_hash, schema_version, evidence_uri)
        author = _identity(author)
        self._state["cases"][case_id] = {"case_id": case_id, "case_id_hex": case_hex, "author": author,
                                           "head_version_id": 0, "version_ids": [], "review_ids": [], "created_at": _now()}
        return self._append(case_id, 0, content_hash, schema_version, evidence_uri, author)

    def append_version(self, case_id: str, parent_version_id: int, content_hash: str,
                       schema_version: str = "cluetide.evidence.v1", evidence_uri: str = "",
                       author: str = "local-author") -> dict:
        case = self._case(case_id)
        if case["author"] != author:
            raise RegistryError("unauthorized_author")
        parent = self._version(parent_version_id)
        if parent["case_id"] != case_id:
            raise RegistryError("parent_case_mismatch")
        if case["head_version_id"] != parent_version_id:
            raise RegistryError("stale_parent")
        content_hash = _metadata(content_hash, schema_version, evidence_uri)
        return self._append(case_id, parent_version_id, content_hash, schema_version, evidence_uri, author)

    def _append(self, case_id: str, parent: int, content_hash: str, schema: str, uri: str, author: str) -> dict:
        version_id = len(self._state["versions"]) + 1
        record = {"case_id": case_id, "case_id_hex": self._state["cases"][case_id]["case_id_hex"],
                  "version_id": version_id, "parent_version_id": parent, "content_hash": content_hash,
                  "schema_version": schema, "evidence_uri": uri, "author": author, "created_at": _now()}
        self._state["versions"][str(version_id)] = record
        self._state["cases"][case_id]["head_version_id"] = version_id
        self._state["cases"][case_id]["version_ids"].append(version_id)
        self._save()
        return copy.deepcopy(record)

    def add_review(self, case_id: str, version_id: int, content_hash: str, review_hash: str,
                   decision: str = "correction_requested", reviewer: str = "local-reviewer",
                   review_uri: str = "") -> dict:
        self._case(case_id)
        version = self._version(version_id)
        if version["case_id"] != case_id:
            raise RegistryError("review_case_mismatch")
        content_hash = normalize_hash(content_hash)
        if version["content_hash"] != content_hash:
            raise RegistryError("review_hash_mismatch")
        review_hash = normalize_hash(review_hash)
        if not isinstance(decision, str) or decision not in DECISIONS:
            raise RegistryError("invalid_decision")
        reviewer = _identity(reviewer)
        if not isinstance(review_uri, str) or len(review_uri.encode("utf-8")) > 512:
            raise RegistryError("invalid_metadata")
        review_id = len(self._state["reviews"]) + 1
        record = {"case_id": case_id, "case_id_hex": version["case_id_hex"], "version_id": version_id,
                  "review_id": review_id, "content_hash": content_hash, "review_hash": review_hash,
                  "decision": decision, "reviewer": reviewer, "review_uri": review_uri, "created_at": _now()}
        self._state["reviews"][str(review_id)] = record
        self._state["cases"][case_id]["review_ids"].append(review_id)
        self._save()
        return copy.deepcopy(record)

    def _case(self, case_id: str) -> dict:
        case_id_bytes(case_id)
        try:
            return self._state["cases"][case_id]
        except KeyError as exc:
            raise RegistryError("unknown_case") from exc

    def _version(self, version_id: int) -> dict:
        if type(version_id) is not int or version_id < 1:
            raise RegistryError("unknown_version")
        try:
            return self._state["versions"][str(version_id)]
        except KeyError as exc:
            raise RegistryError("unknown_version") from exc

    def get_case(self, case_id: str) -> dict:
        return copy.deepcopy(self._case(case_id))

    def get_version(self, version_id: int) -> dict:
        return copy.deepcopy(self._version(version_id))

    def get_review(self, review_id: int) -> dict:
        if type(review_id) is not int or review_id < 1 or str(review_id) not in self._state["reviews"]:
            raise RegistryError("unknown_review")
        return copy.deepcopy(self._state["reviews"][str(review_id)])

    def export_state(self) -> dict:
        return copy.deepcopy(self._state)
