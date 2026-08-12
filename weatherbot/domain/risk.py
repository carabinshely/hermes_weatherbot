"""Durable backend-neutral identity and valuation contracts for portfolio risk."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal

from weatherbot.domain.model import EventId, MarketId, OutcomeId, require_aware
from weatherbot.domain.money import Money, as_decimal, require_nonnegative


def _text(value: str, *, label: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{label} must not be blank")
    return normalized


@dataclass(frozen=True, slots=True)
class RiskScope:
    """Stable portfolio/correlation identity for one outcome exposure."""

    market_id: MarketId
    outcome_id: OutcomeId
    event_id: str
    city_key: str
    market_date: date
    correlation_groups: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _text(str(self.market_id), label="market_id")
        _text(str(self.outcome_id), label="outcome_id")
        event_id = _text(self.event_id, label="event_id")
        city_key = _text(self.city_key, label="city_key").casefold()
        normalized_groups = tuple(
            sorted(
                {
                    _text(group, label="correlation group").casefold()
                    for group in self.correlation_groups
                }
            )
        )
        object.__setattr__(self, "event_id", event_id)
        object.__setattr__(self, "city_key", city_key)
        object.__setattr__(self, "correlation_groups", normalized_groups)

    @property
    def position_key(self) -> tuple[MarketId, OutcomeId]:
        return self.market_id, self.outcome_id

    @property
    def city_date_key(self) -> str:
        return f"{self.city_key}:{self.market_date.isoformat()}"

    @property
    def all_correlation_groups(self) -> tuple[str, ...]:
        """Same-event/date exposure is always correlated; callers add extra groups."""
        return tuple(
            sorted(
                {
                    f"date:{self.market_date.isoformat()}",
                    f"event:{self.event_id.casefold()}",
                    *self.correlation_groups,
                }
            )
        )


def risk_scope_event_id(scope: RiskScope) -> EventId:
    """Stable identifier makes one position key map to one immutable scope forever."""
    material = f"{scope.market_id}\n{scope.outcome_id}".encode()
    return EventId(f"risk_scope_{hashlib.sha256(material).hexdigest()}")


@dataclass(frozen=True, slots=True)
class PositionValuation:
    """Conservative liquidation value for the exact current position quantity."""

    market_id: MarketId
    outcome_id: OutcomeId
    quantity: Decimal
    liquidation_value: Money
    observed_at: datetime

    def __post_init__(self) -> None:
        _text(str(self.market_id), label="market_id")
        _text(str(self.outcome_id), label="outcome_id")
        quantity = as_decimal(self.quantity)
        if quantity <= 0:
            raise ValueError("valuation quantity must be positive")
        require_nonnegative(self.liquidation_value, label="liquidation_value")
        require_aware(self.observed_at, label="valuation observed_at")
        object.__setattr__(self, "quantity", quantity)

    @property
    def position_key(self) -> tuple[MarketId, OutcomeId]:
        return self.market_id, self.outcome_id


@dataclass(frozen=True, slots=True)
class PortfolioValuation:
    """One auditable mark-to-liquidation snapshot used by a risk decision."""

    positions: tuple[PositionValuation, ...]
    equity: Money
    assembled_at: datetime
    source: str

    def __post_init__(self) -> None:
        require_nonnegative(self.equity, label="portfolio equity")
        require_aware(self.assembled_at, label="valuation assembled_at")
        source = _text(self.source, label="valuation source")
        keys = [mark.position_key for mark in self.positions]
        if len(keys) != len(set(keys)):
            raise ValueError("portfolio valuation contains duplicate position keys")
        for mark in self.positions:
            if mark.liquidation_value.currency != self.equity.currency:
                raise ValueError("portfolio valuation mixes currencies")
            if mark.observed_at > self.assembled_at:
                raise ValueError("position valuation cannot be observed after assembly")
        object.__setattr__(self, "source", source)
