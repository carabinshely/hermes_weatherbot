"""Derived ledger state and financial invariants."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass, field
from decimal import Decimal
from types import MappingProxyType
from typing import Self

from weatherbot.domain.errors import InvariantViolation
from weatherbot.domain.model import (
    EventId,
    MarketId,
    MarketResolution,
    OrderAggregate,
    OrderIntentId,
    OutcomeId,
    Position,
    PositionStatus,
    Side,
)
from weatherbot.domain.money import (
    Money,
    as_decimal,
    money_from_unit_price,
    require_nonnegative,
)
from weatherbot.domain.observation import WeatherObservationEvidence
from weatherbot.domain.resolution import MarketResolutionEvidence

type PositionKey = tuple[MarketId, OutcomeId]


def position_key(market_id: MarketId, outcome_id: OutcomeId) -> PositionKey:
    return market_id, outcome_id


def _freeze_mapping[K, V](value: Mapping[K, V]) -> Mapping[K, V]:
    return MappingProxyType(dict(value))


def _empty_orders() -> Mapping[OrderIntentId, OrderAggregate]:
    return {}


def _empty_positions() -> Mapping[PositionKey, Position]:
    return {}


def _empty_resolutions() -> Mapping[MarketId, MarketResolution]:
    return {}


def _empty_resolution_evidence() -> Mapping[MarketId, MarketResolutionEvidence]:
    return {}


def _empty_weather_observations() -> Mapping[MarketId, tuple[WeatherObservationEvidence, ...]]:
    return {}


def _empty_event_fingerprints() -> Mapping[EventId, str]:
    return {}


@dataclass(frozen=True, slots=True)
class LedgerState:
    """State derived exclusively by replaying immutable ledger events."""

    currency: str
    opened: bool
    cash: Money
    reserved_cash: Money
    orders: Mapping[OrderIntentId, OrderAggregate] = field(default_factory=_empty_orders)
    positions: Mapping[PositionKey, Position] = field(default_factory=_empty_positions)
    resolutions: Mapping[MarketId, MarketResolution] = field(default_factory=_empty_resolutions)
    resolution_evidence: Mapping[MarketId, MarketResolutionEvidence] = field(
        default_factory=_empty_resolution_evidence
    )
    weather_observations: Mapping[MarketId, tuple[WeatherObservationEvidence, ...]] = field(
        default_factory=_empty_weather_observations
    )
    event_fingerprints: Mapping[EventId, str] = field(default_factory=_empty_event_fingerprints)

    def __post_init__(self) -> None:
        currency = self.currency.strip().upper()
        if not currency:
            raise ValueError("ledger currency must not be blank")
        object.__setattr__(self, "currency", currency)
        object.__setattr__(self, "orders", _freeze_mapping(self.orders))
        object.__setattr__(self, "positions", _freeze_mapping(self.positions))
        object.__setattr__(self, "resolutions", _freeze_mapping(self.resolutions))
        object.__setattr__(
            self,
            "resolution_evidence",
            _freeze_mapping(self.resolution_evidence),
        )
        object.__setattr__(
            self,
            "weather_observations",
            _freeze_mapping(
                {
                    market_id: tuple(observations)
                    for market_id, observations in self.weather_observations.items()
                }
            ),
        )
        object.__setattr__(
            self,
            "event_fingerprints",
            _freeze_mapping(self.event_fingerprints),
        )

    @classmethod
    def empty(cls, currency: str = "USDC") -> Self:
        normalized = currency.strip().upper()
        return cls(
            currency=normalized,
            opened=False,
            cash=Money.zero(normalized),
            reserved_cash=Money.zero(normalized),
        )

    @property
    def available_cash(self) -> Money:
        return self.cash - self.reserved_cash

    def assert_invariants(self) -> None:
        if self.cash.currency != self.currency or self.reserved_cash.currency != self.currency:
            raise InvariantViolation("ledger money uses a different currency")
        require_nonnegative(self.cash, label="cash")
        require_nonnegative(self.reserved_cash, label="reserved_cash")
        require_nonnegative(self.available_cash, label="available_cash")

        expected_reserved_cash = Money.zero(self.currency)
        expected_sell_reservations: defaultdict[PositionKey, Decimal] = defaultdict(
            lambda: as_decimal(0)
        )

        for intent_id, order in self.orders.items():
            if intent_id != order.intent.intent_id:
                raise InvariantViolation("order map key does not match intent identifier")
            if order.filled_quantity < 0 or order.filled_quantity > order.intent.quantity:
                raise InvariantViolation("order filled quantity is outside valid bounds")
            for label, amount in (
                ("order gross value", order.gross_value),
                ("order fees", order.fees),
                ("order reserved cash", order.reserved_cash),
                ("order fee reserve", order.intent.fee_reserve),
            ):
                if amount.currency != self.currency:
                    raise InvariantViolation(f"{label} uses a different currency")
                require_nonnegative(amount, label=label)
            if order.reserved_quantity < 0:
                raise InvariantViolation("order reserved quantity must not be negative")
            if order.state.is_terminal and (
                not order.reserved_cash.is_zero or order.reserved_quantity != 0
            ):
                raise InvariantViolation("terminal orders must retain no reservation")
            if order.intent.side is Side.BUY:
                if order.reserved_quantity != 0:
                    raise InvariantViolation("buy orders must not reserve position quantity")
                remaining_fee_reserve = order.intent.fee_reserve - order.fees
                require_nonnegative(
                    remaining_fee_reserve,
                    label="remaining fee reserve",
                )
                if not order.state.is_terminal:
                    expected_order_reservation = (
                        money_from_unit_price(
                            order.intent.limit_price,
                            order.remaining_quantity,
                            self.currency,
                        )
                        + remaining_fee_reserve
                    )
                    if order.reserved_cash != expected_order_reservation:
                        raise InvariantViolation(
                            "buy order reservation does not cover remaining quantity and fees"
                        )
                expected_reserved_cash += order.reserved_cash
            else:
                if not order.reserved_cash.is_zero:
                    raise InvariantViolation("sell orders must not reserve cash")
                if (
                    not order.state.is_terminal
                    and order.reserved_quantity != order.remaining_quantity
                ):
                    raise InvariantViolation(
                        "sell order reservation does not equal remaining quantity"
                    )
                key = position_key(order.intent.market_id, order.intent.outcome_id)
                expected_sell_reservations[key] += order.reserved_quantity

        if expected_reserved_cash != self.reserved_cash:
            raise InvariantViolation(
                "ledger reserved cash does not equal active order reservations"
            )

        for key, position in self.positions.items():
            if key != position_key(position.market_id, position.outcome_id):
                raise InvariantViolation("position map key does not match position identity")
            if position.quantity < 0 or position.reserved_quantity < 0:
                raise InvariantViolation("position quantities must not be negative")
            if position.reserved_quantity > position.quantity:
                raise InvariantViolation("reserved position quantity exceeds total quantity")
            require_nonnegative(position.cost_basis, label="position cost basis")
            if position.reserved_quantity != expected_sell_reservations[key]:
                raise InvariantViolation(
                    "position reservation does not equal active sell-order reservations"
                )
            if position.status is PositionStatus.SETTLED and (
                position.quantity != 0
                or position.reserved_quantity != 0
                or not position.cost_basis.is_zero
            ):
                raise InvariantViolation("settled positions must have zero open exposure")

        for key, reserved_quantity in expected_sell_reservations.items():
            if reserved_quantity and key not in self.positions:
                raise InvariantViolation("sell order reserves a missing position")

        for market_id, evidence in self.resolution_evidence.items():
            if market_id != evidence.market_id:
                raise InvariantViolation("resolution evidence map key does not match market")
            resolution = self.resolutions.get(market_id)
            if resolution is not None and resolution.payouts != evidence.payouts:
                raise InvariantViolation(
                    "recorded resolution differs from its authoritative evidence"
                )

        for market_id, observations in self.weather_observations.items():
            hashes: set[str] = set()
            for observation in observations:
                if observation.market_id != market_id:
                    raise InvariantViolation("weather observation map key does not match market")
                if observation.payload_hash in hashes:
                    raise InvariantViolation(
                        "weather observation history contains a duplicate payload hash"
                    )
                if (
                    observation.supersedes_payload_hash is not None
                    and observation.supersedes_payload_hash not in hashes
                ):
                    raise InvariantViolation(
                        "weather observation revision supersedes an unknown prior payload"
                    )
                hashes.add(observation.payload_hash)

        if not self.opened and (
            not self.cash.is_zero
            or not self.reserved_cash.is_zero
            or self.orders
            or self.positions
            or self.resolutions
            or self.resolution_evidence
            or self.weather_observations
        ):
            raise InvariantViolation("an unopened ledger must have no financial state")
