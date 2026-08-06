from __future__ import annotations

from decimal import Decimal

import pytest

from tests.domain.helpers import NOW, account_opened, buy_intent, event_id
from weatherbot.domain import (
    DuplicateEventConflict,
    FillId,
    FillReceived,
    InvalidTransition,
    InvariantViolation,
    LedgerState,
    Money,
    OrderAcknowledged,
    OrderCancelled,
    OrderIntentCreated,
    OrderOutcomeUnknown,
    OrderRejected,
    OrderState,
    OrderSubmitted,
    allowed_transitions,
    apply_event,
    replay,
)
from weatherbot.domain.model import require_transition


@pytest.mark.parametrize("source", list(OrderState))
@pytest.mark.parametrize("target", list(OrderState))
def test_transition_matrix_is_explicit_and_fail_closed(
    source: OrderState,
    target: OrderState,
) -> None:
    if target in allowed_transitions(source):
        require_transition(source, target)
    else:
        with pytest.raises(InvalidTransition):
            require_transition(source, target)


def test_intent_identifier_is_stable_across_reconstruction() -> None:
    first = buy_intent()
    reconstructed = buy_intent()
    changed_decision = buy_intent(decision_id="decision-2")

    assert first.intent_id == reconstructed.intent_id
    assert first.intent_id != changed_decision.intent_id


def test_partial_then_full_fill_reconciles_cash_reservation_and_position() -> None:
    intent = buy_intent()
    events = [
        account_opened(),
        OrderIntentCreated(event_id=event_id("intent"), occurred_at=NOW, intent=intent),
        OrderSubmitted(
            event_id=event_id("submitted"),
            occurred_at=NOW,
            intent_id=intent.intent_id,
            backend_order_id="paper-1",
        ),
        OrderAcknowledged(
            event_id=event_id("acknowledged"),
            occurred_at=NOW,
            intent_id=intent.intent_id,
        ),
        FillReceived(
            event_id=event_id("fill-event-1"),
            occurred_at=NOW,
            intent_id=intent.intent_id,
            fill_id=FillId("fill-1"),
            quantity=Decimal("4"),
            price=Decimal("0.40"),
            fee=Money.of("0.02"),
        ),
    ]

    partial = replay(events)
    order = partial.orders[intent.intent_id]
    position = next(iter(partial.positions.values()))

    assert order.state is OrderState.PARTIALLY_FILLED
    assert order.filled_quantity == Decimal("4.000000")
    assert partial.cash == Money.of("98.38")
    assert partial.reserved_cash == Money.of("3.08")
    assert position.quantity == Decimal("4.000000")
    assert position.cost_basis == Money.of("1.62")

    completed = apply_event(
        partial,
        FillReceived(
            event_id=event_id("fill-event-2"),
            occurred_at=NOW,
            intent_id=intent.intent_id,
            fill_id=FillId("fill-2"),
            quantity=Decimal("6"),
            price=Decimal("0.45"),
            fee=Money.of("0.03"),
        ),
    )
    completed_order = completed.orders[intent.intent_id]
    completed_position = next(iter(completed.positions.values()))

    assert completed_order.state is OrderState.FILLED
    assert completed_order.reserved_cash == Money.zero()
    assert completed.reserved_cash == Money.zero()
    assert completed.cash == Money.of("95.65")
    assert completed.available_cash == completed.cash
    assert completed_position.quantity == Decimal("10.000000")
    assert completed_position.cost_basis == Money.of("4.35")


def test_partial_fill_then_cancellation_releases_only_remaining_reservation() -> None:
    intent = buy_intent()
    events = [
        account_opened(),
        OrderIntentCreated(event_id=event_id("intent"), occurred_at=NOW, intent=intent),
        OrderSubmitted(
            event_id=event_id("submitted"),
            occurred_at=NOW,
            intent_id=intent.intent_id,
            backend_order_id="paper-1",
        ),
        FillReceived(
            event_id=event_id("fill-event-1"),
            occurred_at=NOW,
            intent_id=intent.intent_id,
            fill_id=FillId("fill-1"),
            quantity=Decimal("4"),
            price=Decimal("0.40"),
            fee=Money.of("0.02"),
        ),
        OrderCancelled(
            event_id=event_id("cancelled"),
            occurred_at=NOW,
            intent_id=intent.intent_id,
            reason="remaining quantity cancelled",
        ),
    ]

    state = replay(events)
    order = state.orders[intent.intent_id]

    assert order.state is OrderState.CANCELLED
    assert state.cash == Money.of("98.38")
    assert state.reserved_cash == Money.zero()
    assert next(iter(state.positions.values())).quantity == Decimal("4.000000")


def test_rejection_releases_all_reserved_cash() -> None:
    intent = buy_intent()
    state = replay(
        [
            account_opened(),
            OrderIntentCreated(event_id=event_id("intent"), occurred_at=NOW, intent=intent),
            OrderSubmitted(
                event_id=event_id("submitted"),
                occurred_at=NOW,
                intent_id=intent.intent_id,
                backend_order_id="paper-1",
            ),
            OrderRejected(
                event_id=event_id("rejected"),
                occurred_at=NOW,
                intent_id=intent.intent_id,
                reason="backend rejected order",
            ),
        ]
    )

    assert state.orders[intent.intent_id].state is OrderState.REJECTED
    assert state.cash == Money.of("100")
    assert state.reserved_cash == Money.zero()


