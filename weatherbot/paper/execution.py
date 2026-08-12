"""Deterministic size-aware paper execution over contemporaneous token order books."""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Mapping
from datetime import UTC, datetime, timedelta
from decimal import ROUND_DOWN, Decimal

from weatherbot.domain import (
    EventId,
    FillId,
    FillReceived,
    LedgerEvent,
    Money,
    OrderAcknowledged,
    OrderAggregate,
    OrderCancelled,
    OrderIntent,
    OrderIntentId,
    OrderRejected,
    OrderState,
    OrderSubmitted,
    Side,
    as_decimal,
    money_from_unit_price,
)
from weatherbot.markets import OrderBookSnapshot
from weatherbot.paper.model import PaperExecutionPlan, PaperExecutionStatus, PaperFillLevel
from weatherbot.quoting import CostPolicy

_QUANTITY_QUANTUM = Decimal("0.000001")
_ZERO = Decimal("0")
_ONE = Decimal("1")


def _floor_quantity(value: Decimal) -> Decimal:
    if value <= 0:
        return _ZERO
    return value.quantize(_QUANTITY_QUANTUM, rounding=ROUND_DOWN)


def _stable_id(prefix: str, intent_id: OrderIntentId, suffix: str = "") -> str:
    material = f"{prefix}\n{intent_id}\n{suffix}".encode()
    return f"paper_{prefix}_{hashlib.sha256(material).hexdigest()}"


def _backend_order_id(intent_id: OrderIntentId) -> str:
    return _stable_id("order", intent_id)


def _fee(gross: Money, policy: CostPolicy) -> Money:
    return gross.scale(policy.platform_fee_rate) + Money.of(
        policy.transaction_cost,
        gross.currency,
    )


def _reject_plan(
    intent: OrderIntent,
    book: OrderBookSnapshot,
    *,
    submitted_at: datetime,
    reason: str,
) -> PaperExecutionPlan:
    currency = intent.fee_reserve.currency
    return PaperExecutionPlan(
        intent_id=intent.intent_id,
        status=PaperExecutionStatus.REJECTED,
        backend_order_id=_backend_order_id(intent.intent_id),
        submitted_at=submitted_at,
        order_book_hash=book.book_hash,
        order_book_observed_at=book.observed_at,
        condition_id=str(book.condition_id),
        token_id=str(book.token_id),
        requested_quantity=intent.quantity,
        filled_quantity=_ZERO,
        average_price=None,
        worst_price=None,
        gross_value=Money.zero(currency),
        fee=Money.zero(currency),
        levels=(),
        reason=reason,
    )


def _buy_levels(
    intent: OrderIntent,
    book: OrderBookSnapshot,
    policy: CostPolicy,
) -> tuple[PaperFillLevel, ...]:
    average_cap = min(
        intent.limit_price,
        book.best_ask + policy.maximum_average_slippage,
    )
    worst_cap = book.best_ask + policy.maximum_worst_slippage
    available_for_gross = intent.cash_reservation.amount - policy.transaction_cost
    if available_for_gross <= 0:
        return ()
    maximum_gross = available_for_gross / (_ONE + policy.platform_fee_rate) - _QUANTITY_QUANTUM
    if maximum_gross <= 0:
        return ()

    remaining = intent.quantity
    shares = _ZERO
    book_cost = _ZERO
    levels: list[PaperFillLevel] = []
    for level in book.asks:
        if remaining <= 0 or level.price > worst_cap:
            break
        take = min(remaining, level.size)
        cash_headroom = maximum_gross - book_cost
        if cash_headroom <= 0:
            break
        take = min(take, cash_headroom / level.price)
        if level.price > average_cap:
            average_headroom = average_cap * shares - book_cost
            if average_headroom <= 0:
                break
            take = min(take, average_headroom / (level.price - average_cap))
        take = _floor_quantity(take)
        if take <= 0:
            break
        levels.append(PaperFillLevel(price=level.price, quantity=take))
        remaining = as_decimal(remaining - take)
        shares += take
        book_cost += take * level.price
        if take < level.size:
            break
    return tuple(levels)


