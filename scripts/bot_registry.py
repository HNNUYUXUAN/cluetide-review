"""Prepare BOT wallet transactions, read receipts, and rehearse in local Py-EVM."""
from __future__ import annotations

import argparse
from datetime import UTC, datetime
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


def emit(result: dict, output: str | None) -> None:
    payload = (json.dumps(result, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    if output is None:
        print(payload.decode("utf-8"), end="")
        return
    destination = (ROOT / output).resolve()
    permitted = [ROOT / "local-data", ROOT / "local-only"]
    if not any(destination.is_relative_to(path.resolve()) for path in permitted):
        raise ValueError("Output must be inside project local-data or local-only")
    original = ROOT / output
    if any(path.is_symlink() or path.is_junction() for path in (original, *original.parents)):
        raise ValueError("Output cannot traverse links or junctions")
    if destination.exists():
        raise ValueError("Choose a new output filename to retain the previous record")
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=destination.parent, prefix=".bot-", delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, destination)
        temporary = None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    print(json.dumps({"status": "saved", "path": destination.relative_to(ROOT).as_posix(),
                      "sha256": hashlib.sha256(payload).hexdigest()}))


def artifact() -> dict:
    value = json.loads((ROOT / "contracts/artifacts/ClueTideRegistry.json").read_text("utf-8"))
    if value["sourceSha256"] != hashlib.sha256((ROOT / "contracts/ClueTideRegistry.sol").read_bytes()).hexdigest():
        raise ValueError("Contract source and artifact must match")
    if value["evmVersion"] != "paris":
        raise ValueError("The reviewed contract targets Paris")
    for field in ("bytecode", "deployedBytecode"):
        if not bytes.fromhex(value[field].removeprefix("0x")):
            raise ValueError("Compiled bytecode is required")
    return value


def rehearse() -> dict:
    """Execute the compiled contract only with an in-process provider."""
    from eth_tester import EthereumTester, PyEVMBackend
    from eth_tester.exceptions import TransactionFailed
    from web3 import EthereumTesterProvider, Web3
    from cluetide.bundles import canonical_json_bytes, import_bundle, manifest_sha256
    from cluetide.registry import case_id_bytes

    compiled = artifact()
    provider = EthereumTesterProvider(EthereumTester(backend=PyEVMBackend()))
    if type(provider) is not EthereumTesterProvider:
        raise ValueError("Rehearsal requires the in-process provider")
    web3 = Web3(provider)
    author, reviewer = web3.eth.accounts[:2]
    factory = web3.eth.contract(abi=compiled["abi"], bytecode=compiled["bytecode"])
    receipts = []

    def execute(function, sender, action):
        receipt = web3.eth.wait_for_transaction_receipt(function.transact({"from": sender}))
        if receipt.status != 1:
            raise ValueError("Local EVM execution failed")
        receipts.append({"action": action, "transaction_hash": web3.to_hex(receipt.transactionHash),
                         "block_number": receipt.blockNumber, "gas_used": receipt.gasUsed})
        return receipt

    deployment = execute(factory.constructor(), author, "deploy")
    contract = web3.eth.contract(address=deployment.contractAddress, abi=compiled["abi"])
    runtime = bytes(web3.eth.get_code(contract.address))
    if runtime != bytes.fromhex(compiled["deployedBytecode"].removeprefix("0x")):
        raise ValueError("Local runtime differs from the reviewed artifact")
    source_names = ["gcc-uniswap93-adaptive-v1.zip", "gcc-uniswap93-adaptive-v2.zip"]
    bundles = [import_bundle(ROOT / "data/demo/bundles" / name) for name in source_names]
    first, corrected = bundles
    first_digest, second_digest = manifest_sha256(first.manifest), manifest_sha256(corrected.manifest)
    case_id = first.report["case_id"]
    if corrected.report["case_id"] != case_id or corrected.report.get("parent_manifest_hash") != first_digest:
        raise ValueError("Public rehearsal versions must retain their exact parent commitment")
    case_hex = case_id_bytes(case_id)
    first_hash = bytes.fromhex(first_digest)
    second_hash = bytes.fromhex(second_digest)
    schema = "cluetide.evidence.v1"
    execute(contract.functions.createCase(case_hex, first_hash, schema, ""), author, "create_case")
    review_payload = {"case_id": case_id, "version_id": 1, "content_hash": first_digest,
                      "comment": "本地演练核对v1证据范围并保留更正父版本", "reviewer": "local-evm-reviewer"}
    review_hash = hashlib.sha256(canonical_json_bytes(review_payload)).digest()
    execute(contract.functions.addReview(case_hex, 1, first_hash, 2, review_hash, ""), reviewer, "add_review")
    execute(contract.functions.appendVersion(case_hex, 1, second_hash, schema, ""), author, "append_version")
    case = contract.functions.getCase(case_hex).call()
    v1 = contract.functions.getVersion(1).call()
    v2 = contract.functions.getVersion(2).call()
    review = contract.functions.getReview(1).call()
    if case[:4] != (author, 2, 2, 1) or v1[4] != first_hash or v2[2] != 1 or v2[4] != second_hash:
        raise ValueError("Local version getters differ from the prepared commitments")
    if review[2] != 1 or review[3] != first_hash or review[6] != review_hash:
        raise ValueError("Local review no longer binds the exact v1")
    rejected = []
    for name, function, sender in [
        ("author_permission", contract.functions.appendVersion(case_hex, 2, first_hash, schema, ""), reviewer),
        ("current_parent", contract.functions.appendVersion(case_hex, 1, first_hash, schema, ""), author),
        ("exact_review_hash", contract.functions.addReview(case_hex, 1, second_hash, 2, review_hash, ""), reviewer),
    ]:
        try:
            function.transact({"from": sender})
        except TransactionFailed:
            rejected.append(name)
        else:
            raise ValueError("Local contract accepted an invalid operation")
    return {"schema_version": "cluetide-bot-local-rehearsal/v1", "recorded_at_utc": datetime.now(UTC).isoformat(),
            "status": "PASS", "execution_scope": "in_process_py_evm", "provider": "EthereumTesterProvider",
            "real_network_deployments": 0, "real_signed_or_broadcast_transactions": 0,
            "local_contract_address": contract.address, "local_receipts": receipts,
            "source_sha256": compiled["sourceSha256"], "runtime_sha256": hashlib.sha256(runtime).hexdigest(),
            "case_id": case_id, "case_id_hex": "0x" + case_hex.hex(),
            "versions": [{"local_evm_version_id": 1, "manifest_hash": first_digest},
                         {"local_evm_version_id": 2, "manifest_hash": second_digest, "parent_id": 1}],
            "review": {"local_evm_review_id": 1, "version_id": 1, "review_hash": review_hash.hex(),
                       "payload": review_payload}, "rejected_operations": rejected,
            "public_source_bundles": [{"path": "data/demo/bundles/" + name,
                                       "zip_sha256": hashlib.sha256((ROOT / "data/demo/bundles" / name).read_bytes()).hexdigest()}
                                      for name in source_names],
            "statement": "Local contract rehearsal; user-controlled BOT testnet and mainnet transactions require separate receipts and getters. Hashes commit to bytes; these local roles do not establish independent review."}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("networks")
    local = commands.add_parser("rehearse")
    local.add_argument("--output")
    for name in ("prepare-deploy", "verify-deploy", "read"):
        command = commands.add_parser(name)
        command.add_argument("--chain-id", type=int, choices=(968, 677), required=True)
        command.add_argument("--output")
        if name in ("prepare-deploy", "verify-deploy"):
            command.add_argument("--account", required=True, help="Public user wallet address")
        if name == "verify-deploy":
            command.add_argument("--tx-hash", required=True)
            command.add_argument("--minimum-confirmations", type=int, default=3)
        if name == "read":
            command.add_argument("--contract", required=True)
            command.add_argument("--case-id", required=True)
            command.add_argument("--version-id", type=int)
            command.add_argument("--review-id", type=int)
    args = parser.parse_args()
    try:
        if args.command == "rehearse":
            result = rehearse()
        else:
            from cluetide.bot import BotService

            def no_local_case(_case_id):
                raise ValueError("Use the workbench for saved-case wallet preparation")

            service = BotService(required=no_local_case)
            if args.command == "networks":
                result = service.networks()
            elif args.command == "prepare-deploy":
                result = service.prepare({"chain_id": args.chain_id, "action": "deploy", "account": args.account})
            elif args.command == "verify-deploy":
                result = service.verify({"chain_id": args.chain_id, "transaction_hash": args.tx_hash,
                                         "minimum_confirmations": args.minimum_confirmations,
                                         "prepared": {"chain_id": args.chain_id, "action": "deploy", "account": args.account}})
            else:
                result = service.read({"chain_id": args.chain_id, "contract_address": args.contract,
                                       "case_id": args.case_id,
                                       **({"version_id": str(args.version_id)} if args.version_id is not None else {}),
                                       **({"review_id": str(args.review_id)} if args.review_id is not None else {})})
        emit(result, getattr(args, "output", None))
        if args.command == "verify-deploy" and result.get("status") != "verified":
            return 2 if result.get("status") == "pending" else 3
        return 0
    except Exception as exc:
        # Endpoint bodies and local configuration are outside command output.
        from cluetide.bot import BotError
        print(json.dumps({"status": "failed", "code": exc.code if isinstance(exc, BotError) else "invalid_command_or_local_input",
                          "detail": "BOT command did not complete; check validated parameters, reviewed artifact, and read-only network availability."}), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
