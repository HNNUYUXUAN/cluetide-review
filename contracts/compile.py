"""Compile the registry with a locally installed, pinned Windows solc binary.

This script never downloads tools, connects to a chain, or handles credentials.
"""

import hashlib
import json
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "contracts" / "ClueTideRegistry.sol"
COMPILER = ROOT / ".tools" / "solidity" / "solc-0.8.30.exe"
EXPECTED_COMPILER_SHA256 = "ccbd3ed44d5fbd26fe039702d403421f1212d2e8752e3cbe3bfd074986911586"
COMPILER_VERSION = "0.8.30+commit.73712a01"


def compile_registry():
    if not COMPILER.exists():
        raise SystemExit("Missing pinned local compiler. See docs/contracts.md.")
    if hashlib.sha256(COMPILER.read_bytes()).hexdigest() != EXPECTED_COMPILER_SHA256:
        raise SystemExit("Compiler checksum mismatch.")
    source_bytes = SOURCE.read_bytes()
    request = {"language": "Solidity", "sources": {SOURCE.name: {"content": source_bytes.decode("utf-8")}},
               "settings": {"optimizer": {"enabled": True, "runs": 200}, "evmVersion": "paris",
                            "outputSelection": {"*": {"*": ["abi", "evm.bytecode.object", "evm.deployedBytecode.object"]}}}}
    result = subprocess.run([str(COMPILER), "--standard-json"], input=json.dumps(request), text=True,
                            capture_output=True, check=True, timeout=60)
    output = json.loads(result.stdout)
    errors = [item for item in output.get("errors", []) if item.get("severity") == "error"]
    if errors:
        raise SystemExit("\n".join(item["formattedMessage"] for item in errors))
    contract = output["contracts"][SOURCE.name]["ClueTideRegistry"]
    artifact = {"contractName": "ClueTideRegistry", "compiler": COMPILER_VERSION,
                "compilerSha256": EXPECTED_COMPILER_SHA256, "evmVersion": "paris",
                "optimizerRuns": 200, "sourceSha256": hashlib.sha256(source_bytes).hexdigest(),
                "abi": contract["abi"], "bytecode": "0x" + contract["evm"]["bytecode"]["object"],
                "deployedBytecode": "0x" + contract["evm"]["deployedBytecode"]["object"]}
    target = ROOT / "contracts" / "artifacts" / "ClueTideRegistry.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(artifact, indent=2) + "\n", encoding="utf-8")
    print(f"Compiled {artifact['contractName']} with {COMPILER_VERSION}, target paris; source sha256 {artifact['sourceSha256']}")
    return artifact


if __name__ == "__main__":
    compile_registry()
