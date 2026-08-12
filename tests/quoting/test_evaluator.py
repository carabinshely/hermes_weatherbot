from __future__ import annotations

from datetime import timedelta
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


def test_accepted_quote_reconciles_depth_costs_edge_and_metadata() -> None:
    result = evaluate_executable_buy(
        probability=Decimal("0.65"),
        requested_budget=Decimal("2"),
        weather=weather_snapshot(),
        event=event_snapshot(),
        order_book=order_book(),
        balance=balance_snapshot(),
        evaluated_at=NOW,
        freshness_policy=freshness_policy(),
        cost_policy=cost_policy(),
    )
    assert result.accepted
    quote = result.quote
    assert quote is not None
    assert quote.book_budget_limit < quote.requested_budget
    assert quote.quote.total_cost == quote.book_budget_limit
    assert quote.executable_budget == quote.quote.total_cost
    assert quote.platform_fee == quote.quote.total_cost * Decimal("0.01")
    assert quote.transaction_cost == Decimal("0.01")
    assert quote.safety_margin == quote.quote.total_cost * Decimal("0.02")
    assert quote.total_all_in_cost <= quote.requested_budget
    assert quote.requested_budget - quote.total_all_in_cost < Decimal("1e-24")
    assert quote.all_in_average_price == quote.total_all_in_cost / quote.quote.shares
    assert quote.gross_expected_payout == Decimal("0.65") * quote.quote.shares
    assert quote.expected_profit == quote.gross_expected_payout - quote.total_all_in_cost
    assert quote.expected_return == quote.expected_profit / quote.total_all_in_cost
    assert quote.probability_edge == Decimal("0.65") - quote.all_in_average_price
    assert not quote.depth_reduced
    assert quote.freshness["forecast"].age_seconds == 3600
    assert quote.freshness["event"].age_seconds == 10
    assert quote.freshness["order_book"].age_seconds == 5
    assert quote.freshness["balance"].age_seconds == 2
    assert quote.cost_policy.platform_fee_rate == Decimal("0.01")
    assert quote.cost_policy.depth_policy is DepthPolicy.REJECT
    assert quote.freshness_policy.maximum_order_book_age == timedelta(seconds=30)

    metadata = quote.metadata()
    metadata_all_in = Decimal(str(metadata["quote_total_all_in_cost"]))
    assert metadata_all_in <= quote.requested_budget
    assert quote.requested_budget - metadata_all_in < Decimal("1e-24")
    assert Decimal(str(metadata["quote_book_budget_limit"])) == quote.book_budget_limit
    assert metadata["quote_depth_reduced"] is False
    assert metadata["forecast_freshness_passed"] is True
    assert metadata["event_freshness_passed"] is True
    assert metadata["order_book_freshness_passed"] is True
    assert metadata["balance_freshness_passed"] is True
    assert metadata["quote_platform_fee_rate"] == "0.01"
    assert metadata["quote_safety_margin_rate"] == "0.02"
    assert metadata["quote_depth_policy"] == "reject"
    assert metadata["quote_future_tolerance_seconds"] == 5.0
    assert isinstance(metadata["quote_fingerprint"], str)


def test_small_budget_remains_within_ceiling_after_decimal_cost_reserves() -> None:
    budget = Decimal("0.833333")
    result = evaluate_executable_buy(
        probability=Decimal("0.65"),
        requested_budget=budget,
        weather=weather_snapshot(),
        event=event_snapshot(),
        order_book=order_book(),
        evaluated_at=NOW,
        freshness_policy=freshness_policy(),
        cost_policy=cost_policy(),
    )

    assert result.accepted
    quote = result.quote
    assert quote is not None
    assert quote.total_all_in_cost <= budget
    assert quote.average_slippage >= 0
    assert quote.quote.average_price >= quote.quote.best_ask


def test_stale_forecast_rejects_before_cost_calculation() -> None:
    result = evaluate_executable_buy(
        probability="0.65",
        requested_budget="2",
        weather=weather_snapshot(issued_at=NOW - timedelta(hours=7)),
        event=event_snapshot(),
        order_book=order_book(),
        evaluated_at=NOW,
        freshness_policy=freshness_policy(),
        cost_policy=cost_policy(),
    )
    assert not result.accepted
    assert result.rejection_reason is QuoteRejectionReason.STALE_FORECAST
    assert result.quote is None


def test_stale_event_rejects() -> None:
    result = evaluate_executable_buy(
        probability="0.65",
        requested_budget="2",
        weather=weather_snapshot(),
        event=event_snapshot(retrieved_at=NOW - timedelta(minutes=3)),
        order_book=order_book(),
        evaluated_at=NOW,
        freshness_policy=freshness_policy(),
        cost_policy=cost_policy(),
    )
    assert result.rejection_reason is QuoteRejectionReason.STALE_EVENT


def test_stale_order_book_rejects() -> None:
    result = evaluate_executable_buy(
        probability="0.65",
        requested_budget="2",
        weather=weather_snapshot(),
        event=event_snapshot(),
        order_book=order_book(observed_at=NOW - timedelta(seconds=31)),
        evaluated_at=NOW,
        freshness_policy=freshness_policy(),
        cost_policy=cost_policy(),
    )
    assert result.rejection_reason is QuoteRejectionReason.STALE_ORDER_BOOK


def test_stale_balance_rejects() -> None:
    result = evaluate_executable_buy(
        probability="0.65",
        requested_budget="2",
        weather=weather_snapshot(),
        event=event_snapshot(),
        order_book=order_book(),
        balance=balance_snapshot(observed_at=NOW - timedelta(seconds=31)),
        evaluated_at=NOW,
        freshness_policy=freshness_policy(),
        cost_policy=cost_policy(),
    )
    assert result.rejection_reason is QuoteRejectionReason.STALE_BALANCE


def test_insufficient_balance_rejects_before_quote() -> None:
    balance = balance_snapshot()
    insufficient = type(balance)(
        available_cash=Decimal("1"),
        reserved_cash=balance.reserved_cash,
        observed_at_utc=balance.observed_at_utc,
        source=balance.source,
    )
    result = evaluate_executable_buy(
        probability="0.65",
        requested_budget="2",
        weather=weather_snapshot(),
        event=event_snapshot(),
        order_book=order_book(),
        balance=insufficient,
        evaluated_at=NOW,
        freshness_policy=freshness_policy(),
        cost_policy=cost_policy(),
    )
    assert result.rejection_reason is QuoteRejectionReason.INSUFFICIENT_BALANCE


def test_future_snapshot_fails_closed() -> None:
    result = evaluate_executable_buy(
        probability="0.65",
        requested_budget="2",
        weather=weather_snapshot(),
        event=event_snapshot(retrieved_at=NOW + timedelta(seconds=10)),
        order_book=order_book(),
        evaluated_at=NOW,
        freshness_policy=freshness_policy(),
        cost_policy=cost_policy(),
    )
    assert result.rejection_reason is QuoteRejectionReason.INVALID_INPUT
    assert result.detail is not None
    assert "future" in result.detail
