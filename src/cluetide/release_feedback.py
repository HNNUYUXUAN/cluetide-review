"""Reviewer-authenticated correction feedback bound to one confirmed decision."""
from __future__ import annotations

import re
from eth_account import Account
from eth_account.messages import encode_typed_data
from pydantic import Field, field_validator

from .bundles import canonical_json_bytes
from .release_exchange import bounded_text
from .releases import StrictModel, ReleaseError, digest, identifier, now

MAX_FEEDBACK_REQUEST_BYTES = 256 * 1024


class FeedbackRequest(StrictModel):
    version_id: int = Field(gt=0, le=50)
    manifest_hash: str
    correction_transaction_hash: str
    reason: str = Field(min_length=1, max_length=4000)
    acceptance_conditions: list[str] = Field(default_factory=list, max_length=12)
    content_hash: str
    signature: str

    @field_validator("manifest_hash", "correction_transaction_hash", "content_hash")
    @classmethod
    def commitment(cls, value):
        return identifier(value)

    @field_validator("reason")
    @classmethod
    def reason_text(cls, value):
        return bounded_text(value, 4000, nonblank=True)

    @field_validator("acceptance_conditions")
    @classmethod
    def conditions(cls, value):
        for item in value:
            bounded_text(item, 1000, nonblank=True)
        return value

    @field_validator("signature")
    @classmethod
    def signed_message(cls, value):
        if not re.fullmatch(r"0x[0-9a-fA-F]{130}", value):
            raise ValueError("Invalid review signature")
        raw = bytes.fromhex(value[2:])
        if raw[64] not in (27, 28) or int.from_bytes(raw[32:64], "big") > 0x7fffffffffffffffffffffffffffffff5d576e7357a4501ddfe92f46681b20a0:
            raise ValueError("Invalid review signature")
        return value.lower()


def feedback_typed_data(task_id, payload, contract_address):
    return {"types": {
        "EIP712Domain": [{"name": "name", "type": "string"}, {"name": "version", "type": "string"},
            {"name": "chainId", "type": "uint256"}, {"name": "verifyingContract", "type": "address"}],
        "ReviewFeedback": [{"name": "taskId", "type": "bytes32"}, {"name": "versionId", "type": "uint256"},
            {"name": "manifestHash", "type": "bytes32"}, {"name": "correctionTransactionHash", "type": "bytes32"},
            {"name": "contentHash", "type": "bytes32"}]}, "primaryType": "ReviewFeedback",
        "domain": {"name": "ClueTideReviewFeedback", "version": "1", "chainId": 677, "verifyingContract": contract_address},
        "message": {"taskId": task_id, "versionId": payload["version_id"], "manifestHash": payload["manifest_hash"],
            "correctionTransactionHash": payload["correction_transaction_hash"], "contentHash": payload["content_hash"]}}


def authenticate(task, payload, contract_address):
    content = {key: payload[key] for key in ("reason", "acceptance_conditions")}
    if digest(canonical_json_bytes(content)) != payload["content_hash"]:
        raise ReleaseError("The correction feedback differs from its signed content.", 422)
    try:
        message = encode_typed_data(full_message=feedback_typed_data(task["task_id"], payload, contract_address))
        signer = Account.recover_message(message, signature=payload["signature"]).lower()
    except (ValueError, TypeError):
        raise ReleaseError("The correction feedback signature could not be verified.", 422) from None
    if signer != task["reviewer"]:
        raise ReleaseError("The correction feedback must be signed by the designated reviewer.", 403)


def submit_feedback(service, task_id, payload):
    task_id = identifier(task_id)
    request = payload if isinstance(payload, FeedbackRequest) else FeedbackRequest.model_validate(payload)
    data = request.model_dump()
    with service._locked():
        task = service._task(task_id)
        version = service._version(task, request.version_id)
        if version["manifest_hash"] != request.manifest_hash:
            raise ReleaseError("The correction feedback belongs to a different version manifest.", 422)
        gateway = service._adapter()
        config = gateway.config()
        if config.get("chain_id") != 677 or not config.get("contract_address"):
            raise ReleaseError("The BOT Mainnet review contract is unavailable.", 503)
        authenticate(task, data, config["contract_address"])
        verification = gateway.verify("request_correction", task=task, version=version, account=task["reviewer"],
                                      transaction_hash=request.correction_transaction_hash)
        if verification.get("status") != "verified" or int(verification.get("confirmations", 0)) < 3:
            raise ReleaseError("The correction transaction requires three canonical confirmations.")
        state = verification.get("getter_state", {})
        chain_task, chain_version = state.get("task", {}), state.get("version", {})
        if (any(chain_task.get(key) != task[key] for key in ("owner", "proposer", "reviewer", "executor", "receipt_signer"))
                or chain_task.get("target_hash") != task["target_id"]
                or chain_version.get("version_id") != str(request.version_id)
                or chain_version.get("manifest_hash") != request.manifest_hash
                or chain_version.get("artifact_hash") != version["artifact_hash"]
                or chain_version.get("deadline") != str(version["expires_at"])
                or chain_version.get("decision") != "3"):
            raise ReleaseError("The correction transaction differs from the exact task and version.")
        path = service._safe(service.root / "feedback" / task_id / (request.correction_transaction_hash + ".json"))
        if path.exists():
            stored = service._json(path)
            if any(stored.get(key) != value for key, value in data.items() if key != "signature"):
                raise ReleaseError("This correction transaction already has immutable feedback.")
            return stored
        observed = now()
        record = {"schema": "cluetide.review-feedback.v1", "task_id": task_id, **data,
            "reviewer": task["reviewer"], "chain_id": 677, "contract_address": config["contract_address"],
            "received_at": observed, "verification": {"status": "verified", "confirmations": verification["confirmations"],
                "block_number": verification["block_number"], "block_hash": verification["block_hash"], "observed_at": observed}}
        service._save(path, record, immutable=True)
        return record


def get_feedback(service, task_id, version_id=None):
    task_id = identifier(task_id)
    with service._locked():
        task = service._task(task_id)
        if version_id is not None:
            service._version(task, version_id)
        records = []
        for path in sorted((service.root / "feedback" / task_id).glob("*.json")):
            record = service._json(path)
            if version_id is not None and record.get("version_id") != version_id:
                continue
            data = FeedbackRequest.model_validate({key: record[key] for key in FeedbackRequest.model_fields}).model_dump()
            authenticate(task, data, record["contract_address"])
            if (record.get("schema") != "cluetide.review-feedback.v1" or record.get("task_id") != task_id
                    or record.get("reviewer") != task["reviewer"] or record.get("chain_id") != 677
                    or path.stem != data["correction_transaction_hash"]
                    or service._version(task, data["version_id"])["manifest_hash"] != data["manifest_hash"]):
                raise ReleaseError("The saved correction feedback failed verification.")
            records.append(record)
        return {"task_id": task_id, "feedback": records}
