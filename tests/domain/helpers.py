from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from weatherbot.domain import (
    AccountOpened,
    EventId,
    MarketId,
    Money,
    OrderIntent,
    OutcomeId,
    Side,
)

NOW = datetime(2026, 1, 2, 3, 4, tzinfo=UTC)
MARKET = MarketId("market-weather-nyc")
YES = OutcomeId("yes-token")


def event_id(value: str) -> EventId:
    return EventId(value)


def account_opened(cash: str = "100") -> AccountOpened:
    return AccountOpened(
        event_id=event_id("account-opened"),
        occurred_at=NOW,
        initial_cash=Money.of(cash),
    )


def buy_intent(
    *,
    decision_id: str = "decision-1",
    quantity: str = "10",
    limit_price: str = "0.50",
    fee_reserve: str = "0.10",
) -> OrderIntent:
    return OrderIntent.create(
        strategy_id="weather-v1",
        decision_id=decision_id,
        market_id=MARKET,
        outcome_id=YES,
        side=Side.BUY,
        quantity=Decimal(quantity),
        limit_price=Decimal(limit_price),
        fee_reserve=Money.of(fee_reserve),
        created_at=NOW,
    )


def sell_intent(
    *,
    decision_id: str = "decision-sell-1",
    quantity: str = "4",
    limit_price: str = "0.60",
) -> OrderIntent:
    return OrderIntent.create(
        strategy_id="weather-v1",
        decision_id=decision_id,
        market_id=MARKET,
        outcome_id=YES,
        side=Side.SELL,
        quantity=Decimal(quantity),
        limit_price=Decimal(limit_price),
        fee_reserve=Money.zero(),
        created_at=NOW,
    )
