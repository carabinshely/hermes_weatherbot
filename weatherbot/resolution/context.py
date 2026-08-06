"""Recover resolution context from immutable order decisions in the event store."""

from __future__ import annotations

from collections.abc import Mapping
from decimal import Decimal, InvalidOperation

from weatherbot.domain import MarketId
from weatherbot.markets import (
    ConditionId,
    MarketCalendar,
    TemperatureBucket,
    TemperatureMarketError,
    TemperatureUnit,
)
from weatherbot.persistence import SQLiteEventStore
from weatherbot.resolution.model import ResolutionContext


class ResolutionContextError(ValueError):
    """Raised when a persisted position lacks consistent resolution metadata."""


def _text(value: object, *, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ResolutionContextError(f"{label} must be a non-blank string")
    return value.strip()


def _optional_text(value: object, *, label: str) -> str | None:
    if value is None or value == "":
        return None
    return _text(value, label=label)


def _bound(value: str, *, label: str) -> Decimal | None:
    if value in {"-inf", "inf"}:
        return None
    try:
        result = Decimal(value)
    except InvalidOperation as exc:
        raise ResolutionContextError(f"{label} is not decimal") from exc
    if not result.is_finite() or result != result.to_integral_value():
        raise ResolutionContextError(f"{label} must be a finite whole degree")
    return result


def bucket_from_key(value: str) -> TemperatureBucket:
    parts = _text(value, label="bucket_key").split(":")
    if len(parts) != 3:
        raise ResolutionContextError("bucket_key must contain unit, lower, and upper")
    try:
        unit = TemperatureUnit.parse(parts[0])
        lower = _bound(parts[1], label="bucket lower bound")
        upper = _bound(parts[2], label="bucket upper bound")
        if parts[1] == "-inf":
            if upper is None:
                raise ResolutionContextError("lower-tail bucket requires an upper bound")
            return TemperatureBucket.lower_tail(upper, unit)
        if parts[2] == "inf":
            if lower is None:
                raise ResolutionContextError("upper-tail bucket requires a lower bound")
            return TemperatureBucket.upper_tail(lower, unit)
        if lower is None or upper is None:
            raise ResolutionContextError("bounded bucket requires finite bounds")
        return TemperatureBucket.bounded(lower, upper, unit)
    except TemperatureMarketError as exc:
        raise ResolutionContextError(str(exc)) from exc


def _context_from_metadata(
    market_id: MarketId,
    metadata: Mapping[str, object],
) -> ResolutionContext:
    condition_id = ConditionId(_text(metadata.get("condition_id"), label="condition_id"))
    market_date_text = _text(metadata.get("market_date"), label="market_date")
    timezone_name = _text(metadata.get("market_timezone"), label="market_timezone")
    calendar = MarketCalendar(timezone_name)
    market_date = calendar.parse_market_date(market_date_text)
    bucket = bucket_from_key(_text(metadata.get("bucket_key"), label="bucket_key"))
    declared_source = _optional_text(
        metadata.get("declared_resolution_source"),
        label="declared_resolution_source",
    )
    return ResolutionContext(
        market_id=market_id,
        condition_id=condition_id,
        market_date=market_date,
        market_timezone=calendar.timezone_name,
        bucket=bucket,
        declared_resolution_source=declared_source,
    )


class StoredDecisionContextProvider:
    """Read write-once scan metadata associated with filled order intents."""

    def context_for_market(
        self,
        store: SQLiteEventStore,
        market_id: MarketId,
    ) -> ResolutionContext:
        state = store.load_state()
        relevant_intents = {
            order.intent.intent_id
            for order in state.orders.values()
            if order.intent.market_id == market_id and order.filled_quantity > 0
        }
        if not relevant_intents:
            raise ResolutionContextError(
                f"market {market_id} has no filled order intent carrying resolution context"
            )

        contexts: list[ResolutionContext] = []
        for claim in store.list_decision_claims():
            if claim.intent_id not in relevant_intents:
                continue
            contexts.append(_context_from_metadata(market_id, claim.metadata))
        if not contexts:
            raise ResolutionContextError(f"market {market_id} has no committed decision metadata")
        first = contexts[0]
        if any(context != first for context in contexts[1:]):
            raise ResolutionContextError(
                f"market {market_id} has conflicting resolution context across orders"
            )
        return first