def _sell_levels(
    intent: OrderIntent,
    book: OrderBookSnapshot,
    policy: CostPolicy,
) -> tuple[PaperFillLevel, ...]:
    average_floor = max(
        intent.limit_price,
        book.best_bid - policy.maximum_average_slippage,
    )
    worst_floor = book.best_bid - policy.maximum_worst_slippage
    remaining = intent.quantity
    shares = _ZERO
    gross = _ZERO
    levels: list[PaperFillLevel] = []
    for level in book.bids:
        if remaining <= 0 or level.price < worst_floor:
            break
        take = min(remaining, level.size)
        if level.price < average_floor:
            average_headroom = gross - average_floor * shares
            if average_headroom <= 0:
                break
            take = min(take, average_headroom / (average_floor - level.price))
        take = _floor_quantity(take)
        if take <= 0:
            break
        levels.append(PaperFillLevel(price=level.price, quantity=take))
        remaining = as_decimal(remaining - take)
        shares += take
        gross += take * level.price
        if take < level.size:
            break
    return tuple(levels)


def build_paper_execution_plan(
    intent: OrderIntent,
    book: OrderBookSnapshot,
    *,
    policy: CostPolicy,
    submitted_at: datetime,
    maximum_book_age: timedelta,
) -> PaperExecutionPlan:
    """Build one deterministic immediate execution plan without any external write call."""
    if submitted_at.tzinfo is None or submitted_at.utcoffset() is None:
        raise ValueError("paper submission time must be timezone-aware")
    submitted_at = submitted_at.astimezone(UTC)
    book.require_fresh(now=submitted_at, maximum_age=maximum_book_age)
    if intent.quantity < book.minimum_order_size:
        return _reject_plan(
            intent,
            book,
            submitted_at=submitted_at,
            reason="requested quantity is below the market minimum",
        )

    levels = (
        _buy_levels(intent, book, policy)
        if intent.side is Side.BUY
        else _sell_levels(intent, book, policy)
    )
    filled = as_decimal(sum((level.quantity for level in levels), _ZERO))
    if filled < book.minimum_order_size:
        return _reject_plan(
            intent,
            book,
            submitted_at=submitted_at,
            reason="contemporaneous executable depth is below the market minimum",
        )

    weighted = sum((level.quantity * level.price for level in levels), _ZERO)
    average = as_decimal(weighted / filled)
    worst = levels[-1].price
    if intent.side is Side.BUY and average > intent.limit_price:
        return _reject_plan(
            intent,
            book,
            submitted_at=submitted_at,
            reason="contemporaneous average execution price exceeds the approved limit",
        )
    if intent.side is Side.SELL and average < intent.limit_price:
        return _reject_plan(
            intent,
            book,
            submitted_at=submitted_at,
            reason="contemporaneous average execution price is below the exit limit",
        )

    gross = money_from_unit_price(
        average,
        filled,
        intent.fee_reserve.currency,
    )
    fee = _fee(gross, policy)
    if intent.side is Side.BUY and (gross + fee).amount > intent.cash_reservation.amount:
        return _reject_plan(
            intent,
            book,
            submitted_at=submitted_at,
            reason="contemporaneous fill plus simulated fees exceeds the durable reservation",
        )
    if intent.side is Side.SELL and fee.amount > gross.amount:
        return _reject_plan(
            intent,
            book,
            submitted_at=submitted_at,
            reason="simulated exit fee would exceed gross proceeds",
        )

    status = (
        PaperExecutionStatus.FULL_FILL
        if filled == intent.quantity
        else PaperExecutionStatus.PARTIAL_FILL
    )
    return PaperExecutionPlan(
        intent_id=intent.intent_id,
        status=status,
        backend_order_id=_backend_order_id(intent.intent_id),
        submitted_at=submitted_at,
        order_book_hash=book.book_hash,
        order_book_observed_at=book.observed_at,
        condition_id=str(book.condition_id),
        token_id=str(book.token_id),
        requested_quantity=intent.quantity,
        filled_quantity=filled,
        average_price=average,
        worst_price=worst,
        gross_value=gross,
        fee=fee,
        levels=levels,
        reason=(
            "paper order fully filled from contemporaneous displayed depth"
            if status is PaperExecutionStatus.FULL_FILL
            else "paper order partially filled; unavailable remainder cancelled"
        ),
    )


PlanLoader = Callable[[OrderIntentId], Mapping[str, object] | None]
BookProvider = Callable[[OrderIntent], OrderBookSnapshot]
PlanRecorder = Callable[[OrderIntentId, Mapping[str, object]], None]
Clock = Callable[[], datetime]


