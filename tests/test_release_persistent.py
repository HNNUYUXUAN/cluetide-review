"""Real PostgreSQL integration. Every database namespace is isolated per test."""
from concurrent.futures import ThreadPoolExecutor
import os
import threading
import time
from uuid import uuid4

import pytest

from cluetide.release_persistent import PersistentReleaseService
from cluetide.release_storage import PostgresRecordStore, FileRecordStore, RecordPath
from cluetide.releases import ReleaseService, ReleaseError
from test_releases import ConfirmedChain, OWNER, REVIEWER, EXECUTOR, TX
from test_release_integration_evm import release, create, version, chain_action, start
from test_release_feedback import signed_feedback


@pytest.fixture
def database():
    dsn = os.environ.get("CLUETIDE_TEST_DATABASE_URL")
    if not dsn:
        pytest.skip("Set CLUETIDE_TEST_DATABASE_URL for real PostgreSQL integration")
    pytest.importorskip("psycopg")
    store = PostgresRecordStore(dsn, "test_" + uuid4().hex)
    store.initialize()
    with store.transaction():
        store.set_writer(True)
    yield store
    with store.connect() as connection:
        for table in ("cluetide_records", "cluetide_task_leases", "cluetide_writer_control"):
            connection.execute(f"DELETE FROM {table} WHERE namespace=%s", (store.namespace,))


@pytest.fixture
def persistent(database):
    service = PersistentReleaseService(database, chain=ConfirmedChain(), signing_key=bytes.fromhex("07" * 32), writes_enabled=True)
    task = service.create_task({"title": "Persistent release", "owner": OWNER, "proposer": OWNER, "reviewer": REVIEWER, "executor": EXECUTOR})
    package = service.create_version(task["task_id"], {"summary": "Exact persistent bytes", "source_reference": "test:postgres",
        "artifact_text": "\ufeff<!doctype html>\r\n<h1>中文 😀</h1>\r\n", "test_evidence": ["Real database fixture"], "expires_at": int(time.time()) + 3600})
    request = {"version_id": 1, "attempt_id": "1", "start_transaction_hash": TX}
    return service, task, package, request


def recreated(service):
    store = PostgresRecordStore(service.store._dsn, service.store.namespace)
    return PersistentReleaseService(store, chain=service.chain, signing_key=service._key, writes_enabled=True)


def expire(service, task):
    with service.store.transaction("test-expiry") as connection:
        connection.execute("UPDATE cluetide_task_leases SET expires_at=clock_timestamp()-interval '1 second' WHERE namespace=%s AND task_id=%s",
                           (service.store.namespace, task["task_id"]))


def test_raw_bytes_and_signed_receipt_survive_a_new_instance(persistent):
    service, task, package, request = persistent
    result = service.execute(task["task_id"], request)
    assert result["status"] == "succeeded"
    fresh = recreated(service)
    assert fresh.artifact(task["task_id"]) == "\ufeff<!doctype html>\r\n<h1>中文 😀</h1>\r\n".encode()
    first = service.receipt("1", task["task_id"])
    assert fresh.receipt("1", task["task_id"]) == first
    assert all(first["verification"][key] for key in ("signature_valid", "artifact_matches", "current_target_matches"))
    assert fresh.execute(task["task_id"], request)["status"] == "succeeded"
    assert fresh.bundle(task["task_id"], 1) == service.bundle(task["task_id"], 1)


def test_immutable_record_conflict_rolls_back_the_whole_transaction(database):
    with database.transaction():
        database.write("original.json", b"original", immutable=True)
    with pytest.raises(ReleaseError):
        with database.transaction():
            database.write("partial.json", b"must rollback")
            database.write("original.json", b"changed", immutable=True)
    with database.transaction():
        assert not database.exists("partial.json")
        assert database.read("original.json") == b"original"


