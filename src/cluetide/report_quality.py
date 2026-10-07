"""Exact ERC-20 display facts and limited, deterministic report quality checks.

These checks preserve the original report. A result with no detected wording
flags is not factual verification or an independent review.
"""
from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

from pydantic import BaseModel

from .schemas import normalize_address, uint256_decimal


def _document(value: Any) -> dict[str, Any]:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, Mapping):
        return dict(value)
    if value is None:
        return {}
    raise TypeError("Expected a Pydantic model or a mapping")


def _format_amount(raw: str, decimals: int) -> str:
    """Use string arithmetic, including for uint256 and 255-decimal tokens."""
    if decimals == 0:
        return raw
    padded = raw.zfill(decimals + 1)
    whole, fraction = padded[:-decimals], padded[-decimals:].rstrip("0")
    return whole + ("." + fraction if fraction else "")


def _decimals_references(document: dict[str, Any], token: str | None,
                         decimals: int | None, observation_ids: list[str]) -> list[str]:
    """Identify decimals reads instead of relabeling symbol/name observations."""
    if token is None or decimals is None:
        return []
    raw = _document(document.get("raw"))
    metadata = _document(document.get("metadata"))
    anchor = _document(metadata.get("block_anchor") or document.get("window_end_anchor"))
    anchor_hash = anchor.get("block_hash")
    references = []
    for item in raw.get("rpc_observations", []):
        observation = _document(item)
        params = observation.get("params")
        result = observation.get("result")
        if (observation.get("method") != "eth_call" or observation.get("error") is not None
                or not isinstance(params, list) or not params or not isinstance(params[0], Mapping)
                or not isinstance(result, str) or not re.fullmatch(r"0x[0-9a-fA-F]{64}", result)):
            continue
        call = params[0]
        target = call.get("to")
        if anchor_hash is not None and (len(params) < 2 or not isinstance(params[1], Mapping)
                or params[1].get("blockHash") != anchor_hash or params[1].get("requireCanonical") is not True):
            continue
        if (isinstance(target, str) and target.lower() == token
                and call.get("data") == "0x313ce567" and int(result, 16) == decimals
                and observation.get("observation_id") in observation_ids):
            references.append(observation["observation_id"])
    return list(dict.fromkeys(references))


def deterministic_transfer_facts(evidence: BaseModel | Mapping[str, Any]) -> list[dict[str, Any]]:
    """Project validated raw amounts using metadata for the same token only.

    Missing decimals produces raw_only facts. Malformed supplied amounts or
    decimals raise ValueError instead of silently approximating a quantity.
    No model output or human amount_display field participates in formatting.
    """
    document = _document(evidence)
    metadata = _document(document.get("metadata"))
    request = _document(document.get("request"))
    metadata_token = metadata.get("token_address") or request.get("token_address")
    if metadata_token is not None:
        metadata_token = normalize_address(metadata_token)
    decimals = metadata.get("decimals")
    if decimals is not None and (type(decimals) is not int or not 0 <= decimals <= 255):
        raise ValueError("Token decimals must be an integer from 0 through 255")
    symbol = metadata.get("symbol")
    if symbol is not None and not isinstance(symbol, str):
        raise ValueError("Token symbol must be text")
    observation_ids = metadata.get("observation_ids", [])
    if not isinstance(observation_ids, list) or any(not isinstance(item, str) for item in observation_ids):
        raise ValueError("Metadata observation IDs must be strings")
    decimals_references = _decimals_references(document, metadata_token, decimals, observation_ids)
    facts = []
    for item in document.get("transfers", []):
        transfer = _document(item)
        token = normalize_address(transfer["token_address"])
        raw = uint256_decimal(transfer["value_raw"])
        evidence_id = transfer.get("evidence_id")
        if not isinstance(evidence_id, str) or not evidence_id:
            raise ValueError("Transfer evidence ID is required")
        same_token = token == metadata_token
        known_decimals = decimals if same_token else None
        formatted = _format_amount(raw, known_decimals) if known_decimals is not None else None
        facts.append({
            "evidence_id": evidence_id,
            "token_address": token,
            "raw_amount": raw,
            "decimals": known_decimals,
            "formatted_amount": formatted,
            "symbol": symbol if same_token else None,
            "unit": "ERC-20 token units" if formatted is not None else "ERC-20 base units",
            "formatting_status": "formatted" if formatted is not None else "raw_only",
            "decimals_observation_ids": list(decimals_references) if known_decimals is not None else [],
        })
    return facts


