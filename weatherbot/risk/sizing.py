"""Pure bankroll sizing composed with the executable-quote contract from #17."""

from __future__ import annotations

from datetime import datetime
from decimal import ROUND_DOWN, Decimal, InvalidOperation

from weatherbot.domain import Money, RiskDecisionStatus
from weatherbot.forecasting import WeatherInputSnapshot
from weatherbot.markets import OrderBookSnapshot
from weatherbot.quoting import (
    BalanceSnapshot,
    CostPolicy,
    FreshnessPolicy,
    MarketEventSnapshot,
    QuoteRejectionReason,
    evaluate_executable_buy,
)
from weatherbot.risk.model import (
    BindingCap,
    RiskCapitalSnapshot,
    SizingDecision,
    SizingPolicy,
    SizingRejectionReason,
)


type RatioInput = Decimal | int | str

_MONEY_QUANTUM = Decimal("0.000001")
_ZERO = Decimal("0")
_ONE = Decimal("1")


def _ratio(value: RatioInput, *, label: str) -> Decimal:
    if isinstance(value, bool):
        raise TypeError(f"{label} must not be boolean")
    try:
        parsed = value if isinstance(value, Decimal) else Decimal(value)
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"{label} must be decimal") from exc
    if not parsed.is_finite():
        raise ValueError(f"{label} must be finite")
    return parsed


def _floor_money(value: Decimal, *, currency: str) -> Money:
    """Round a positive cash budget toward zero so a cap can never be overspent."""
    if value <= 0:
        return Money.zero(currency)
    floored = value.quantize(_MONEY_QUANTUM, rounding=ROUND_DOWN)
    return Money.of(floored, currency)


def _kelly(probability: Decimal, all_in_price: Decimal) -> Decimal:
    if all_in_price <= 0 or all_in_price >= 1:
        raise ValueError("Kelly price must be greater than zero and less than one")
    if probability <= all_in_price:
        return _ZERO
    return (probability - all_in_price) / (_ONE - all_in_price)


def _cash_from_kelly(
    *,
    capital: RiskCapitalSnapshot,
    policy: SizingPolicy,
    raw_kelly: Decimal,
) -> tuple[Decimal, Decimal, Decimal, BindingCap]:
    fractional_kelly = raw_kelly * policy.fractional_kelly_multiplier
    uncapped = capital.available_cash.amount * fractional_kelly
    available = capital.available_cash.amount
    per_trade = policy.maximum_cash_per_trade.amount

    if available <= per_trade and available <= uncapped:
        binding = BindingCap.AVAILABLE_CASH
        target = available
    elif per_trade <= uncapped:
        binding = BindingCap.MAX_CASH_PER_TRADE
        target = per_trade
    else:
        binding = BindingCap.KELLY
        target = uncapped
    return fractional_kelly, uncapped, target, binding


def _decision(
    *,
    status: RiskDecisionStatus,
    capital: RiskCapitalSnapshot,
    policy: SizingPolicy,
    probability: Decimal | None,
    seed_price: Decimal,
    raw_kelly: Decimal = _ZERO,
    fractional_kelly: Decimal = _ZERO,
    uncapped_cash: Decimal = _ZERO,
    target_cash: Money | None = None,
    binding_cap: BindingCap | None = None,
    iterations: int = 0,
    final_all_in_price: Decimal | None = None,
    quote=None,
    rejection_reason: SizingRejectionReason | None = None,
    quote_rejection_reason: QuoteRejectionReason | None = None,
    detail: str | None = None,
    depth_reduced: bool = False,
) -> SizingDecision:
    currency = capital.cash.currency
    return SizingDecision(
        status=status,
        capital=capital,
        policy=policy,
        model_probability=probability,
        seed_price=seed_price,
        final_all_in_price=final_all_in_price,
        raw_kelly=raw_kelly,
        fractional_kelly=fractional_kelly,
        uncapped_kelly_cash=Money.of(uncapped_cash, currency),
        target_cash=target_cash or Money.zero(currency),
        binding_cap=binding_cap,
        iterations=iterations,
        quote=quote,
        rejection_reason=rejection_reason,
        quote_rejection_reason=quote_rejection_reason,
        detail=detail,
        depth_reduced=depth_reduced,
    )


