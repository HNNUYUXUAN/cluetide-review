"""Conservative, durable local accounting for the authorized model budget.

Reservations are never released automatically: a timeout, failed response, or
process crash can still be billable upstream. This ledger records an upper
bound on consumption, not the provider's account balance.
"""
from __future__ import annotations

import sqlite3
import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, ROUND_CEILING
from pathlib import Path
from urllib.parse import urlparse
from uuid import uuid4

HARD_CAP_RMB = Decimal("10")
MICRO_RMB = Decimal("1000000")
SUPPORTED_MODELS = frozenset({"deepseek-v3.2", "minimax-m2.7"})


class BudgetExhausted(RuntimeError):
    """The next request cannot fit inside the durable budget."""


class PriceNotVerified(RuntimeError):
    """No current official price evidence permits a paid request."""


@dataclass(frozen=True)
class PriceQuote:
    model: str
    input_rmb_per_million: Decimal
    output_rmb_per_million: Decimal
    source_url: str
    verified_at: datetime
    verified: bool = False
    max_age_seconds: int = 86400
    provider_slug: str = "agentuniverse"
    source_sha256: str | None = None

    def validate(self, *, now: datetime | None = None) -> None:
        now = now or datetime.now(timezone.utc)
        parsed = urlparse(self.source_url)
        if (not self.verified or self.model not in SUPPORTED_MODELS
                or parsed.scheme != "https"
                or parsed.hostname != "tokendance.space"
                or parsed.username or parsed.password
                or self.source_url != f"https://tokendance.space/portal/api/models/{self.model}/endpoints/stats"
                or not isinstance(self.source_sha256, str)
                or not re.fullmatch(r"[0-9a-f]{64}", self.source_sha256)
                or not self.provider_slug or not self.provider_slug.replace("-", "").isalnum()
                or self.verified_at.tzinfo is None
                or not 0 < self.max_age_seconds <= 86400):
            raise PriceNotVerified("Official route price verification is required")
        age = (now - self.verified_at).total_seconds()
        if age < -60 or age > self.max_age_seconds:
            raise PriceNotVerified("Official route price verification has expired")
        if (not self.input_rmb_per_million.is_finite()
                or not self.output_rmb_per_million.is_finite()
                or self.input_rmb_per_million <= 0
                or self.output_rmb_per_million <= 0):
            raise PriceNotVerified("Positive finite route prices are required")

    def upper_bound_micro_rmb(self, input_tokens: int, output_tokens: int) -> int:
        self.validate()
        if input_tokens < 0 or output_tokens <= 0:
            raise ValueError("Invalid token reservation")
        # Twofold billing headroom also covers possible route accounting differences.
        cost = (Decimal(input_tokens) * self.input_rmb_per_million
                + Decimal(output_tokens) * self.output_rmb_per_million) * 2
        return max(1, int(cost.to_integral_value(rounding=ROUND_CEILING)))


def price_quote_from_public_payload(payload: dict, *, model: str, provider_slug: str,
                                    source_url: str, verified_at: datetime | None = None) -> PriceQuote:
    """Use the highest applicable advertised tier of one explicit provider.

    A model-directory minimum is insufficient because provider and context tiers
    can differ. The selected endpoint must actually advertise Chat Completions.
    """
    expected_url = f"https://tokendance.space/portal/api/models/{model}/endpoints/stats"
    if model not in SUPPORTED_MODELS or source_url != expected_url:
        raise PriceNotVerified("A fixed official endpoint pricing URL is required")
    matches = [endpoint for endpoint in payload.get("endpoints", [])
               if endpoint.get("slug") == provider_slug
               and "openai:chat-completions" in endpoint.get("supported_protocols", [])]
    if len(matches) != 1:
        raise PriceNotVerified("The selected provider has no unique Chat Completions endpoint")
    prices = {}
    for item in matches[0].get("pricing", {}).get("items", []):
        if item.get("id") not in {"openai:chat-completions:input_tokens", "openai:chat-completions:completion_tokens"}:
            continue
        plans = item.get("plans", [])
        if not plans or any(plan.get("unit") != "millionTokens" for plan in plans):
            raise PriceNotVerified("Unsupported endpoint pricing unit")
        try:
            rates = [Decimal(plan["rate"]) for plan in plans]
            if any(not rate.is_finite() or rate <= 0 for rate in rates):
                raise ValueError
            prices[item["id"]] = max(rates)
        except Exception:
            raise PriceNotVerified("Invalid advertised endpoint rate") from None
    if len(prices) != 2:
        raise PriceNotVerified("Input and output route prices are required")
    digest = hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False,
                                      separators=(",", ":")).encode("utf-8")).hexdigest()
    result = PriceQuote(model, prices["openai:chat-completions:input_tokens"],
                        prices["openai:chat-completions:completion_tokens"], source_url,
                        verified_at or datetime.now(timezone.utc), verified=True,
                        provider_slug=provider_slug, source_sha256=digest)
    result.validate()
    return result


