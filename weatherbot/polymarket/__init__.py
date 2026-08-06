"""Maintained, backend-isolated Polymarket integration."""

from weatherbot.polymarket.errors import (
    AuthenticatedTradingUnavailable,
    MarketDataUnavailable,
    PolymarketAdapterError,
    UnsupportedAccountConfiguration,
)
from weatherbot.polymarket.models import (
    MarketIdentifiers,
    MarketSnapshot,
    OrderBookLevel,
    OrderBookSnapshot,
    OutcomeSide,
    OutcomeSnapshot,
    PriceSnapshot,
)
from weatherbot.polymarket.read_client import OfficialPolymarketReadClient, PublicSdkClient
from weatherbot.polymarket.trading import (
    AccountConfiguration,
    AccountSignatureType,
    AuthenticatedPolymarketTrading,
)

__all__ = [
    "AccountConfiguration",
    "AccountSignatureType",
    "AuthenticatedPolymarketTrading",
    "AuthenticatedTradingUnavailable",
    "MarketDataUnavailable",
    "MarketIdentifiers",
    "MarketSnapshot",
    "OfficialPolymarketReadClient",
    "OrderBookLevel",
    "OrderBookSnapshot",
    "OutcomeSide",
    "OutcomeSnapshot",
    "PolymarketAdapterError",
    "PriceSnapshot",
    "PublicSdkClient",
    "UnsupportedAccountConfiguration",
]
