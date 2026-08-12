"""Strict token-specific Polymarket CLOB order books and executable quotes."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import cast

from weatherbot.markets.identity import ConditionId, OutcomeTokenId


class OrderBookError(ValueError):
    """Raised when an order book is empty, stale, crossed, or malformed."""


def _mapping(value: object, *, label: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise OrderBookError(f"{label} must be an object")
    return cast(Mapping[str, object], value)


def _text(value: object, *, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise OrderBookError(f"{label} must be a non-blank string")
    return value.strip()


def _decimal(value: object, *, label: str) -> Decimal:
    if isinstance(value, bool):
        raise OrderBookError(f"{label} must be decimal")
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise OrderBookError(f"{label} must be decimal") from exc
    if not result.is_finite():
        raise OrderBookError(f"{label} must be finite")
    return result


def _timestamp(value: object, *, label: str) -> datetime:
    text = _text(value, label=label)
    try:
        numeric = Decimal(text)
    except InvalidOperation:
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError as exc:
            raise OrderBookError(f"{label} is not a timestamp") from exc
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise OrderBookError(f"{label} must include a timezone") from None
        return parsed.astimezone(UTC)

    if not numeric.is_finite() or numeric < 0:
        raise OrderBookError(f"{label} must be a non-negative finite timestamp")
    seconds = numeric / Decimal("1000") if numeric >= Decimal("100000000000") else numeric
    try:
        return datetime.fromtimestamp(float(seconds), tz=UTC)
    except (OverflowError, OSError, ValueError) as exc:
        raise OrderBookError(f"{label} is outside the supported timestamp range") from exc


@dataclass(frozen=True, slots=True)
class OrderLevel:
    price: Decimal
    size: Decimal

    def __post_init__(self) -> None:
        price = _decimal(self.price, label="order price")
        size = _decimal(self.size, label="order size")
        if not Decimal("0") < price < Decimal("1"):
            raise OrderBookError("order price must be greater than zero and less than one")
        if size <= 0:
            raise OrderBookError("order size must be positive")
        object.__setattr__(self, "price", price)
        object.__setattr__(self, "size", size)


@dataclass(frozen=True, slots=True)
class ExecutableQuote:
    token_id: OutcomeTokenId
    shares: Decimal
    total_cost: Decimal
    average_price: Decimal
    worst_price: Decimal
    best_bid: Decimal
    best_ask: Decimal
    observed_at: datetime
    book_hash: str


@dataclass(frozen=True, slots=True)
class OrderBookSnapshot:
    condition_id: ConditionId
    token_id: OutcomeTokenId
    observed_at: datetime
    bids: tuple[OrderLevel, ...]
    asks: tuple[OrderLevel, ...]
    minimum_order_size: Decimal
    tick_size: Decimal
    neg_risk: bool
    book_hash: str

    def __post_init__(self) -> None:
        observed = self.observed_at
        if observed.tzinfo is None or observed.utcoffset() is None:
            raise OrderBookError("order-book timestamp must be timezone-aware")
        observed = observed.astimezone(UTC)
        minimum = _decimal(self.minimum_order_size, label="minimum order size")
        tick = _decimal(self.tick_size, label="tick size")
        if minimum <= 0:
            raise OrderBookError("minimum order size must be positive")
        if tick <= 0 or tick >= 1:
            raise OrderBookError("tick size must be greater than zero and less than one")
        if not self.book_hash.strip():
            raise OrderBookError("order-book hash must not be blank")
        if not self.bids or not self.asks:
            raise OrderBookError("order book requires at least one bid and one ask")
        bid_prices = [level.price for level in self.bids]
        ask_prices = [level.price for level in self.asks]
        if bid_prices != sorted(bid_prices, reverse=True):
            raise OrderBookError("bids must be sorted from highest to lowest price")
        if ask_prices != sorted(ask_prices):
            raise OrderBookError("asks must be sorted from lowest to highest price")
        if len(set(bid_prices)) != len(bid_prices):
            raise OrderBookError("order book contains duplicate bid price levels")
        if len(set(ask_prices)) != len(ask_prices):
            raise OrderBookError("order book contains duplicate ask price levels")
        if bid_prices[0] >= ask_prices[0]:
            raise OrderBookError("order book is crossed or locked")
        object.__setattr__(self, "observed_at", observed)
        object.__setattr__(self, "minimum_order_size", minimum)
        object.__setattr__(self, "tick_size", tick)

    @property
    def best_bid(self) -> Decimal:
        return self.bids[0].price

    @property
    def best_ask(self) -> Decimal:
        return self.asks[0].price

    @property
    def spread(self) -> Decimal:
        return self.best_ask - self.best_bid

    def require_fresh(
        self,
        *,
        now: datetime,
        maximum_age: timedelta,
    ) -> None:
        if now.tzinfo is None or now.utcoffset() is None:
            raise OrderBookError("freshness reference time must be timezone-aware")
        if maximum_age <= timedelta(0):
            raise OrderBookError("maximum order-book age must be positive")
        age = now.astimezone(UTC) - self.observed_at
        if age < -timedelta(seconds=5):
            raise OrderBookError("order-book timestamp is unexpectedly in the future")
        if age > maximum_age:
            raise OrderBookError(f"order book is stale by {age.total_seconds():.3f} seconds")

    def quote_buy(self, shares: Decimal | int | str | float) -> ExecutableQuote:
        requested = _decimal(shares, label="requested shares")
        if requested < self.minimum_order_size:
            raise OrderBookError(
                f"requested shares {requested} are below minimum {self.minimum_order_size}"
            )
        remaining = requested
        cost = Decimal("0")
        worst_price: Decimal | None = None
        for level in self.asks:
            take = min(remaining, level.size)
            if take > 0:
                cost += take * level.price
                remaining -= take
                worst_price = level.price
            if remaining == 0:
                break
        if remaining > 0:
            available = requested - remaining
            raise OrderBookError(
                f"insufficient ask depth: requested {requested}, available {available}"
            )
        assert worst_price is not None
        average_price = max(cost / requested, self.best_ask)
        return ExecutableQuote(
            token_id=self.token_id,
            shares=requested,
            total_cost=cost,
            average_price=average_price,
            worst_price=worst_price,
            best_bid=self.best_bid,
            best_ask=self.best_ask,
            observed_at=self.observed_at,
            book_hash=self.book_hash,
        )

    def quote_buy_budget(
        self,
        budget: Decimal | int | str | float,
    ) -> ExecutableQuote:
        """Consume ask depth without spending more than the approved cash budget."""
        requested_budget = _decimal(budget, label="cash budget")
        if requested_budget <= 0:
            raise OrderBookError("cash budget must be positive")

        remaining_budget = requested_budget
        shares = Decimal("0")
        cost = Decimal("0")
        worst_price: Decimal | None = None

        for level in self.asks:
            level_cost = level.size * level.price
            if remaining_budget >= level_cost:
                shares += level.size
                cost += level_cost
                remaining_budget -= level_cost
                worst_price = level.price
            else:
                shares += remaining_budget / level.price
                cost += remaining_budget
                remaining_budget = Decimal("0")
                worst_price = level.price
            if remaining_budget == 0:
                break

        if remaining_budget > 0:
            available_budget = requested_budget - remaining_budget
            raise OrderBookError(
                "insufficient ask depth for cash budget: "
                f"requested {requested_budget}, executable {available_budget}"
            )
        if shares < self.minimum_order_size:
            raise OrderBookError(
                f"cash budget buys {shares} shares, below minimum {self.minimum_order_size}"
            )
        assert worst_price is not None
        if cost > requested_budget:
            raise OrderBookError("executable quote exceeds approved cash budget")

        average_price = max(cost / shares, self.best_ask)
        return ExecutableQuote(
            token_id=self.token_id,
            shares=shares,
            total_cost=cost,
            average_price=average_price,
            worst_price=worst_price,
            best_bid=self.best_bid,
            best_ask=self.best_ask,
            observed_at=self.observed_at,
            book_hash=self.book_hash,
        )


def _levels(value: object, *, side: str) -> tuple[OrderLevel, ...]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        raise OrderBookError(f"{side} must be an array")
    levels: list[OrderLevel] = []
    for index, raw in enumerate(cast(Sequence[object], value)):
        data = _mapping(raw, label=f"{side}[{index}]")
        extra = set(data) - {"price", "size"}
        missing = {"price", "size"} - set(data)
        if missing or extra:
            raise OrderBookError(
                f"{side}[{index}] fields mismatch; missing={sorted(missing)}, extra={sorted(extra)}"
            )
        levels.append(
            OrderLevel(
                price=_decimal(data["price"], label=f"{side}[{index}].price"),
                size=_decimal(data["size"], label=f"{side}[{index}].size"),
            )
        )
    return tuple(levels)


def parse_order_book(
    payload: Mapping[str, object],
    *,
    expected_condition_id: ConditionId,
    expected_token_id: OutcomeTokenId,
    now: datetime | None = None,
    maximum_age: timedelta | None = None,
) -> OrderBookSnapshot:
    condition_id = ConditionId(_text(payload.get("market"), label="market"))
    token_id = OutcomeTokenId(_text(payload.get("asset_id"), label="asset_id"))
    if condition_id != expected_condition_id:
        raise OrderBookError(
            f"order book condition {condition_id} does not match {expected_condition_id}"
        )
    if token_id != expected_token_id:
        raise OrderBookError(f"order book asset {token_id} does not match {expected_token_id}")

    neg_risk = payload.get("neg_risk")
    if not isinstance(neg_risk, bool):
        raise OrderBookError("neg_risk must be boolean")

    snapshot = OrderBookSnapshot(
        condition_id=condition_id,
        token_id=token_id,
        observed_at=_timestamp(payload.get("timestamp"), label="timestamp"),
        bids=_levels(payload.get("bids"), side="bids"),
        asks=_levels(payload.get("asks"), side="asks"),
        minimum_order_size=_decimal(
            payload.get("min_order_size"),
            label="min_order_size",
        ),
        tick_size=_decimal(payload.get("tick_size"), label="tick_size"),
        neg_risk=neg_risk,
        book_hash=_text(payload.get("hash"), label="hash"),
    )
    if maximum_age is not None:
        if now is None:
            raise OrderBookError("freshness validation requires a reference time")
        snapshot.require_fresh(now=now, maximum_age=maximum_age)
    return snapshot
