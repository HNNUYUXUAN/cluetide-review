"""Publication faults keep SQLite, registry commitments and ZIPs consistent.

All cases live in temporary directories. The saved sanitized v1 is only read;
these regressions never call a model gateway or an external RPC endpoint.
"""
from __future__ import annotations

import asyncio
import copy
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import sqlite3
from threading import Barrier

import httpx
import pytest
from pydantic_ai import models

from cluetide import app as module
from cluetide.bundles import import_bundle, manifest_sha256
from fastapi import HTTPException

from cluetide.registry import LocalRegistry, RegistryError
from cluetide.store import CaseStore


FAULT_STAGES = ("_write_document", "_write_registry", "_commit")


@pytest.fixture
def published_case(monkeypatch, tmp_path):
    """Bootstrap an actual legacy case/mirror before SQLite becomes authority."""
    source = module.ROOT / "data/demo/bundles/real-acceptance-v1.zip"
    verified = import_bundle(source)
    case_id = verified.report["case_id"]
    archive = tmp_path / "bundles" / "retained-original-v1.zip"
    archive.parent.mkdir()
    archive.write_bytes(source.read_bytes())
    digest = manifest_sha256(verified.manifest)
    registry = LocalRegistry(tmp_path / "registry.json")
    version = registry.create_case(
        case_id, digest, author="local-author",
        evidence_uri=f"/api/investigations/{case_id}/bundle?revision=1",
    )
    store = CaseStore(tmp_path / "cases.sqlite3")
    document = {
        "id": case_id, "title": "Atomic publication fixture", "status": "completed",
        "input": {**verified.evidence["request"], "mode": "offline", "agent_mode": "offline"},
        "evidence": {**copy.deepcopy(verified.evidence), "raw": copy.deepcopy(verified.raw)},
        "agent": copy.deepcopy(verified.report["agent"]),
        "report": copy.deepcopy(verified.report),
        "registry": registry.get_case(case_id), "versions": [version], "reviews": [],
        "manifest": copy.deepcopy(verified.manifest), "manifest_hash": digest,
        "bundle_files": {str(version["version_id"]): str(archive)},
    }
    # This is the on-disk format from before registry-state transactions were
    # introduced. Deliberately avoid a new-publication API in this seed step.
    with store.connect() as connection:
        connection.execute("INSERT INTO cases VALUES (?, ?)", (
            case_id, json.dumps(document, ensure_ascii=False, separators=(",", ":")),
        ))
    registry = store.initialize_registry(registry)
    monkeypatch.setattr(module, "LOCAL_DATA", tmp_path)
    monkeypatch.setattr(module, "store", store)
    monkeypatch.setattr(module, "registry", registry)
    monkeypatch.setattr(module, "tasks", {})
    monkeypatch.setattr(module, "stops", {})
    monkeypatch.setattr(module, "mutation_lock", asyncio.Lock())
    monkeypatch.setattr(module, "paid_session_available", lambda: False)

    def forbid_external(*args, **kwargs):
        raise AssertionError("Publication regressions must remain offline")

    monkeypatch.setattr(module, "ReadOnlyRpcClient", forbid_external)
    monkeypatch.setattr(module, "run_paid", forbid_external)
    with models.override_allow_model_requests(False):
        yield case_id


def raw_case(case_id):
    with module.store.connect() as connection:
        return connection.execute(
            "SELECT document FROM cases WHERE id=?", (case_id,),
        ).fetchone()[0]


def bundle_bytes():
    return {str(path): path.read_bytes() for path in (module.LOCAL_DATA / "bundles").glob("*.zip")}


def assert_preserved(expected):
    for path, payload in expected.items():
        assert Path(path).read_bytes() == payload, path


