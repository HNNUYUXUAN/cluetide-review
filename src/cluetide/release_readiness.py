"""Deployment identity and recovery observations without exposing credentials."""
import os

import httpx

from .releases import ReleaseError, identifier, attempt_identifier, now

CONTRACT = "0xca71b77708674f5f2407c0ad4e599eaf5eb07281"
SIGNER = "0xc0a54fc7daa998a2e8bacc015eeafd4591ac15a4"


def readiness(service, *, check_chain=True):
    result = {"schema": "cluetide.readiness.v1", "checked_at": now(),
              "deployment_version": os.environ.get("VERCEL_GIT_COMMIT_SHA", "local"),
              "storage": "unavailable", "signer": "unavailable", "contract": "unavailable",
              "chain_id": 677, "contract_address": CONTRACT, "receipt_signer": SIGNER,
              "writes_enabled": False}
    enabled = False
    try:
        with service._locked():
            if hasattr(service, "store"):
                service.store.connection().execute("SELECT 1").fetchone()
                enabled = service.writes_enabled and service.store.writer_enabled()
                result["storage"] = "postgresql"
            else:
                enabled = os.environ.get("CLUETIDE_RELEASE_WRITES_ENABLED", "1") == "1"
                result["storage"] = "local-files"
            result["signer"] = "matched" if service._signer().address.lower() == SIGNER else "mismatch"
            configured = service._adapter().config()
        if configured.get("chain_id") == 677 and configured.get("contract_address") == CONTRACT:
            result["contract"] = "configured"
            if check_chain:
                from .release_chain import ReleaseChainService
                artifact = ReleaseChainService().artifact()
                with httpx.Client(timeout=4, trust_env=False, follow_redirects=False) as client:
                    response = client.post("https://rpc.botchain.ai", json=[
                        {"jsonrpc": "2.0", "id": 1, "method": "eth_chainId", "params": []},
                        {"jsonrpc": "2.0", "id": 2, "method": "eth_getCode", "params": [CONTRACT, "latest"]}])
                    response.raise_for_status()
                    values = {item["id"]: item.get("result") for item in response.json()}
                    result["contract"] = "matched" if values == {1: "0x2a5", 2: artifact.runtime} else "mismatch"
        result["writes_enabled"] = enabled and result["signer"] == "matched" and result["contract"] == "matched"
    except (ReleaseError, OSError, ValueError, KeyError, TypeError, httpx.HTTPError):
        pass
    return result


def attempt_status(service, task_id, attempt_id):
    if hasattr(service, "attempt_status"):
        return service.attempt_status(task_id, attempt_id)
    task_id, attempt_id = identifier(task_id), attempt_identifier(attempt_id)
    with service._locked():
        service._task(task_id)
        directory = service._attempt_path(task_id, attempt_id)
        path = directory / "state.json"
        state = service._json(path) if path.exists() else None
        return {"task_id": task_id, "attempt_id": attempt_id, "checked_at": now(),
                "status": state["status"] if state else "not_started", "stage": state["stage"] if state else "awaiting_execution",
                "lease_expires_at": None, "lease_active": False, "receipt_available": (directory / "receipt.json").exists(),
                "can_resume": state is None}
