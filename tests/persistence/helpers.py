from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from tests.domain.helpers import MARKET, NOW, YES, account_opened, buy_intent, event_id
from weatherbot.domain import (
    FillId,
    FillReceived,
    MarketResolution,
    MarketResolved,
    Money,
    OrderAcknowledged,
    OrderCancelled,
    OrderIntentCreated,
    OrderOutcomeUnknown,
    OrderRejected,
    OrderSubmitted,
    OutcomeId,
    OutcomePayout,
    PositionSettled,
)

NO = OutcomeId("no-token")
LATER = datetime(2026, 1, 2, 4, 5, tzinfo=UTC)


def intent_created(
    *,
    event_name: str = "intent-created",
    decision_id: str = "decision-1",
    quantity: str = "10",
    limit_price: str = "0.50",
    fee_reserve: str = "0.10",
) -> OrderIntentCreated:
    return OrderIntentCreated(
        event_id=event_id(event_name),
        occurred_at=NOW,
        intent=buy_intent(
            decision_id=decision_id,
            quantity=quantity,
            limit_price=limit_price,
            fee_reserve=fee_reserve,
        ),
    )


def submitted(intent: OrderIntentCreated, *, event_name: str = "submitted") -> OrderSubmitted:
    return OrderSubmitted(
        event_id=event_id(event_name),
        occurred_at=NOW,
        intent_id=intent.intent.intent_id,
        backend_order_id=f"backend-{intent.intent.decision_id}",
    )


def acknowledged(
    intent: OrderIntentCreated,
    *,
    event_name: str = "acknowledged",
) -> OrderAcknowledged:
    return OrderAcknowledged(
        event_id=event_id(event_name),
        occurred_at=NOW,
        intent_id=intent.intent.intent_id,
    )


def fill(
    intent: OrderIntentCreated,
    *,
    event_name: str = "fill",
    fill_name: str = "fill-1",
    quantity: str = "10",
    price: str = "0.40",
    fee: str = "0.02",
) -> FillReceived:
    return FillReceived(
        event_id=event_id(event_name),
        occurred_at=NOW,
        intent_id=intent.intent.intent_id,
        fill_id=FillId(fill_name),
        quantity=Decimal(quantity),
        price=Decimal(price),
        fee=Money.of(fee),
    )


def rejected(intent: OrderIntentCreated) -> OrderRejected:
    return OrderRejected(
        event_id=event_id("rejected"),
        occurred_at=NOW,
        intent_id=intent.intent.intent_id,
        reason="backend rejected order",
    )


def cancelled(intent: OrderIntentCreated) -> OrderCancelled:
    return OrderCancelled(
        event_id=event_id("cancelled"),
        occurred_at=NOW,
        intent_id=intent.intent.intent_id,
        reason="operator cancelled order",
    )


def unknown(intent: OrderIntentCreated) -> OrderOutcomeUnknown:
    return OrderOutcomeUnknown(
        event_id=event_id("unknown"),
        occurred_at=NOW,
        intent_id=intent.intent.intent_id,
        reason="submission response timed out",
    )


def market_resolved() -> MarketResolved:
    return MarketResolved(
        event_id=event_id("market-resolved"),
        occurred_at=LATER,
        resolution=MarketResolution(
            market_id=MARKET,
            payouts=(
                OutcomePayout(outcome_id=YES, payout=Decimal("1")),
                OutcomePayout(outcome_id=NO, payout=Decimal("0")),
            ),
            resolved_at=LATER,
        ),
    )


def position_settled() -> PositionSettled:
    return PositionSettled(
        event_id=event_id("position-settled"),
        occurred_at=LATER,
        market_id=MARKET,
        outcome_id=YES,
        fee=Money.of("0.05"),
    )


__all__ = [
    "acknowledged",
    "account_opened",
    "cancelled",
    "fill",
    "intent_created",
    "market_resolved",
    "position_settled",
    "rejected",
    "submitted",
    "unknown",
]
