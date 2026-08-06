from __future__ import annotations

from decimal import Decimal

import pytest

from tests.domain.helpers import NOW, account_opened, buy_intent, event_id, sell_intent
from tests.domain.test_positions_and_settlement import opened_position_events
from weatherbot.domain import (
    FillId,
    FillReceived,
    InvariantViolation,
    LedgerState,
    Money,
    OrderIntent,
    OrderIntentCreated,
    OrderSubmitted,
    apply_event,
    as_decimal,
    replay,
)


def submitted_buy_state(
    *, cash: str = "100", fee_reserve: str = "0.10"
) -> tuple[LedgerState, OrderIntent]:
    intent = buy_intent(fee_reserve=fee_reserve)
    state = replay(
        (
            account_opened(cash),
            OrderIntentCreated(
                event_id=event_id("intent"),
                occurred_at=NOW,
                intent=intent,
            ),
            OrderSubmitted(
                event_id=event_id("submitted"),
                occurred_at=NOW,
                intent_id=intent.intent_id,
                backend_order_id="paper-buy-1",
            ),
        )
    )
    return state, intent


def test_binary_float_inputs_are_rejected_at_runtime() -> None:
    with pytest.raises(TypeError, match="floating-point"):
        as_decimal(0.1)


def test_buy_intent_cannot_reserve_more_than_available_cash() -> None:
    state = apply_event(LedgerState.empty(), account_opened("5"))
    intent = buy_intent()

    with pytest.raises(InvariantViolation, match="insufficient available cash"):
        apply_event(
            state,
            OrderIntentCreated(
                event_id=event_id("oversized-intent"),
                occurred_at=NOW,
                intent=intent,
            ),
        )

    assert state.cash == Money.of("5")
    assert state.reserved_cash == Money.zero()


def test_fill_fees_cannot_consume_remaining_quantity_reservation() -> None:
    state, intent = submitted_buy_state()

    with pytest.raises(InvariantViolation, match="fee reserve"):
        apply_event(
            state,
            FillReceived(
                event_id=event_id("excessive-fee-fill"),
                occurred_at=NOW,
                intent_id=intent.intent_id,
                fill_id=FillId("fill-excessive-fee"),
                quantity=Decimal("4"),
                price=Decimal("0.40"),
                fee=Money.of("0.20"),
            ),
        )

    assert state.cash == Money.of("100")
    assert state.reserved_cash == Money.of("5.10")


def test_buy_fill_above_limit_price_is_rejected() -> None:
    state, intent = submitted_buy_state()

    with pytest.raises(InvariantViolation, match="exceeds order limit"):
        apply_event(
            state,
            FillReceived(
                event_id=event_id("bad-buy-price"),
                occurred_at=NOW,
                intent_id=intent.intent_id,
                fill_id=FillId("fill-bad-buy-price"),
                quantity=Decimal("1"),
                price=Decimal("0.51"),
                fee=Money.zero(),
            ),
        )


def test_sell_intent_cannot_reserve_cash_fees() -> None:
    sell = sell_intent()

    with pytest.raises(InvariantViolation, match="sell orders"):
        OrderIntent.create(
            strategy_id=sell.strategy_id,
            decision_id=sell.decision_id,
            market_id=sell.market_id,
            outcome_id=sell.outcome_id,
            side=sell.side,
            quantity=sell.quantity,
            limit_price=sell.limit_price,
            fee_reserve=Money.of("0.10"),
            created_at=sell.created_at,
        )


def test_sell_intent_cannot_reserve_more_than_owned_position() -> None:
    state = replay(opened_position_events())
    sell = sell_intent(quantity="11")

    with pytest.raises(InvariantViolation, match="insufficient available position"):
        apply_event(
            state,
            OrderIntentCreated(
                event_id=event_id("oversized-sell"),
                occurred_at=NOW,
                intent=sell,
            ),
        )


def test_sell_fill_below_limit_price_is_rejected() -> None:
    state = replay(opened_position_events())
    sell = sell_intent(limit_price="0.60")
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
        OrderSubmitted(
            event_id=event_id("sell-submitted"),
            occurred_at=NOW,
            intent_id=sell.intent_id,
            backend_order_id="paper-sell-1",
        ),
    )

    with pytest.raises(InvariantViolation, match="below order limit"):
        apply_event(
            state,
            FillReceived(
                event_id=event_id("bad-sell-price"),
                occurred_at=NOW,
                intent_id=sell.intent_id,
                fill_id=FillId("fill-bad-sell-price"),
                quantity=Decimal("1"),
                price=Decimal("0.59"),
                fee=Money.zero(),
            ),
        )


@pytest.mark.parametrize(
    ("cash", "reserved_cash", "message"),
    [
        ("-1", "0", "cash must not be negative"),
        ("1", "2", "available_cash must not be negative"),
    ],
)
def test_ledger_rejects_negative_or_over_reserved_cash(
    cash: str,
    reserved_cash: str,
    message: str,
) -> None:
    state = LedgerState(
        currency="USDC",
        opened=True,
        cash=Money.of(cash),
        reserved_cash=Money.of(reserved_cash),
    )

    with pytest.raises(InvariantViolation, match=message):
        state.assert_invariants()
