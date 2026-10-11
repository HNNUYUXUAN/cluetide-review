"""Public evidence archives with bounded, integrity-checked import.

An archive establishes which bytes were shared. It does not authenticate an
Ethereum source, an interpretation, or the independence of a reviewer.

Public JSON and manifest commitments use RFC 8785 JSON Canonicalization Scheme
(JCS) UTF-8 bytes. Integers must fit the I-JSON safe integer domain; EVM uint256
amounts use decimal strings.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import hmac
import json
import math
import os
from pathlib import Path
import re
import stat
import tempfile
from typing import Any
import zipfile
import zlib

import rfc8785


BUNDLE_FORMAT = "cluetide-public-evidence/v1"
CANONICALIZATION = "RFC8785"
INTEGRITY_STATEMENT = "SHA-256 verifies artifact integrity, not factual truth."
DEFAULT_MAX_FILE_BYTES = 16 * 1024 * 1024
DEFAULT_MAX_TOTAL_BYTES = 16 * 1024 * 1024
DEFAULT_MAX_COMPRESSION_RATIO = 100
_DATA_FILES = frozenset({"raw.json", "evidence.json", "report.json", "report.md"})
_ALL_FILES = _DATA_FILES | {"manifest.json"}
_SHA256_PATTERN = re.compile(r"[0-9a-f]{64}\Z")
_PRIVATE_FIELDS = frozenset(
    {
        "apikey", "apikeys", "authorization", "proxyauthorization", "xapikey",
        "privatekey", "privatekeys", "secret", "secrets", "clientsecret",
        "password", "passwd", "credential", "credentials", "accesskey",
        "accesskeyid", "secretaccesskey", "sessiontoken", "accesstoken",
        "refreshtoken", "idtoken", "authtoken", "bearertoken", "seedphrase",
        "mnemonic", "env", "dotenv", "keys", "auth", "authentication",
        "xauthtoken", "cookie", "setcookie", "secretkey",
    }
)
_PRIVATE_FIELD_SUFFIXES = (
    "apikey", "privatekey", "clientsecret", "accesstoken", "refreshtoken",
    "authtoken", "password", "secretkey", "secretaccesskey",
)
_SECRET_PATTERNS = (
    re.compile(r"-----BEGIN (?:[A-Z ]+ )?PRIVATE KEY-----"),
    re.compile(r"\bsk-(?:proj-|ant-)?[A-Za-z0-9_-]{16,}\b"),
    re.compile(r"\b(?:ghp_|github_pat_)[A-Za-z0-9_]{20,}\b"),
    re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b"),
    re.compile(r"\b(?:xox[baprs]-)[A-Za-z0-9-]{16,}\b"),
    re.compile(r"(?i)\b(?:Bearer|Basic)\s+[A-Za-z0-9._~+/-]{12,}={0,2}\b"),
    re.compile(r"(?i)https?://[^\s/@:]+:[^\s/@]+@"),
    re.compile(r"(?i)https?://[^\s/]*alchemy\.com/v2/[A-Za-z0-9_-]{8,}"),
    re.compile(r"(?i)https?://[^\s/]*infura\.io/v3/[A-Za-z0-9_-]{8,}"),
    re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b"),
    re.compile(
        r"(?i)\b[A-Z][A-Z0-9_]*(?:API_KEY|PRIVATE_KEY|ACCESS_TOKEN|AUTH_TOKEN|"
        r"PASSWORD|CLIENT_SECRET|SECRET_KEY)\s*=\s*[\"']?[A-Za-z0-9_./+~=-]{8,}"
    ),
    re.compile(
        r"(?i)\b(?:authorization|proxy-authorization|x-api-key|api[-_ ]?key|"
        r"private[-_ ]?key|client[-_ ]?secret|access[-_ ]?token|refresh[-_ ]?token|"
        r"password|passwd|secret[-_ ]?access[-_ ]?key)\s*[:=]\s*[\"']?"
        r"(?:Bearer\s+)?[A-Za-z0-9_./+~=-]{8,}"
    ),
)


class BundleValidationError(ValueError):
    """A public bundle is malformed, unsafe, or fails integrity verification."""


class BundleConflictError(BundleValidationError):
    """An immutable archive path already belongs to different file bytes."""


@dataclass(frozen=True)
class VerifiedBundle:
    manifest: dict[str, Any]
    evidence: dict[str, Any]
    raw: Any
    report: dict[str, Any]
    report_markdown: str
    validation: dict[str, Any]

    @property
    def verified(self) -> bool:
        """Whether byte integrity was checked; this is not factual verification."""
        return self.validation["verified"] is True


def _scan_public(value: Any, *, depth: int = 0) -> None:
    """Reject credential fields and recognizable credentials without logging them."""
    if depth > 64:
        raise BundleValidationError("Public JSON exceeds the maximum nesting depth.")
    if isinstance(value, dict):
        for key, child in value.items():
            if not isinstance(key, str):
                raise BundleValidationError("Public JSON object keys must be strings.")
            normalized = re.sub(r"[^a-z0-9]", "", key.casefold())
            if normalized in _PRIVATE_FIELDS or normalized.endswith(_PRIVATE_FIELD_SUFFIXES):
                raise BundleValidationError("Public data contains a private credential field.")
            _scan_public(key, depth=depth + 1)
            _scan_public(child, depth=depth + 1)
    elif isinstance(value, (list, tuple)):
        for child in value:
            _scan_public(child, depth=depth + 1)
    elif isinstance(value, str):
        if any(pattern.search(value) for pattern in _SECRET_PATTERNS):
            raise BundleValidationError("Public data contains a recognizable credential.")
    elif value is None or isinstance(value, (bool, int)):
        return
    elif isinstance(value, float) and math.isfinite(value):
        return
    else:
        raise BundleValidationError("Public data contains an unsupported JSON value.")


def _canonical_json(value: Any) -> bytes:
    _scan_public(value)
    try:
        return rfc8785.dumps(value)
    except (rfc8785.CanonicalizationError, TypeError, ValueError, UnicodeError, RecursionError) as exc:
        raise BundleValidationError("Public data cannot be encoded as canonical JSON.") from exc


def canonical_json_bytes(value: Any) -> bytes:
    """Return RFC 8785 / JCS UTF-8 bytes for public commitments."""
    return _canonical_json(value)


def manifest_sha256(manifest: dict[str, Any]) -> str:
    """Hash canonical manifest bytes; return 64 lowercase hex digits without 0x.

    A commitment to this hash does not establish that a manifest's claims are
    true. The archive's own manifest deliberately has no self-hash member.
    """
    if not isinstance(manifest, dict):
        raise BundleValidationError("The manifest commitment requires a JSON object.")
    return hashlib.sha256(canonical_json_bytes(manifest)).hexdigest()


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise BundleValidationError("Public JSON contains duplicate object keys.")
        result[key] = value
    return result


def _reject_json_constant(_: str) -> None:
    raise BundleValidationError("Public JSON contains a non-finite numeric value.")


def _read_json(payload: bytes, name: str) -> Any:
    try:
        value = json.loads(
            payload.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_json_constant,
        )
    except (UnicodeError, json.JSONDecodeError, RecursionError, ValueError) as exc:
        raise BundleValidationError(f"Invalid UTF-8 JSON in {name}.") from exc
    _scan_public(value)
    if _canonical_json(value) != payload:
        raise BundleValidationError(f"{name} is not RFC 8785 canonical JSON.")
    return value


def export_bundle(
    evidence: Any,
    report_json: dict[str, Any],
    report_markdown: str,
    output_path: Path,
    *,
    immutable: bool = False,
) -> dict[str, Any]:
    """Atomically export public evidence and reports, returning their manifest.

    Only the evidence object's ``raw`` member is written to ``raw.json``. The
    caller must supply public material; credentials are rejected before writing.
    Pydantic evidence is converted through ``model_dump(mode="json")``.

    With ``immutable=True``, publish a complete, flushed archive exclusively.
    An existing archive is accepted only if its manifest and every ZIP byte
    match; otherwise raise ``BundleConflictError`` without changing that file.
    Publication requires filesystem hard links; there is no overwrite fallback.
    """
    if not isinstance(immutable, bool):
        raise BundleValidationError("The immutable publication option must be a boolean.")
    if hasattr(evidence, "model_dump"):
        evidence = evidence.model_dump(mode="json")
    if not isinstance(evidence, dict) or not isinstance(report_json, dict):
        raise BundleValidationError("Evidence and report must be JSON objects.")
    if not isinstance(report_markdown, str):
        raise BundleValidationError("The Markdown report must be a string.")
    public_evidence = dict(evidence)
    raw = public_evidence.pop("raw", {})
    _scan_public(report_markdown)
    try:
        markdown_bytes = report_markdown.encode("utf-8")
    except UnicodeError as exc:
        raise BundleValidationError("The Markdown report must be valid UTF-8.") from exc
    payloads = {
        "raw.json": _canonical_json(raw),
        "evidence.json": _canonical_json(public_evidence),
        "report.json": _canonical_json(report_json),
        "report.md": markdown_bytes,
    }
    manifest: dict[str, Any] = {
        "format": BUNDLE_FORMAT,
        "canonicalization": CANONICALIZATION,
        "integrity_statement": INTEGRITY_STATEMENT,
        "files": {
            name: {"sha256": hashlib.sha256(payload).hexdigest(), "size": len(payload)}
            for name, payload in sorted(payloads.items())
        },
    }
    payloads["manifest.json"] = _canonical_json(manifest)
    if any(len(payload) > DEFAULT_MAX_FILE_BYTES for payload in payloads.values()):
        raise BundleValidationError("A public bundle file exceeds the size limit.")
    if sum(map(len, payloads.values())) > DEFAULT_MAX_TOTAL_BYTES:
        raise BundleValidationError("The public bundle exceeds the total size limit.")
    output_path = Path(output_path)
    if output_path.suffix.casefold() != ".zip" or output_path.name.casefold().startswith(".env"):
        raise BundleValidationError("The output path must identify a public evidence archive.")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            dir=output_path.parent, prefix=".cluetide-bundle-", suffix=".zip", delete=False
        ) as temporary:
            temporary_path = Path(temporary.name)
        # Stored members give bounded, predictable archives, including repetitive
        # RPC payloads that would otherwise exceed the import compression ratio.
        with zipfile.ZipFile(temporary_path, "w", compression=zipfile.ZIP_STORED) as archive:
            for name, payload in sorted(payloads.items()):
                info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
                info.create_system = 3
                info.external_attr = (stat.S_IFREG | 0o644) << 16
                archive.writestr(info, payload)
        # Flush the ZIP central directory too, after ZipFile has closed. A
        # published path must never expose a partially written archive.
        # Windows _commit()/FlushFileBuffers requires a writable descriptor.
        with temporary_path.open("r+b") as completed:
            os.fsync(completed.fileno())
        if immutable:
            _publish_immutable(temporary_path, output_path, manifest)
        else:
            os.replace(temporary_path, output_path)
            temporary_path = None
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
    return manifest


def _publish_immutable(
    temporary_path: Path, output_path: Path, manifest: dict[str, Any]
) -> None:
    try:
        # Unlike replace() or exists()+write(), link() fails atomically if
        # another publisher already owns the final path. Both files are in
        # the same directory/filesystem; the completed temporary is retained
        # until the exclusive publication or idempotence check has finished.
        os.link(temporary_path, output_path)
    except FileExistsError:
        if not _matches_immutable_archive(output_path, temporary_path, manifest):
            raise BundleConflictError(
                "An immutable archive already exists with different bytes."
            ) from None


def _matches_immutable_archive(
    existing_path: Path, temporary_path: Path, manifest: dict[str, Any]
) -> bool:
    try:
        existing_stat = existing_path.lstat()
        if not stat.S_ISREG(existing_stat.st_mode):
            return False
        if existing_stat.st_size != temporary_path.stat().st_size:
            return False
        if import_bundle(existing_path).manifest != manifest:
            return False
        with existing_path.open("rb") as existing, temporary_path.open("rb") as candidate:
            while True:
                existing_bytes = existing.read(64 * 1024)
                candidate_bytes = candidate.read(64 * 1024)
                if existing_bytes != candidate_bytes:
                    return False
                if not existing_bytes:
                    return True
    except (OSError, BundleValidationError):
        return False


def _validate_limits(max_file_bytes: int, max_total_bytes: int, max_compression_ratio: float) -> None:
    for value in (max_file_bytes, max_total_bytes):
        if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
            raise BundleValidationError("Bundle size limits must be positive integers.")
    if (
        not isinstance(max_compression_ratio, (int, float))
        or isinstance(max_compression_ratio, bool)
        or not math.isfinite(max_compression_ratio)
        or max_compression_ratio < 1
    ):
        raise BundleValidationError("The compression ratio limit must be finite and at least one.")


def import_bundle(
    path: Path,
    *,
    max_file_bytes: int = DEFAULT_MAX_FILE_BYTES,
    max_total_bytes: int = DEFAULT_MAX_TOTAL_BYTES,
    max_compression_ratio: float = DEFAULT_MAX_COMPRESSION_RATIO,
) -> VerifiedBundle:
    """Verify an archive in memory without extracting or executing its contents."""
    _validate_limits(max_file_bytes, max_total_bytes, max_compression_ratio)
    if Path(path).name.casefold().startswith(".env"):
        raise BundleValidationError("Environment files cannot be imported as public archives.")
    payloads: dict[str, bytes] = {}
    try:
        with zipfile.ZipFile(path, "r") as archive:
            members = archive.infolist()
            if len(members) != len(_ALL_FILES):
                raise BundleValidationError("The archive must contain exactly five public files.")
            names: set[str] = set()
            total_size = 0
            for member in members:
                name = member.filename
                if member.orig_filename != name:
                    raise BundleValidationError("The archive contains an ambiguous filename.")
                if name in names:
                    raise BundleValidationError("The archive contains duplicate filenames.")
                names.add(name)
                if name not in _ALL_FILES or member.is_dir():
                    raise BundleValidationError("The archive contains an unknown or unsafe path.")
                mode = member.external_attr >> 16
                if stat.S_ISLNK(mode) or (stat.S_IFMT(mode) not in (0, stat.S_IFREG)):
                    raise BundleValidationError("The archive contains a non-regular file.")
                if member.flag_bits & 1:
                    raise BundleValidationError("Encrypted archive files are not supported.")
                if member.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED):
                    raise BundleValidationError("The archive uses an unsupported compression method.")
                if member.file_size > max_file_bytes:
                    raise BundleValidationError("An archive file exceeds the size limit.")
                total_size += member.file_size
                if total_size > max_total_bytes:
                    raise BundleValidationError("The archive exceeds the total size limit.")
                if member.file_size and (
                    member.compress_size == 0
                    or member.file_size / member.compress_size > max_compression_ratio
                ):
                    raise BundleValidationError("An archive file exceeds the compression ratio limit.")
            if names != _ALL_FILES:
                raise BundleValidationError("The archive is missing a required public file.")
            for member in members:
                # Do not trust advertised sizes alone. Bound the actual read too.
                with archive.open(member, "r") as source:
                    payload = source.read(max_file_bytes + 1)
                if len(payload) != member.file_size or len(payload) > max_file_bytes:
                    raise BundleValidationError("An archive file has an invalid byte size.")
                payloads[member.filename] = payload
    except (zipfile.BadZipFile, zipfile.LargeZipFile, RuntimeError, EOFError, NotImplementedError,
            OSError, UnicodeError, zlib.error) as exc:
        raise BundleValidationError("The evidence archive is invalid or unreadable.") from None

    manifest = _read_json(payloads["manifest.json"], "manifest.json")
    if not isinstance(manifest, dict) or manifest.get("format") != BUNDLE_FORMAT:
        raise BundleValidationError("The archive has an unsupported manifest format.")
    if set(manifest) != {"format", "canonicalization", "files", "integrity_statement"}:
        raise BundleValidationError("The archive manifest contains unsupported fields.")
    if manifest["canonicalization"] != CANONICALIZATION:
        raise BundleValidationError("The archive has an unsupported JSON canonicalization profile.")
    if manifest["integrity_statement"] != INTEGRITY_STATEMENT:
        raise BundleValidationError("The archive must state the limits of hash verification.")
    files = manifest.get("files")
    if not isinstance(files, dict) or set(files) != _DATA_FILES:
        raise BundleValidationError("The manifest must list exactly the four public data files.")
    for name in sorted(_DATA_FILES):
        entry = files[name]
        if not isinstance(entry, dict) or set(entry) != {"sha256", "size"}:
            raise BundleValidationError("A manifest file entry is malformed.")
        expected_hash = entry["sha256"]
        expected_size = entry["size"]
        if not isinstance(expected_hash, str) or _SHA256_PATTERN.fullmatch(expected_hash) is None:
            raise BundleValidationError("A manifest SHA-256 digest is malformed.")
        if not isinstance(expected_size, int) or isinstance(expected_size, bool) or expected_size < 0:
            raise BundleValidationError("A manifest byte size is malformed.")
        payload = payloads[name]
        if len(payload) != expected_size:
            raise BundleValidationError("A public file does not match its manifest byte size.")
        if not hmac.compare_digest(hashlib.sha256(payload).hexdigest(), expected_hash):
            raise BundleValidationError("A public file fails SHA-256 integrity verification.")

    evidence = _read_json(payloads["evidence.json"], "evidence.json")
    raw = _read_json(payloads["raw.json"], "raw.json")
    report = _read_json(payloads["report.json"], "report.json")
    if not isinstance(evidence, dict) or not isinstance(report, dict):
        raise BundleValidationError("Evidence and report must be JSON objects.")
    if "raw" in evidence:
        raise BundleValidationError("Raw evidence must be confined to raw.json.")
    try:
        report_markdown = payloads["report.md"].decode("utf-8")
    except UnicodeError as exc:
        raise BundleValidationError("The Markdown report is not valid UTF-8.") from exc
    _scan_public(report_markdown)
    return VerifiedBundle(
        manifest=manifest,
        evidence=evidence,
        raw=raw,
        report=report,
        report_markdown=report_markdown,
        validation={
            "verified": True,
            "statement": INTEGRITY_STATEMENT,
            "files_verified": sorted(_DATA_FILES),
            "source_authenticity_verified": False,
            "reviewer_independence_verified": False,
        },
    )
