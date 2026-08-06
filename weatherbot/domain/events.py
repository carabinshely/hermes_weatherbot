"""Immutable domain events used by paper and live execution backends."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, fields, is_dataclass
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from typing import cast

from weatherbot.domain.model import (
    EventId,
    FillId,
    MarketId,
    MarketResolution,
    OrderIntent,
    OrderIntentId,
    OutcomeId,
    require_aware,
)
from weatherbot.domain.money import Money, as_decimal, require_nonnegative
from weatherbot.domain.observation import WeatherObservationEvidence
from weatherbot.domain.resolution import MarketResolutionEvidence


def _canonicalize(value: object) -> object:
    if is_dataclass(value) and not isinstance(value, type):
        value_type = type(value)
        canonical: dict[str, object] = {
            "__type__": f"{value_type.__module__}.{value_type.__qualname__}"
        }
        canonical.update(
            {
                field.name: _canonicalize(cast(object, getattr(value, field.name)))
                for field in fields(value)
            }
        )
        return canonical
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Enum):
        return cast(object, value.value)
    if isinstance(value, Mapping):
        mapping = cast(Mapping[object, object], value)
        entries = sorted(mapping.items(), key=lambda pair: str(pair[0]))
        return {str(key): _canonicalize(item) for key, item in entries}
    if isinstance(value, (tuple, list, frozenset, set)):
        items = cast(Iterable[object], value)
        return [_canonicalize(item) for item in items]
    return value


def fingerprint(value: object) -> str:
    encoded = json.dumps(
        _canonicalize(value),
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True, slots=True, kw_only=True)
class DomainEvent:
    event_id: EventId
    occurred_at: datetime

    def __post_init__(self) -> None:
        if not str(self.event_id).strip():
            raise ValueError("event_id must not be blank")
        require_aware(self.occurred_at, label="occurred_at")


@dataclass(frozen=True, slots=True, kw_only=True)
class AccountOpened(DomainEvent):
    initial_cash: Money

    def __post_init__(self) -> None:
        DomainEvent.__post_init__(self)
        require_nonnegative(self.initial_cash, label="initial_cash")


@dataclass(frozen=True, slots=True, kw_only=True)
class OrderIntentCreated(DomainEvent):
    intent: OrderIntent


@dataclass(frozen=True, slots=True, kw_only=True)
class OrderSubmitted(DomainEvent):
    intent_id: OrderIntentId
    backend_order_id: str

    def __post_init__(self) -> None:
        DomainEvent.__post_init__(self)
        if not self.backend_order_id.strip():
            raise ValueError("backend_order_id must not be blank")


@dataclass(frozen=True, slots=True, kw_only=True)
class OrderAcknowledged(DomainEvent):
    intent_id: OrderIntentId


@dataclass(frozen=True, slots=True, kw_only=True)
class FillReceived(DomainEvent):
    intent_id: OrderIntentId
    fill_id: FillId
    quantity: Decimal
    price: Decimal
    fee: Money

    def __post_init__(self) -> None:
        DomainEvent.__post_init__(self)
        quantity = as_decimal(self.quantity)
        price = as_decimal(self.price)
        if quantity <= 0:
            raise ValueError("fill quantity must be positive")
        if not Decimal("0") < price <= Decimal("1"):
            raise ValueError("fill price must be greater than zero and at most one")
        if not str(self.fill_id).strip():
            raise ValueError("fill_id must not be blank")
        require_nonnegative(self.fee, label="fill fee")
        object.__setattr__(self, "quantity", quantity)
        object.__setattr__(self, "price", price)

    @property
    def delivery_fingerprint(self) -> str:
        return fingerprint(
            {
                "intent_id": self.intent_id,
                "fill_id": self.fill_id,
                "quantity": self.quantity,
                "price": self.price,
                "fee": self.fee,
            }
        )


@dataclass(frozen=True, slots=True, kw_only=True)
class OrderRejected(DomainEvent):
    intent_id: OrderIntentId
    reason: str

    def __post_init__(self) -> None:
        DomainEvent.__post_init__(self)
        if not self.reason.strip():
            raise ValueError("rejection reason must not be blank")


@dataclass(frozen=True, slots=True, kw_only=True)
class OrderCancelled(DomainEvent):
    intent_id: OrderIntentId
    reason: str

    def __post_init__(self) -> None:
        DomainEvent.__post_init__(self)
        if not self.reason.strip():
            raise ValueError("cancellation reason must not be blank")


@dataclass(frozen=True, slots=True, kw_only=True)
class OrderOutcomeUnknown(DomainEvent):
    intent_id: OrderIntentId
    reason: str

    def __post_init__(self) -> None:
        DomainEvent.__post_init__(self)
        if not self.reason.strip():
            raise ValueError("unknown-outcome reason must not be blank")


@dataclass(frozen=True, slots=True, kw_only=True)
class WeatherObservationRecorded(DomainEvent):
    evidence: WeatherObservationEvidence


@dataclass(frozen=True, slots=True, kw_only=True)
class MarketResolutionEvidenceRecorded(DomainEvent):
    evidence: MarketResolutionEvidence


@dataclass(frozen=True, slots=True, kw_only=True)
class MarketResolved(DomainEvent):
    resolution: MarketResolution


@dataclass(frozen=True, slots=True, kw_only=True)
class PositionSettled(DomainEvent):
    market_id: MarketId
    outcome_id: OutcomeId
    fee: Money

    def __post_init__(self) -> None:
        DomainEvent.__post_init__(self)
        require_nonnegative(self.fee, label="settlement fee")


type LedgerEvent = (
    AccountOpened
    | OrderIntentCreated
    | OrderSubmitted
    | OrderAcknowledged
    | FillReceived
    | OrderRejected
    | OrderCancelled
    | OrderOutcomeUnknown
    | WeatherObservationRecorded
    | MarketResolutionEvidenceRecorded
    | MarketResolved
    | PositionSettled
)
