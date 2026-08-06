"""Temporary fail-closed bridge for the legacy monolithic bot.

This module intentionally implements no authenticated SDK calls. It allows legacy
entry points to import while ensuring every wallet-dependent operation fails before
network or transaction construction.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from weatherbot.polymarket.errors import AuthenticatedTradingUnavailable
from weatherbot.polymarket.trading import AccountConfiguration


@dataclass(frozen=True, slots=True)
class MarketOrderArgs:
    token_id: str
    amount: float
    side: str
    price: float


@dataclass(frozen=True, slots=True)
class OrderArgs:
    token_id: str
    price: float
    size: float
    side: str


class OrderType(StrEnum):
    GTC = "GTC"
    FOK = "FOK"
    GTD = "GTD"
    FAK = "FAK"


class UnsupportedTradingClient:
    """Old method shape whose operations all fail closed."""

    def __init__(
        self,
        *,
        signature_type: int = 0,
        wallet_address: str | None = None,
    ) -> None:
        self.configuration = AccountConfiguration.from_values(
            signature_type=signature_type,
            wallet_address=wallet_address,
        )

    @staticmethod
    def _blocked(operation: str) -> AuthenticatedTradingUnavailable:
        return AuthenticatedTradingUnavailable(
            f"{operation} is unavailable: py-clob-client was removed and the "
            "official authenticated adapter has not been enabled"
        )

    def assert_level_1_auth(self) -> None:
        raise self._blocked("authentication")

    def create_market_order(self, _args: MarketOrderArgs) -> None:
        raise self._blocked("market-order creation")

    def post_order(self, *_args: object, **_kwargs: object) -> None:
        raise self._blocked("order submission")

    def cancel(self, _order_id: str) -> None:
        raise self._blocked("order cancellation")

    def cancel_all(self) -> None:
        raise self._blocked("bulk cancellation")

    def get_orders(self) -> None:
        raise self._blocked("authenticated order listing")
