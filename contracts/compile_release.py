"""Compile the release witness with an existing, checksum-pinned solc binary."""

import argparse
import hashlib
import json
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]
COMPILER_SHA256 = "ccbd3ed44d5fbd26fe039702d403421f1212d2e8752e3cbe3bfd074986911586"
COMPILER_VERSION = "0.8.30+commit.73712a01"


def compile_release(compiler: Path, *, check=False):
    if not compiler.is_file() or hashlib.sha256(compiler.read_bytes()).hexdigest() != COMPILER_SHA256:
        raise SystemExit("Missing compiler or compiler checksum mismatch.")
    source = ROOT / "contracts/ClueTideReleaseWitness.sol"
    content = source.read_bytes()
    request = {"language": "Solidity", "sources": {source.name: {"content": content.decode("utf-8")}},
        "settings": {"optimizer": {"enabled": True, "runs": 200}, "evmVersion": "paris",
            "outputSelection": {"*": {"*": ["abi", "evm.bytecode.object", "evm.deployedBytecode.object"]}}}}
    process = subprocess.run([str(compiler.resolve()), "--standard-json"], input=json.dumps(request),
        text=True, capture_output=True, check=True, timeout=60)
    output = json.loads(process.stdout)
    errors = [entry["formattedMessage"] for entry in output.get("errors", []) if entry["severity"] == "error"]
    if errors:
        raise SystemExit("\n".join(errors))
    compiled = output["contracts"][source.name]["ClueTideReleaseWitness"]
    artifact = {"contractName": "ClueTideReleaseWitness", "compiler": COMPILER_VERSION,
        "compilerSha256": COMPILER_SHA256, "evmVersion": "paris", "optimizerRuns": 200,
        "sourceSha256": hashlib.sha256(content).hexdigest(), "abi": compiled["abi"],
        "bytecode": "0x" + compiled["evm"]["bytecode"]["object"],
        "deployedBytecode": "0x" + compiled["evm"]["deployedBytecode"]["object"]}
    target = ROOT / "contracts/artifacts/ClueTideReleaseWitness.json"
    serialized = (json.dumps(artifact, indent=2) + "\n").encode("utf-8")
    if check:
        if not target.is_file() or target.read_bytes().replace(b"\r\n", b"\n") != serialized:
            raise SystemExit("Committed artifact differs from the pinned compiler output.")
    else:
        target.write_bytes(serialized)
    print(f"{'Verified' if check else 'Compiled'} {artifact['contractName']} with {COMPILER_VERSION}; target paris; source SHA-256 {artifact['sourceSha256']}")
    return artifact


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--compiler", type=Path, default=ROOT / ".tools/solidity/solc-0.8.30.exe")
    parser.add_argument("--check", action="store_true", help="Verify the saved artifact without writing files.")
    args = parser.parse_args()
    compile_release(args.compiler, check=args.check)
