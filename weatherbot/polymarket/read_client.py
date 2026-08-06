"""Read-only adapter over Polymarket's maintained unified Python SDK."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from decimal import Decimal
from typing import Protocol, Self, cast

from polymarket import PublicClient

from weatherbot.polymarket.errors import MarketDataUnavailable
from weatherbot.polymarket.models import (
    MarketIdentifiers,
    MarketSnapshot,
    OrderBookLevel,
    OrderBookSnapshot,
    OutcomeSide,
    OutcomeSnapshot,
    PriceSnapshot,
)


class _Outcome(Protocol):
    label: str
    token_id: str | None
    price: Decimal | None


class _Outcomes(Protocol):
    yes: _Outcome
    no: _Outcome


class _MarketState(Protocol):
    active: bool | None
    closed: bool | None
    accepting_orders: bool | None
    end_date: object | None


class _MarketMetrics(Protocol):
    volume: Decimal | None
    liquidity: Decimal | None


class _Market(Protocol):
    id: object
    condition_id: object | None
    question: str | None
    slug: str | None
    state: _MarketState
    outcomes: _Outcomes
    metrics: _MarketMetrics


class _Level(Protocol):
    price: Decimal
    size: Decimal


class _OrderBook(Protocol):
    market: str
    token_id: object
    timestamp: object | None
    bids: Sequence[_Level]
    asks: Sequence[_Level]
    min_order_size: Decimal
    tick_size: Decimal
    neg_risk: bool
    last_trade_price: Decimal | None


class _Page(Protocol):
    items: Sequence[_Market]


class _Paginator(Protocol):
    def first_page(self) -> _Page: ...


class PublicSdkClient(Protocol):
    def close(self) -> None: ...

    def get_market(
        self,
        *,
        id: str | None = None,
        slug: str | None = None,
        url: str | None = None,
    ) -> _Market: ...

    def list_markets(self, *, closed: bool | None = None, page_size: int = 20) -> _Paginator: ...

    def get_order_book(self, *, token_id: str) -> _OrderBook: ...

    def get_price(self, *, token_id: str, side: str) -> Decimal: ...

    def get_midpoint(self, *, token_id: str) -> Decimal: ...

    def get_spread(self, *, token_id: str) -> Decimal: ...


def _text(value: object, *, label: str) -> str:
    result = str(value).strip()
    if not result:
        raise MarketDataUnavailable(f"Polymarket returned a blank {label}")
    return result


def _optional_text(value: object | None) -> str | None:
    if value is None:
        return None
    result = str(value).strip()
    return result or None


def _decimal(value: object, *, label: str) -> Decimal:
    try:
        result = value if isinstance(value, Decimal) else Decimal(str(value))
    except (ArithmeticError, ValueError) as exc:
        raise MarketDataUnavailable(f"Polymarket returned an invalid {label}") from exc
    if not result.is_finite():
        raise MarketDataUnavailable(f"Polymarket returned a non-finite {label}")
    return result


class OfficialPolymarketReadClient:
    """Normalize public SDK objects into repository-owned immutable models."""

    def __init__(
        self,
        sdk_client: PublicSdkClient | None = None,
        *,
        client_factory: Callable[[], PublicSdkClient] | None = None,
    ) -> None:
        if sdk_client is not None and client_factory is not None:
            raise ValueError("provide sdk_client or client_factory, not both")
        if sdk_client is None:
            factory = client_factory or (lambda: cast(PublicSdkClient, PublicClient()))
            sdk_client = factory()
            self._owns_client = True
        else:
            self._owns_client = False
        self._client = sdk_client
        self._closed = False

    def __enter__(self) -> Self:
        if self._closed:
            raise RuntimeError("Polymarket read client is closed")
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    def close(self) -> None:
        if self._closed:
            return
        if self._owns_client:
            self._client.close()
        self._closed = True

    def _ensure_open(self) -> None:
        if self._closed:
            raise RuntimeError("Polymarket read client is closed")

    @staticmethod
    def _market(value: _Market) -> MarketSnapshot:
        market_id = _text(value.id, label="market id")
        yes_token = _optional_text(value.outcomes.yes.token_id)
        no_token = _optional_text(value.outcomes.no.token_id)
        identifiers = MarketIdentifiers(
            market_id=market_id,
            condition_id=_optional_text(value.condition_id),
            yes_token_id=yes_token,
            no_token_id=no_token,
        )
        end_date = value.state.end_date
        if end_date is not None and not hasattr(end_date, "tzinfo"):
            raise MarketDataUnavailable(f"market {market_id} has an invalid end date")
        return MarketSnapshot(
            identifiers=identifiers,
            question=value.question,
            slug=value.slug,
            active=value.state.active,
            closed=value.state.closed,
            accepting_orders=value.state.accepting_orders,
            end_date=cast(object, end_date),
            volume=value.metrics.volume,
            liquidity=value.metrics.liquidity,
            yes=OutcomeSnapshot(
                side=OutcomeSide.YES,
                label=value.outcomes.yes.label,
                token_id=yes_token,
                price=value.outcomes.yes.price,
            ),
            no=OutcomeSnapshot(
                side=OutcomeSide.NO,
                label=value.outcomes.no.label,
                token_id=no_token,
                price=value.outcomes.no.price,
            ),
        )

    def get_market(
        self,
        *,
        market_id: str | None = None,
        slug: str | None = None,
        url: str | None = None,
    ) -> MarketSnapshot:
        self._ensure_open()
        selectors = [market_id is not None, slug is not None, url is not None]
        if sum(selectors) != 1:
            raise ValueError("exactly one market selector is required")
        try:
            market = self._client.get_market(id=market_id, slug=slug, url=url)
            return self._market(market)
        except MarketDataUnavailable:
            raise
        except Exception as exc:
            raise MarketDataUnavailable("public market lookup failed") from exc

    def list_open_markets(self, *, limit: int = 20) -> tuple[MarketSnapshot, ...]:
        self._ensure_open()
        if limit < 1 or limit > 100:
            raise ValueError("limit must be between 1 and 100")
        try:
            page = self._client.list_markets(closed=False, page_size=limit).first_page()
            return tuple(self._market(market) for market in page.items[:limit])
        except MarketDataUnavailable:
            raise
        except Exception as exc:
            raise MarketDataUnavailable("public market listing failed") from exc

    def get_order_book(self, token_id: str) -> OrderBookSnapshot:
        self._ensure_open()
        normalized_token = _text(token_id, label="token id")
        try:
            book = self._client.get_order_book(token_id=normalized_token)
            returned_token = _text(book.token_id, label="order-book token id")
            if returned_token != normalized_token:
                raise MarketDataUnavailable("order book returned a different token id")
            return OrderBookSnapshot(
                market_id=_text(book.market, label="order-book market id"),
                token_id=returned_token,
                timestamp=cast(object, book.timestamp),
                bids=tuple(
                    OrderBookLevel(
                        price=_decimal(level.price, label="bid price"),
                        size=_decimal(level.size, label="bid size"),
                    )
                    for level in book.bids
                ),
                asks=tuple(
                    OrderBookLevel(
                        price=_decimal(level.price, label="ask price"),
                        size=_decimal(level.size, label="ask size"),
                    )
                    for level in book.asks
                ),
                minimum_order_size=_decimal(book.min_order_size, label="minimum order size"),
                tick_size=_decimal(book.tick_size, label="tick size"),
                negative_risk=book.neg_risk,
                last_trade_price=(
                    _decimal(book.last_trade_price, label="last trade price")
                    if book.last_trade_price is not None
                    else None
                ),
            )
        except MarketDataUnavailable:
            raise
        except Exception as exc:
            raise MarketDataUnavailable("public order-book lookup failed") from exc

    def get_prices(self, token_id: str) -> PriceSnapshot:
        self._ensure_open()
        normalized_token = _text(token_id, label="token id")
        try:
            return PriceSnapshot(
                token_id=normalized_token,
                buy_price=_decimal(
                    self._client.get_price(token_id=normalized_token, side="BUY"),
                    label="buy price",
                ),
                midpoint=_decimal(
                    self._client.get_midpoint(token_id=normalized_token),
                    label="midpoint",
                ),
                spread=_decimal(
                    self._client.get_spread(token_id=normalized_token),
                    label="spread",
                ),
            )
        except MarketDataUnavailable:
            raise
        except Exception as exc:
            raise MarketDataUnavailable("public price lookup failed") from exc