_WEI_AMOUNT = re.compile(r"(?<![a-zA-Z0-9_.])\d[\d,]*(?:\.\d+)?(?:\s*e\s*[+-]?\d+)?\s*wei\b", re.I)
_TOKEN_CONTEXT = re.compile(r"ERC[ -]?20|token|代币|(?<![a-z0-9])(?:UNI|USDC|USDT|DAI|WETH)(?![a-z])", re.I)
_NATIVE_AMOUNT_PREFIX = re.compile(
    r"(?:(?:tx|transaction)[.\s]*value|native\s+(?:ETH|Ether)(?:\s+value)?|"
    r"(?:ETH|Ether)\s+(?:(?:transaction\s+)?value|gas(?:\s+(?:cost|fee|paid))?)|"
    r"原生(?:ETH|以太币|币)(?:交易)?(?:金额|value)?)"
    r"\)?\s*(?:[:=]|is|was|equals|equal to|为|是)?\s*$", re.I)
_ATTACK_EXCLUSION = re.compile(
    r"(?:而|并)?非\s*(?:一次|一个|一起)?\s*攻击|(?:并)?不是\s*(?:一次|一个|一起)?\s*攻击|"
    r"(?:不存在|未发生|没有发生|没有)\s*(?:任何)?攻击|"
    r"(?:排除|排除了)\s*(?:所有|任何|此次)?\s*攻击|"
    r"(?:确认|证明|确定)\s*(?:无|没有|不存在)\s*攻击|"
    r"攻击(?:已(?:经)?(?:被)?|被)(?:完全)?排除|"
    r"\b(?:not|rather than)\s+(?:an?\s+)?attack\b|"
    r"\b(?:rules? out|ruled out|exclude[ds]?)\s+(?:an?\s+|any\s+|all\s+)?attacks?\b|"
    r"\battacks?\s+(?:has|have|is|are|was|were)\s+(?:been\s+)?ruled out\b|"
    r"\bno\s+attack\s+(?:occurred|took place)\b", re.I)
_QUALIFIED_PREFIX = re.compile(
    r"(?:无法|不能|不可|不足以|尚未|不应|不得|不宜|未能|不代表|不意味着|并不意味着|"
    r"没有证据(?:足以|可以)?(?:证明|表明))"
    r"[^。；;!?！？\n]{0,22}$|"
    r"\b(?:cannot|can't|could not|does not|do not|did not|not sufficient to|insufficient to|"
    r"should not|must not|unable to|impossible to|never|no evidence(?:\s+to|\s+that)?)"
    r"\b[^.;!?\n]{0,50}$", re.I)
_SENTENCE_BREAK = re.compile(r"[。；;!?！？\n]|\.\s+")
_ADVERSATIVE = re.compile(r"\b(?:but|however|yet)\b|但是|然而|不过|但", re.I)
_CONTRACT_BURN_GENERALIZATION = re.compile(
    r"(?:合约|代币)[^。；;!?！？\n]{0,25}"
    r"(?P<zh_burn>(?:未实现|没有实现|不存在|没有)\s*(?:任何|原生|自动|内置)?\s*(?:销毁|burn))|"
    r"\b(?:contract|token)\b[^.;!?\n]{0,35}"
    r"(?P<en_burn>\b(?:has no|does not implement|lacks)\s+(?:(?:a|any|native|automatic|built-in)\s+)*burn)", re.I)
_SUCCESS_CONTEXT = re.compile(
    r"(?:交易|回执|执行|状态)[^。；;!?！？\n]{0,18}成功|"
    r"\b(?:transaction|receipt|execution)\b[^.;!?\n]{0,25}\b(?:success(?:ful)?|succeeded)\b|"
    r"\bstatus\s*[=:]\s*(?:0x1|1)\b", re.I)
_EXECUTION_OVERCLAIM = re.compile(
    r"(?:治理提案|提案|操作|合约)(?:\s*[0-9]{1,10})?\s*(?:已经被|已被|已经|被|已|得以)?\s*(?:正确执行|安全执行)|"
    r"(?:正常|常规|安全)\s*(?:的)?\s*合约(?:调用|交互|操作)|"
    r"执行的常规操作|"
    r"\b(?:correct|safe|normal|routine)\s+(?:contract\s+)?(?:execution|call|operation|interaction)\b|"
    r"\b(?:proposal|governance|contract)\s+(?:(?:was|is|has been|had been)\s+)?executed\s+(?:correctly|safely)\b", re.I)
