"""Build an allowlisted ClueTide handoff ZIP; never open environment files."""
from __future__ import annotations

import argparse
from datetime import UTC, datetime
import hashlib
import importlib.util
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import subprocess
import sys
import tempfile
from typing import Any
import zipfile


REPOSITORY = Path(__file__).resolve().parents[1]
REQUIRED_FILES = ('README.md', 'AGENTS.md', 'LICENSE', 'SOURCE.md', '.gitignore', '.gitattributes', 'pyproject.toml', 'requirements-lock.txt', 'src/cluetide/bundles.py', 'frontend/package.json', 'frontend/package-lock.json', 'frontend/index.html', 'frontend/bot.html', 'frontend/tsconfig.json', 'frontend/vite.config.ts', 'frontend/dist/index.html', 'frontend/dist/bot.html', 'contracts/ClueTideRegistry.sol', 'contracts/compile.py', 'contracts/artifacts/ClueTideRegistry.json', 'scripts/run-local.ps1', 'scripts/probe_gateway.py', 'scripts/package_demo.py', 'scripts/seed_demo.py', 'scripts/screenshot_metadata.py', 'scripts/bot_registry.py', 'scripts/build_bot_story_data.py')
OPTIONAL_FILES = ("frontend/README.md", "source-manifest.json", "scripts/verify_sources.py")
DIRECTORIES = {
    'design': frozenset(('.md', '.png')),
    'src/cluetide': frozenset(('.py',)),
    'frontend/tests': frozenset(('.mjs', '.py')),
    'frontend/src': frozenset(('.ts', '.tsx', '.js', '.json', '.css', '.svg', '.png', '.jpg', '.jpeg', '.webp')),
    'frontend/dist': frozenset(('.zip', '.html', '.js', '.json', '.css', '.svg', '.png', '.jpg', '.jpeg', '.webp', '.woff', '.woff2', '.txt')),
    'frontend/public': frozenset(('.zip', '.svg', '.png', '.jpg', '.jpeg', '.webp', '.woff', '.woff2', '.txt', '.json')),
    'docs': frozenset(('.md', '.jpg', '.png')),
    'tests': frozenset(('.py', '.json')),
    'data/cases/uniswap93': frozenset(('.json', '.txt', '.md', '.py', '.ps1')),
    'data/cases/euler-20230313': frozenset(('.json', '.txt', '.md', '.py', '.ps1')),
    'data/attribution': frozenset(('', '.apache', '.apache2', '.bsd', '.json', '.license', '.md', '.mit', '.psf', '.py', '.rst', '.txt')),
}
DEMO_FILES = ('data/demo/bot-testnet-review-receipt-20261008.json', 'data/demo/bot-testnet-v1-receipt-20261008.json', 'data/demo/bot-testnet-v2-receipt-20261008.json', 'data/demo/bot-testnet-workflow-20261008.json', 'data/demo/gcc-saved-model-review-20261007.json')
FORBIDDEN_PARTS = frozenset({
    ".git", ".env", "node_modules", "local-only", "local-data", ".tools", ".venv",
    "__pycache__", ".pytest_cache", "literature", "references", "sources", "incoming",
    "incoming-increment", "qa",
})
FORBIDDEN_SUFFIXES = frozenset({".pem", ".key", ".p12", ".pfx", ".pyc", ".sqlite", ".sqlite3", ".db", ".log"})
FORBIDDEN_DOCS = frozenset({"docs/project-manuscript.md", "docs/device-handoff.md"})
SOURCE_SUFFIXES = frozenset({".py", ".ps1", ".ts", ".tsx", ".sol"})
MAX_FILE_BYTES = 32 * 1024 * 1024
MAX_TOTAL_BYTES = 64 * 1024 * 1024


class PackagingError(ValueError):
    pass


def sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def safe_relative(name: str) -> str:
    path = PurePosixPath(name)
    if path.is_absolute() or "\\" in name or ":" in name or not path.parts or any(part in {"", ".", ".."} for part in path.parts):
        raise PackagingError("An archive path is invalid.")
    if any(part.casefold() in FORBIDDEN_PARTS or part.casefold().startswith(".env") or part.casefold().endswith(".egg-info") for part in path.parts):
        raise PackagingError(f"A restricted archive path was selected: {path.as_posix()}")
    if path.suffix.casefold() in FORBIDDEN_SUFFIXES or path.as_posix().casefold() in FORBIDDEN_DOCS:
        raise PackagingError(f"A restricted file was selected: {path.as_posix()}")
    return path.as_posix()


def reject_link(path: Path, root: Path) -> None:
    for candidate in (path, *path.parents):
        if candidate == root.parent:
            break
        if candidate.is_symlink() or candidate.is_junction():
            raise PackagingError("Selected source paths must not contain symlinks or directory junctions.")
    if not path.resolve().is_relative_to(root.resolve()):
        raise PackagingError("A selected source escapes the repository.")


def walk_allowlisted(root: Path, relative: str, extensions: frozenset[str]):
    selected_root = root / relative
    if not selected_root.exists():
        return
    reject_link(selected_root, root)
    pending = [selected_root]
    while pending:
        directory = pending.pop()
        with os.scandir(directory) as entries:
            for entry in sorted(entries, key=lambda item: item.name.casefold()):
                path = Path(entry.path)
                name = path.relative_to(root).as_posix()
                try:
                    safe_relative(name)
                except PackagingError:
                    continue  # Exclude before reading or descending.
                reject_link(path, root)
                if entry.is_dir(follow_symlinks=False):
                    pending.append(path)
                elif entry.is_file(follow_symlinks=False) and path.suffix.casefold() in extensions:
                    yield name


def select_files(root: Path, *, include_demo: bool, documents: list[str], demo_files: list[str]) -> tuple[list[str], list[str]]:
    selected: set[str] = set()
    missing_optional: list[str] = []
    for name in REQUIRED_FILES:
        safe_relative(name)
        if not (root / name).is_file():
            raise PackagingError(f"Required product file is missing: {name}")
        selected.add(name)
    for name in OPTIONAL_FILES + (DEMO_FILES if include_demo else ()):
        safe_relative(name)
        if (root / name).is_file():
            selected.add(name)
        else:
            missing_optional.append(name)
    for relative, extensions in DIRECTORIES.items():
        selected.update(walk_allowlisted(root, relative, extensions))
    if include_demo:
        selected.update(walk_allowlisted(root, "data/demo/bundles", frozenset({".zip"})))
    for name in documents:
        name = safe_relative(Path(name).as_posix())
        if PurePosixPath(name).parent != PurePosixPath("docs") or not name.endswith(".md"):
            raise PackagingError("Additional product documents must be explicitly named docs/*.md files.")
        if not (root / name).is_file():
            raise PackagingError(f"Additional product document is missing: {name}")
        selected.add(name)
    for name in demo_files:
        name = safe_relative(Path(name).as_posix())
        if not include_demo or not name.startswith("data/demo/") or PurePosixPath(name).suffix not in {".json", ".zip"}:
            raise PackagingError("Additional demo data requires --include-demo and an explicit data/demo JSON or ZIP path.")
        if not (root / name).is_file():
            raise PackagingError(f"Additional demo artifact is missing: {name}")
        selected.add(name)
    ordered = sorted(selected)
    if len({name.casefold() for name in ordered}) != len(ordered):
        raise PackagingError("Selected archive paths collide when compared without case.")
    return ordered, sorted(missing_optional)


def load_bundle_policy(root: Path):
    # Load only the dedicated public archive module; it has no .env/settings imports.
    path = root / "src/cluetide/bundles.py"
    reject_link(path, root)
    module_name = "_cluetide_demo_bundle_policy"
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise PackagingError("The public bundle validator is unavailable.")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    bytecode_setting = sys.dont_write_bytecode
    try:
        sys.dont_write_bytecode = True
        spec.loader.exec_module(module)
    except ImportError:
        raise PackagingError("Run this script with the project Python 3.12 virtual environment and locked dependencies installed.") from None
    finally:
        sys.dont_write_bytecode = bytecode_setting
    return module