def test_concurrent_versions_are_serialized_by_task(persistent):
    service, task, _, _ = persistent
    def add(number):
        return recreated(service).create_version(task["task_id"], {"summary": f"Revision {number}", "source_reference": "test:concurrent",
            "artifact_text": f"<h1>{number}</h1>", "test_evidence": ["Concurrent test"], "expires_at": int(time.time()) + 3600})["version_id"]
    with ThreadPoolExecutor(max_workers=6) as pool:
        assert sorted(pool.map(add, range(6))) == list(range(2, 8))
    assert len(service.get_task(task["task_id"])["versions"]) == 7


def test_function_interruption_leaves_recoverable_original_attempt(persistent, monkeypatch):
    service, task, _, request = persistent
    def interrupted(*_):
        raise RuntimeError("Simulated function interruption")
    monkeypatch.setattr(service, "_stage_artifact", interrupted)
    with pytest.raises(RuntimeError):
        service.execute(task["task_id"], request)
    fresh = recreated(service)
    assert fresh.attempt_status(task["task_id"], "1")["lease_active"]
    assert not fresh.attempt_status(task["task_id"], "1")["can_resume"]
    assert fresh.execute(task["task_id"], request)["status"] == "pending"
    expire(service, task)
    assert fresh.attempt_status(task["task_id"], "1")["can_resume"]
    assert fresh.execute(task["task_id"], request)["status"] == "succeeded"
    assert fresh.get_task(task["task_id"])["attempts"][0]["execution_generation"] == 2


def test_expired_instance_cannot_commit_after_a_new_generation(persistent, monkeypatch):
    service, task, _, request = persistent
    entered, resume = threading.Event(), threading.Event()
    actual = service._stage_artifact
    def slow(*args):
        entered.set()
        assert resume.wait(15)
        return actual(*args)
    monkeypatch.setattr(service, "_stage_artifact", slow)
    with ThreadPoolExecutor(max_workers=1) as pool:
        old = pool.submit(service.execute, task["task_id"], request)
        assert entered.wait(10)
        expire(service, task)
        fresh = recreated(service)
        assert fresh.execute(task["task_id"], request)["status"] == "succeeded"
        receipt = fresh.receipt("1", task["task_id"])
        resume.set()
        with pytest.raises(ReleaseError, match="lease expired"):
            old.result()
        assert fresh.receipt("1", task["task_id"]) == receipt


def test_artifact_marker_receipt_commit_atomically(persistent, monkeypatch):
    service, task, _, request = persistent
    def interrupted(*_):
        raise RuntimeError("Interrupted before receipt commit")
    monkeypatch.setattr(service, "_finish", interrupted)
    with pytest.raises(RuntimeError):
        service.execute(task["task_id"], request)
    with service.store.transaction(task["task_id"]):
        assert not (service._target(task) / "current.json").exists()
        assert not (service._target(task) / "releases/1/artifact.html").exists()
        assert not (service._attempt_path(task["task_id"], "1") / "receipt.json").exists()
    expire(service, task)
    assert recreated(service).execute(task["task_id"], request)["status"] == "succeeded"


def test_existing_attempt_parameters_and_paused_writer_are_enforced(persistent):
    service, task, _, request = persistent
    service.execute(task["task_id"], request)
    with pytest.raises(ReleaseError, match="different release parameters"):
        service.execute(task["task_id"], {**request, "start_transaction_hash": "0x" + "aa" * 32})
    with service.store.transaction():
        service.store.set_writer(False)
    with pytest.raises(ReleaseError, match="paused"):
        recreated(service).create_version(task["task_id"], {"summary": "Paused", "source_reference": "test:pause", "artifact_text": "<h1>Paused</h1>", "test_evidence": ["Paused writer test"], "expires_at": int(time.time()) + 3600})
    assert service.receipt("1", task["task_id"])["verification"]["signature_valid"]


def test_production_identity_is_required_and_never_generated(database):
    service = PersistentReleaseService(database, writes_enabled=True)
    with pytest.raises(ReleaseError, match="identity must be configured"):
        service.config()
    wrong = PersistentReleaseService(database, signing_key=bytes.fromhex("07" * 32), expected_signer=OWNER, writes_enabled=True)
    with pytest.raises(ReleaseError, match="does not match"):
        wrong.config()
    with database.transaction():
        assert database.keys() == []


