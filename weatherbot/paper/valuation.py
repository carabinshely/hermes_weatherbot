"""Conservative bid-side liquidation valuation and paper-account status."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from weatherbot.domain import (
    AccountOpened,
    LedgerEvent,
    LedgerState,
    Money,
    PortfolioValuation,
    PortfolioValuationRecorded,
    PositionKey,
    PositionStatus,
    PositionValuation,
    Side,
)
from weatherbot.markets import OrderBookSnapshot
from weatherbot.paper.model import PaperStatus
from weatherbot.quoting import CostPolicy

_ZERO = Decimal("0")


def _liquidation_value(
    *,
    quantity: Decimal,
    book: OrderBookSnapshot,
    policy: CostPolicy,
    currency: str,
) -> Money:
    remaining = quantity
    gross = _ZERO
    for level in book.bids:
        take = min(remaining, level.size)
        if take > 0:
            gross += take * level.price
            remaining -= take
        if remaining <= 0:
            break
    if gross <= 0:
        return Money.zero(currency)
    gross_money = Money.of(gross, currency)
    fee = gross_money.scale(policy.platform_fee_rate) + Money.of(
        policy.transaction_cost,
        currency,
    )
    if fee.amount >= gross_money.amount:
        return Money.zero(currency)
    return gross_money - fee


def build_paper_valuation(
    state: LedgerState,
    books: Mapping[PositionKey, OrderBookSnapshot],
    *,
    policy: CostPolicy,
    observed_at: datetime,
    maximum_book_age: timedelta,
) -> PortfolioValuation:
    """Mark every open position at size-aware executable bids, net of simulated exit fees.

    Any unexecutable quantity contributes zero liquidation value instead of being marked at
    midpoint, last trade, or best bid independently of size.
    """
    if observed_at.tzinfo is None or observed_at.utcoffset() is None:
        raise ValueError("paper valuation time must be timezone-aware")
    observed_at = observed_at.astimezone(UTC)
    state.assert_invariants()
    marks: list[PositionValuation] = []
    for key, position in sorted(state.positions.items(), key=lambda item: (str(item[0][0]), str(item[0][1]))):
        if position.status is not PositionStatus.OPEN or position.quantity <= 0:
            continue
        try:
            book = books[key]
        except KeyError as exc:
            raise ValueError(f"paper valuation is missing a book for {key[0]}/{key[1]}") from exc
        book.require_fresh(now=observed_at, maximum_age=maximum_book_age)
        marks.append(
            PositionValuation(
                market_id=position.market_id,
                outcome_id=position.outcome_id,
                quantity=position.quantity,
                liquidation_value=_liquidation_value(
                    quantity=position.quantity,
                    book=book,
                    policy=policy,
                    currency=state.currency,
                ),
                observed_at=book.observed_at,
            )
        )
    market_value = Money.zero(state.currency)
    for mark in marks:
        market_value += mark.liquidation_value
    return PortfolioValuation(
        positions=tuple(marks),
        equity=state.cash + market_value,
        assembled_at=observed_at,
        source="paper:bid-side-size-aware-net-exit-fees",
    )


def paper_status(
    state: LedgerState,
    events: tuple[LedgerEvent, ...],
    valuation: PortfolioValuation,
) -> PaperStatus:
    """Build a complete PAPER status report from durable state plus one fresh valuation."""
    state.assert_invariants()
    initial_cash: Money | None = None
    high_water_amount: Decimal | None = None
    for event in events:
        if isinstance(event, AccountOpened) and initial_cash is None:
            initial_cash = event.initial_cash
            high_water_amount = event.initial_cash.amount
        elif isinstance(event, PortfolioValuationRecorded):
            if event.valuation.equity.currency != state.currency:
                raise ValueError("historical paper valuation uses another currency")
            if high_water_amount is None or event.valuation.equity.amount > high_water_amount:
                high_water_amount = event.valuation.equity.amount
    if initial_cash is None or high_water_amount is None:
        raise ValueError("paper status requires an initialized ledger")
    if valuation.equity.currency != state.currency:
        raise ValueError("paper status valuation uses another currency")

    market_value = Money.zero(state.currency)
    for mark in valuation.positions:
        market_value += mark.liquidation_value
    realized = Money.zero(state.currency)
    open_cost_basis = Money.zero(state.currency)
    open_positions = 0
    for position in state.positions.values():
        realized += position.realized_pnl
        if position.status is PositionStatus.OPEN and position.quantity > 0:
            open_cost_basis += position.cost_basis
            open_positions += 1
    fees = Money.zero(state.currency)
    for order in state.orders.values():
        fees += order.fees

    active_buy_reservations = Money.zero(state.currency)
    for order in state.orders.values():
        if order.intent.side is Side.BUY and not order.state.is_terminal:
            active_buy_reservations += order.reserved_cash
    exposure = open_cost_basis + active_buy_reservations
    unrealized = market_value - open_cost_basis
    high_water_amount = max(high_water_amount, valuation.equity.amount)
    high_water = Money.of(high_water_amount, state.currency)
    drawdown = Money.of(max(_ZERO, high_water.amount - valuation.equity.amount), state.currency)
    return PaperStatus(
        starting_cash=initial_cash,
        cash=state.cash,
        reserved_cash=state.reserved_cash,
        available_cash=state.available_cash,
        market_value=market_value,
        realized_pnl=realized,
        unrealized_pnl=unrealized,
        fees=fees,
        exposure=exposure,
        equity=valuation.equity,
        high_water_mark=high_water,
        drawdown=drawdown,
        open_positions=open_positions,
    )
