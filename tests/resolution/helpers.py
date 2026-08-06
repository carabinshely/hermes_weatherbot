from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from weatherbot.domain import (
    AccountOpened,
    EventId,
    FillId,
    FillReceived,
    MarketId,
    Money,
    OrderIntent,
    OrderIntentCreated,
    OrderSubmitted,
    OutcomeId,
    Side,
)
from weatherbot.persistence import SQLiteEventStore

MARKET_ID = MarketId("1996416")
CONDITION_ID = "0x" + "ab" * 32
YES_TOKEN = "123456789012345678901234567890"
NO_TOKEN = "987654321098765432109876543210"
MARKET_DATE = date(2026, 4, 18)
MARKET_TIMEZONE = "America/Chicago"
DECLARED_SOURCE = "https://www.wunderground.com/history/daily/us/il/chicago/KMDW"
NOW = datetime(2026, 4, 19, 6, 0, tzinfo=UTC)


def seed_open_position(
    store: SQLiteEventStore,
    *,
    outcome_token: str = YES_TOKEN,
    bucket_key: str = "F:62:63",
    quantity: str = "10",
    price: str = "0.40",
) -> OrderIntent:
    store.append(
        AccountOpened(
            event_id=EventId("account-opened"),
            occurred_at=NOW - timedelta(hours=2),
            initial_cash=Money.of("100"),
        )
    )
    intent = OrderIntent.create(
        strategy_id="weather-v1",
        decision_id="decision-chicago-2026-04-18-62-63",
        market_id=MARKET_ID,
        outcome_id=OutcomeId(outcome_token),
        side=Side.BUY,
        quantity=Decimal(quantity),
        limit_price=Decimal(price),
        fee_reserve=Money.zero(),
        created_at=NOW - timedelta(hours=1, minutes=30),
    )
    store.commit_order_intent(
        OrderIntentCreated(
            event_id=EventId("intent-created"),
            occurred_at=intent.created_at,
            intent=intent,
        ),
        owner_id="paper-worker",
        metadata={
            "condition_id": CONDITION_ID,
            "market_date": MARKET_DATE.isoformat(),
            "market_timezone": MARKET_TIMEZONE,
            "bucket_key": bucket_key,
            "declared_resolution_source": DECLARED_SOURCE,
        },
    )
    store.append_many(
        (
            OrderSubmitted(
                event_id=EventId("order-submitted"),
                occurred_at=NOW - timedelta(hours=1),
                intent_id=intent.intent_id,
                backend_order_id="paper-order-1",
            ),
            FillReceived(
                event_id=EventId("fill-received"),
                occurred_at=NOW - timedelta(minutes=59),
                intent_id=intent.intent_id,
                fill_id=FillId("paper-fill-1"),
                quantity=Decimal(quantity),
                price=Decimal(price),
                fee=Money.zero(),
            ),
        )
    )
    return intent


def gamma_payload(
    *,
    yes: str = "1",
    no: str = "0",
    closed: bool = True,
    status: str = "resolved",
    question: str = (
        "Will the highest temperature in Chicago be between 62-63°F on April 18?"
    ),
    end_date: str = "2026-04-19T04:00:00Z",
    closed_time: str = "2026-04-19T05:00:00Z",
) -> dict[str, object]:
    return {
        "id": str(MARKET_ID),
        "question": question,
        "conditionId": CONDITION_ID,
        "outcomes": ["Yes", "No"],
        "clobTokenIds": [YES_TOKEN, NO_TOKEN],
        "outcomePrices": [yes, no],
        "description": "Daily high temperature market",
        "resolutionSource": DECLARED_SOURCE,
        "endDate": end_date,
        "active": not closed,
        "closed": closed,
        "closedTime": closed_time,
        "updatedAt": closed_time,
        "umaResolutionStatus": status,
    }


class StaticGammaTransport:
    def __init__(self, payload: Mapping[str, object]) -> None:
        self.payload = payload
        self.calls: list[str] = []

    def get_market(self, market_id: str) -> Mapping[str, object]:
        self.calls.append(market_id)
        return self.payload