def scan_payload(root: Path, name: str, payload: bytes, policy) -> str:
    suffix = PurePosixPath(name).suffix.casefold()
    if suffix == ".zip":
        try:
            with tempfile.TemporaryDirectory(prefix="cluetide-nested-verify-") as temporary:
                frozen_path = Path(temporary) / "evidence.zip"
                frozen_path.write_bytes(payload)
                policy.import_bundle(frozen_path)
        except (ValueError, OSError):
            raise PackagingError(f"Nested public evidence archive failed validation: {name}") from None
        return "public_evidence_bundle_verified"
    # Source code contains credential field names and artificial security-test
    # inputs. It is integrity indexed; the final local configured-key byte audit
    # is separate. No source-specific credential exceptions are implemented.
    if suffix in SOURCE_SUFFIXES and not name.startswith("frontend/dist/"):
        return "source_integrity_indexed"
    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError:
        text = payload.decode("latin-1")  # Scan recognizable plaintext in assets.
    try:
        if suffix == ".json":
            policy._scan_public(json.loads(text, object_pairs_hook=policy._reject_duplicate_keys, parse_constant=policy._reject_json_constant))
        else:
            policy._scan_public(text)
    except (ValueError, UnicodeError, RecursionError):
        raise PackagingError(f"Public content failed JSON or credential validation: {name}") from None
    return "public_content_credential_scanned"


def snapshot(root: Path, names: list[str], policy) -> tuple[dict[str, bytes], list[dict[str, Any]]]:
    payloads: dict[str, bytes] = {}
    index = []
    total = 0
    for name in names:
        safe_relative(name)
        path = root / name
        reject_link(path, root)
        if not path.is_file() or path.stat().st_size > MAX_FILE_BYTES:
            raise PackagingError(f"Selected source is missing, non-regular or too large: {name}")
        with path.open("rb") as stream:
            payload = stream.read(MAX_FILE_BYTES + 1)
        total += len(payload)
        if len(payload) > MAX_FILE_BYTES or total > MAX_TOTAL_BYTES:
            raise PackagingError("The selected demonstration exceeds the archive size limits.")
        validation = scan_payload(root, name, payload, policy)
        payloads[name] = payload
        index.append({"path": name, "source": name, "size": len(payload), "sha256": sha256(payload), "validation": validation})
    return payloads, index


def verify_archive(path: Path, payloads: dict[str, bytes]) -> None:
    with zipfile.ZipFile(path, "r") as archive:
        members = archive.infolist()
        if len(members) != len(payloads) or {member.filename for member in members} != set(payloads):
            raise PackagingError("The produced archive has unexpected or duplicate members.")
        for member in members:
            safe_relative(member.filename)
            if member.is_dir() or member.flag_bits & 1 or stat.S_ISLNK(member.external_attr >> 16):
                raise PackagingError("The produced archive contains an unsafe member.")
            if member.file_size != len(payloads[member.filename]):
                raise PackagingError("The produced archive has an inconsistent member size.")
            with archive.open(member) as stream:
                actual = stream.read(MAX_FILE_BYTES + 1)
            if actual != payloads[member.filename]:
                raise PackagingError("The produced archive fails byte-integrity verification.")


def head_commit(root: Path) -> str | None:
    try:
        result = subprocess.run(["git", "rev-parse", "HEAD"], cwd=root, capture_output=True, text=True, check=True)
        value = result.stdout.strip()
        return value if re.fullmatch(r"[0-9a-f]{40}", value) else None
    except (OSError, subprocess.CalledProcessError):
        return None


