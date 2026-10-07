"""Saved demo restoration uses SQL authority and preserves source ZIP bytes."""

import copy
import importlib.util
import json
from pathlib import Path
import shutil
import socket
import sqlite3

import pytest

from cluetide.bundles import import_bundle, manifest_sha256
from cluetide.registry import LocalRegistry, RegistryError
from cluetide.store import CaseStore


REPOSITORY = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("seed_demo_publication", REPOSITORY / "scripts" / "seed_demo.py")
seed = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(seed)


@pytest.fixture
def demo_root(tmp_path, monkeypatch):
    for relative in (seed.V1_SOURCE, seed.V2_REFERENCE):
        destination = tmp_path / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(REPOSITORY / relative, destination)
    monkeypatch.setattr(seed, "ensure_service_stopped", lambda port: None)
    return tmp_path


def _snapshot(root):
    return {
        str(path.relative_to(root)): (path.read_bytes(), path.stat().st_mtime_ns)
        for path in root.rglob("*") if path.is_file()
    }


def _sql(root):
    path = root / "local-data" / "cases.sqlite3"
    with sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True) as db:
        documents = [json.loads(row[0]) for row in db.execute("SELECT document FROM cases")]
        rows = db.execute("SELECT state FROM publication_registry WHERE singleton=1").fetchall()
    return documents, json.loads(rows[0][0]) if rows else None


def _restored_path(root):
    source = import_bundle(root / seed.V1_SOURCE)
    return root / "local-data" / "bundles" / (source.report["case_id"] + "-restored-v1.zip")


def _assert_complete_source_copy(root):
    target = _restored_path(root)
    assert target.read_bytes() == (root / seed.V1_SOURCE).read_bytes()
    assert import_bundle(target).verified is True
    assert list(target.parent.glob(".seed-bundle-*.zip")) == []


def test_first_seed_commits_document_and_registry_with_original_archive_bytes(demo_root):
    source = import_bundle(demo_root / seed.V1_SOURCE)
    result = seed.restore_v1(demo_root)
    documents, state = _sql(demo_root)
    assert result["status"] == "restored_v1"
    assert result["model_requests_in_this_script"] == result["chain_reads_in_this_script"] == 0
    assert len(documents) == 1
    document = documents[0]
    registry = LocalRegistry.from_state(state)
    assert document["registry"] == registry.get_case(result["case_id"])
    assert document["versions"] == [registry.get_version(result["local_version_id"])]
    assert document["report"] == source.report
    assert document["agent"] == source.report["agent"]
    assert document["evidence"]["raw"] == source.raw
    assert document["manifest"] == source.manifest
    restoration = document["metadata"]["restoration"]
    assert restoration["source_report_and_agent_preserved"] is True
    assert restoration["reference_v2"]["registered_in_local_registry"] is False
    assert len(state["versions"]) == 1
    assert LocalRegistry(demo_root / "local-data" / "registry.json").export_state() == state
    _assert_complete_source_copy(demo_root)


def test_repeat_seed_is_strict_zero_write(demo_root, monkeypatch):
    first = seed.restore_v1(demo_root)
    before = _snapshot(demo_root)

    def forbid_store(*args, **kwargs):
        raise AssertionError("A no-op must not instantiate a writing CaseStore")

    monkeypatch.setattr(CaseStore, "__init__", forbid_store)
    assert seed.restore_v1(demo_root) == {
        "status": "already_present", "case_id": first["case_id"],
        "manifest_hash": first["manifest_hash"], "local_version_id": first["local_version_id"], "write_count": 0,
    }
    assert _snapshot(demo_root) == before


def test_legacy_existing_case_noop_does_not_create_authority_table_or_lease(demo_root):
    seed.restore_v1(demo_root)
    database = demo_root / "local-data" / "cases.sqlite3"
    with sqlite3.connect(database) as db:
        db.execute("DROP TABLE publication_registry")
    database.with_suffix(".service.lock").unlink()
    before = _snapshot(demo_root)
    assert seed.restore_v1(demo_root)["status"] == "already_present"
    assert _snapshot(demo_root) == before
    with sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True) as db:
        assert not db.execute("SELECT 1 FROM sqlite_master WHERE name='publication_registry'").fetchone()


