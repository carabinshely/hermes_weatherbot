from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import cast

import pytest

from weatherbot.polymarket import (
    MarketDataUnavailable,
    OfficialPolymarketReadClient,
    OutcomeSide,
    PublicSdkClient,
)


@dataclass
class FakeOutcome:
    label: str
    token_id: str | None
    price: Decimal | None


@dataclass
class FakeOutcomes:
    yes: FakeOutcome
    no: FakeOutcome


@dataclass
class FakeState:
    active: bool | None
    closed: bool | None
    accepting_orders: bool | None
    end_date: datetime | None


@dataclass
class FakeMetrics:
    volume: Decimal | None
    liquidity: Decimal | None


@dataclass
class FakeMarket:
    id: str
    condition_id: str | None
    question: str | None
    slug: str | None
    state: FakeState
    outcomes: FakeOutcomes
    metrics: FakeMetrics


@dataclass
class FakeLevel:
    price: Decimal
    size: Decimal


@dataclass
class FakeBook:
    market: str
    token_id: str
    timestamp: datetime | None
    bids: tuple[FakeLevel, ...]
    asks: tuple[FakeLevel, ...]
    min_order_size: Decimal
    tick_size: Decimal
    neg_risk: bool
    last_trade_price: Decimal | None


@dataclass
class FakePage:
    items: tuple[FakeMarket, ...]


class FakePaginator:
    def __init__(self, items: tuple[FakeMarket, ...]) -> None:
        self._items = items

    def first_page(self) -> FakePage:
        return FakePage(items=self._items)


class FakeSdkClient:
    def __init__(self) -> None:
        self.market = FakeMarket(
            id="market-42",
            condition_id="0xcondition",
            question="Will it rain?",
            slug="will-it-rain",
            state=FakeState(
                active=True,
                closed=False,
                accepting_orders=True,
                end_date=datetime(2026, 8, 7, tzinfo=UTC),
            ),
            outcomes=FakeOutcomes(
                yes=FakeOutcome("Yes", "token-yes", Decimal("0.42")),
                no=FakeOutcome("No", "token-no", Decimal("0.58")),
            ),
            metrics=FakeMetrics(Decimal("1000"), Decimal("500")),
        )
        self.book = FakeBook(
            market="0xcondition",
            token_id="token-yes",
            timestamp=datetime(2026, 8, 6, tzinfo=UTC),
            bids=(FakeLevel(Decimal("0.40"), Decimal("5")),),
            asks=(FakeLevel(Decimal("0.44"), Decimal("4")),),
            min_order_size=Decimal("1"),
            tick_size=Decimal("0.01"),
            neg_risk=False,
            last_trade_price=Decimal("0.41"),
        )
        self.closed = False
        self.list_closed: bool | None = None
        self.list_page_size: int | None = None
        self.market_selector: tuple[str | None, str | None, str | None] | None = None

    def close(self) -> None:
        self.closed = True

    def get_market(
        self,
        *,
        id: str | None = None,
        slug: str | None = None,
        url: str | None = None,
    ) -> FakeMarket:
        self.market_selector = (id, slug, url)
        return self.market

    def list_markets(
        self,
        *,
        closed: bool | None = None,
        page_size: int = 20,
    ) -> FakePaginator:
        self.list_closed = closed
        self.list_page_size = page_size
        return FakePaginator((self.market,))

    def get_order_book(self, *, token_id: str) -> FakeBook:
        assert token_id == "token-yes"
        return self.book

    def get_price(self, *, token_id: str, side: str) -> Decimal:
        assert (token_id, side) == ("token-yes", "BUY")
        return Decimal("0.44")

    def get_midpoint(self, *, token_id: str) -> Decimal:
        assert token_id == "token-yes"
        return Decimal("0.42")

    def get_spread(self, *, token_id: str) -> Decimal:
        assert token_id == "token-yes"
        return Decimal("0.04")


def adapter(fake: FakeSdkClient) -> OfficialPolymarketReadClient:
    return OfficialPolymarketReadClient(cast(PublicSdkClient, fake))


def test_market_snapshot_keeps_identifier_types_distinct() -> None:
    fake = FakeSdkClient()
    snapshot = adapter(fake).get_market(market_id="market-42")

    assert fake.market_selector == ("market-42", None, None)
    assert snapshot.identifiers.market_id == "market-42"
    assert snapshot.identifiers.condition_id == "0xcondition"
    assert snapshot.identifiers.yes_token_id == "token-yes"
    assert snapshot.identifiers.no_token_id == "token-no"
    assert snapshot.identifiers.token_id_for(OutcomeSide.YES) == "token-yes"
    assert snapshot.identifiers.token_id_for(OutcomeSide.NO) == "token-no"
    assert snapshot.yes.price == Decimal("0.42")
    assert snapshot.no.price == Decimal("0.58")


def test_list_markets_requests_only_open_markets() -> None:
    fake = FakeSdkClient()
    snapshots = adapter(fake).list_open_markets(limit=7)

    assert len(snapshots) == 1
    assert fake.list_closed is False
    assert fake.list_page_size == 7


def test_order_book_is_queried_by_outcome_token_id() -> None:
    fake = FakeSdkClient()
    book = adapter(fake).get_order_book("token-yes")

    assert book.token_id == "token-yes"
    assert book.market_id == "0xcondition"
    assert book.bids[0].price == Decimal("0.40")
    assert book.asks[0].price == Decimal("0.44")


def test_order_book_rejects_mismatched_token() -> None:
    fake = FakeSdkClient()
    fake.book.token_id = "token-no"

    with pytest.raises(MarketDataUnavailable, match="different token"):
        adapter(fake).get_order_book("token-yes")


def test_prices_remain_decimal() -> None:
    prices = adapter(FakeSdkClient()).get_prices("token-yes")

    assert prices.buy_price == Decimal("0.44")
    assert prices.midpoint == Decimal("0.42")
    assert prices.spread == Decimal("0.04")


def test_owned_client_factory_receives_no_wallet_credentials() -> None:
    fake = FakeSdkClient()
    calls = 0

    def factory() -> PublicSdkClient:
        nonlocal calls
        calls += 1
        return cast(PublicSdkClient, fake)

    client = OfficialPolymarketReadClient(client_factory=factory)
    client.close()

    assert calls == 1
    assert fake.closed is True


def test_exactly_one_market_selector_is_required() -> None:
    client = adapter(FakeSdkClient())

    with pytest.raises(ValueError, match="exactly one"):
        client.get_market()
    with pytest.raises(ValueError, match="exactly one"):
        client.get_market(market_id="1", slug="two")
