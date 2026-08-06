"""Errors raised by the maintained Polymarket integration boundary."""


class PolymarketAdapterError(RuntimeError):
    """Base error for normalized Polymarket adapter failures."""


class MarketDataUnavailable(PolymarketAdapterError):
    """The official public SDK could not provide a safe normalized response."""


class AuthenticatedTradingUnavailable(PolymarketAdapterError):
    """Authenticated writes are disabled until the live adapter is implemented."""


class UnsupportedAccountConfiguration(PolymarketAdapterError):
    """A requested account/signature mode is not supported by this repository."""
