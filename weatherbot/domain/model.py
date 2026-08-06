"""Typed backend-neutral domain aggregates and lifecycle states."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import NewType, Self

from weatherbot.domain.errors import InvalidTransition, InvariantViolation
from weatherbot.domain.money import Money, as_decimal, money_from_unit_price, require_nonnegative

MarketId = NewType("MarketId", str)
OutcomeId = NewType("OutcomeId", str)
OrderIntentId = NewType("OrderIntentId", str)
EventId = NewType("EventId", str)
FillId = NewType("FillId", str)


def _empty_fill_fingerprints() -> Mapping[FillId, str]:
    return {}


def _require_text(value: str, *, label: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{label} must not be blank")
    return normalized


def require_aware(value: datetime, *, label: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{label} must be timezone-aware")


class Side(StrEnum):
    BUY = "buy"
    SELL = "sell"


class RiskDecisionStatus(StrEnum):
    APPROVED = "approved"
    REJECTED = "rejected"


class OrderState(StrEnum):
    CREATED = "created"
    SUBMITTED = "submitted"
    ACKNOWLEDGED = "acknowledged"
    PARTIALLY_FILLED = "partially_filled"
    FILLED = "filled"
    REJECTED = "rejected"
    CANCELLED = "cancelled"
    UNKNOWN = "unknown"

    @property
    def is_terminal(self) -> bool:
        return self in {self.FILLED, self.REJECTED, self.CANCELLED}


class PositionStatus(StrEnum):
    OPEN = "open"
    SETTLED = "settled"


_ALLOWED_TRANSITIONS: Mapping[OrderState, frozenset[OrderState]] = {
    OrderState.CREATED: frozenset(
        {OrderState.SUBMITTED, OrderState.REJECTED, OrderState.CANCELLED}
    ),
    OrderState.SUBMITTED: frozenset(
        {
            OrderState.ACKNOWLEDGED,
            OrderState.PARTIALLY_FILLED,
            OrderState.FILLED,
            OrderState.REJECTED,
            OrderState.CANCELLED,
            OrderState.UNKNOWN,
        }
    ),
    OrderState.ACKNOWLEDGED: frozenset(
        {
            OrderState.PARTIALLY_FILLED,
            OrderState.FILLED,
            OrderState.CANCELLED,
            OrderState.UNKNOWN,
        }
    ),
    OrderState.PARTIALLY_FILLED: frozenset(
        {
            OrderState.PARTIALLY_FILLED,
            OrderState.FILLED,
            OrderState.CANCELLED,
            OrderState.UNKNOWN,
        }
    ),
    OrderState.UNKNOWN: frozenset(
        {
            OrderState.ACKNOWLEDGED,
            OrderState.PARTIALLY_FILLED,
            OrderState.FILLED,
            OrderState.REJECTED,
            OrderState.CANCELLED,
            OrderState.UNKNOWN,
        }
    ),
    OrderState.FILLED: frozenset(),
    OrderState.REJECTED: frozenset(),
    OrderState.CANCELLED: frozenset(),
}


def allowed_transitions(state: OrderState) -> frozenset[OrderState]:
    return _ALLOWED_TRANSITIONS[state]


def require_transition(current: OrderState, target: OrderState) -> None:
    if target not in allowed_transitions(current):
        raise InvalidTransition(f"order transition {current.value} -> {target.value} is invalid")


def build_order_intent_id(
    *,
    strategy_id: str,
    decision_id: str,
    market_id: MarketId,
    outcome_id: OutcomeId,
    side: Side,
    quantity: Decimal,
    limit_price: Decimal,
) -> OrderIntentId:
    """Build a stable logical-order identifier for retries and restarts."""
    payload = {
        "decision_id": _require_text(decision_id, label="decision_id"),
        "limit_price": format(as_decimal(limit_price), "f"),
        "market_id": _require_text(str(market_id), label="market_id"),
        "outcome_id": _require_text(str(outcome_id), label="outcome_id"),
        "quantity": format(as_decimal(quantity), "f"),
        "side": side.value,
        "strategy_id": _require_text(strategy_id, label="strategy_id"),
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return OrderIntentId(f"intent_{hashlib.sha256(encoded).hexdigest()}")


@dataclass(frozen=True, slots=True)
class Signal:
    strategy_id: str
    decision_id: str
    market_id: MarketId
    outcome_id: OutcomeId
    probability: Decimal
    observed_price: Decimal
    generated_at: datetime

    def __post_init__(self) -> None:
        _require_text(self.strategy_id, label="strategy_id")
        _require_text(self.decision_id, label="decision_id")
        _require_text(str(self.market_id), label="market_id")
        _require_text(str(self.outcome_id), label="outcome_id")
        probability = as_decimal(self.probability)
        observed_price = as_decimal(self.observed_price)
        if not Decimal("0") <= probability <= Decimal("1"):
            raise ValueError("probability must be between zero and one")
        if not Decimal("0") < observed_price <= Decimal("1"):
            raise ValueError("observed_price must be greater than zero and at most one")
        require_aware(self.generated_at, label="generated_at")
        object.__setattr__(self, "probability", probability)
        object.__setattr__(self, "observed_price", observed_price)


@dataclass(frozen=True, slots=True)
class PreTradeDecision:
    signal: Signal
    status: RiskDecisionStatus
    max_cash: Money
    reason: str

    def __post_init__(self) -> None:
        require_nonnegative(self.max_cash, label="max_cash")
        if self.status is RiskDecisionStatus.APPROVED and self.max_cash.is_zero:
            raise InvariantViolation("approved decisions require positive max_cash")
        if self.status is RiskDecisionStatus.REJECTED and not self.max_cash.is_zero:
            raise InvariantViolation("rejected decisions must reserve no cash")
        _require_text(self.reason, label="reason")


@dataclass(frozen=True, slots=True)
class OrderIntent:
    intent_id: OrderIntentId
    strategy_id: str
    decision_id: str
    market_id: MarketId
    outcome_id: OutcomeId
    side: Side
    quantity: Decimal
    limit_price: Decimal
    fee_reserve: Money
    created_at: datetime

    def __post_init__(self) -> None:
        _require_text(str(self.intent_id), label="intent_id")
        _require_text(self.strategy_id, label="strategy_id")
        _require_text(self.decision_id, label="decision_id")
        _require_text(str(self.market_id), label="market_id")
        _require_text(str(self.outcome_id), label="outcome_id")
        quantity = as_decimal(self.quantity)
        limit_price = as_decimal(self.limit_price)
        if quantity <= 0:
            raise ValueError("order quantity must be positive")
        if not Decimal("0") < limit_price <= Decimal("1"):
            raise ValueError("limit_price must be greater than zero and at most one")
        require_nonnegative(self.fee_reserve, label="fee_reserve")
        require_aware(self.created_at, label="created_at")
        object.__setattr__(self, "quantity", quantity)
        object.__setattr__(self, "limit_price", limit_price)

    @classmethod
    def create(
        cls,
        *,
        strategy_id: str,
        decision_id: str,
        market_id: MarketId,
        outcome_id: OutcomeId,
        side: Side,
        quantity: Decimal,
        limit_price: Decimal,
        fee_reserve: Money,
        created_at: datetime,
    ) -> Self:
        intent_id = build_order_intent_id(
            strategy_id=strategy_id,
            decision_id=decision_id,
            market_id=market_id,
            outcome_id=outcome_id,
            side=side,
            quantity=quantity,
            limit_price=limit_price,
        )
        return cls(
            intent_id=intent_id,
            strategy_id=strategy_id,
            decision_id=decision_id,
            market_id=market_id,
            outcome_id=outcome_id,
            side=side,
            quantity=quantity,
            limit_price=limit_price,
            fee_reserve=fee_reserve,
            created_at=created_at,
        )

    @property
    def limit_notional(self) -> Money:
        return money_from_unit_price(
            self.limit_price,
            self.quantity,
            self.fee_reserve.currency,
        )

    @property
    def cash_reservation(self) -> Money:
        if self.side is Side.SELL:
            return Money.zero(self.fee_reserve.currency)
        return self.limit_notional + self.fee_reserve


@dataclass(frozen=True, slots=True)
class OrderAggregate:
    intent: OrderIntent
    state: OrderState
    filled_quantity: Decimal
    gross_value: Money
    fees: Money
    reserved_cash: Money
    reserved_quantity: Decimal
    backend_order_id: str | None = None
    terminal_reason: str | None = None
    unknown_reason: str | None = None
    fill_fingerprints: Mapping[FillId, str] = field(
        default_factory=_empty_fill_fingerprints
    )

    @classmethod
    def new(cls, intent: OrderIntent) -> Self:
        currency = intent.fee_reserve.currency
        return cls(
            intent=intent,
            state=OrderState.CREATED,
            filled_quantity=as_decimal(0),
            gross_value=Money.zero(currency),
            fees=Money.zero(currency),
            reserved_cash=intent.cash_reservation,
            reserved_quantity=(intent.quantity if intent.side is Side.SELL else as_decimal(0)),
        )

    @property
    def remaining_quantity(self) -> Decimal:
        return as_decimal(self.intent.quantity - self.filled_quantity)


@dataclass(frozen=True, slots=True)
class Position:
    market_id: MarketId
    outcome_id: OutcomeId
    quantity: Decimal
    reserved_quantity: Decimal
    cost_basis: Money
    realized_pnl: Money
    status: PositionStatus = PositionStatus.OPEN
    settlement_payout: Decimal | None = None

    @classmethod
    def empty(cls, market_id: MarketId, outcome_id: OutcomeId, currency: str) -> Self:
        return cls(
            market_id=market_id,
            outcome_id=outcome_id,
            quantity=as_decimal(0),
            reserved_quantity=as_decimal(0),
            cost_basis=Money.zero(currency),
            realized_pnl=Money.zero(currency),
        )

    @property
    def available_quantity(self) -> Decimal:
        return as_decimal(self.quantity - self.reserved_quantity)


@dataclass(frozen=True, slots=True)
class OutcomePayout:
    outcome_id: OutcomeId
    payout: Decimal

    def __post_init__(self) -> None:
        payout = as_decimal(self.payout)
        if not Decimal("0") <= payout <= Decimal("1"):
            raise ValueError("outcome payout must be between zero and one")
        object.__setattr__(self, "payout", payout)


@dataclass(frozen=True, slots=True)
class MarketResolution:
    market_id: MarketId
    payouts: tuple[OutcomePayout, ...]
    resolved_at: datetime

    def __post_init__(self) -> None:
        _require_text(str(self.market_id), label="market_id")
        require_aware(self.resolved_at, label="resolved_at")
        if not self.payouts:
            raise ValueError("market resolution requires at least one outcome payout")
        outcome_ids = [payout.outcome_id for payout in self.payouts]
        if len(outcome_ids) != len(set(outcome_ids)):
            raise ValueError("market resolution contains duplicate outcome payouts")

    def payout_for(self, outcome_id: OutcomeId) -> Decimal:
        for payout in self.payouts:
            if payout.outcome_id == outcome_id:
                return payout.payout
        raise InvariantViolation(f"resolution has no payout for outcome {outcome_id}")
