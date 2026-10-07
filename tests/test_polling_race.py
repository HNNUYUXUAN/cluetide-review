"""Polling must never write a stale investigation snapshot back to storage."""
from __future__ import annotations

import asyncio
import copy
import inspect
import json
import threading
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from cluetide import app as module
from cluetide.bundles import import_bundle, manifest_sha256
from cluetide.registry import LocalRegistry
from cluetide.store import CaseStore


@pytest.fixture
def isolated_polling(monkeypatch, tmp_path):
    monkeypatch.setattr(module, "LOCAL_DATA", tmp_path)
    monkeypatch.setattr(module, "store", CaseStore(tmp_path / "cases.sqlite3"))
    monkeypatch.setattr(module, "registry", LocalRegistry(tmp_path / "registry.json"))
    monkeypatch.setattr(module, "tasks", {})
    monkeypatch.setattr(module, "stops", {})
    monkeypatch.setattr(module, "mutation_lock", asyncio.Lock())
    return module.store


def running_document(case_id: str) -> dict:
    start = module.StartRequest(**{
        key: module.PRESET[key]
        for key in ("address", "token_address", "from_block", "to_block")
    })
    return {
        "id": case_id, "title": "Poll race fixture", "status": "running",
        "input": start.model_dump(), "evidence": None, "agent": None,
        "report": None, "registry": None, "versions": [], "reviews": [],
    }


def database_bytes(store: CaseStore, case_id: str) -> str:
    with store.connect() as connection:
        return connection.execute(
            "SELECT document FROM cases WHERE id=?", (case_id,)
        ).fetchone()[0]


def test_poll_read_before_actual_completion_cannot_erase_report(isolated_polling, monkeypatch):
    """A barrier fixes the precise old read-running/write-completed interleaving.

    The worker invokes the real GET endpoint. The real offline executor publishes
    the case on the main thread, then removes its task before the GET continues.
    No network, model gateway, shared demo state or timing-dependent sleep is used.
    """
    case_id = "poll-completion-race"
    initial = running_document(case_id)
    isolated_polling.put(case_id, initial)
    module.tasks[case_id] = object()
    module.stops[case_id] = asyncio.Event()

    read_snapshot = threading.Event()
    release_snapshot = threading.Event()
    get_original = isolated_polling.get
    put_original = isolated_polling.put
    poll_writes = []
    result = []
    errors = []
    worker = None

    def controlled_get(identifier):
        document = get_original(identifier)
        if threading.current_thread() is worker:
            read_snapshot.set()
            if not release_snapshot.wait(15):
                raise AssertionError("Completion did not release the polling snapshot")
        return document

    def record_put(identifier, document):
        if threading.current_thread() is worker:
            poll_writes.append(copy.deepcopy(document))
        return put_original(identifier, document)

    def poll_once():
        try:
            value = module.investigation(case_id)
            if inspect.isawaitable(value):
                value = asyncio.run(value)
            result.append(value)
        except BaseException as error:
            errors.append(error)

    monkeypatch.setattr(isolated_polling, "get", controlled_get)
    monkeypatch.setattr(isolated_polling, "put", record_put)
    worker = threading.Thread(target=poll_once, name="blocked-investigation-poll")
    worker.start()
    try:
        assert read_snapshot.wait(10), "GET must first read its running snapshot"
        asyncio.run(module.execute(case_id, module.StartRequest(**initial["input"])))
        completed = get_original(case_id)
        completed_bytes = database_bytes(isolated_polling, case_id)
        assert completed["status"] == "completed", completed
        assert case_id not in module.tasks
        assert completed["report"]["conclusion"]
        assert completed["versions"] and completed["bundle_files"]
    finally:
        release_snapshot.set()
        worker.join(20)
    assert not worker.is_alive(), "Blocked GET must terminate after release"
    assert not errors, errors
    assert result, "Actual GET must have returned a document"
    assert poll_writes == [], "GET must not persist its stale running snapshot"
    assert get_original(case_id) == completed
    assert database_bytes(isolated_polling, case_id) == completed_bytes
    # The committed bundle still resolves through the case index and manifest.
    head = completed["versions"][-1]
    committed_path = Path(completed["bundle_files"][str(head["version_id"])])
    assert manifest_sha256(import_bundle(committed_path).manifest) == head["content_hash"]
    assert module.investigation(case_id)["report"] == completed["report"]


def test_get_without_task_is_read_only_until_startup_recovery(isolated_polling, monkeypatch):
    case_id = "orphan-read-only"
    document = running_document(case_id)
    isolated_polling.put(case_id, document)
    original_bytes = database_bytes(isolated_polling, case_id)

    def forbid_put(*args, **kwargs):
        raise AssertionError("Investigation GET cannot write case storage")

    monkeypatch.setattr(isolated_polling, "put", forbid_put)
    response = module.investigation(case_id)
    if inspect.isawaitable(response):
        response = asyncio.run(response)
    assert response == module.public_doc(document)
    assert database_bytes(isolated_polling, case_id) == original_bytes