def test_file_to_postgres_migration_preserves_originals_and_receipts(database, tmp_path):
    from scripts.release_storage_admin import inventory, validate_records
    local = ReleaseService(tmp_path / "source", chain=ConfirmedChain(), signing_key=bytes.fromhex("07" * 32))
    task = local.create_task({"title": "Migration", "owner": OWNER, "proposer": OWNER, "reviewer": REVIEWER, "executor": EXECUTOR})
    local.create_version(task["task_id"], {"summary": "Raw migration", "source_reference": "test:bytes", "artifact_text": "\ufeff<h1>Original</h1>\r\n", "test_evidence": ["Migration"], "expires_at": int(time.time()) + 3600})
    local.execute(task["task_id"], {"version_id": 1, "attempt_id": "1", "start_transaction_hash": TX})
    source = FileRecordStore(local.root)
    expected = inventory(source)
    with database.transaction():
        for entry in expected:
            database.write(entry["path"], source.read(entry["path"]), immutable=True)
        assert inventory(database) == expected
    persistent = PersistentReleaseService(database, chain=local.chain)
    assert validate_records(persistent) == {"tasks": 1, "versions": 1, "feedback": 0, "receipts": 1}
    assert persistent.artifact(task["task_id"]) == local.artifact(task["task_id"])


def test_real_evm_correction_revision_and_receipt_on_postgres(database, release):
    local, bridge, contract = release
    service = PersistentReleaseService(database, chain=local.chain, signing_key=local._key, clock=local._clock, writes_enabled=True)
    context = service, bridge, contract
    task = create(context)
    v1 = version(context, task)
    chain_action(context, "approve", task, v1)
    v2 = version(context, task, "<h1>Needs correction</h1>")
    correction = chain_action(context, "request_correction", task, v2)
    data = signed_feedback(context, task, v2, correction["transaction_hash"])
    feedback = service.submit_feedback(task["task_id"], data)
    assert service.submit_feedback(task["task_id"], data) == feedback
    assert recreated(service).feedback(task["task_id"], 2)["feedback"] == [feedback]
    v3 = version(context, task, "<h1>Corrected release</h1>")
    request = start(context, task, v3)
    assert recreated(service).execute(task["task_id"], request)["status"] == "succeeded"
    chain_action(context, "record_receipt", task, v3, "1")
    assert recreated(service).receipt("1", task["task_id"])["verification"]["signature_valid"]


def test_record_keys_reject_path_aliases(database):
    for key in ("../key", "/key", "a//b", "a/./b", "a\\b"):
        with pytest.raises(ReleaseError):
            RecordPath(database, key)


def test_staging_io_failure_has_a_signed_failure_result(persistent, monkeypatch):
    service, task, _, request = persistent
    def failed(*_):
        raise OSError("Isolated staging filesystem unavailable")
    monkeypatch.setattr(service, "_stage_artifact", failed)
    result = service.execute(task["task_id"], request)
    assert result["status"] == "failed"
    receipt = recreated(service).receipt("1", task["task_id"])
    assert receipt["verification"]["signature_valid"]
    assert receipt["receipt"]["output_hash"] is None
    assert not receipt["verification"]["artifact_matches"]


def test_task_lease_serializes_different_attempts(persistent, monkeypatch):
    service, task, _, request = persistent
    entered, resume = threading.Event(), threading.Event()
    actual = service._stage_artifact
    def slow(*args):
        entered.set()
        assert resume.wait(15)
        return actual(*args)
    monkeypatch.setattr(service, "_stage_artifact", slow)
    with ThreadPoolExecutor(max_workers=1) as pool:
        first = pool.submit(service.execute, task["task_id"], request)
        assert entered.wait(10)
        fresh = recreated(service)
        second = {**request, "attempt_id": "2"}
        assert fresh.execute(task["task_id"], second)["status"] == "pending"
        assert not fresh.attempt_status(task["task_id"], "2")["can_resume"]
        resume.set()
        assert first.result()["status"] == "succeeded"
        assert fresh.execute(task["task_id"], second)["status"] == "succeeded"
