"""Exclusive archive publication, including process-like faults and races."""

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier
import zipfile

import pytest

import cluetide.bundles as bundles
from cluetide.bundles import BundleConflictError, export_bundle, import_bundle


EVIDENCE = {"case_id": "immutable-case", "raw": {"observations": []}}


def _export(path: Path, summary: str = "first"):
    return export_bundle(
        EVIDENCE, {"summary": summary}, "# " + summary, path, immutable=True
    )


def _assert_no_temporaries(directory: Path):
    assert list(directory.glob(".cluetide-bundle-*.zip")) == []


def test_immutable_publication_is_complete_and_fsynced_before_final_path(tmp_path, monkeypatch):
    target = tmp_path / "case.zip"
    real_fsync, real_link = bundles.os.fsync, bundles.os.link
    flushed = False

    def fsync(handle):
        nonlocal flushed
        real_fsync(handle)
        flushed = True

    def link(source, destination):
        assert flushed is True
        assert not target.exists()
        assert import_bundle(source).report == {"summary": "first"}
        real_link(source, destination)
        assert import_bundle(destination).verified is True

    monkeypatch.setattr(bundles.os, "fsync", fsync)
    monkeypatch.setattr(bundles.os, "link", link)
    manifest = _export(target)
    assert import_bundle(target).manifest == manifest
    _assert_no_temporaries(tmp_path)


def test_same_bytes_are_idempotent_without_replacing_existing_archive(tmp_path):
    target = tmp_path / "case.zip"
    first_manifest = _export(target)
    before, before_stat = target.read_bytes(), target.stat()
    assert _export(target) == first_manifest
    assert target.read_bytes() == before
    assert target.stat().st_mtime_ns == before_stat.st_mtime_ns
    _assert_no_temporaries(tmp_path)


def test_different_content_never_overwrites_existing_archive(tmp_path):
    target = tmp_path / "case.zip"
    first_manifest = _export(target)
    before = target.read_bytes()
    with pytest.raises(BundleConflictError, match="immutable archive already exists"):
        _export(target, "replacement")
    assert target.read_bytes() == before
    assert import_bundle(target).manifest == first_manifest
    _assert_no_temporaries(tmp_path)


def test_same_manifest_with_different_zip_bytes_is_a_conflict(tmp_path):
    target = tmp_path / "case.zip"
    manifest = _export(target)
    with zipfile.ZipFile(target) as archive:
        members = [(info, archive.read(info)) for info in archive.infolist()]
    # Only container timestamps change. Payloads, manifest and ZIP size remain
    # identical, so a manifest-only check would wrongly accept this archive.
    with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_STORED) as archive:
        for info, payload in members:
            info.date_time = (2000, 1, 1, 0, 0, 0)
            archive.writestr(info, payload)
    changed_bytes = target.read_bytes()
    assert import_bundle(target).manifest == manifest
    with pytest.raises(BundleConflictError):
        _export(target)
    assert target.read_bytes() == changed_bytes
    _assert_no_temporaries(tmp_path)


def test_unreadable_or_invalid_existing_archive_is_not_replaced(tmp_path):
    target = tmp_path / "case.zip"
    target.write_bytes(b"existing malformed archive")
    with pytest.raises(BundleConflictError):
        _export(target)
    assert target.read_bytes() == b"existing malformed archive"
    _assert_no_temporaries(tmp_path)


def test_existing_directory_is_a_conflict_and_is_preserved(tmp_path):
    target = tmp_path / "case.zip"
    target.mkdir()
    with pytest.raises((BundleConflictError, FileExistsError)):
        _export(target)
    assert target.is_dir()
    _assert_no_temporaries(tmp_path)


@pytest.mark.parametrize("same_content", [True, False])
def test_concurrent_publishers_use_one_complete_immutable_file(
    tmp_path, monkeypatch, same_content
):
    target = tmp_path / "case.zip"
    expected_paths = [tmp_path / "first.zip", tmp_path / "second.zip"]
    summaries = ["first", "first" if same_content else "second"]
    expected_bytes = []
    for path, summary in zip(expected_paths, summaries):
        _export(path, summary)
        expected_bytes.append(path.read_bytes())

    barrier = Barrier(2)
    real_link = bundles.os.link

    def link(source, destination):
        barrier.wait(timeout=5)
        real_link(source, destination)

    monkeypatch.setattr(bundles.os, "link", link)

    def publish(summary):
        try:
            return _export(target, summary)
        except BundleConflictError as exc:
            return exc

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(publish, summaries))
    if same_content:
        assert all(isinstance(result, dict) for result in results)
        assert results[0] == results[1]
    else:
        assert sum(isinstance(result, dict) for result in results) == 1
        assert sum(isinstance(result, BundleConflictError) for result in results) == 1
        winner = next(index for index, result in enumerate(results) if isinstance(result, dict))
        assert target.read_bytes() == expected_bytes[winner]
    assert target.read_bytes() in expected_bytes
    assert import_bundle(target).verified is True
    _assert_no_temporaries(tmp_path)


@pytest.mark.parametrize("existing", [True, False])
@pytest.mark.parametrize("stage", ["write", "fsync", "link"])
def test_faults_before_publication_preserve_existing_archive(
    tmp_path, monkeypatch, existing, stage
):
    target = tmp_path / "case.zip"
    before = None
    if existing:
        _export(target)
        before = target.read_bytes()

    def fail(*args, **kwargs):
        raise OSError("injected publication failure")

    if stage == "write":
        monkeypatch.setattr(bundles.zipfile.ZipFile, "writestr", fail)
    else:
        monkeypatch.setattr(bundles.os, stage, fail)
    with pytest.raises(OSError, match="injected publication failure"):
        _export(target, "replacement")
    if existing:
        assert target.read_bytes() == before
    else:
        assert not target.exists()
    _assert_no_temporaries(tmp_path)


def test_fault_after_exclusive_publication_leaves_complete_recoverable_archive(
    tmp_path, monkeypatch
):
    target = tmp_path / "case.zip"
    real_link = bundles.os.link

    def link_then_fail(source, destination):
        real_link(source, destination)
        raise OSError("injected failure after publication")

    monkeypatch.setattr(bundles.os, "link", link_then_fail)
    with pytest.raises(OSError, match="after publication"):
        _export(target)
    before = target.read_bytes()
    assert import_bundle(target).verified is True
    _assert_no_temporaries(tmp_path)
    monkeypatch.setattr(bundles.os, "link", real_link)
    assert _export(target) == import_bundle(target).manifest
    assert target.read_bytes() == before


def test_default_export_retains_atomic_replacement_compatibility(tmp_path):
    target = tmp_path / "case.zip"
    _export(target)
    before = target.read_bytes()
    export_bundle(EVIDENCE, {"summary": "replacement"}, "# replacement", target)
    assert target.read_bytes() != before
    assert import_bundle(target).report == {"summary": "replacement"}
    _assert_no_temporaries(tmp_path)


@pytest.mark.parametrize("value", [1, "true", None])
def test_immutable_option_requires_boolean(tmp_path, value):
    target = tmp_path / "case.zip"
    with pytest.raises(bundles.BundleValidationError, match="must be a boolean"):
        export_bundle(EVIDENCE, {}, "", target, immutable=value)
    assert not target.exists()