@pytest.mark.parametrize("terminal_status", ["completed", "partial", "stopped", "error"])
def test_startup_recovery_only_changes_current_running_documents(isolated_polling, terminal_status):
    orphan = running_document("interrupted-restart")
    orphan.update(
        evidence={"raw": {"rpc_observations": [{"method": "eth_chainId", "result": "0x1"}]}},
        agent={"status": "partial", "trace": [{"kind": "observed-before-exit"}]},
    )
    # A legacy restart fixture needs a real verified archive and matching
    # registry records. Seed these directly before the first authority migration,
    # as the supported pre-migration layout does; put() intentionally refuses to
    # create a published document outside an atomic publication transaction.
    source = module.ROOT / "data/demo/bundles/real-acceptance-v1.zip"
    source_bytes = source.read_bytes()
    archive = isolated_polling.path.parent / "preserved-v1.zip"
    archive.write_bytes(source_bytes)
    verified = import_bundle(archive)
    digest = manifest_sha256(verified.manifest)
    final = running_document(verified.report["case_id"])
    version = module.registry.create_case(
        final["id"], digest, author="local-author",
        evidence_uri=f"/api/investigations/{final['id']}/bundle?revision=1",
    )
    final.update(
        status=terminal_status,
        evidence={**verified.evidence, "raw": verified.raw},
        agent=verified.report["agent"], report=verified.report,
        registry=module.registry.get_case(final["id"]), versions=[version],
        bundle_files={str(version["version_id"]): str(archive)},
        manifest=verified.manifest, manifest_hash=digest,
    )
    isolated_polling.put(orphan["id"], orphan)
    with isolated_polling.connect() as connection:
        connection.execute(
            "INSERT INTO cases VALUES (?,?)",
            (final["id"], json.dumps(final, ensure_ascii=False, separators=(",", ":"))),
        )
    final_bytes = database_bytes(isolated_polling, final["id"])

    # TestClient runs the actual ASGI startup/shutdown lifecycle. An ordinary
    # ASGITransport GET intentionally does not perform process recovery.
    with TestClient(module.app) as client:
        recovered = isolated_polling.get(orphan["id"])
        assert recovered["status"] == "partial"
        assert "Previous local process" in recovered["error"]
        assert {key: value for key, value in recovered.items() if key not in {"status", "error"}} == {
            key: value for key, value in orphan.items() if key not in {"status", "error"}
        }
        assert database_bytes(isolated_polling, final["id"]) == final_bytes
        response = client.get("/api/investigations/" + final["id"])
        assert response.status_code == 200
        assert response.json() == module.public_doc(final)
    recovered_bytes = database_bytes(isolated_polling, orphan["id"])
    with TestClient(module.app):
        assert database_bytes(isolated_polling, orphan["id"]) == recovered_bytes
        assert database_bytes(isolated_polling, final["id"]) == final_bytes
    assert archive.read_bytes() == source_bytes
    assert source.read_bytes() == source_bytes


def test_store_recovery_reads_current_row_under_write_transaction(isolated_polling):
    """Restart recovery operates on the current row, never an earlier GET copy."""
    case_id = "recovery-current-row"
    isolated_polling.put(case_id, running_document(case_id))
    stale_read = isolated_polling.get(case_id)
    completed = {**stale_read, "status": "completed", "report": {"preserved": True}}
    isolated_polling.put(case_id, completed)
    before = database_bytes(isolated_polling, case_id)
    isolated_polling.recover_interrupted()
    assert database_bytes(isolated_polling, case_id) == before
    assert json.loads(before) == completed


def test_recovery_waits_for_in_flight_completion_before_selecting_running_rows(isolated_polling, monkeypatch):
    """An uncommitted completion wins over concurrent process recovery.

    A reserved SQLite writer lock permits a stale SELECT on another connection.
    Recovery must therefore acquire its writer lock before deciding which rows
    are interrupted, or use an atomic current-row condition in its UPDATE.
    """
    case_id = "recovery-writer-race"
    initial = running_document(case_id)
    isolated_polling.put(case_id, initial)
    completed = {
        **initial, "status": "completed", "report": {"preserved": True},
        "versions": [{"version_id": 8}], "bundle_files": {"8": "preserved-v1.zip"},
    }
    encoded = json.dumps(completed, ensure_ascii=False, separators=(",", ":"))
    lock_attempt = threading.Event()
    original_connect = isolated_polling.connect
    failures = []

    def watched_connect():
        connection = original_connect()

        def observe(sql):
            normalized = " ".join(sql.upper().split())
            if normalized.startswith(("BEGIN IMMEDIATE", "BEGIN EXCLUSIVE", "UPDATE ")):
                lock_attempt.set()

        connection.set_trace_callback(observe)
        return connection

    def recover():
        try:
            isolated_polling.recover_interrupted()
        except BaseException as error:
            failures.append(error)

    # Hold completion uncommitted so a read before the recovery's writer lock
    # would observe running, rather than relying on nondeterministic timing.
    with original_connect() as writer:
        writer.execute("BEGIN IMMEDIATE")
        writer.execute("UPDATE cases SET document=? WHERE id=?", (encoded, case_id))
        monkeypatch.setattr(isolated_polling, "connect", watched_connect)
        worker = threading.Thread(target=recover, name="startup-recovery-race")
        worker.start()
        try:
            assert lock_attempt.wait(10), "Recovery must reach its SQLite write operation"
        finally:
            writer.commit()
            worker.join(15)
    assert not worker.is_alive()
    assert not failures, failures
    assert database_bytes(isolated_polling, case_id) == encoded