def package(root: Path, output: Path, *, dry_run: bool, include_demo: bool, documents: list[str], demo_files: list[str]) -> dict[str, Any]:
    root = root.resolve()
    reject_link(root / "local-only/deliverables", root)
    original_destination = root / output if not output.is_absolute() else output
    for candidate in (original_destination, original_destination.with_suffix(".index.json"), original_destination.with_suffix(".zip.sha256")):
        reject_link(candidate, root)
        if candidate.exists() and not candidate.is_file():
            raise PackagingError("The output and sidecars must identify regular files.")
    destination = original_destination.resolve()
    allowed_destination = (root / "local-only/deliverables").resolve()
    if not destination.is_relative_to(allowed_destination) or destination.suffix.casefold() != ".zip":
        raise PackagingError("The output must be a ZIP inside ignored local-only/deliverables.")
    names, missing = select_files(root, include_demo=include_demo, documents=documents, demo_files=demo_files)
    policy = load_bundle_policy(root)
    payloads, files = snapshot(root, names, policy)
    manifest = {
        "format": "cluetide-product-demo/v1",
        "track": "bot",
        "base_commit": "cf19b51af64fa289de19ccd70ab26425c09584d3",
        "created_at_utc": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "source_head_commit": head_commit(root),
        "source_scope": "Explicit product file and directory allowlist; working-tree file bytes indexed individually.",
        "files": files,
        "scanner_policy_source": "src/cluetide/bundles.py",
        "scanner_policy_sha256": sha256(payloads["src/cluetide/bundles.py"]),
        "validation_scope": "Public JSON, evidence bundles, documentation and static assets use the public credential scanner. Product source is integrity indexed and requires the separate final local configured-key byte audit.",
        "integrity_statement": "SHA-256 commits to included bytes and does not establish factual truth, independent certification or contract deployment.",
    }
    summary = {"dry_run": dry_run, "file_count": len(files), "source_bytes": sum(item["size"] for item in files), "missing_optional": missing, "include_demo": include_demo}
    if dry_run:
        return summary
    manifest_bytes = (json.dumps(manifest, indent=2, ensure_ascii=False) + "\n").encode("utf-8")
    payloads["demo-manifest.json"] = manifest_bytes
    destination.parent.mkdir(parents=True, exist_ok=True)
    reject_link(destination.parent, root)
    temporary_path = None
    try:
        with tempfile.NamedTemporaryFile(dir=destination.parent, prefix=".cluetide-demo-", suffix=".zip", delete=False) as temporary:
            temporary_path = Path(temporary.name)
        with zipfile.ZipFile(temporary_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
            for name, payload in sorted(payloads.items()):
                info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
                info.create_system = 3
                info.external_attr = (stat.S_IFREG | 0o644) << 16
                info.compress_type = zipfile.ZIP_DEFLATED
                archive.writestr(info, payload)
        verify_archive(temporary_path, payloads)
        archive_hash = sha256(temporary_path.read_bytes())
        os.replace(temporary_path, destination)
        temporary_path = None
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
    index = {**manifest, "archive": destination.name, "archive_sha256": archive_hash, "archive_bytes": destination.stat().st_size, "manifest_sha256": sha256(manifest_bytes), "archive_member_count": len(payloads)}
    destination.with_suffix(".index.json").write_text(json.dumps(index, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    destination.with_suffix(".zip.sha256").write_text(f"{archive_hash}  {destination.name}\n", encoding="utf-8")
    return {**summary, "archive": destination.relative_to(root).as_posix(), "archive_sha256": archive_hash, "archive_bytes": destination.stat().st_size, "archive_member_count": len(payloads)}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("local-only/deliverables/cluetide-demo.zip"))
    parser.add_argument("--dry-run", action="store_true", help="Validate selected files without writing an archive or sidecars.")
    parser.add_argument("--include-demo", action="store_true", help="Include explicitly selected sanitized data/demo JSON and public evidence bundles.")
    parser.add_argument("--include-doc", action="append", default=[], help="Add one explicitly named docs/*.md product document.")
    parser.add_argument("--demo-file", action="append", default=[], help="Add one explicit data/demo/*.json or *.zip artifact; requires --include-demo.")
    args = parser.parse_args()
    try:
        result = package(REPOSITORY, args.output, dry_run=args.dry_run, include_demo=args.include_demo, documents=args.include_doc, demo_files=args.demo_file)
    except (PackagingError, OSError, zipfile.BadZipFile) as exc:
        print(json.dumps({"status": "failed", "reason": str(exc) if isinstance(exc, PackagingError) else "An archive or filesystem operation failed."}), file=sys.stderr)
        return 1
    print(json.dumps({"status": "validated" if args.dry_run else "created", **result}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
