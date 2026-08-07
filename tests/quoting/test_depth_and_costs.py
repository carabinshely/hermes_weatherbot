from __future__ import annotations

from decimal import Decimal

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
    DepthPolicy,
    QuoteRejectionReason,
    evaluate_executable_buy,
)


def test_thin_book_reject_policy_is_deterministic() -> None:
    result = evaluate_executable_buy(
        probability="0.70",
        requested_budget="6",
        weather=weather_snapshot(),
        event=event_snapshot(),
        order_book=order_book(),
        balance=balance_snapshot(),
        evaluated_at=NOW,
        freshness_policy=freshness_policy(),
        cost_policy=cost_policy(depth_policy=DepthPolicy.REJECT),
    )
    assert result.rejection_reason is QuoteRejectionReason.INSUFFICIENT_DEPTH
    assert result.detail is not None
    assert "displayed ask notional 5.40" in result.detail


def test_thin_book_reduce_policy_consumes_only_displayed_depth() -> None:
    result = evaluate_executable_buy(
        probability="0.70",
        requested_budget="6",
        weather=weather_snapshot(),
        event=event_snapshot(),
        order_book=order_book(),
        balance=balance_snapshot(),
        evaluated_at=NOW,
        freshness_policy=freshness_policy(),
        cost_policy=cost_policy(
            depth_policy=DepthPolicy.REDUCE,
            maximum_all_in_price="0.80",
        ),
    )
    quote = result.quote
    assert quote is not None
    assert quote.requested_budget == Decimal("6")
    assert quote.book_budget_limit > Decimal("5.40")
    assert quote.executable_budget == Decimal("5.40")
    assert quote.quote.total_cost == Decimal("5.40")
    assert quote.quote.shares == Decimal("13")
    assert quote.total_all_in_cost < quote.requested_budget
    assert quote.depth_reduced


def test_budget_below_minimum_order_rejects() -> None:
    result = evaluate_executable_buy(
        probability="0.70",
        requested_budget="0.30",
        weather=weather_snapshot(),
        event=event_snapshot(),
        order_book=order_book(),
        evaluated_at=NOW,
        freshness_policy=freshness_policy(),
        cost_policy=cost_policy(),
    )
    assert result.rejection_reason is QuoteRejectionReason.BELOW_MINIMUM_ORDER


def test_fee_and_safety_margin_can_erase_nominal_edge() -> None:
    result = evaluate_executable_buy(
        probability="0.41",
        requested_budget="1.20",
        weather=weather_snapshot(),
        event=event_snapshot(),
        order_book=order_book(),
        evaluated_at=NOW,
        freshness_policy=freshness_policy(),
        cost_policy=cost_policy(
            fee_rate="0.10",
            transaction_cost="0",
            safety_margin_rate="0",
            minimum_expected_return="0",
        ),
    )
    assert result.rejection_reason is QuoteRejectionReason.FEE_ERASED_EDGE


def test_fixed_transaction_cost_can_push_return_below_floor() -> None:
    result = evaluate_executable_buy(
        probability="0.50",
        requested_budget="1.20",
        weather=weather_snapshot(),
        event=event_snapshot(),
        order_book=order_book(),
        evaluated_at=NOW,
        freshness_policy=freshness_policy(),
        cost_policy=cost_policy(
            fee_rate="0",
            transaction_cost="0.20",
            safety_margin_rate="0",
            minimum_expected_return="0.20",
        ),
    )
    assert result.rejection_reason is QuoteRejectionReason.EXPECTED_RETURN_BELOW_FLOOR


def test_average_and_worst_slippage_are_checked_separately() -> None:
    average_result = evaluate_executable_buy(
        probability="0.75",
        requested_budget="2",
        weather=weather_snapshot(),
        event=event_snapshot(),
        order_book=order_book(second_ask="0.45"),
        evaluated_at=NOW,
        freshness_policy=freshness_policy(),
        cost_policy=cost_policy(
            maximum_average_slippage="0.005",
            maximum_worst_slippage="0.06",
        ),
    )
    assert average_result.rejection_reason is QuoteRejectionReason.SLIPPAGE_EXCEEDED
    assert average_result.detail is not None
    assert "average slippage" in average_result.detail

    worst_result = evaluate_executable_buy(
        probability="0.75",
        requested_budget="2",
        weather=weather_snapshot(),
        event=event_snapshot(),
        order_book=order_book(second_ask="0.45"),
        evaluated_at=NOW,
        freshness_policy=freshness_policy(),
        cost_policy=cost_policy(
            maximum_average_slippage="0.03",
            maximum_worst_slippage="0.04",
        ),
    )
    assert worst_result.rejection_reason is QuoteRejectionReason.SLIPPAGE_EXCEEDED
    assert worst_result.detail is not None
    assert "worst slippage" in worst_result.detail


def test_all_in_price_cap_uses_fees_not_only_book_price() -> None:
    result = evaluate_executable_buy(
        probability="0.80",
        requested_budget="1.20",
        weather=weather_snapshot(),
        event=event_snapshot(),
        order_book=order_book(),
        evaluated_at=NOW,
        freshness_policy=freshness_policy(),
        cost_policy=cost_policy(
            fee_rate="0.10",
            transaction_cost="0",
            safety_margin_rate="0",
            maximum_all_in_price="0.43",
            minimum_expected_return="0",
        ),
    )
    assert result.rejection_reason is QuoteRejectionReason.PRICE_EXCEEDED
