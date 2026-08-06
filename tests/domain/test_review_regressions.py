from __future__ import annotations

from decimal import Decimal
from typing import cast

import pytest

from tests.domain.helpers import NOW, account_opened, buy_intent, event_id, sell_intent
from weatherbot.domain import (
    DuplicateEventConflict,
    FillId,
    FillReceived,
    Money,
    OrderCancelled,
    OrderIntentCreated,
    OrderRejected,
    OrderState,
    OrderSubmitted,
    apply_event,
    replay,
)


def test_event_fingerprint_distinguishes_event_classes() -> None:
    intent = buy_intent()
    shared_event_id = event_id("conflicting-terminal-event")
    state = replay(
        (
            account_opened(),
            OrderIntentCreated(
                event_id=event_id("intent"),
                occurred_at=NOW,
                intent=intent,
            ),
            OrderSubmitted(
                event_id=event_id("submitted"),
                occurred_at=NOW,
                intent_id=intent.intent_id,
                backend_order_id="paper-1",
            ),
        )
    )
    cancelled = apply_event(
        state,
        OrderCancelled(
            event_id=shared_event_id,
            occurred_at=NOW,
            intent_id=intent.intent_id,
            reason="terminal reason",
        ),
    )

    with pytest.raises(DuplicateEventConflict, match="reused"):
        apply_event(
            cancelled,
            OrderRejected(
                event_id=shared_event_id,
                occurred_at=NOW,
                intent_id=intent.intent_id,
                reason="terminal reason",
            ),
        )


def test_money_scale_preserves_ratio_precision_until_final_quantization() -> None:
    one_third = Decimal("1") / Decimal("3")

    assert Money.of("3").scale(one_third) == Money.of("1")


def test_partial_fill_rounding_reserves_from_remaining_quantity() -> None:
    intent = buy_intent(
        quantity="20.000038",
        limit_price="0.027",
        fee_reserve="0",
    )
    state = replay(
        (
            account_opened(),
            OrderIntentCreated(
                event_id=event_id("intent"),
                occurred_at=NOW,
                intent=intent,
            ),
            OrderSubmitted(
                event_id=event_id("submitted"),
                occurred_at=NOW,
                intent_id=intent.intent_id,
                backend_order_id="paper-rounding-1",
            ),
            FillReceived(
                event_id=event_id("fill-event-1"),
                occurred_at=NOW,
                intent_id=intent.intent_id,
                fill_id=FillId("fill-1"),
                quantity=Decimal("10.000019"),
                price=Decimal("0.027"),
                fee=Money.zero(),
            ),
        )
    )

    assert state.reserved_cash == Money.of("0.270001")

    completed = apply_event(
        state,
        FillReceived(
            event_id=event_id("fill-event-2"),
            occurred_at=NOW,
            intent_id=intent.intent_id,
            fill_id=FillId("fill-2"),
            quantity=Decimal("10.000019"),
            price=Decimal("0.027"),
            fee=Money.zero(),
        ),
    )

    assert completed.orders[intent.intent_id].state is OrderState.FILLED
    assert completed.reserved_cash == Money.zero()


def test_fill_fingerprint_mapping_is_immutable() -> None:
    intent = buy_intent()
    state = replay(
        (
            account_opened(),
            OrderIntentCreated(
                event_id=event_id("intent"),
                occurred_at=NOW,
                intent=intent,
            ),
            OrderSubmitted(
                event_id=event_id("submitted"),
                occurred_at=NOW,
                intent_id=intent.intent_id,
                backend_order_id="paper-1",
            ),
            FillReceived(
                event_id=event_id("fill-event"),
                occurred_at=NOW,
                intent_id=intent.intent_id,
                fill_id=FillId("fill-1"),
                quantity=Decimal("1"),
                price=Decimal("0.40"),
                fee=Money.zero(),
            ),
        )
    )
    mutable_view = cast(
        dict[FillId, str],
        state.orders[intent.intent_id].fill_fingerprints,
    )

    with pytest.raises(TypeError):
        mutable_view[FillId("injected-fill")] = "forged"


def test_partial_sell_uses_precise_cost_basis_ratio() -> None:
    buy = buy_intent(quantity="3", limit_price="1", fee_reserve="0")
    state = replay(
        (
            account_opened(),
            OrderIntentCreated(
                event_id=event_id("buy-intent"),
                occurred_at=NOW,
                intent=buy,
            ),
            OrderSubmitted(
                event_id=event_id("buy-submitted"),
                occurred_at=NOW,
                intent_id=buy.intent_id,
                backend_order_id="paper-buy-1",
            ),
            FillReceived(
                event_id=event_id("buy-fill-event"),
                occurred_at=NOW,
                intent_id=buy.intent_id,
                fill_id=FillId("buy-fill"),
                quantity=Decimal("3"),
                price=Decimal("1"),
                fee=Money.zero(),
            ),
        )
    )
    sell = sell_intent(quantity="1", limit_price="1")
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
    state = apply_event(
        state,
        FillReceived(
            event_id=event_id("sell-fill-event"),
            occurred_at=NOW,
            intent_id=sell.intent_id,
            fill_id=FillId("sell-fill"),
            quantity=Decimal("1"),
            price=Decimal("1"),
            fee=Money.zero(),
        ),
    )
    position = next(iter(state.positions.values()))

    assert position.quantity == Decimal("2.000000")
    assert position.cost_basis == Money.of("2")
    assert position.realized_pnl == Money.zero()