def assert_consistent(case_id):
    document = module.store.get(case_id)
    assert document["registry"] == module.registry.get_case(case_id)
    assert document["registry"]["head_version_id"] == document["versions"][-1]["version_id"]
    for version in document["versions"]:
        assert module.registry.get_version(version["version_id"]) == version
        path = Path(document["bundle_files"][str(version["version_id"])])
        assert manifest_sha256(import_bundle(path).manifest) == version["content_hash"]
    for review in document["reviews"]:
        committed = module.registry.get_review(review["review_id"])
        assert committed == {key: value for key, value in review.items() if key != "comment"}
    return document


def restart(monkeypatch):
    store = CaseStore(module.LOCAL_DATA / "cases.sqlite3")
    registry = LocalRegistry(module.LOCAL_DATA / "registry.json")
    registry = store.initialize_registry(registry)
    monkeypatch.setattr(module, "store", store)
    monkeypatch.setattr(module, "registry", registry)
    return store, registry


def correction_body(document, text="Corrected with retained evidence."):
    return {
        "author": "local-author", "parent_version_id": document["versions"][-1]["version_id"],
        "correction": text, "corrected_summary": text,
    }


def client():
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=module.app, raise_app_exceptions=False),
        base_url="http://127.0.0.1:8765",
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("stage", FAULT_STAGES)
async def test_correction_sql_fault_rolls_back_then_restart_retry_preserves_archives(
    published_case, monkeypatch, stage,
):
    case_id = published_case
    original = assert_consistent(case_id)
    before_row, before_state = raw_case(case_id), module.registry.export_state()
    before_mirror = module.registry.path.read_bytes()
    original_archives = bundle_bytes()
    failures = []

    def fail(*args, **kwargs):
        failures.append(stage)
        raise sqlite3.OperationalError("Injected publication transaction failure")

    async with client() as api:
        with monkeypatch.context() as fault:
            fault.setattr(module.store, stage, fail)
            response = await api.post(
                f"/api/investigations/{case_id}/versions", json=correction_body(original),
            )
        assert response.status_code == 503
        assert "Injected publication" not in response.text
        assert failures == [stage]
        assert raw_case(case_id) == before_row
        assert module.registry.export_state() == before_state
        assert module.registry.path.read_bytes() == before_mirror
        assert_consistent(case_id)
        assert_preserved(original_archives)

        # An exported-but-uncommitted attempt may remain as an orphan. Its bytes
        # must survive retry; a different UUID archive receives the committed v2.
        retained_after_fault = bundle_bytes()
        restart(monkeypatch)
        assert raw_case(case_id) == before_row
        assert module.registry.export_state() == before_state
        retried = await api.post(
            f"/api/investigations/{case_id}/versions",
            json=correction_body(original, "Retried correction after a local restart."),
        )
        assert retried.status_code == 200, retried.text
        revised = assert_consistent(case_id)
        assert len(revised["versions"]) == 2
        assert revised["report"]["parent_manifest_hash"] == original["manifest_hash"]
        assert_preserved(retained_after_fault)
        committed_path = revised["bundle_files"][str(revised["versions"][-1]["version_id"])]
        assert committed_path not in retained_after_fault
        stale = await api.post(
            f"/api/investigations/{case_id}/versions", json=correction_body(original, "Stale retry."),
        )
        assert stale.status_code == 409
        after_success = bundle_bytes()
        restart(monkeypatch)
        assert_consistent(case_id)
        assert_preserved(after_success)