@pytest.mark.parametrize("mirror", ["stale", "damaged", "missing"])
def test_existing_v2_uses_sql_state_and_is_zero_write_even_with_bad_mirror(demo_root, mirror):
    first = seed.restore_v1(demo_root)
    local_data = demo_root / "local-data"
    mirror_path = local_data / "registry.json"
    store = CaseStore(local_data / "cases.sqlite3")
    registry = store.initialize_registry(mirror_path)
    source_v2 = import_bundle(demo_root / seed.V2_REFERENCE)
    digest_v2 = manifest_sha256(source_v2.manifest)
    v2_path = local_data / "bundles" / "saved-correction-v2.zip"
    seed.publish_frozen_bundle((demo_root / seed.V2_REFERENCE).read_bytes(), v2_path, demo_root)

    def correction(document, current):
        document = copy.deepcopy(document)
        version = current.append_version(first["case_id"], document["versions"][-1]["version_id"],
                                         digest_v2, author="local-author")
        document["report"] = source_v2.report
        document["versions"].append(version)
        document["registry"] = current.get_case(first["case_id"])
        document["manifest"] = source_v2.manifest
        document["manifest_hash"] = digest_v2
        document["bundle_files"][str(version["version_id"])] = str(v2_path)
        return document

    with store.service_lease():
        completed_v2 = store.mutate_publication(registry, first["case_id"], correction)
    if mirror == "missing":
        mirror_path.unlink()
    elif mirror == "damaged":
        mirror_path.write_bytes(b"damaged mirror")
    else:
        mirror_path.write_text(json.dumps(LocalRegistry().export_state()), encoding="utf-8")
    before = _snapshot(demo_root)
    assert seed.restore_v1(demo_root)["status"] == "already_present"
    assert _snapshot(demo_root) == before
    assert _sql(demo_root)[0] == [completed_v2]
    assert completed_v2["manifest_hash"] == digest_v2
    _assert_complete_source_copy(demo_root)


@pytest.mark.parametrize("stage", ["_write_document", "_write_registry", "_commit"])
@pytest.mark.parametrize("after_write", [False, True])
def test_sql_faults_keep_document_and_registry_atomic_and_retries_preserve_archive(
    demo_root, monkeypatch, stage, after_write
):
    original = getattr(CaseStore, stage)

    def fail(self, *args):
        if after_write:
            original(self, *args)
        raise sqlite3.OperationalError("injected SQL publication failure")

    with monkeypatch.context() as patch:
        patch.setattr(CaseStore, stage, fail)
        if stage == "_commit" and after_write:
            # The transaction recognizes an exact committed target despite an
            # ambiguous acknowledgement and safely reports its durable result.
            assert seed.restore_v1(demo_root)["status"] == "restored_v1"
        else:
            with pytest.raises(sqlite3.OperationalError, match="injected SQL"):
                seed.restore_v1(demo_root)
    documents, state = _sql(demo_root)
    committed = stage == "_commit" and after_write
    assert bool(documents) is committed
    assert bool(state) is committed
    _assert_complete_source_copy(demo_root)
    before_archive = _restored_path(demo_root).read_bytes()
    if committed:
        # Simulate restart after the SQL commit's acknowledgement failed.
        # No missing/stale mirror can justify deleting the committed archive.
        before = _snapshot(demo_root)
        assert seed.restore_v1(demo_root)["status"] == "already_present"
        assert _snapshot(demo_root) == before
    else:
        assert seed.restore_v1(demo_root)["status"] == "restored_v1"
    assert _restored_path(demo_root).read_bytes() == before_archive
    assert len(_sql(demo_root)[0]) == len(_sql(demo_root)[1]["cases"]) == 1


@pytest.mark.parametrize("after_write", [False, True])
def test_mirror_failure_after_sql_commit_never_rolls_back_or_deletes_bundle(
    demo_root, monkeypatch, after_write
):
    original = CaseStore._write_registry_mirror

    def fail(self, *args):
        if after_write:
            original(self, *args)
        raise OSError("injected mirror failure")

    with monkeypatch.context() as patch:
        patch.setattr(CaseStore, "_write_registry_mirror", fail)
        assert seed.restore_v1(demo_root)["status"] == "restored_v1"
    documents, state = _sql(demo_root)
    assert len(documents) == len(state["cases"]) == 1
    _assert_complete_source_copy(demo_root)
    before = _snapshot(demo_root)
    assert seed.restore_v1(demo_root)["status"] == "already_present"
    assert _snapshot(demo_root) == before
    local_data = demo_root / "local-data"
    # Service startup repairs only the mirror from authoritative SQL.
    recovered = CaseStore(local_data / "cases.sqlite3").initialize_registry(local_data / "registry.json")
    assert recovered.export_state() == state
    assert LocalRegistry(local_data / "registry.json").export_state() == state
    _assert_complete_source_copy(demo_root)


