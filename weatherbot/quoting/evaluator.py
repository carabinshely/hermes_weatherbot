"""Deterministic freshness and executable-edge evaluation."""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal
from types import MappingProxyType

from weatherbot.forecasting import WeatherInputSnapshot
from weatherbot.markets import OrderBookError, OrderBookSnapshot
from weatherbot.quoting.model import (
    BalanceSnapshot,
    CostPolicy,
    DepthPolicy,
    FreshnessCheck,
    FreshnessPolicy,
    MarketEventSnapshot,
    QuoteEvaluation,
    QuoteRejectionReason,
    QuoteValidationError,
    ValidatedExecutableQuote,
    as_decimal,
    as_utc,
)


def _age(
    *,
    observed_at: datetime,
    evaluated_at: datetime,
    maximum_age: timedelta,
    future_tolerance: timedelta,
    label: str,
) -> FreshnessCheck:
    observed = as_utc(observed_at, label=f"{label} observation time")
    evaluated = as_utc(evaluated_at, label="quote evaluation time")
    difference = evaluated - observed
    if difference < -future_tolerance:
        raise QuoteValidationError(f"{label} timestamp is unexpectedly in the future")
    age_seconds = max(0.0, difference.total_seconds())
    return FreshnessCheck(
        label=label,
        observed_at_utc=observed,
        age_seconds=age_seconds,
        maximum_age_seconds=maximum_age.total_seconds(),
    )


def _reject(
    evaluated_at: datetime,
    reason: QuoteRejectionReason,
    detail: str,
    freshness: dict[str, FreshnessCheck] | None = None,
) -> QuoteEvaluation:
    return QuoteEvaluation(
        evaluated_at_utc=evaluated_at,
        rejection_reason=reason,
        detail=detail,
        freshness=MappingProxyType(dict(freshness or {})),
    )


def _check_freshness(
    *,
    weather: WeatherInputSnapshot,
    event: MarketEventSnapshot,
    order_book: OrderBookSnapshot,
    balance: BalanceSnapshot | None,
    evaluated_at: datetime,
    policy: FreshnessPolicy,
) -> tuple[dict[str, FreshnessCheck], QuoteEvaluation | None]:
    checks: dict[str, FreshnessCheck] = {}
    definitions = (
        (
            "forecast",
            weather.forecast.snapshot_issued_at_utc,
            policy.maximum_forecast_age,
            QuoteRejectionReason.STALE_FORECAST,
        ),
        (
            "event",
            event.retrieved_at_utc,
            policy.maximum_event_age,
            QuoteRejectionReason.STALE_EVENT,
        ),
        (
            "order_book",
            order_book.observed_at,
            policy.maximum_order_book_age,
            QuoteRejectionReason.STALE_ORDER_BOOK,
        ),
    )
    try:
        for label, observed_at, maximum_age, rejection_reason in definitions:
            check = _age(
                observed_at=observed_at,
                evaluated_at=evaluated_at,
                maximum_age=maximum_age,
                future_tolerance=policy.future_tolerance,
                label=label,
            )
            checks[label] = check
            if not check.fresh:
                return checks, _reject(
                    evaluated_at,
                    rejection_reason,
                    (
                        f"{label} age {check.age_seconds:.3f}s exceeds "
                        f"{check.maximum_age_seconds:.3f}s"
                    ),
                    checks,
                )
        if balance is not None:
            check = _age(
                observed_at=balance.observed_at_utc,
                evaluated_at=evaluated_at,
                maximum_age=policy.maximum_balance_age,
                future_tolerance=policy.future_tolerance,
                label="balance",
            )
            checks["balance"] = check
            if not check.fresh:
                return checks, _reject(
                    evaluated_at,
                    QuoteRejectionReason.STALE_BALANCE,
                    (
                        f"balance age {check.age_seconds:.3f}s exceeds "
                        f"{check.maximum_age_seconds:.3f}s"
                    ),
                    checks,
                )
    except QuoteValidationError as exc:
        return checks, _reject(
            evaluated_at,
            QuoteRejectionReason.INVALID_INPUT,
            str(exc),
            checks,
        )
    return checks, None