@pytest.mark.asyncio
@pytest.mark.parametrize("stage", FAULT_STAGES)
async def test_review_sql_fault_rolls_back_both_stores_and_retries(
    published_case, monkeypatch, stage,
):
    case_id = published_case
    original = assert_consistent(case_id)
    before_row, before_state = raw_case(case_id), module.registry.export_state()
    before_mirror, before_archives = module.registry.path.read_bytes(), bundle_bytes()

    def fail(*args, **kwargs):
        raise sqlite3.OperationalError("Injected review transaction failure")

    body = {"reviewer": "local-reviewer", "comment": "Keep the evidence limitations explicit."}
    async with client() as api:
        with monkeypatch.context() as fault:
            fault.setattr(module.store, stage, fail)
            response = await api.post(f"/api/investigations/{case_id}/reviews", json=body)
        assert response.status_code == 503
        assert "Injected review" not in response.text
        assert raw_case(case_id) == before_row
        assert module.registry.export_state() == before_state
        assert module.registry.path.read_bytes() == before_mirror
        restart(monkeypatch)
        response = await api.post(f"/api/investigations/{case_id}/reviews", json=body)
        assert response.status_code == 200, response.text
    reviewed = assert_consistent(case_id)
    assert len(reviewed["reviews"]) == 1
    assert reviewed["versions"] == original["versions"]
    assert reviewed["manifest_hash"] == original["manifest_hash"]
    assert_preserved(before_archives)


@pytest.mark.asyncio
@pytest.mark.parametrize("stage", FAULT_STAGES)
async def test_initial_publication_sql_fault_does_not_commit_a_partial_version(
    published_case, monkeypatch, stage,
):
    initial_id = "new-publication-fault"
    start = module.StartRequest(**{
        key: module.PRESET[key]
        for key in ("address", "token_address", "from_block", "to_block")
    })
    initial = {
        "id": initial_id, "title": "Initial publication fault", "status": "running",
        "input": start.model_dump(), "evidence": None, "agent": None,
        "report": None, "registry": None, "versions": [], "reviews": [],
    }
    module.store.put(initial_id, initial)
    before_state, before_mirror = module.registry.export_state(), module.registry.path.read_bytes()
    previous_archives = bundle_bytes()
    original_method = getattr(module.store, stage)
    failures = []

    def fail_publication(*args, **kwargs):
        if stage == "_write_document" and not args[2].get("versions"):
            return original_method(*args, **kwargs)
        failures.append(stage)
        raise sqlite3.OperationalError("Injected initial publication failure")

    module.stops[initial_id] = asyncio.Event()
    with monkeypatch.context() as fault:
        fault.setattr(module.store, stage, fail_publication)
        await module.execute(initial_id, start)
    assert failures == [stage]
    interrupted = module.store.get(initial_id)
    assert interrupted["status"] in {"error", "partial"}
    assert interrupted["versions"] == []
    assert not interrupted.get("bundle_files")
    assert module.registry.export_state() == before_state
    assert module.registry.path.read_bytes() == before_mirror
    assert_preserved(previous_archives)
    retained = bundle_bytes()
    restart(monkeypatch)
    assert module.registry.export_state() == before_state
    module.stops[initial_id] = asyncio.Event()
    await module.execute(initial_id, start)
    completed = assert_consistent(initial_id)
    assert completed["status"] == "completed"
    assert len(completed["versions"]) == 1
    assert_preserved(retained)


@pytest.mark.asyncio
async def test_mirror_failure_after_sql_commit_is_successful_and_restart_repairs_it(
    published_case, monkeypatch,
):
    case_id = published_case
    original = assert_consistent(case_id)
    mirror_before, before_archives = module.registry.path.read_bytes(), bundle_bytes()

    def fail_mirror(*args, **kwargs):
        raise OSError("Injected mirror failure after SQLite commit")

    async with client() as api:
        with monkeypatch.context() as fault:
            fault.setattr(module.store, "_write_registry_mirror", fail_mirror)
            response = await api.post(
                f"/api/investigations/{case_id}/versions", json=correction_body(original),
            )
        assert response.status_code == 200, response.text
        revised = assert_consistent(case_id)
        assert len(revised["versions"]) == 2
        assert module.registry.path.read_bytes() == mirror_before
        assert module.store.registry_mirror_pending is True
        committed_row, committed_archives = raw_case(case_id), bundle_bytes()
        restart(monkeypatch)
        assert raw_case(case_id) == committed_row
        assert json.loads(module.registry.path.read_text(encoding="utf-8")) == module.registry.export_state()
        assert module.store.registry_mirror_pending is False
        assert_consistent(case_id)
        original_again = await api.get(f"/api/investigations/{case_id}/bundle?revision=1")
        assert original_again.status_code == 200
        assert original_again.content == next(iter(before_archives.values()))
        current = await api.get(f"/api/investigations/{case_id}/bundle?revision=2")
        assert current.status_code == 200
        imported = await api.post("/api/bundles/import", files={
            "file": ("committed.zip", current.content, "application/zip"),
        })
        assert imported.status_code == 200
        assert imported.json()["manifest_hash"] == revised["manifest_hash"]
    assert_preserved(committed_archives)


