"""Stable repository-owned models for public Polymarket data."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum


class OutcomeSide(StrEnum):
    YES = "yes"
    NO = "no"


@dataclass(frozen=True, slots=True)
class MarketIdentifiers:
    market_id: str
    condition_id: str | None
    yes_token_id: str | None
    no_token_id: str | None

    def token_id_for(self, side: OutcomeSide) -> str:
        token_id = self.yes_token_id if side is OutcomeSide.YES else self.no_token_id
        if token_id is None:
            raise ValueError(f"market {self.market_id} has no tradable {side.value.upper()} token")
        return token_id


@dataclass(frozen=True, slots=True)
class OutcomeSnapshot:
    side: OutcomeSide
    label: str
    token_id: str | None
    price: Decimal | None


@dataclass(frozen=True, slots=True)
class MarketSnapshot:
    identifiers: MarketIdentifiers
    question: str | None
    slug: str | None
    active: bool | None
    closed: bool | None
    accepting_orders: bool | None
    end_date: datetime | None
    volume: Decimal | None
    liquidity: Decimal | None
    yes: OutcomeSnapshot
    no: OutcomeSnapshot


@dataclass(frozen=True, slots=True)
class OrderBookLevel:
    price: Decimal
    size: Decimal


@dataclass(frozen=True, slots=True)
class OrderBookSnapshot:
    market_id: str
    token_id: str
    timestamp: datetime | None
    bids: tuple[OrderBookLevel, ...]
    asks: tuple[OrderBookLevel, ...]
    minimum_order_size: Decimal
    tick_size: Decimal
    negative_risk: bool
    last_trade_price: Decimal | None


@dataclass(frozen=True, slots=True)
class PriceSnapshot:
    token_id: str
    buy_price: Decimal
    midpoint: Decimal
    spread: Decimal
