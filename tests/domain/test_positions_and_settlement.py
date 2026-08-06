from __future__ import annotations

from decimal import Decimal

import pytest

from tests.domain.helpers import (
    MARKET,
    NOW,
    YES,
    account_opened,
    buy_intent,
    event_id,
    sell_intent,
)
from weatherbot.domain import (
    AggregateNotFound,
    EventId,
    FillId,
    FillReceived,
    InvariantViolation,
    LedgerEvent,
    MarketResolution,
    MarketResolved,
    Money,
    OrderCancelled,
    OrderIntentCreated,
    OrderState,
    OrderSubmitted,
    OutcomeId,
    OutcomePayout,
    PositionSettled,
    PositionStatus,
    apply_event,
    replay,
)

NO = OutcomeId("no-token")


def opened_position_events() -> tuple[LedgerEvent, ...]:
    intent = buy_intent()
    return (
        account_opened(),
        OrderIntentCreated(event_id=event_id("intent"), occurred_at=NOW, intent=intent),
        OrderSubmitted(
            event_id=event_id("submitted"),
            occurred_at=NOW,
            intent_id=intent.intent_id,
            backend_order_id="paper-buy-1",
        ),
        FillReceived(
            event_id=event_id("fill-buy"),
            occurred_at=NOW,
            intent_id=intent.intent_id,
            fill_id=FillId("buy-fill-1"),
            quantity=Decimal("10"),
            price=Decimal("0.40"),
            fee=Money.of("0.02"),
        ),
    )


def test_sell_order_reserves_position_and_realizes_profit_on_fill() -> None:
    state = replay(opened_position_events())
    sell = sell_intent()
    reserved = apply_event(
        state,
        OrderIntentCreated(
            event_id=event_id("sell-intent"),
            occurred_at=NOW,
            intent=sell,
        ),
    )
    position = next(iter(reserved.positions.values()))
    assert position.quantity == Decimal("10.000000")
    assert position.reserved_quantity == Decimal("4.000000")
    assert position.available_quantity == Decimal("6.000000")

    submitted = apply_event(
        reserved,
        OrderSubmitted(
            event_id=event_id("sell-submitted"),
            occurred_at=NOW,
            intent_id=sell.intent_id,
            backend_order_id="paper-sell-1",
        ),
    )
    filled = apply_event(
        submitted,
        FillReceived(
            event_id=event_id("sell-fill-event"),
            occurred_at=NOW,
            intent_id=sell.intent_id,
            fill_id=FillId("sell-fill-1"),
            quantity=Decimal("4"),
            price=Decimal("0.70"),
            fee=Money.of("0.02"),
        ),
    )
    position = next(iter(filled.positions.values()))

    assert filled.orders[sell.intent_id].state is OrderState.FILLED
    assert position.quantity == Decimal("6.000000")
    assert position.reserved_quantity == Decimal("0.000000")
    assert position.cost_basis == Money.of("2.412")
    assert position.realized_pnl == Money.of("1.172")
    assert filled.cash == Money.of("98.76")


def test_cancelled_sell_order_releases_position_reservation() -> None:
    state = replay(opened_position_events())
    sell = sell_intent()
    state = apply_event(
        state,
        OrderIntentCreated(
            event_id=event_id("sell-intent"),
            occurred_at=NOW,
            intent=sell,
        ),
    )
    state = apply_event(
        state,
        OrderCancelled(
            event_id=event_id("sell-cancelled"),
            occurred_at=NOW,
            intent_id=sell.intent_id,
            reason="strategy withdrew intent before submission",
        ),
    )

    position = next(iter(state.positions.values()))
    assert position.reserved_quantity == Decimal("0.000000")
    assert position.available_quantity == position.quantity


@pytest.mark.parametrize(
    ("yes_payout", "settlement_fee", "expected_cash", "expected_pnl"),
    [
        ("1", "0.10", "105.88", "5.88"),
        ("0", "0", "95.98", "-4.02"),
        ("0.5", "0.05", "100.93", "0.93"),
    ],
)
def test_winning_losing_and_voided_settlement_derive_cash_and_pnl(
    yes_payout: str,
    settlement_fee: str,
    expected_cash: str,
    expected_pnl: str,
) -> None:
    state = replay(opened_position_events())
    resolution = MarketResolution(
        market_id=MARKET,
        payouts=(
            OutcomePayout(outcome_id=YES, payout=Decimal(yes_payout)),
            OutcomePayout(
                outcome_id=NO,
                payout=Decimal("1") - Decimal(yes_payout),
            ),
        ),
        resolved_at=NOW,
    )
    state = apply_event(
        state,
        MarketResolved(
            event_id=EventId(f"resolution-{yes_payout}"),
            occurred_at=NOW,
            resolution=resolution,
        ),
    )
    settled = apply_event(
        state,
        PositionSettled(
            event_id=EventId(f"settlement-{yes_payout}"),
            occurred_at=NOW,
            market_id=MARKET,
            outcome_id=YES,
            fee=Money.of(settlement_fee),
        ),
    )
    position = next(iter(settled.positions.values()))

    assert settled.cash == Money.of(expected_cash)
    assert position.status is PositionStatus.SETTLED
    assert position.quantity == Decimal("0.000000")
    assert position.cost_basis == Money.zero()
    assert position.realized_pnl == Money.of(expected_pnl)
    assert position.settlement_payout == Decimal(yes_payout).quantize(Decimal("0.000001"))


def test_position_cannot_settle_before_resolution() -> None:
    state = replay(opened_position_events())

    with pytest.raises(AggregateNotFound, match="resolution"):
        apply_event(
            state,
            PositionSettled(
                event_id=event_id("early-settlement"),
                occurred_at=NOW,
                market_id=MARKET,
                outcome_id=YES,
                fee=Money.zero(),
            ),
        )


def test_position_cannot_settle_while_sell_quantity_is_reserved() -> None:
    state = replay(opened_position_events())
    sell = sell_intent()
    state = apply_event(
        state,
        OrderIntentCreated(
            event_id=event_id("sell-intent"),
            occurred_at=NOW,
            intent=sell,
        ),
    )
    state = apply_event(
        state,
        MarketResolved(
            event_id=event_id("resolution"),
            occurred_at=NOW,
            resolution=MarketResolution(
                market_id=MARKET,
                payouts=(OutcomePayout(outcome_id=YES, payout=Decimal("1")),),
                resolved_at=NOW,
            ),
        ),
    )

    with pytest.raises(InvariantViolation, match="reserved"):
        apply_event(
            state,
            PositionSettled(
                event_id=event_id("settlement"),
                occurred_at=NOW,
                market_id=MARKET,
                outcome_id=YES,
                fee=Money.zero(),
            ),
        )