@pytest.mark.asyncio
async def test_simultaneous_clients_commit_one_current_parent_correction(
    published_case, monkeypatch,
):
    case_id = published_case
    original = assert_consistent(case_id)
    before_archives = bundle_bytes()
    # Queue both clients before either enters the same local mutation lock.
    await module.mutation_lock.acquire()
    async with client() as first, client() as second:
        calls = [
            asyncio.create_task(api.post(
                f"/api/investigations/{case_id}/versions",
                json=correction_body(original, text),
            ))
            for api, text in ((first, "First simultaneously queued correction."),
                              (second, "Second simultaneously queued correction."))
        ]
        await asyncio.sleep(0)
        module.mutation_lock.release()
        responses = await asyncio.gather(*calls)
    assert sorted(response.status_code for response in responses) == [200, 409]
    winner = next(response.json() for response in responses if response.status_code == 200)
    document = assert_consistent(case_id)
    assert document["report"] == winner["report"]
    assert len(document["versions"]) == 2
    assert len(bundle_bytes()) == len(before_archives) + 1
    assert_preserved(before_archives)
    retained = bundle_bytes()
    restart(monkeypatch)
    assert_consistent(case_id)
    assert_preserved(retained)


def test_stale_put_cannot_replace_a_published_document(published_case):
    case_id = published_case
    before_row, before_state, before_archives = raw_case(case_id), module.registry.export_state(), bundle_bytes()
    stale = copy.deepcopy(module.store.get(case_id))
    stale.update(status="running", report=None, versions=[], registry=None, bundle_files={})
    with pytest.raises((ValueError, RuntimeError)):
        module.store.put(case_id, stale)
    assert raw_case(case_id) == before_row
    assert module.registry.export_state() == before_state
    assert_consistent(case_id)
    assert_preserved(before_archives)


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["versions", "reviews"])
async def test_commit_acknowledgement_error_retains_the_already_committed_publication(
    published_case, monkeypatch, operation,
):
    """A successful SQLite COMMIT followed by an error is not a rollback."""
    case_id = published_case
    original = assert_consistent(case_id)
    archives_before = bundle_bytes()
    commit = module.store._commit
    calls = []

    def committed_then_error(connection):
        calls.append(1)
        commit(connection)
        raise sqlite3.OperationalError("Injected lost commit acknowledgement")

    body = (correction_body(original) if operation == "versions" else
            {"reviewer": "local-reviewer", "comment": "Committed review despite lost acknowledgement."})
    async with client() as api:
        with monkeypatch.context() as fault:
            fault.setattr(module.store, "_commit", committed_then_error)
            response = await api.post(f"/api/investigations/{case_id}/{operation}", json=body)
        assert response.status_code == 200, response.text
        assert calls == [1]
        committed = assert_consistent(case_id)
        assert len(committed["versions"]) == (2 if operation == "versions" else 1)
        assert len(committed["reviews"]) == (1 if operation == "reviews" else 0)
        assert response.json() == module.public_doc(committed)
        assert_preserved(archives_before)
        committed_row, committed_archives = raw_case(case_id), bundle_bytes()
        restart(monkeypatch)
        assert raw_case(case_id) == committed_row
        assert_consistent(case_id)
        assert_preserved(committed_archives)


