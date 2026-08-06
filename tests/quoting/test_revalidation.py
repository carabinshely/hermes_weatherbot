from __future__ import annotations

from tests.quoting.helpers import (
    NOW,
    balance_snapshot,
    cost_policy,
    event_snapshot,
    freshness_policy,
    order_book,
    weather_snapshot,
)
from weatherbot.quoting import (
    QuoteRejectionReason,
    evaluate_executable_buy,
    revalidate_executable_buy,
)


def test_quote_movement_is_re_evaluated_before_execution() -> None:
    initial = evaluate_executable_buy(
        probability="0.65",
        requested_budget="2",
        weather=weather_snapshot(),
        event=event_snapshot(),
        order_book=order_book(),
        balance=balance_snapshot(),
        evaluated_at=NOW,
        freshness_policy=freshness_policy(),
        cost_policy=cost_policy(),
    )
    assert initial.quote is not None

    moved = revalidate_executable_buy(
        initial.quote,
        probability="0.65",
        requested_budget="2",
        weather=weather_snapshot(),
        event=event_snapshot(),
        order_book=order_book(
            first_ask="0.66",
            second_ask="0.68",
            book_hash="book-hash-moved",
        ),
        balance=balance_snapshot(),
        evaluated_at=NOW,
        freshness_policy=freshness_policy(),
        cost_policy=cost_policy(
            maximum_average_slippage="0.03",
            maximum_worst_slippage="0.04",
            maximum_all_in_price="0.90",
            minimum_expected_return="0",
        ),
    )
    assert moved.quote is None
    assert moved.rejection_reason is QuoteRejectionReason.NON_POSITIVE_EDGE


def test_refreshed_token_mismatch_fails_before_quote_math() -> None:
    initial = evaluate_executable_buy(
        probability="0.65",
        requested_budget="2",
        weather=weather_snapshot(),
        event=event_snapshot(),
        order_book=order_book(),
        evaluated_at=NOW,
        freshness_policy=freshness_policy(),
        cost_policy=cost_policy(),
    )
    assert initial.quote is not None

    payload_book = order_book()
    different = type(payload_book)(
        condition_id=payload_book.condition_id,
        token_id=type(payload_book.token_id)("999999999999999"),
        observed_at=payload_book.observed_at,
        bids=payload_book.bids,
        asks=payload_book.asks,
        minimum_order_size=payload_book.minimum_order_size,
        tick_size=payload_book.tick_size,
        neg_risk=payload_book.neg_risk,
        book_hash="different-token-book",
    )
    result = revalidate_executable_buy(
        initial.quote,
        probability="0.65",
        requested_budget="2",
        weather=weather_snapshot(),
        event=event_snapshot(),
        order_book=different,
        evaluated_at=NOW,
        freshness_policy=freshness_policy(),
        cost_policy=cost_policy(),
    )
    assert result.rejection_reason is QuoteRejectionReason.SNAPSHOT_MISMATCH