class PaperExecutionAdapter:
    """Backend-neutral adapter that turns durable paper plans into lifecycle events."""

    def __init__(
        self,
        *,
        policy: CostPolicy,
        maximum_book_age: timedelta,
        book_provider: BookProvider | None = None,
        plan_loader: PlanLoader | None = None,
        plan_recorder: PlanRecorder | None = None,
        clock: Clock = lambda: datetime.now(UTC),
    ) -> None:
        self._policy = policy
        self._maximum_book_age = maximum_book_age
        self._book_provider = book_provider
        self._plan_loader = plan_loader
        self._plan_recorder = plan_recorder
        self._clock = clock

    @property
    def backend_name(self) -> str:
        return "paper"

    def _load_plan(self, intent_id: OrderIntentId) -> PaperExecutionPlan | None:
        if self._plan_loader is None:
            return None
        payload = self._plan_loader(intent_id)
        return None if payload is None else PaperExecutionPlan.from_metadata(payload)

    def _plan(self, intent: OrderIntent) -> PaperExecutionPlan:
        existing = self._load_plan(intent.intent_id)
        if existing is not None:
            if existing.intent_id != intent.intent_id:
                raise ValueError("paper plan metadata belongs to another intent")
            return existing
        if self._book_provider is None:
            raise ValueError("paper execution requires a durable plan or an order-book provider")
        plan = build_paper_execution_plan(
            intent,
            self._book_provider(intent),
            policy=self._policy,
            submitted_at=self._clock(),
            maximum_book_age=self._maximum_book_age,
        )
        if self._plan_recorder is not None:
            self._plan_recorder(intent.intent_id, plan.metadata())
        return plan

    def events_for_plan(
        self, intent: OrderIntent, plan: PaperExecutionPlan
    ) -> tuple[LedgerEvent, ...]:
        if plan.intent_id != intent.intent_id:
            raise ValueError("paper execution plan does not match the order intent")
        submitted = OrderSubmitted(
            event_id=EventId(_stable_id("submitted", intent.intent_id)),
            occurred_at=plan.submitted_at,
            intent_id=intent.intent_id,
            backend_order_id=plan.backend_order_id,
        )
        if plan.status is PaperExecutionStatus.REJECTED:
            rejected = OrderRejected(
                event_id=EventId(_stable_id("rejected", intent.intent_id)),
                occurred_at=plan.submitted_at,
                intent_id=intent.intent_id,
                reason=plan.reason,
            )
            return submitted, rejected

        assert plan.average_price is not None
        acknowledged = OrderAcknowledged(
            event_id=EventId(_stable_id("acknowledged", intent.intent_id)),
            occurred_at=plan.submitted_at,
            intent_id=intent.intent_id,
        )
        fill = FillReceived(
            event_id=EventId(_stable_id("fill", intent.intent_id)),
            occurred_at=plan.submitted_at,
            intent_id=intent.intent_id,
            fill_id=FillId(_stable_id("fill_id", intent.intent_id)),
            quantity=plan.filled_quantity,
            price=plan.average_price,
            fee=plan.fee,
        )
        if plan.status is PaperExecutionStatus.FULL_FILL:
            return submitted, acknowledged, fill
        cancelled = OrderCancelled(
            event_id=EventId(_stable_id("remainder_cancelled", intent.intent_id)),
            occurred_at=plan.submitted_at,
            intent_id=intent.intent_id,
            reason=plan.reason,
        )
        return submitted, acknowledged, fill, cancelled

    def submit(self, intent: OrderIntent) -> tuple[LedgerEvent, ...]:
        return self.events_for_plan(intent, self._plan(intent))

    def cancel(self, order: OrderAggregate) -> tuple[LedgerEvent, ...]:
        if order.state.is_terminal:
            return ()
        return (
            OrderCancelled(
                event_id=EventId(_stable_id("manual_cancel", order.intent.intent_id)),
                occurred_at=self._clock(),
                intent_id=order.intent.intent_id,
                reason="paper order cancelled explicitly",
            ),
        )

    def reconcile(self, order: OrderAggregate) -> tuple[LedgerEvent, ...]:
        if order.state.is_terminal:
            return ()
        plan = self._load_plan(order.intent.intent_id)
        if plan is None:
            raise ValueError("paper recovery requires durable execution-plan metadata")
        planned = self.events_for_plan(order.intent, plan)
        if order.state is OrderState.CREATED:
            return planned
        if order.state is OrderState.SUBMITTED:
            return planned[1:]
        if order.state is OrderState.ACKNOWLEDGED:
            return planned[2:]
        if order.state is OrderState.PARTIALLY_FILLED:
            return planned[-1:] if plan.status is PaperExecutionStatus.PARTIAL_FILL else ()
        if order.state is OrderState.UNKNOWN:
            if plan.status is PaperExecutionStatus.REJECTED:
                return planned[-1:]
            return planned[2:]
        return ()