@pytest.mark.asyncio
@pytest.mark.parametrize("mirror_damage", ["missing", "invalid_json"])
async def test_authoritative_sqlite_recovers_an_unreadable_registry_mirror(
    published_case, monkeypatch, mirror_damage,
):
    case_id = published_case
    original = assert_consistent(case_id)
    async with client() as api:
        response = await api.post(
            f"/api/investigations/{case_id}/versions", json=correction_body(original),
        )
    assert response.status_code == 200, response.text
    committed_row, committed_archives = raw_case(case_id), bundle_bytes()
    mirror_path = module.registry.path
    if mirror_damage == "missing":
        mirror_path.unlink()
    else:
        mirror_path.write_bytes(b"{interrupted mirror write")
    fresh_store = CaseStore(module.store.path)
    fresh_registry = fresh_store.initialize_registry(mirror_path)
    monkeypatch.setattr(module, "store", fresh_store)
    monkeypatch.setattr(module, "registry", fresh_registry)
    assert raw_case(case_id) == committed_row
    assert json.loads(mirror_path.read_text(encoding="utf-8")) == fresh_registry.export_state()
    assert fresh_store.registry_mirror_pending is False
    assert_consistent(case_id)
    assert_preserved(committed_archives)


@pytest.mark.asyncio
@pytest.mark.parametrize("stage", ["before_archive", "after_archive", "after_candidate_append"])
async def test_correction_builder_fault_never_publishes_in_memory_candidate_state(
    published_case, monkeypatch, stage,
):
    case_id = published_case
    original = assert_consistent(case_id)
    before_row, before_state = raw_case(case_id), module.registry.export_state()
    before_mirror, original_archives = module.registry.path.read_bytes(), bundle_bytes()
    original_export, original_append = module.export_bundle, LocalRegistry.append_version

    def fail_export(*args, **kwargs):
        if stage == "after_archive":
            original_export(*args, **kwargs)
        raise OSError("Injected archive publication interruption")

    def fail_append(candidate, *args, **kwargs):
        original_append(candidate, *args, **kwargs)
        raise RegistryError("injected_candidate_append_failure")

    async with client() as api:
        with monkeypatch.context() as fault:
            if stage == "after_candidate_append":
                fault.setattr(LocalRegistry, "append_version", fail_append)
            else:
                fault.setattr(module, "export_bundle", fail_export)
            response = await api.post(
                f"/api/investigations/{case_id}/versions", json=correction_body(original),
            )
        assert response.status_code == (409 if stage == "after_candidate_append" else 500)
        assert raw_case(case_id) == before_row
        assert module.registry.export_state() == before_state
        assert module.registry.path.read_bytes() == before_mirror
        assert_preserved(original_archives)
        orphan_archives = bundle_bytes()
        assert len(orphan_archives) == len(original_archives) + (stage != "before_archive")
        restart(monkeypatch)
        response = await api.post(
            f"/api/investigations/{case_id}/versions", json=correction_body(original),
        )
        assert response.status_code == 200, response.text
        assert_consistent(case_id)
        assert_preserved(orphan_archives)


@pytest.mark.asyncio
async def test_initial_commit_acknowledgement_error_cannot_overwrite_completed_result(
    published_case, monkeypatch,
):
    case_id = "initial-commit-acknowledgement"
    start = module.StartRequest(**{
        key: module.PRESET[key]
        for key in ("address", "token_address", "from_block", "to_block")
    })
    module.store.put(case_id, {
        "id": case_id, "title": "Initial commit acknowledgement", "status": "running",
        "input": start.model_dump(), "evidence": None, "agent": None,
        "report": None, "registry": None, "versions": [], "reviews": [],
    })
    commit = module.store._commit
    calls = []

    def committed_then_error(connection):
        calls.append(1)
        commit(connection)
        raise sqlite3.OperationalError("Injected initial lost commit acknowledgement")

    module.stops[case_id] = asyncio.Event()
    with monkeypatch.context() as fault:
        fault.setattr(module.store, "_commit", committed_then_error)
        await module.execute(case_id, start)
    assert calls == [1]
    document = assert_consistent(case_id)
    assert document["status"] == "completed"
    assert len(document["versions"]) == 1
    assert "error" not in document
    row, archives = raw_case(case_id), bundle_bytes()
    restart(monkeypatch)
    assert raw_case(case_id) == row
    assert_consistent(case_id)
    assert_preserved(archives)