def test_unknown_outcome_retains_reservation_until_reconciled() -> None:
    intent = buy_intent()
    unknown = replay(
        [
            account_opened(),
            OrderIntentCreated(event_id=event_id("intent"), occurred_at=NOW, intent=intent),
            OrderSubmitted(
                event_id=event_id("submitted"),
                occurred_at=NOW,
                intent_id=intent.intent_id,
                backend_order_id="live-1",
            ),
            OrderOutcomeUnknown(
                event_id=event_id("unknown"),
                occurred_at=NOW,
                intent_id=intent.intent_id,
                reason="submission timed out",
            ),
        ]
    )

    assert unknown.orders[intent.intent_id].state is OrderState.UNKNOWN
    assert unknown.reserved_cash == Money.of("5.10")

    reconciled = apply_event(
        unknown,
        OrderCancelled(
            event_id=event_id("reconciled-cancel"),
            occurred_at=NOW,
            intent_id=intent.intent_id,
            reason="venue confirms no live order",
        ),
    )
    assert reconciled.reserved_cash == Money.zero()


def test_invalid_transition_does_not_mutate_prior_state() -> None:
    intent = buy_intent()
    state = replay(
        [
            account_opened(),
            OrderIntentCreated(event_id=event_id("intent"), occurred_at=NOW, intent=intent),
        ]
    )

    with pytest.raises(InvalidTransition):
        apply_event(
            state,
            OrderAcknowledged(
                event_id=event_id("bad-ack"),
                occurred_at=NOW,
                intent_id=intent.intent_id,
            ),
        )

    assert state.orders[intent.intent_id].state is OrderState.CREATED
    assert state.reserved_cash == Money.of("5.10")


def test_overfill_fails_closed() -> None:
    intent = buy_intent()
    state = replay(
        [
            account_opened(),
            OrderIntentCreated(event_id=event_id("intent"), occurred_at=NOW, intent=intent),
            OrderSubmitted(
                event_id=event_id("submitted"),
                occurred_at=NOW,
                intent_id=intent.intent_id,
                backend_order_id="paper-1",
            ),
        ]
    )

    with pytest.raises(InvariantViolation, match="exceed order quantity"):
        apply_event(
            state,
            FillReceived(
                event_id=event_id("overfill"),
                occurred_at=NOW,
                intent_id=intent.intent_id,
                fill_id=FillId("fill-over"),
                quantity=Decimal("11"),
                price=Decimal("0.40"),
                fee=Money.zero(),
            ),
        )


def test_duplicate_event_and_fill_delivery_are_idempotent() -> None:
    intent = buy_intent()
    submitted = replay(
        [
            account_opened(),
            OrderIntentCreated(event_id=event_id("intent"), occurred_at=NOW, intent=intent),
            OrderSubmitted(
                event_id=event_id("submitted"),
                occurred_at=NOW,
                intent_id=intent.intent_id,
                backend_order_id="paper-1",
            ),
        ]
    )
    fill = FillReceived(
        event_id=event_id("fill-event-1"),
        occurred_at=NOW,
        intent_id=intent.intent_id,
        fill_id=FillId("fill-1"),
        quantity=Decimal("4"),
        price=Decimal("0.40"),
        fee=Money.of("0.02"),
    )
    once = apply_event(submitted, fill)

    assert apply_event(once, fill) is once

    redelivered = apply_event(
        once,
        FillReceived(
            event_id=event_id("fill-event-redelivery"),
            occurred_at=NOW,
            intent_id=intent.intent_id,
            fill_id=FillId("fill-1"),
            quantity=Decimal("4"),
            price=Decimal("0.40"),
            fee=Money.of("0.02"),
        ),
    )
    assert redelivered.cash == once.cash
    assert redelivered.positions == once.positions
    assert len(redelivered.event_fingerprints) == len(once.event_fingerprints) + 1


def test_conflicting_duplicate_fill_is_rejected() -> None:
    intent = buy_intent()
    state = replay(
        [
            account_opened(),
            OrderIntentCreated(event_id=event_id("intent"), occurred_at=NOW, intent=intent),
            OrderSubmitted(
                event_id=event_id("submitted"),
                occurred_at=NOW,
                intent_id=intent.intent_id,
                backend_order_id="paper-1",
            ),
            FillReceived(
                event_id=event_id("fill-event-1"),
                occurred_at=NOW,
                intent_id=intent.intent_id,
                fill_id=FillId("fill-1"),
                quantity=Decimal("4"),
                price=Decimal("0.40"),
                fee=Money.of("0.02"),
            ),
        ]
    )

    with pytest.raises(DuplicateEventConflict):
        apply_event(
            state,
            FillReceived(
                event_id=event_id("conflicting-redelivery"),
                occurred_at=NOW,
                intent_id=intent.intent_id,
                fill_id=FillId("fill-1"),
                quantity=Decimal("5"),
                price=Decimal("0.40"),
                fee=Money.of("0.02"),
            ),
        )


def test_replay_after_restart_reconstructs_identical_state() -> None:
    intent = buy_intent()
    events = [
        account_opened(),
        OrderIntentCreated(event_id=event_id("intent"), occurred_at=NOW, intent=intent),
        OrderSubmitted(
            event_id=event_id("submitted"),
            occurred_at=NOW,
            intent_id=intent.intent_id,
            backend_order_id="paper-1",
        ),
        FillReceived(
            event_id=event_id("fill-event-1"),
            occurred_at=NOW,
            intent_id=intent.intent_id,
            fill_id=FillId("fill-1"),
            quantity=Decimal("4"),
            price=Decimal("0.40"),
            fee=Money.of("0.02"),
        ),
    ]

    uninterrupted = replay(events)
    restarted = LedgerState.empty()
    for event in events:
        restarted = apply_event(restarted, event)

    assert restarted == uninterrupted
