"""Public Ethereum case presets and their bounded captured evidence."""
from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any, Mapping


CASE_ROOT = Path(__file__).resolve().parents[2] / "data" / "cases"
_MAX_JSON_BYTES = 8 * 1024 * 1024
_CATALOG = (
    {
        "case_id": "uniswap93", "title": "Uniswap 提案 93",
        "address": "0x1a9c8182c09f50c8318d769245bea52c32be35bc",
        "token_address": "0x1f9840a85d5af5bf1d1762f925bdaddc4201f984",
        "from_block": 24106368, "to_block": 24106388,
        "tx_hash": "0x091f0083242a777d55821c1189e568d6d033d9da501b75087dc736fa143d2c1e",
        "alert_threshold_raw": "10000000000000000000000000",
        "token_symbol": "UNI", "source_category": "governance",
    },
    {
        "case_id": "euler-20230313", "title": "Euler 2023-03-13 DAI 事件",
        "address": "0x27182842e098f60e3d576794a5bffb0777e025d3",
        "token_address": "0x6b175474e89094c44da98b954eedeac495271d0f",
        "from_block": 16817995, "to_block": 16817997,
        "tx_hash": "0xc310a0affe2169d1f6feec1c63dbc7f7c62a887fa48795d327d4d2da2d6b111d",
        "alert_threshold_raw": "1000000000000000000000000",
        "token_symbol": "DAI", "source_category": "incident_postmortem",
    },
)


def list_cases() -> list[dict[str, Any]]:
    """Return independent copies of fixed public presets, without runtime state."""
    return copy.deepcopy(list(_CATALOG))


def _preset(case_id: str) -> dict[str, Any]:
    for preset in _CATALOG:
        if preset["case_id"] == case_id:
            return copy.deepcopy(preset)
    raise ValueError("Unknown public case identifier")


def case_directory(case_id: str) -> Path:
    """Resolve only a catalogued directory, never an arbitrary supplied path."""
    preset = _preset(case_id)
    directory = CASE_ROOT / preset["case_id"]
    if directory.is_symlink() or directory.is_junction():
        raise ValueError("Public case directories must not be links")
    return directory


def _read_json(path: Path) -> Any:
    if path.is_symlink() or not path.is_file() or path.stat().st_size > _MAX_JSON_BYTES:
        raise ValueError("A bounded public case JSON file is required")
    return json.loads(path.read_text(encoding="utf-8"))


def load_catalog_case(case_id: str) -> tuple[dict, dict, list[dict]]:
    """Load a preset, its exact captured RPC snapshot and attributed sources.

    Cache identity is checked before callers can use it. Captured observations
    remain provider-reported data; this check does not establish factual truth.
    """
    preset = _preset(case_id)
    directory = case_directory(case_id)
    snapshot = _read_json(directory / "rpc.json")
    if not isinstance(snapshot, dict) or not isinstance(snapshot.get("request"), dict):
        raise ValueError("Unsupported public case snapshot")
    request = snapshot["request"]
    expected = {
        "chain_id": 1, "subject_address": preset["address"],
        "token_address": preset["token_address"], "start_block": preset["from_block"],
        "end_block": preset["to_block"], "transaction_hash": preset["tx_hash"],
    }
    if any(request.get(key) != value or isinstance(request.get(key), bool) for key, value in expected.items()):
        raise ValueError("Public case snapshot conflicts with its catalog identity")
    receipt = snapshot.get("receipt")
    if not isinstance(receipt, dict) or receipt.get("transactionHash", "").lower() != preset["tx_hash"]:
        raise ValueError("Public case receipt conflicts with its catalog transaction")
    sources = _read_json(directory / "sources.json")
    if not isinstance(sources, list) or any(not isinstance(source, dict) for source in sources):
        raise ValueError("Public case sources must be an array of attributed records")
    for source in sources:
        source.setdefault("category", preset["source_category"])
    return preset, snapshot, sources


def match_catalog_case(request: Any) -> dict[str, Any] | None:
    """Match the subject, token and exact cached window; threshold may vary."""
    def value(name: str, default=None):
        return request.get(name, default) if isinstance(request, Mapping) else getattr(request, name, default)
    if type(value("chain_id", 1)) is not int or value("chain_id", 1) != 1:
        return None
    if type(value("from_block")) is not int or type(value("to_block")) is not int:
        return None
    for preset in _CATALOG:
        if (value("address") == preset["address"] and value("token_address") == preset["token_address"]
                and value("from_block") == preset["from_block"] and value("to_block") == preset["to_block"]):
            return copy.deepcopy(preset)
    return None