def test_separate_sqlite_connections_serialize_same_parent_corrections(published_case, monkeypatch):
    """The SQLite transaction also protects writers outside the asyncio lock."""
    case_id = published_case
    original = assert_consistent(case_id)
    archives_before = bundle_bytes()
    second_store = CaseStore(module.store.path)
    second_registry = second_store.initialize_registry(module.registry.path)
    writers = [(module.store, module.registry), (second_store, second_registry)]
    gate = Barrier(2)

    def publish(index):
        store, registry = writers[index]
        body = module.CorrectionRequest(**correction_body(original, f"Concurrent SQLite writer {index}."))
        gate.wait(timeout=5)
        try:
            return store.mutate_publication(
                registry, case_id,
                lambda document, candidate: module.build_correction(document, candidate, case_id, body),
            )
        except HTTPException as exc:
            return exc

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(publish, (0, 1)))
    winners = [result for result in results if isinstance(result, dict)]
    failures = [result for result in results if isinstance(result, HTTPException)]
    assert len(winners) == len(failures) == 1
    assert failures[0].status_code == 409
    restart(monkeypatch)
    document = assert_consistent(case_id)
    assert document == winners[0]
    assert len(document["versions"]) == 2
    assert len(bundle_bytes()) == len(archives_before) + 1
    assert_preserved(archives_before)


@pytest.mark.asyncio
async def test_stop_during_post_publication_rpc_cleanup_preserves_completed_document(
    published_case, monkeypatch,
):
    """Task membership during close does not make a published case stoppable."""
    close_entered, release_close = asyncio.Event(), asyncio.Event()
    original_close = module.CachedRpc.close

    async def blocked_close(rpc):
        close_entered.set()
        await release_close.wait()
        await original_close(rpc)

    monkeypatch.setattr(module.CachedRpc, "close", blocked_close)
    fields = {
        key: module.PRESET[key]
        for key in ("address", "token_address", "from_block", "to_block")
    }
    task = None
    async with client() as api:
        started = await api.post("/api/investigations", json=fields)
        assert started.status_code == 202, started.text
        case_id = started.json()["id"]
        task = module.tasks[case_id]
        try:
            await asyncio.wait_for(close_entered.wait(), timeout=10)
            completed = assert_consistent(case_id)
            assert completed["status"] == "completed"
            assert completed["versions"]
            assert module.tasks[case_id] is task
            assert case_id in module.stops
            assert task.done() is False
            row_before, state_before, archives_before = (
                raw_case(case_id), module.registry.export_state(), bundle_bytes(),
            )
            stopped = await api.post(f"/api/investigations/{case_id}/stop")
            assert stopped.status_code == 200, stopped.text
            assert stopped.json() == module.public_doc(completed)
            assert raw_case(case_id) == row_before
            assert module.registry.export_state() == state_before
            assert_preserved(archives_before)
            retained = await api.get(f"/api/investigations/{case_id}/bundle?revision=1")
            assert retained.status_code == 200
            path = completed["bundle_files"][str(completed["versions"][0]["version_id"])]
            assert retained.content == archives_before[path]
            assert task.cancelled() is False
        finally:
            release_close.set()
            await asyncio.wait_for(asyncio.gather(task, return_exceptions=True), timeout=3)
    assert case_id not in module.tasks
    assert case_id not in module.stops
    assert raw_case(case_id) == row_before
    assert module.registry.export_state() == state_before
    assert_consistent(case_id)
    assert_preserved(archives_before)
