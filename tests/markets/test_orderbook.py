from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from weatherbot.markets import (
    ConditionId,
    OrderBookError,
    OutcomeTokenId,
    parse_order_book,
)

CONDITION = ConditionId("0x" + "cd" * 32)
TOKEN = OutcomeTokenId("12345678901234567890")
NOW = datetime(2026, 8, 6, 6, 0, tzinfo=UTC)


def payload() -> dict[str, object]:
    return {
        "market": str(CONDITION),
        "asset_id": str(TOKEN),
        "timestamp": str(int(NOW.timestamp() * 1000)),
        "hash": "book-hash-1",
        "bids": [
            {"price": "0.34", "size": "100"},
            {"price": "0.33", "size": "250"},
        ],
        "asks": [
            {"price": "0.40", "size": "3"},
            {"price": "0.42", "size": "10"},
        ],
        "min_order_size": "1",
        "tick_size": "0.01",
        "neg_risk": False,
        "last_trade_price": "0.37",
    }


def test_bid_and_ask_come_from_one_token_specific_book() -> None:
    book = parse_order_book(
        payload(),
        expected_condition_id=CONDITION,
        expected_token_id=TOKEN,
        now=NOW,
        maximum_age=timedelta(seconds=5),
    )
    assert book.best_bid == Decimal("0.34")
    assert book.best_ask == Decimal("0.40")
    assert book.spread == Decimal("0.06")


def test_034_066_outcome_prices_are_not_a_32_percent_spread() -> None:
    book = parse_order_book(
        payload(),
        expected_condition_id=CONDITION,
        expected_token_id=TOKEN,
    )
    assert Decimal("0.66") - Decimal("0.34") == Decimal("0.32")
    assert book.spread == Decimal("0.06")
    assert book.spread != Decimal("0.32")


def test_size_aware_average_fill_uses_available_ask_depth() -> None:
    book = parse_order_book(
        payload(),
        expected_condition_id=CONDITION,
        expected_token_id=TOKEN,
    )
    quote = book.quote_buy(Decimal("5"))
    assert quote.total_cost == Decimal("2.04")
    assert quote.average_price == Decimal("0.408")
    assert quote.worst_price == Decimal("0.42")
    assert quote.best_ask == Decimal("0.40")


def test_cash_budget_quote_absorbs_depth_without_overspending() -> None:
    book = parse_order_book(
        payload(),
        expected_condition_id=CONDITION,
        expected_token_id=TOKEN,
    )
    budget = Decimal("2")
    quote = book.quote_buy_budget(budget)
    assert quote.total_cost == budget
    assert quote.total_cost <= budget
    assert quote.shares == Decimal("3") + Decimal("0.80") / Decimal("0.42")
    assert quote.average_price == quote.total_cost / quote.shares
    assert quote.worst_price == Decimal("0.42")
    assert quote.best_ask == Decimal("0.40")


def test_small_cash_budget_never_reports_average_below_best_ask() -> None:
    book = parse_order_book(
        payload(),
        expected_condition_id=CONDITION,
        expected_token_id=TOKEN,
    )
    quote = book.quote_buy_budget(Decimal("0.4959543689320388349514563106"))

    assert quote.total_cost <= Decimal("0.4959543689320388349514563106")
    assert quote.average_price == book.best_ask
    assert quote.average_price >= quote.best_ask


def _empty_bids(data: dict[str, object]) -> None:
    data["bids"] = []


def _empty_asks(data: dict[str, object]) -> None:
    data["asks"] = []


def _cross_book(data: dict[str, object]) -> None:
    data["bids"] = [{"price": "0.41", "size": "1"}]
    data["asks"] = [{"price": "0.40", "size": "1"}]


def _unsorted_bids(data: dict[str, object]) -> None:
    data["bids"] = [
        {"price": "0.33", "size": "1"},
        {"price": "0.34", "size": "1"},
    ]


def _unsorted_asks(data: dict[str, object]) -> None:
    data["asks"] = [
        {"price": "0.42", "size": "1"},
        {"price": "0.40", "size": "1"},
    ]


_BOOK_MUTATIONS: tuple[Callable[[dict[str, object]], None], ...] = (
    _empty_bids,
    _empty_asks,
    _cross_book,
    _unsorted_bids,
    _unsorted_asks,
)


@pytest.mark.parametrize("mutation", _BOOK_MUTATIONS)
def test_empty_crossed_or_unsorted_books_fail_closed(
    mutation: Callable[[dict[str, object]], None],
) -> None:
    data = payload()
    mutation(data)
    with pytest.raises(OrderBookError):
        parse_order_book(
            data,
            expected_condition_id=CONDITION,
            expected_token_id=TOKEN,
        )


def test_token_and_condition_mismatch_fail_closed() -> None:
    with pytest.raises(OrderBookError, match="condition"):
        parse_order_book(
            payload(),
            expected_condition_id=ConditionId("0x" + "ef" * 32),
            expected_token_id=TOKEN,
        )
    with pytest.raises(OrderBookError, match="asset"):
        parse_order_book(
            payload(),
            expected_condition_id=CONDITION,
            expected_token_id=OutcomeTokenId("999"),
        )


def test_stale_book_fails_closed() -> None:
    with pytest.raises(OrderBookError, match="stale"):
        parse_order_book(
            payload(),
            expected_condition_id=CONDITION,
            expected_token_id=TOKEN,
            now=NOW + timedelta(minutes=2),
            maximum_age=timedelta(seconds=30),
        )


def test_insufficient_depth_and_minimum_size_fail_closed() -> None:
    book = parse_order_book(
        payload(),
        expected_condition_id=CONDITION,
        expected_token_id=TOKEN,
    )
    with pytest.raises(OrderBookError, match="minimum"):
        book.quote_buy(Decimal("0.5"))
    with pytest.raises(OrderBookError, match="insufficient"):
        book.quote_buy(Decimal("20"))
    with pytest.raises(OrderBookError, match="below minimum"):
        book.quote_buy_budget(Decimal("0.30"))
    with pytest.raises(OrderBookError, match="insufficient"):
        book.quote_buy_budget(Decimal("10"))