_TRANSFER_CONTEXT = re.compile(r"转账|转移|\btransfer\b", re.I)
_OTHER_AMOUNT_CONTEXT = re.compile(
    r"total[_ ]?supply|供应|Approval|授权金额|余额|balance|tx\.value|gas|合计|累计|总计|"
    r"\b(?:aggregate|total|sum|allowance|approval)\b", re.I)
_RAW_INTEGER = r"(?<![A-Za-z0-9_.,])(?:[0-9]{1,3}(?:,[0-9]{3})+|[0-9]+)(?![A-Za-z0-9_]|[.,][0-9])"
_RAW_LABEL = re.compile(
    r"(?:原始(?:值|金额|数量|整数)|raw(?:[_ ]+(?:amount|value|units))?)\s*"
    r"(?:为|是|[:=：]|is|was|equals)?\s*[`\"']?(?P<amount>" + _RAW_INTEGER + r")", re.I)
_RAW_SUFFIX = re.compile(
    r"(?P<amount>" + _RAW_INTEGER + r")\s*(?:原始单位|基础单位|raw\s+(?:units|amount)|base\s+units)", re.I)
_HYPOTHETICAL_PREFIX = re.compile(r"(?:如果|假设|若|例如|举例|是否|能否)[^。；;!?！？\n]{0,40}$|"
                                   r"\b(?:if|suppose|for example|whether)\b[^.;!?\n]{0,60}$", re.I)


def _conclusion_document(report: Any) -> tuple[dict[str, Any], str]:
    document = _document(report)
    if "conclusion" in document:
        return _document(document.get("conclusion")), "conclusion."
    if "report" in document:
        return _document(document.get("report")), "report."
    return document, ""


def _claim_texts(report: Any) -> list[tuple[str, str]]:
    document, prefix = _conclusion_document(report)
    values = []
    summary = document.get("summary")
    if isinstance(summary, str):
        values.append((prefix + "summary", summary))
    for index, item in enumerate(document.get("claims", [])):
        claim = _document(item)
        if isinstance(claim.get("text"), str):
            values.append((f"{prefix}claims[{index}].text", claim["text"]))
    for index, item in enumerate(document.get("assessments", [])):
        assessment = _document(item)
        if assessment.get("status") == "supported" and isinstance(assessment.get("explanation"), str):
            values.append((f"{prefix}assessments[{index}].explanation", assessment["explanation"]))
    # Limitations, quoted source payloads, and agent traces are not factual claims.
    return values


def _erc20_wei(text: str, symbol: str | None) -> bool:
    symbol_pattern = re.compile(r"(?<![a-z0-9])" + re.escape(symbol) + r"(?![a-z])", re.I) if symbol else None
    for amount in _WEI_AMOUNT.finditer(text):
        before = text[max(0, amount.start() - 70):amount.start()]
        if _NATIVE_AMOUNT_PREFIX.search(before):
            continue
        left, right = max(0, amount.start() - 100), min(len(text), amount.end() + 100)
        for boundary in _SENTENCE_BREAK.finditer(text):
            if boundary.end() <= amount.start():
                left = max(left, boundary.end())
            elif boundary.start() >= amount.end():
                right = min(right, boundary.start())
                break
        context = text[left:right]
        if _TOKEN_CONTEXT.search(context) or (symbol_pattern and symbol_pattern.search(context)):
            return True
    return False


def _categorical_attack_exclusion(text: str) -> bool:
    for match in _ATTACK_EXCLUSION.finditer(text):
        prefix = text[max(0, match.start() - 80):match.start()]
        # An uncertainty in an earlier clause cannot qualify a later assertion.
        prefix = _ADVERSATIVE.split(prefix)[-1]
        if not _QUALIFIED_PREFIX.search(prefix):
            return True
    return False


def _qualified_at(text: str, position: int) -> bool:
    prefix = _SENTENCE_BREAK.split(text[:position])[-1]
    prefix = _ADVERSATIVE.split(prefix)[-1]
    return bool(_QUALIFIED_PREFIX.search(prefix) or _HYPOTHETICAL_PREFIX.search(prefix))


def _unqualified_wording(pattern: re.Pattern[str], text: str) -> bool:
    return any(not _qualified_at(text, match.start()) for match in pattern.finditer(text))


def _contract_burn_generalization(text: str) -> bool:
    return any(not _qualified_at(text, match.start("zh_burn") if match.group("zh_burn") else match.start("en_burn"))
               for match in _CONTRACT_BURN_GENERALIZATION.finditer(text))