async def fetch_official_price_quote(model: str, *, provider_slug: str = "agentuniverse", http_client=None) -> PriceQuote:
    """Fetch public official pricing without authentication or inference charges."""
    import httpx
    if model not in SUPPORTED_MODELS:
        raise PriceNotVerified("Model route is outside the fixed allowlist")
    source_url = f"https://tokendance.space/portal/api/models/{model}/endpoints/stats"
    async def read(client):
        response = await client.get(source_url, timeout=20, follow_redirects=False)
        response.raise_for_status()
        return price_quote_from_public_payload(response.json(), model=model,
                    provider_slug=provider_slug, source_url=source_url)
    if http_client is not None:
        return await read(http_client)
    async with httpx.AsyncClient(timeout=20, follow_redirects=False) as client:
        return await read(client)


class BalanceReadError(RuntimeError):
    """A safe public error: do not serialize upstream authentication details."""


async def read_gateway_balance(api_key: str, http_client=None) -> dict[str, object]:
    """Return spendable micro-RMB from balance.balance; credited total is ignored."""
    import httpx
    if not api_key:
        raise BalanceReadError("Configured gateway key is missing")
    async def read(client):
        try:
            response = await client.get("https://tokendance.space/portal/api/v1/user/balance",
                headers={"Authorization": "Bearer " + api_key}, timeout=20, follow_redirects=False)
            response.raise_for_status()
            amount = response.json()["balance"]["balance"]
            if type(amount) is not int or amount < 0:
                raise ValueError
        except Exception:
            raise BalanceReadError("Gateway spendable balance could not be verified") from None
        return {"balance_micro_rmb": amount, "account_balance_verified": True,
                "verified_at": datetime.now(timezone.utc).isoformat(),
                "source_url": "https://tokendance.space/docs/open-api.md"}
    if http_client is not None:
        return await read(http_client)
    async with httpx.AsyncClient(timeout=20, follow_redirects=False) as client:
        return await read(client)


