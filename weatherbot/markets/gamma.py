"""Strict parsing of public Polymarket Gamma binary-market payloads."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from types import MappingProxyType
from typing import cast

from weatherbot.markets.identity import (
    BinaryMarketIdentity,
    BinaryOutcome,
    ConditionId,
    GammaMarketId,
    MarketIdentityError,
    MarketSelection,
    OutcomeToken,
    OutcomeTokenId,
)


class GammaMarketError(MarketIdentityError):
    """Raised when a Gamma market payload is malformed or ambiguous."""


def _mapping(value: object, *, label: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise GammaMarketError(f"{label} must be an object")
    return cast(Mapping[str, object], value)


def _text(value: object, *, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise GammaMarketError(f"{label} must be a non-blank string")
    return value.strip()


def _optional_text(value: object, *, label: str) -> str | None:
    if value is None or value == "":
        return None
    return _text(value, label=label)


def _bool(value: object, *, label: str, default: bool = False) -> bool:
    if value is None:
        return default
    if not isinstance(value, bool):
        raise GammaMarketError(f"{label} must be boolean")
    return value


def _json_array(value: object, *, label: str) -> list[object]:
    parsed = value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError as exc:
            raise GammaMarketError(f"{label} is not valid JSON") from exc
    if not isinstance(parsed, Sequence) or isinstance(parsed, (str, bytes, bytearray)):
        raise GammaMarketError(f"{label} must be an array or JSON-encoded array")
    return list(cast(Sequence[object], parsed))


def _decimal(value: object, *, label: str) -> Decimal:
    if isinstance(value, bool):
        raise GammaMarketError(f"{label} must be a decimal")
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise GammaMarketError(f"{label} must be a decimal") from exc
    if not result.is_finite():
        raise GammaMarketError(f"{label} must be finite")
    return result


def _aware_datetime(value: object, *, label: str) -> datetime | None:
    text = _optional_text(value, label=label)
    if text is None:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise GammaMarketError(f"{label} must be ISO-8601") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise GammaMarketError(f"{label} must include a timezone")
    return parsed


@dataclass(frozen=True, slots=True)
class GammaBinaryMarket:
    identity: BinaryMarketIdentity
    question: str
    description: str | None
    resolution_source: str | None
    end_at: datetime | None
    active: bool
    closed: bool
    outcome_prices: Mapping[BinaryOutcome, Decimal]

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "outcome_prices",
            MappingProxyType(dict(self.outcome_prices)),
        )

    def select(self, outcome: BinaryOutcome) -> MarketSelection:
        return self.identity.select(outcome)

    def descriptive_price(self, outcome: BinaryOutcome) -> Decimal | None:
        return self.outcome_prices.get(outcome)


def parse_gamma_binary_market(payload: Mapping[str, object]) -> GammaBinaryMarket:
    market_id = GammaMarketId(_text(payload.get("id"), label="id"))
    condition_id = ConditionId(_text(payload.get("conditionId"), label="conditionId"))
    question = _text(payload.get("question"), label="question")

    raw_outcomes = _json_array(payload.get("outcomes"), label="outcomes")
    raw_token_ids = _json_array(payload.get("clobTokenIds"), label="clobTokenIds")
    if len(raw_outcomes) != 2 or len(raw_token_ids) != 2:
        raise GammaMarketError("binary market must contain exactly two outcomes and token ids")
    if len(raw_outcomes) != len(raw_token_ids):
        raise GammaMarketError("outcomes and clobTokenIds lengths do not match")

    outcome_tokens: list[OutcomeToken] = []
    seen_outcomes: set[BinaryOutcome] = set()
    try:
        for index, (raw_outcome, raw_token_id) in enumerate(
            zip(raw_outcomes, raw_token_ids, strict=True)
        ):
            outcome = BinaryOutcome.parse(_text(raw_outcome, label=f"outcomes[{index}]"))
            if outcome in seen_outcomes:
                raise GammaMarketError(f"duplicate outcome label: {outcome.value}")
            seen_outcomes.add(outcome)
            token_id = OutcomeTokenId(_text(raw_token_id, label=f"clobTokenIds[{index}]"))
            outcome_tokens.append(OutcomeToken(outcome=outcome, token_id=token_id))

        identity = BinaryMarketIdentity(
            market_id=market_id,
            condition_id=condition_id,
            outcome_tokens=cast(tuple[OutcomeToken, OutcomeToken], tuple(outcome_tokens)),
        )
    except MarketIdentityError as exc:
        if isinstance(exc, GammaMarketError):
            raise
        raise GammaMarketError(str(exc)) from exc

    prices: dict[BinaryOutcome, Decimal] = {}
    raw_prices_value = payload.get("outcomePrices")
    if raw_prices_value not in (None, ""):
        raw_prices = _json_array(raw_prices_value, label="outcomePrices")
        if len(raw_prices) != len(raw_outcomes):
            raise GammaMarketError("outcomePrices length does not match outcomes")
        for index, (raw_outcome, raw_price) in enumerate(
            zip(raw_outcomes, raw_prices, strict=True)
        ):
            outcome = BinaryOutcome.parse(_text(raw_outcome, label=f"outcomes[{index}]"))
            price = _decimal(raw_price, label=f"outcomePrices[{index}]")
            if not Decimal("0") <= price <= Decimal("1"):
                raise GammaMarketError(f"outcomePrices[{index}] must be between zero and one")
            prices[outcome] = price
        if len(prices) != 2:
            raise GammaMarketError("outcomePrices must map both YES and NO outcomes")

    return GammaBinaryMarket(
        identity=identity,
        question=question,
        description=_optional_text(payload.get("description"), label="description"),
        resolution_source=_optional_text(
            payload.get("resolutionSource"),
            label="resolutionSource",
        ),
        end_at=_aware_datetime(payload.get("endDate"), label="endDate"),
        active=_bool(payload.get("active"), label="active", default=False),
        closed=_bool(payload.get("closed"), label="closed", default=False),
        outcome_prices=prices,
    )


def parse_gamma_event_markets(payload: Mapping[str, object]) -> tuple[GammaBinaryMarket, ...]:
    raw_markets = payload.get("markets")
    if not isinstance(raw_markets, Sequence) or isinstance(
        raw_markets,
        (str, bytes, bytearray),
    ):
        raise GammaMarketError("event.markets must be an array")
    markets = tuple(
        parse_gamma_binary_market(_mapping(item, label=f"markets[{index}]"))
        for index, item in enumerate(cast(Sequence[object], raw_markets))
    )
    if not markets:
        raise GammaMarketError("event contains no markets")
    ids = [market.identity.market_id for market in markets]
    conditions = [market.identity.condition_id for market in markets]
    if len(ids) != len(set(ids)):
        raise GammaMarketError("event contains duplicate market ids")
    if len(conditions) != len(set(conditions)):
        raise GammaMarketError("event contains duplicate condition ids")
    return markets