def _execution_generalization_locations(report: Any) -> list[str]:
    document, prefix = _conclusion_document(report)
    # Candidate checks establish which observation the explanation purports to
    # interpret. Unknowns stay outside assertions and cannot erase an overclaim.
    candidate_success = {
        f"{prefix}assessments[{index}].explanation"
        for index, item in enumerate(document.get("assessments", []))
        if _document(item).get("status") == "supported"
        and any(isinstance(check, str) and _unqualified_wording(_SUCCESS_CONTEXT, check)
                for check in _document(item).get("checks", []))
    }
    locations = []
    for path, text in _claim_texts(report):
        for sentence in _SENTENCE_BREAK.split(text):
            if ((path in candidate_success or _unqualified_wording(_SUCCESS_CONTEXT, sentence))
                    and _unqualified_wording(_EXECUTION_OVERCLAIM, sentence)):
                locations.append(path)
                break
    return locations


def _transfer_raw_mismatch_locations(report: Any, evidence: Any) -> list[str]:
    """Compare explicit raw literals only where one cited Transfer is unambiguous.

    The screen deliberately leaves rounded displays, sums, multi-transfer claims,
    other ERC-20 fields and uncited summaries to semantic review. It does not use
    a model's derived transfer_facts or infer the referent of a bare number.
    """
    transfers: dict[str, set[str]] = {}
    for item in _document(evidence).get("transfers", []):
        transfer = _document(item)
        identifier = transfer.get("evidence_id")
        if not isinstance(identifier, str):
            continue
        try:
            raw = uint256_decimal(transfer.get("value_raw"))
        except (ValueError, TypeError):
            continue
        transfers.setdefault(identifier, set()).add(raw)
    document, prefix = _conclusion_document(report)
    locations = []
    for index, item in enumerate(document.get("claims", [])):
        claim = _document(item)
        text = claim.get("text")
        references = claim.get("evidence_ids", [])
        cited = {identifier for identifier in references if identifier in transfers}
        if (not isinstance(text, str) or len(cited) != 1 or not _TRANSFER_CONTEXT.search(text)
                or _OTHER_AMOUNT_CONTEXT.search(text)):
            continue
        expected = transfers[next(iter(cited))]
        if len(expected) != 1 or set(references) - cited - {"derived:transfer-amounts"}:
            continue
        amounts = [match for pattern in (_RAW_LABEL, _RAW_SUFFIX) for match in pattern.finditer(text)
                   if not _qualified_at(text, match.start())]
        if any((match.group("amount").replace(",", "").lstrip("0") or "0") not in expected for match in amounts):
            locations.append(f"{prefix}claims[{index}].text")
    return locations


def report_quality(report: Any, evidence: Any = None) -> dict[str, Any]:
    """Screen observed wording and cited raw amounts while preserving report bytes.

    Summary, claims and supported explanations are inspected. This limited
    screen does not establish overall correctness, completeness or independence.
    """
    metadata = _document(_document(evidence).get("metadata"))
    symbol = metadata.get("symbol")
    symbol = symbol if isinstance(symbol, str) and symbol else None
    texts = _claim_texts(report)
    flags = []
    rules = (
        ("erc20_wei_unit", "ERC-20 amount is described as wei; verify raw token units and decimals.",
         lambda text: _erc20_wei(text, symbol)),
        ("categorical_attack_exclusion", "Governance consistency does not establish that an attack is excluded.",
         _categorical_attack_exclusion),
        ("contract_burn_generalization", "A bounded supply comparison needs complete implementation evidence before supporting a contract-wide burn claim.",
         _contract_burn_generalization),
    )
    for code, message, check in rules:
        locations = [path for path, text in texts if check(text)]
        if locations:
            flags.append({"code": code, "message": message, "locations": locations})
    grounded_rules = (
        ("transfer_raw_amount_mismatch", "An explicit raw amount differs from the uniquely cited Transfer value; compare the original integer.",
         _transfer_raw_mismatch_locations(report, evidence)),
        ("execution_success_generalization", "Execution success alone does not establish correct or safe contract behavior; review the stated execution scope.",
         _execution_generalization_locations(report)),
    )
    for code, message, locations in grounded_rules:
        if locations:
            flags.append({"code": code, "message": message, "locations": locations})
    return {"schema_version": "cluetide-report-quality/v1",
            "status": "needs_review" if flags else "no_flags_detected",
            "flags": flags, "report_modified": False,
            "scope": "Limited checks of units, attack exclusion, contract-wide burn and execution-success wording; explicit raw literals are compared only for an unambiguous cited Transfer claim. Summary, claims and supported explanations are screened; semantic support requires review, no factual verification."}


assess_report_quality = report_quality