def test_write_seed_refuses_database_owned_by_service_on_another_port(demo_root):
    store = CaseStore(demo_root / "local-data" / "cases.sqlite3")
    with store.service_lease():
        with pytest.raises(RegistryError, match="local_service_already_running"):
            seed.restore_v1(demo_root, port=8766)
    assert _sql(demo_root) == ([], None)
    assert not _restored_path(demo_root).exists()


def test_partial_crash_or_conflicting_destination_is_never_overwritten(demo_root):
    target = _restored_path(demo_root)
    target.parent.mkdir(parents=True)
    target.write_bytes(b"partial earlier file")
    with pytest.raises(seed.SeedError, match="different bytes"):
        seed.restore_v1(demo_root)
    assert target.read_bytes() == b"partial earlier file"
    assert _sql(demo_root) == ([], None)
    assert list(target.parent.glob(".seed-bundle-*.zip")) == []


def test_frozen_publish_failure_after_link_is_complete_and_seed_can_resume(demo_root, monkeypatch):
    real_link = seed.os.link

    def link_then_fail(source, destination):
        real_link(source, destination)
        raise OSError("injected failure after archive publication")

    with monkeypatch.context() as patch:
        patch.setattr(seed.os, "link", link_then_fail)
        with pytest.raises(OSError, match="after archive"):
            seed.restore_v1(demo_root)
    assert _sql(demo_root) == ([], None)
    _assert_complete_source_copy(demo_root)
    before = _restored_path(demo_root).read_bytes()
    assert seed.restore_v1(demo_root)["status"] == "restored_v1"
    assert _restored_path(demo_root).read_bytes() == before


def test_read_publication_never_creates_missing_database(tmp_path):
    data = tmp_path / "local-data"
    document, registry = seed.read_publication(data / "cases.sqlite3", data / "registry.json", "missing")
    assert document is None
    assert registry.export_state() == LocalRegistry().export_state()
    assert not data.exists()


def test_existing_inconsistent_document_is_refused_without_writes(demo_root):
    result = seed.restore_v1(demo_root)
    database = demo_root / "local-data" / "cases.sqlite3"
    with sqlite3.connect(database) as db:
        encoded = db.execute("SELECT document FROM cases WHERE id=?", (result["case_id"],)).fetchone()[0]
        document = json.loads(encoded)
        document["registry"]["head_version_id"] += 1
        db.execute("UPDATE cases SET document=? WHERE id=?", (json.dumps(document), result["case_id"]))
    before = _snapshot(demo_root)
    with pytest.raises(RegistryError, match="publication_state_mismatch"):
        seed.restore_v1(demo_root)
    assert _snapshot(demo_root) == before


@pytest.mark.parametrize("relative", ["../outside.zip", ".env.zip"])
def test_seed_path_guards_refuse_outside_and_environment_paths(tmp_path, relative):
    with pytest.raises(seed.SeedError, match="outside the selected project"):
        seed.safe_path(tmp_path / relative, tmp_path)


def test_frozen_source_size_limit_refuses_before_reading(demo_root, monkeypatch):
    monkeypatch.setattr(seed, "MAX_ARCHIVE_BYTES", 1)
    before = _snapshot(demo_root)
    with pytest.raises(seed.SeedError, match="bounded public source ZIP"):
        seed.restore_v1(demo_root)
    assert _snapshot(demo_root) == before


def test_invalid_source_archive_is_refused_before_creating_local_state(demo_root):
    source = demo_root / seed.V1_SOURCE
    source.write_bytes(b"invalid archive")
    before = _snapshot(demo_root)
    with pytest.raises(ValueError, match="invalid or unreadable"):
        seed.restore_v1(demo_root)
    assert _snapshot(demo_root) == before
    assert not (demo_root / "local-data").exists()


def test_real_loopback_port_guard_detects_running_service():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        with pytest.raises(seed.SeedError, match="Stop the loopback service"):
            seed.ensure_service_stopped(listener.getsockname()[1])


@pytest.mark.parametrize("port", [0, 65536, True, "5186"])
def test_seed_port_guard_rejects_invalid_ports(port):
    with pytest.raises(seed.SeedError, match="between 1 and 65535"):
        seed.ensure_service_stopped(port)