class BudgetLedger:
    """SQLite serializes reservations across processes sharing this ledger.

    Use one fixed local-only file throughout the authorized session. Reopening
    it preserves all reservations. No API key, prompt, headers, or endpoint
    credentials are stored here.

    ``cap_rmb`` constrains this instance within the recorded authorization;
    changing that authorization requires an explicit local migration. A recent
    provider balance constrains additional reservations separately. Capture its
    total and unsettled reservation baseline before the account read to include
    concurrent spending and requests whose upstream charge remains uncertain.
    """

    def __init__(self, path: str | Path, *, cap_rmb: Decimal = HARD_CAP_RMB,
                 spendable_micro_rmb: int | None = None,
                 spendable_at_reserved_micro_rmb: int | None = None,
                 spendable_pending_micro_rmb: int | None = None):
        if not cap_rmb.is_finite() or not 0 < cap_rmb <= HARD_CAP_RMB:
            raise ValueError(f"The authorized hard cap is at most RMB {HARD_CAP_RMB}")
        if (spendable_micro_rmb is not None
                and (type(spendable_micro_rmb) is not int or spendable_micro_rmb < 0)):
            raise ValueError("A nonnegative integer spendable balance is required")
        if (spendable_at_reserved_micro_rmb is not None
                and (spendable_micro_rmb is None
                     or type(spendable_at_reserved_micro_rmb) is not int
                     or spendable_at_reserved_micro_rmb < 0)):
            raise ValueError("A nonnegative reservation baseline requires a spendable balance")
        if ((spendable_at_reserved_micro_rmb is None) != (spendable_pending_micro_rmb is None)
                or (spendable_pending_micro_rmb is not None
                    and (type(spendable_pending_micro_rmb) is not int
                         or not 0 <= spendable_pending_micro_rmb <= spendable_at_reserved_micro_rmb))):
            raise ValueError("A balance baseline requires coherent total and pending reservation amounts")
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.cap_micro_rmb = int(cap_rmb * MICRO_RMB)
        self.spendable_micro_rmb = spendable_micro_rmb
        with self._connect() as connection:
            connection.execute("CREATE TABLE IF NOT EXISTS budget_config (id INTEGER PRIMARY KEY CHECK(id=1), cap INTEGER NOT NULL)")
            connection.execute("INSERT OR IGNORE INTO budget_config VALUES (1, ?)", (self.cap_micro_rmb,))
            stored = connection.execute("SELECT cap FROM budget_config WHERE id=1").fetchone()[0]
            # Reopening applies an instance limit while preserving the recorded
            # authorization. An authorized cap migration is an explicit local action.
            self.cap_micro_rmb = min(self.cap_micro_rmb, stored, int(HARD_CAP_RMB * MICRO_RMB))
            connection.execute("""CREATE TABLE IF NOT EXISTS reservations (
                id TEXT PRIMARY KEY, model TEXT NOT NULL, reserved INTEGER NOT NULL,
                state TEXT NOT NULL, created_at TEXT NOT NULL,
                input_tokens INTEGER, output_tokens INTEGER, observed_micro_rmb INTEGER
            )""")
            used, pending = self._reservation_totals(connection)
            if spendable_at_reserved_micro_rmb is not None and spendable_at_reserved_micro_rmb > used:
                raise ValueError("The balance reservation baseline exceeds the retained ledger")
            self.spendable_at_reserved_micro_rmb = (
                used if spendable_at_reserved_micro_rmb is None else spendable_at_reserved_micro_rmb
            )
            self.spendable_pending_micro_rmb = (
                pending if spendable_pending_micro_rmb is None else spendable_pending_micro_rmb
            )

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.path, timeout=10, isolation_level="IMMEDIATE")

    @staticmethod
    def _reservation_totals(connection: sqlite3.Connection) -> tuple[int, int]:
        return connection.execute("""SELECT COALESCE(SUM(reserved),0),
            COALESCE(SUM(CASE WHEN state != 'completed_reserved' THEN reserved ELSE 0 END),0)
            FROM reservations""").fetchone()

    def balance_reservation_baseline(self) -> dict[str, int]:
        """Capture total and unsettled reservations in one read before account lookup."""
        with self._connect() as connection:
            used, pending = self._reservation_totals(connection)
        return {"reserved_micro_rmb": used, "pending_micro_rmb": pending}

    def reserve(self, quote: PriceQuote, *, input_tokens: int, output_tokens: int) -> str:
        amount = quote.upper_bound_micro_rmb(input_tokens, output_tokens)
        reservation_id = uuid4().hex
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            cap = min(connection.execute("SELECT cap FROM budget_config WHERE id=1").fetchone()[0], self.cap_micro_rmb)
            used = connection.execute("SELECT COALESCE(SUM(reserved),0) FROM reservations").fetchone()[0]
            if amount > cap - used:
                raise BudgetExhausted("The next conservative reservation exceeds the authorized budget")
            # Completed historical consumption is already reflected in the
            # account read. Unsettled baseline requests and later reservations
            # retain their upper bound throughout this balance observation.
            if (self.spendable_micro_rmb is not None and amount > self.spendable_micro_rmb
                    - self.spendable_pending_micro_rmb
                    - max(0, used - self.spendable_at_reserved_micro_rmb)):
                raise BudgetExhausted("The next conservative reservation exceeds the verified spendable balance")
            connection.execute(
                "INSERT INTO reservations(id,model,reserved,state,created_at) VALUES(?,?,?,?,?)",
                (reservation_id, quote.model, amount, "reserved", datetime.now(timezone.utc).isoformat()),
            )
        return reservation_id

    def finish(self, reservation_id: str, *, quote: PriceQuote,
               input_tokens: int | None = None, output_tokens: int | None = None,
               failed: bool = False) -> None:
        observed = None
        if input_tokens is not None and output_tokens is not None:
            if input_tokens < 0 or output_tokens < 0:
                raise ValueError("Invalid reported token usage")
            observed = int((Decimal(input_tokens) * quote.input_rmb_per_million
                            + Decimal(output_tokens) * quote.output_rmb_per_million)
                           .to_integral_value(rounding=ROUND_CEILING))
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute("SELECT reserved,model FROM reservations WHERE id=?", (reservation_id,)).fetchone()
            if row is None or row[1] != quote.model:
                raise ValueError("Unknown reservation or route mismatch")
            # If the provider exceeds our token bound, retain the larger amount and
            # exhaust the ledger before allowing another request.
            retained = max(row[0], observed or 0)
            connection.execute(
                "UPDATE reservations SET reserved=?,state=?,input_tokens=?,output_tokens=?,observed_micro_rmb=? WHERE id=?",
                (retained, "failed_reserved" if failed else "completed_reserved", input_tokens, output_tokens, observed, reservation_id),
            )

    def snapshot(self) -> dict[str, object]:
        with self._connect() as connection:
            cap = min(self.cap_micro_rmb, connection.execute("SELECT cap FROM budget_config WHERE id=1").fetchone()[0])
            used, count = connection.execute("SELECT COALESCE(SUM(reserved),0),COUNT(*) FROM reservations").fetchone()
            observed = connection.execute("SELECT COALESCE(SUM(observed_micro_rmb),0) FROM reservations").fetchone()[0]
        return {
            "cap_rmb": str(Decimal(cap) / MICRO_RMB),
            "reserved_rmb": str(Decimal(used) / MICRO_RMB),
            "remaining_rmb": str(Decimal(max(0, cap - used)) / MICRO_RMB),
            "reported_usage_rmb": str(Decimal(observed) / MICRO_RMB),
            "requests_reserved": count,
            "account_balance_verified": False,
        }