def evaluate_executable_buy(
    *,
    probability: Decimal | int | str | float,
    requested_budget: Decimal | int | str | float,
    weather: WeatherInputSnapshot,
    event: MarketEventSnapshot,
    order_book: OrderBookSnapshot,
    evaluated_at: datetime,
    freshness_policy: FreshnessPolicy,
    cost_policy: CostPolicy,
    balance: BalanceSnapshot | None = None,
) -> QuoteEvaluation:
    """Validate one BUY quote from the exact snapshots available at decision time."""
    evaluated = as_utc(evaluated_at, label="quote evaluation time")
    try:
        model_probability = as_decimal(probability, label="model probability")
        budget = as_decimal(requested_budget, label="requested budget")
    except QuoteValidationError as exc:
        return _reject(
            evaluated,
            QuoteRejectionReason.INVALID_INPUT,
            str(exc),
        )
    if model_probability <= 0 or model_probability >= 1:
        return _reject(
            evaluated,
            QuoteRejectionReason.INVALID_INPUT,
            "model probability must be between zero and one",
        )
    if budget <= 0:
        return _reject(
            evaluated,
            QuoteRejectionReason.INVALID_INPUT,
            "requested budget must be positive",
        )
    if weather.assembled_at_utc > evaluated + freshness_policy.future_tolerance:
        return _reject(
            evaluated,
            QuoteRejectionReason.SNAPSHOT_MISMATCH,
            "weather snapshot was assembled after quote evaluation",
        )

    checks, freshness_rejection = _check_freshness(
        weather=weather,
        event=event,
        order_book=order_book,
        balance=balance,
        evaluated_at=evaluated,
        policy=freshness_policy,
    )
    if freshness_rejection is not None:
        return freshness_rejection

    if balance is not None and budget > balance.available_cash:
        return _reject(
            evaluated,
            QuoteRejectionReason.INSUFFICIENT_BALANCE,
            (f"requested budget {budget} exceeds available cash {balance.available_cash}"),
            checks,
        )

    variable_cost_multiplier = (
        Decimal("1") + cost_policy.platform_fee_rate + cost_policy.safety_margin_rate
    )
    remaining_after_fixed_cost = budget - cost_policy.transaction_cost
    if remaining_after_fixed_cost <= 0:
        return _reject(
            evaluated,
            QuoteRejectionReason.BELOW_MINIMUM_ORDER,
            "approved budget does not cover the fixed transaction-cost reserve",
            checks,
        )
    book_budget_limit = (remaining_after_fixed_cost / variable_cost_multiplier).next_minus()
    available_notional = sum(
        (level.price * level.size for level in order_book.asks),
        start=Decimal("0"),
    )
    executable_budget = book_budget_limit
    if available_notional < book_budget_limit:
        if cost_policy.depth_policy is DepthPolicy.REJECT:
            return _reject(
                evaluated,
                QuoteRejectionReason.INSUFFICIENT_DEPTH,
                (
                    f"book budget {book_budget_limit} exceeds displayed ask notional "
                    f"{available_notional}"
                ),
                checks,
            )
        executable_budget = available_notional

    try:
        quote = order_book.quote_buy_budget(executable_budget)
    except OrderBookError as exc:
        message = str(exc)
        reason = (
            QuoteRejectionReason.BELOW_MINIMUM_ORDER
            if "below minimum" in message
            else QuoteRejectionReason.INSUFFICIENT_DEPTH
        )
        return _reject(evaluated, reason, message, checks)

    average_slippage = quote.average_price - quote.best_ask
    worst_slippage = quote.worst_price - quote.best_ask
    if average_slippage > cost_policy.maximum_average_slippage:
        return _reject(
            evaluated,
            QuoteRejectionReason.SLIPPAGE_EXCEEDED,
            (f"average slippage {average_slippage} exceeds {cost_policy.maximum_average_slippage}"),
            checks,
        )
    if worst_slippage > cost_policy.maximum_worst_slippage:
        return _reject(
            evaluated,
            QuoteRejectionReason.SLIPPAGE_EXCEEDED,
            (f"worst slippage {worst_slippage} exceeds {cost_policy.maximum_worst_slippage}"),
            checks,
        )

    platform_fee = quote.total_cost * cost_policy.platform_fee_rate
    safety_margin = quote.total_cost * cost_policy.safety_margin_rate
    total_all_in_cost = (
        quote.total_cost + platform_fee + cost_policy.transaction_cost + safety_margin
    )
    if total_all_in_cost > budget:
        return _reject(
            evaluated,
            QuoteRejectionReason.INVALID_INPUT,
            "calculated all-in cost exceeds the approved budget",
            checks,
        )
    all_in_average_price = total_all_in_cost / quote.shares
    if all_in_average_price >= cost_policy.maximum_all_in_price:
        return _reject(
            evaluated,
            QuoteRejectionReason.PRICE_EXCEEDED,
            (
                f"all-in average price {all_in_average_price} reaches or exceeds "
                f"{cost_policy.maximum_all_in_price}"
            ),
            checks,
        )

    raw_probability_edge = model_probability - quote.average_price
    probability_edge = model_probability - all_in_average_price
    gross_expected_payout = model_probability * quote.shares
    expected_profit = gross_expected_payout - total_all_in_cost
    expected_return = expected_profit / total_all_in_cost

    if raw_probability_edge <= 0:
        return _reject(
            evaluated,
            QuoteRejectionReason.NON_POSITIVE_EDGE,
            f"model probability {model_probability} does not exceed executable price",
            checks,
        )
    if probability_edge <= 0 or expected_profit <= 0:
        return _reject(
            evaluated,
            QuoteRejectionReason.FEE_ERASED_EDGE,
            "fees, transaction cost, or safety margin erase the executable edge",
            checks,
        )
    if expected_return < cost_policy.minimum_expected_return:
        return _reject(
            evaluated,
            QuoteRejectionReason.EXPECTED_RETURN_BELOW_FLOOR,
            (
                f"net expected return {expected_return} is below "
                f"{cost_policy.minimum_expected_return}"
            ),
            checks,
        )

    validated = ValidatedExecutableQuote(
        quote=quote,
        model_probability=model_probability,
        requested_budget=budget,
        book_budget_limit=book_budget_limit,
        executable_budget=quote.total_cost,
        freshness_policy=freshness_policy,
        cost_policy=cost_policy,
        platform_fee=platform_fee,
        transaction_cost=cost_policy.transaction_cost,
        safety_margin=safety_margin,
        total_all_in_cost=total_all_in_cost,
        all_in_average_price=all_in_average_price,
        gross_expected_payout=gross_expected_payout,
        expected_profit=expected_profit,
        expected_return=expected_return,
        probability_edge=probability_edge,
        average_slippage=average_slippage,
        worst_slippage=worst_slippage,
        depth_reduced=quote.total_cost < book_budget_limit,
        evaluated_at_utc=evaluated,
        event_id=event.event_id,
        freshness=MappingProxyType(checks),
    )
    return QuoteEvaluation(evaluated_at_utc=evaluated, quote=validated)