def size_executable_buy(
    *,
    capital: RiskCapitalSnapshot,
    probability: RatioInput,
    weather: WeatherInputSnapshot,
    event: MarketEventSnapshot,
    order_book: OrderBookSnapshot,
    evaluated_at: datetime,
    freshness_policy: FreshnessPolicy,
    cost_policy: CostPolicy,
    sizing_policy: SizingPolicy,
    balance: BalanceSnapshot | None = None,
) -> SizingDecision:
    """Size a BUY from available bankroll and converge downward on executable all-in cost.

    The loop never increases its cash budget. Each accepted step is repriced through the
    same executable quote evaluator used at the execution boundary, so fees, fixed costs,
    safety margin, slippage, market minimums, and displayed depth all participate in sizing.
    """
    seed_price = order_book.best_ask
    try:
        model_probability = _ratio(probability, label="model probability")
    except (TypeError, ValueError) as exc:
        return _decision(
            status=RiskDecisionStatus.REJECTED,
            capital=capital,
            policy=sizing_policy,
            probability=None,
            seed_price=seed_price,
            rejection_reason=SizingRejectionReason.INVALID_PROBABILITY,
            detail=str(exc),
        )
    if model_probability <= 0 or model_probability >= 1:
        return _decision(
            status=RiskDecisionStatus.REJECTED,
            capital=capital,
            policy=sizing_policy,
            probability=model_probability,
            seed_price=seed_price,
            rejection_reason=SizingRejectionReason.INVALID_PROBABILITY,
            detail="model probability must be greater than zero and less than one",
        )
    if capital.available_cash.is_zero:
        return _decision(
            status=RiskDecisionStatus.REJECTED,
            capital=capital,
            policy=sizing_policy,
            probability=model_probability,
            seed_price=seed_price,
            binding_cap=BindingCap.AVAILABLE_CASH,
            rejection_reason=SizingRejectionReason.NO_AVAILABLE_CASH,
            detail="available cash is zero after active reservations",
        )
    if model_probability <= seed_price:
        return _decision(
            status=RiskDecisionStatus.REJECTED,
            capital=capital,
            policy=sizing_policy,
            probability=model_probability,
            seed_price=seed_price,
            rejection_reason=SizingRejectionReason.NON_POSITIVE_EDGE,
            detail="model probability does not exceed the best displayed ask",
        )

    try:
        raw_kelly = _kelly(model_probability, seed_price)
    except ValueError as exc:
        return _decision(
            status=RiskDecisionStatus.REJECTED,
            capital=capital,
            policy=sizing_policy,
            probability=model_probability,
            seed_price=seed_price,
            rejection_reason=SizingRejectionReason.INVALID_PRICE,
            detail=str(exc),
        )
    fractional_kelly, uncapped_cash, target, binding = _cash_from_kelly(
        capital=capital,
        policy=sizing_policy,
        raw_kelly=raw_kelly,
    )
    current_budget = _floor_money(target, currency=capital.cash.currency)
    if current_budget.is_zero:
        return _decision(
            status=RiskDecisionStatus.REJECTED,
            capital=capital,
            policy=sizing_policy,
            probability=model_probability,
            seed_price=seed_price,
            raw_kelly=raw_kelly,
            fractional_kelly=fractional_kelly,
            uncapped_cash=uncapped_cash,
            binding_cap=BindingCap.MINIMUM_ORDER,
            rejection_reason=SizingRejectionReason.BELOW_MINIMUM_ORDER,
            detail="Kelly cash rounds below the six-decimal money quantum",
        )

    current_binding = binding
    depth_reduced = False
    final_all_in_price: Decimal | None = None

    for iteration in range(1, sizing_policy.maximum_iterations + 1):
        evaluation = evaluate_executable_buy(
            probability=model_probability,
            requested_budget=current_budget.amount,
            weather=weather,
            event=event,
            order_book=order_book,
            evaluated_at=evaluated_at,
            freshness_policy=freshness_policy,
            cost_policy=cost_policy,
            balance=balance,
        )
        if not evaluation.accepted:
            quote_reason = evaluation.rejection_reason
            rejection_reason = SizingRejectionReason.QUOTE_REJECTED
            rejection_binding = current_binding
            if quote_reason is QuoteRejectionReason.BELOW_MINIMUM_ORDER:
                rejection_reason = SizingRejectionReason.BELOW_MINIMUM_ORDER
                rejection_binding = BindingCap.MINIMUM_ORDER
            elif quote_reason is QuoteRejectionReason.INSUFFICIENT_DEPTH:
                rejection_binding = BindingCap.EXECUTABLE_DEPTH
            return _decision(
                status=RiskDecisionStatus.REJECTED,
                capital=capital,
                policy=sizing_policy,
                probability=model_probability,
                seed_price=seed_price,
                raw_kelly=raw_kelly,
                fractional_kelly=fractional_kelly,
                uncapped_cash=uncapped_cash,
                binding_cap=rejection_binding,
                iterations=iteration,
                final_all_in_price=final_all_in_price,
                rejection_reason=rejection_reason,
                quote_rejection_reason=quote_reason,
                detail=evaluation.detail,
                depth_reduced=depth_reduced,
            )

        quote = evaluation.quote
        assert quote is not None
        final_all_in_price = quote.all_in_average_price
        raw_kelly = _kelly(model_probability, final_all_in_price)
        if raw_kelly <= 0:
            return _decision(
                status=RiskDecisionStatus.REJECTED,
                capital=capital,
                policy=sizing_policy,
                probability=model_probability,
                seed_price=seed_price,
                raw_kelly=raw_kelly,
                binding_cap=BindingCap.KELLY,
                iterations=iteration,
                final_all_in_price=final_all_in_price,
                rejection_reason=SizingRejectionReason.NON_POSITIVE_EDGE,
                detail="executable all-in price leaves no positive Kelly fraction",
                depth_reduced=depth_reduced,
            )

        fractional_kelly, uncapped_cash, candidate, candidate_binding = _cash_from_kelly(
            capital=capital,
            policy=sizing_policy,
            raw_kelly=raw_kelly,
        )
        candidate_budget = _floor_money(candidate, currency=capital.cash.currency)

        if quote.depth_reduced:
            depth_reduced = True
            depth_budget = _floor_money(
                quote.total_all_in_cost,
                currency=capital.cash.currency,
            )
            if depth_budget.amount < candidate_budget.amount:
                candidate_budget = depth_budget
                candidate_binding = BindingCap.EXECUTABLE_DEPTH

        if candidate_budget.is_zero:
            return _decision(
                status=RiskDecisionStatus.REJECTED,
                capital=capital,
                policy=sizing_policy,
                probability=model_probability,
                seed_price=seed_price,
                raw_kelly=raw_kelly,
                fractional_kelly=fractional_kelly,
                uncapped_cash=uncapped_cash,
                binding_cap=BindingCap.MINIMUM_ORDER,
                iterations=iteration,
                final_all_in_price=final_all_in_price,
                rejection_reason=SizingRejectionReason.BELOW_MINIMUM_ORDER,
                detail="converged Kelly cash rounds below the six-decimal money quantum",
                depth_reduced=depth_reduced,
            )

        if candidate_budget.amount < current_budget.amount:
            current_budget = candidate_budget
            current_binding = candidate_binding
            continue

        return _decision(
            status=RiskDecisionStatus.APPROVED,
            capital=capital,
            policy=sizing_policy,
            probability=model_probability,
            seed_price=seed_price,
            raw_kelly=raw_kelly,
            fractional_kelly=fractional_kelly,
            uncapped_cash=uncapped_cash,
            target_cash=current_budget,
            binding_cap=current_binding,
            iterations=iteration,
            final_all_in_price=final_all_in_price,
            quote=quote,
            depth_reduced=depth_reduced,
        )

    return _decision(
        status=RiskDecisionStatus.REJECTED,
        capital=capital,
        policy=sizing_policy,
        probability=model_probability,
        seed_price=seed_price,
        raw_kelly=raw_kelly,
        fractional_kelly=fractional_kelly,
        uncapped_cash=uncapped_cash,
        binding_cap=current_binding,
        iterations=sizing_policy.maximum_iterations,
        final_all_in_price=final_all_in_price,
        rejection_reason=SizingRejectionReason.NON_CONVERGENT,
        detail="downward-only bankroll sizing did not converge within the iteration limit",
        depth_reduced=depth_reduced,
    )
