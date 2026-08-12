from __future__ import annotations

from collections.abc import Mapping
from datetime import date, datetime
from decimal import Decimal

from tests.quoting.helpers import (
    CONDITION,
    NOW,
    TOKEN,
    cost_policy,
    event_snapshot,
    freshness_policy,
    order_book,
    order_book_payload,
    weather_snapshot,
)
from tests.risk.helpers import policy as sizing_policy
from tests.risk.portfolio_helpers import policy as portfolio_policy
from weatherbot.domain import MarketId, OutcomeId, PositionKey, RiskScope
from weatherbot.markets import OrderBookSnapshot, parse_order_book
from weatherbot.paper import PaperEntryRequest

MARKET = MarketId("paper-weather-market")
OUTCOME = OutcomeId(str(TOKEN))
OTHER_OUTCOME = OutcomeId("98765432109876543210")


def scope() -> RiskScope:
    return RiskScope(
        market_id=MARKET,
        outcome_id=OUTCOME,
        event_id="paper-weather-event",
        city_key="chicago",
        market_date=date(2026, 8, 6),
        correlation_groups=("weather-system:midwest",),
    )


def paper_book(
    *,
    observed_at: datetime | None = None,
    first_ask: str = "0.40",
    second_ask: str = "0.42",
    first_ask_size: str = "100",
    second_ask_size: str = "100",
    first_bid: str = "0.34",
    second_bid: str = "0.33",
    first_bid_size: str = "100",
    second_bid_size: str = "100",
    book_hash: str = "paper-book",
) -> OrderBookSnapshot:
    payload = order_book_payload(
        observed_at=observed_at,
        first_ask=first_ask,
        second_ask=second_ask,
        first_size=first_ask_size,
        second_size=second_ask_size,
        book_hash=book_hash,
    )
    payload["bids"] = [
        {"price": first_bid, "size": first_bid_size},
        {"price": second_bid, "size": second_bid_size},
    ]
    return parse_order_book(
        payload,
        expected_condition_id=CONDITION,
        expected_token_id=TOKEN,
    )


def entry_request(
    *,
    decision_id: str = "paper-decision-1",
    probability: str = "0.65",
    decision_book: OrderBookSnapshot | None = None,
    execution_book: OrderBookSnapshot | None = None,
    valuation_books: Mapping[PositionKey, OrderBookSnapshot] | None = None,
    evaluated_at: datetime = NOW,
) -> PaperEntryRequest:
    selected_decision_book = decision_book or order_book(
        first_size="100",
        second_size="100",
        book_hash="decision-book",
    )
    return PaperEntryRequest(
        strategy_id="paper-weather-v1",
        decision_id=decision_id,
        model_version="fixture-model-1",
        model_probability=Decimal(probability),
        scope=scope(),
        weather=weather_snapshot(),
        event=event_snapshot(),
        decision_order_book=selected_decision_book,
        execution_order_book=execution_book or selected_decision_book,
        valuation_books=valuation_books or {},
        evaluated_at=evaluated_at,
        freshness_policy=freshness_policy(),
        cost_policy=cost_policy(),
        sizing_policy=sizing_policy(maximum_cash="2"),
        portfolio_policy=portfolio_policy(
            total="50",
            event="25",
            city_date="25",
            correlation="25",
            positions=10,
            daily_loss="20",
            drawdown="50",
        ),
        audit_metadata={
            "fixture": "paper",
            "legacy_float": 1.25,
            "bucket_key": "F:85:86",
            "declared_resolution_source": "https://example.com/resolution-rules",
        },
    )