def revalidate_executable_buy(
    previous: ValidatedExecutableQuote,
    *,
    probability: Decimal | int | str | float,
    requested_budget: Decimal | int | str | float,
    weather: WeatherInputSnapshot,
    event: MarketEventSnapshot,
    order_book: OrderBookSnapshot,
    evaluated_at: datetime,
    freshness_policy: FreshnessPolicy,
    cost_policy: CostPolicy,
    balance: BalanceSnapshot | None = None,
) -> QuoteEvaluation:
    """Re-evaluate immediately before adapter execution against a refreshed book."""
    evaluated = as_utc(evaluated_at, label="quote evaluation time")
    try:
        current_probability = as_decimal(probability, label="model probability")
        current_budget = as_decimal(requested_budget, label="requested budget")
    except QuoteValidationError as exc:
        return _reject(
            evaluated,
            QuoteRejectionReason.INVALID_INPUT,
            str(exc),
        )
    mismatch: str | None = None
    if order_book.token_id != previous.token_id:
        mismatch = (
            f"refreshed token {order_book.token_id} does not match "
            f"validated token {previous.token_id}"
        )
    elif event.event_id != previous.event_id:
        mismatch = (
            f"refreshed event {event.event_id} does not match validated event {previous.event_id}"
        )
    elif current_probability != previous.model_probability:
        mismatch = "model probability changed after quote validation"
    elif current_budget != previous.requested_budget:
        mismatch = "requested budget changed after quote validation"
    elif freshness_policy != previous.freshness_policy:
        mismatch = "freshness policy changed after quote validation"
    elif cost_policy != previous.cost_policy:
        mismatch = "cost policy changed after quote validation"
    if mismatch is not None:
        return _reject(
            evaluated,
            QuoteRejectionReason.SNAPSHOT_MISMATCH,
            mismatch,
        )
    return evaluate_executable_buy(
        probability=probability,
        requested_budget=requested_budget,
        weather=weather,
        event=event,
        order_book=order_book,
        evaluated_at=evaluated_at,
        freshness_policy=freshness_policy,
        cost_policy=cost_policy,
        balance=balance,
    )
