from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from weatherbot.forecasting import (
    DailyHighForecast,
    ForecastSource,
    WeatherInputSnapshot,
)
from weatherbot.markets import (
    ConditionId,
    OrderBookSnapshot,
    OutcomeTokenId,
    parse_order_book,
)
from weatherbot.quoting import (
    BalanceSnapshot,
    CostPolicy,
    DepthPolicy,
    FreshnessPolicy,
    MarketEventSnapshot,
)

NOW = datetime(2026, 8, 6, 14, 0, tzinfo=UTC)
CONDITION = ConditionId("0x" + "cd" * 32)
TOKEN = OutcomeTokenId("12345678901234567890")
BOOK_HASH = "1" * 64


def weather_snapshot(
    *,
    issued_at: datetime | None = None,
    model_run_initialized_at_utc: datetime | None = None,
) -> WeatherInputSnapshot:
    market_date = date(2026, 8, 6)
    timezone = ZoneInfo("America/New_York")
    issued = issued_at or NOW - timedelta(hours=1)
    forecast = DailyHighForecast(
        temperature_f=Decimal("86"),
        market_date=market_date,
        market_timezone=timezone.key,
        source=ForecastSource.OPEN_METEO_ECMWF_IFS025,
        snapshot_issued_at_utc=issued,
        valid_from_utc=datetime.combine(market_date, time.min, timezone).astimezone(UTC),
        valid_until_utc=datetime.combine(
            market_date + timedelta(days=1),
            time.min,
            timezone,
        ).astimezone(UTC),
        retrieved_at_utc=issued,
        model_run_initialized_at_utc=model_run_initialized_at_utc,
    )
    return WeatherInputSnapshot(
        forecast=forecast,
        observation=None,
        assembled_at_utc=issued + timedelta(seconds=1),
    )


def event_snapshot(*, retrieved_at: datetime | None = None) -> MarketEventSnapshot:
    retrieved = retrieved_at or NOW - timedelta(seconds=10)
    return MarketEventSnapshot(
        event_id="event-chicago-2026-08-06",
        retrieved_at_utc=retrieved,
        source_updated_at_utc=retrieved - timedelta(minutes=2),
    )


def order_book_payload(
    *,
    observed_at: datetime | None = None,
    first_ask: str = "0.40",
    second_ask: str = "0.42",
    first_size: str = "3",
    second_size: str = "10",
    book_hash: str = BOOK_HASH,
) -> dict[str, object]:
    observed = observed_at or NOW - timedelta(seconds=5)
    return {
        "market": str(CONDITION),
        "asset_id": str(TOKEN),
        "timestamp": str(int(observed.timestamp() * 1000)),
        "hash": book_hash,
        "bids": [
            {"price": "0.34", "size": "100"},
            {"price": "0.33", "size": "250"},
        ],
        "asks": [
            {"price": first_ask, "size": first_size},
            {"price": second_ask, "size": second_size},
        ],
        "min_order_size": "1",
        "tick_size": "0.01",
        "neg_risk": False,
    }


def order_book(
    *,
    observed_at: datetime | None = None,
    first_ask: str = "0.40",
    second_ask: str = "0.42",
    first_size: str = "3",
    second_size: str = "10",
    book_hash: str = BOOK_HASH,
) -> OrderBookSnapshot:
    return parse_order_book(
        order_book_payload(
            observed_at=observed_at,
            first_ask=first_ask,
            second_ask=second_ask,
            first_size=first_size,
            second_size=second_size,
            book_hash=book_hash,
        ),
        expected_condition_id=CONDITION,
        expected_token_id=TOKEN,
    )


def balance_snapshot(*, observed_at: datetime | None = None) -> BalanceSnapshot:
    return BalanceSnapshot(
        available_cash=Decimal("100"),
        reserved_cash=Decimal("0"),
        observed_at_utc=observed_at or NOW - timedelta(seconds=2),
        source="test-ledger",
    )


def freshness_policy() -> FreshnessPolicy:
    return FreshnessPolicy(
        maximum_forecast_age=timedelta(hours=6),
        maximum_event_age=timedelta(minutes=2),
        maximum_order_book_age=timedelta(seconds=30),
        maximum_balance_age=timedelta(seconds=30),
    )


def cost_policy(
    *,
    fee_rate: str = "0.01",
    transaction_cost: str = "0.01",
    safety_margin_rate: str = "0.02",
    maximum_average_slippage: str = "0.03",
    maximum_worst_slippage: str = "0.04",
    maximum_all_in_price: str = "0.80",
    minimum_expected_return: str = "0.10",
    depth_policy: DepthPolicy = DepthPolicy.REJECT,
) -> CostPolicy:
    return CostPolicy(
        platform_fee_rate=Decimal(fee_rate),
        transaction_cost=Decimal(transaction_cost),
        safety_margin_rate=Decimal(safety_margin_rate),
        maximum_average_slippage=Decimal(maximum_average_slippage),
        maximum_worst_slippage=Decimal(maximum_worst_slippage),
        maximum_all_in_price=Decimal(maximum_all_in_price),
        minimum_expected_return=Decimal(minimum_expected_return),
        depth_policy=depth_policy,
    )
