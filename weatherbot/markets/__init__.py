"""Validated market identity, calendars, temperature buckets, and order books."""

from weatherbot.markets.calendar import (
    ForecastObservation,
    MarketCalendar,
    MarketCalendarError,
    index_forecasts,
    qualify_forecast_date,
)
from weatherbot.markets.gamma import (
    GammaBinaryMarket,
    GammaMarketError,
    parse_gamma_binary_market,
    parse_gamma_event_markets,
)
from weatherbot.markets.identity import (
    BinaryMarketIdentity,
    BinaryOutcome,
    ConditionId,
    GammaMarketId,
    MarketIdentityError,
    MarketSelection,
    OutcomeToken,
    OutcomeTokenId,
)
from weatherbot.markets.orderbook import (
    ExecutableQuote,
    OrderBookError,
    OrderBookSnapshot,
    OrderLevel,
    parse_order_book,
)
from weatherbot.markets.temperature import (
    TemperatureBucket,
    TemperatureMarketError,
    TemperatureMarketPartition,
    TemperatureUnit,
    parse_temperature_bucket,
)

__all__ = [
    "BinaryMarketIdentity",
    "BinaryOutcome",
    "ConditionId",
    "ExecutableQuote",
    "ForecastObservation",
    "GammaBinaryMarket",
    "GammaMarketError",
    "GammaMarketId",
    "MarketCalendar",
    "MarketCalendarError",
    "MarketIdentityError",
    "MarketSelection",
    "OrderBookError",
    "OrderBookSnapshot",
    "OrderLevel",
    "OutcomeToken",
    "OutcomeTokenId",
    "TemperatureBucket",
    "TemperatureMarketError",
    "TemperatureMarketPartition",
    "TemperatureUnit",
    "index_forecasts",
    "parse_gamma_binary_market",
    "parse_gamma_event_markets",
    "parse_order_book",
    "parse_temperature_bucket",
    "qualify_forecast_date",
]
