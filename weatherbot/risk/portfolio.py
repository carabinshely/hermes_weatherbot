"""Pure durable-ledger portfolio, correlation, loss, and drawdown controls."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, time
from decimal import Decimal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from weatherbot.domain import (
    AccountOpened,
    LedgerEvent,
    LedgerState,
    Money,
    PortfolioValuation,
    PortfolioValuationRecorded,
    PositionKey,
    PositionStatus,
    RiskDecisionStatus,
    RiskScope,
    RiskScopeRegistered,
    Side,
    apply_event,
)
from weatherbot.risk.portfolio_model import (
    CorrelationExposure,
    PortfolioRiskDecision,
    PortfolioRiskPolicy,
    PortfolioRiskRejectionReason,
)

_ZERO = Decimal("0")


@dataclass(frozen=True, slots=True)
class _DecisionInputs:
    proposed_scope: RiskScope
    proposed_cash: Money
    total_before: Money
    total_after: Money
    event_before: Money
    event_after: Money
    city_date_before: Money
    city_date_after: Money
    correlation: tuple[CorrelationExposure, ...]
    open_before: int
    open_after: int
    realized_today: Money
    unrealized: Money
    daily_pnl: Money
    daily_loss: Money
    current_equity: Money
    high_water: Money
    drawdown: Money


def _risk_scopes(events: tuple[LedgerEvent, ...]) -> dict[PositionKey, RiskScope]:
    scopes: dict[PositionKey, RiskScope] = {}
    for event in events:
        if not isinstance(event, RiskScopeRegistered):
            continue
        key = event.scope.position_key
        existing = scopes.get(key)
        if existing is not None and existing != event.scope:
            raise ValueError(f"risk scope changed for {key[0]}/{key[1]}")
        scopes[key] = event.scope
    return scopes


def _exposure_by_position(state: LedgerState) -> dict[PositionKey, Money]:
    exposure: dict[PositionKey, Money] = {}
    for key, position in state.positions.items():
        if position.status is PositionStatus.OPEN and position.quantity > 0:
            exposure[key] = position.cost_basis
    for order in state.orders.values():
        if order.intent.side is not Side.BUY or order.state.is_terminal:
            continue
        key = (order.intent.market_id, order.intent.outcome_id)
        exposure[key] = exposure.get(key, Money.zero(state.currency)) + order.reserved_cash
    return exposure


def _sum_money(values: list[Money], *, currency: str) -> Money:
    total = Money.zero(currency)
    for value in values:
        total += value
    return total


def _realized_total(state: LedgerState) -> Money:
    return _sum_money(
        [position.realized_pnl for position in state.positions.values()],
        currency=state.currency,
    )


def _realized_pnl_today(
    events: tuple[LedgerEvent, ...],
    *,
    currency: str,
    evaluated_at: datetime,
    timezone_name: str,
) -> Money:
    try:
        timezone = ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError as exc:
        raise ValueError(f"unknown loss timezone {timezone_name!r}") from exc
    local_date = evaluated_at.astimezone(timezone).date()
    start = datetime.combine(local_date, time.min, timezone).astimezone(UTC)
    end = datetime.combine(local_date, time.max, timezone).astimezone(UTC)

    state = LedgerState.empty(currency)
    realized = Money.zero(currency)
    for event in events:
        if event.occurred_at > evaluated_at:
            break
        before = _realized_total(state)
        state = apply_event(state, event)
        after = _realized_total(state)
        if start <= event.occurred_at.astimezone(UTC) <= end:
            realized += after - before
    return realized


def _validate_valuation(
    state: LedgerState,
    valuation: PortfolioValuation,
    *,
    evaluated_at: datetime,
    policy: PortfolioRiskPolicy,
) -> str | None:
    if valuation.equity.currency != state.currency:
        return "portfolio valuation currency differs from ledger currency"
    if valuation.assembled_at > evaluated_at + policy.future_tolerance:
        return "portfolio valuation was assembled in the future"
    if evaluated_at - valuation.assembled_at > policy.maximum_valuation_age:
        return "portfolio valuation is stale"

    open_positions = {
        key: position
        for key, position in state.positions.items()
        if position.status is PositionStatus.OPEN and position.quantity > 0
    }
    marks = {mark.position_key: mark for mark in valuation.positions}
    if set(marks) != set(open_positions):
        return "portfolio valuation does not cover exactly the open positions"

    liquidation_total = Money.zero(state.currency)
    for key, position in open_positions.items():
        mark = marks[key]
        if mark.quantity != position.quantity:
            return f"valuation quantity differs for {key[0]}/{key[1]}"
        if mark.liquidation_value.currency != state.currency:
            return f"valuation currency differs for {key[0]}/{key[1]}"
        if mark.observed_at > evaluated_at + policy.future_tolerance:
            return f"valuation mark is unexpectedly in the future for {key[0]}/{key[1]}"
        if evaluated_at - mark.observed_at > policy.maximum_valuation_age:
            return f"valuation mark is stale for {key[0]}/{key[1]}"
        liquidation_total += mark.liquidation_value

    if valuation.equity != state.cash + liquidation_total:
        return "portfolio valuation equity does not reconcile to cash plus liquidation values"
    return None


def _unrealized_pnl(state: LedgerState, valuation: PortfolioValuation) -> Money:
    marks = {mark.position_key: mark for mark in valuation.positions}
    unrealized = Money.zero(state.currency)
    for key, position in state.positions.items():
        if position.status is not PositionStatus.OPEN or position.quantity <= 0:
            continue
        unrealized += marks[key].liquidation_value - position.cost_basis
    return unrealized


def _high_water_mark(
    events: tuple[LedgerEvent, ...],
    *,
    current_equity: Money,
    evaluated_at: datetime,
) -> Money:
    initial_cash: Money | None = None
    prior_equities: list[Money] = []
    for event in events:
        if isinstance(event, AccountOpened) and initial_cash is None:
            initial_cash = event.initial_cash
        elif isinstance(event, PortfolioValuationRecorded) and event.occurred_at <= evaluated_at:
            prior_equities.append(event.valuation.equity)
    if initial_cash is None:
        raise ValueError("opened ledger has no AccountOpened event")
    if initial_cash.currency != current_equity.currency:
        raise ValueError("initial cash currency differs from current equity")
    amounts = [initial_cash.amount, current_equity.amount]
    for equity in prior_equities:
        if equity.currency != current_equity.currency:
            raise ValueError("historical valuation currency differs from current equity")
        amounts.append(equity.amount)
    return Money.of(max(amounts), current_equity.currency)


def _decision(
    *,
    status: RiskDecisionStatus,
    reason: PortfolioRiskRejectionReason | None,
    detail: str | None,
    inputs: _DecisionInputs,
    missing_scope_keys: tuple[str, ...] = (),
) -> PortfolioRiskDecision:
    return PortfolioRiskDecision(
        status=status,
        proposed_scope=inputs.proposed_scope,
        proposed_cash=inputs.proposed_cash,
        total_exposure_before=inputs.total_before,
        total_exposure_after=inputs.total_after,
        event_exposure_before=inputs.event_before,
        event_exposure_after=inputs.event_after,
        city_date_exposure_before=inputs.city_date_before,
        city_date_exposure_after=inputs.city_date_after,
        correlation_exposures=inputs.correlation,
        open_positions_before=inputs.open_before,
        open_positions_after=inputs.open_after,
        realized_pnl_today=inputs.realized_today,
        unrealized_pnl=inputs.unrealized,
        daily_pnl=inputs.daily_pnl,
        daily_loss=inputs.daily_loss,
        current_equity=inputs.current_equity,
        high_water_mark=inputs.high_water,
        drawdown=inputs.drawdown,
        rejection_reason=reason,
        detail=detail,
        missing_scope_keys=missing_scope_keys,
    )


def evaluate_portfolio_risk(
    *,
    state: LedgerState,
    events: tuple[LedgerEvent, ...],
    proposed_scope: RiskScope,
    proposed_cash: Money,
    valuation: PortfolioValuation,
    policy: PortfolioRiskPolicy,
    evaluated_at: datetime,
) -> PortfolioRiskDecision:
    """Evaluate one new BUY against durable exposure, correlation, and loss controls."""
    if evaluated_at.tzinfo is None or evaluated_at.utcoffset() is None:
        raise ValueError("portfolio risk evaluation time must be timezone-aware")
    state.assert_invariants()
    if not state.opened:
        raise ValueError("portfolio risk requires an opened ledger")
    if proposed_cash.amount <= 0:
        raise ValueError("proposed cash must be positive")
    if proposed_cash.currency != state.currency or policy.currency != state.currency:
        raise ValueError("portfolio risk inputs use a different currency from the ledger")

    scopes = _risk_scopes(events)
    registered_scope = scopes.get(proposed_scope.position_key)
    if registered_scope is not None and registered_scope != proposed_scope:
        raise ValueError("proposed risk scope conflicts with durable registered scope")

    exposure = _exposure_by_position(state)
    total_before = _sum_money(list(exposure.values()), currency=state.currency)
    total_after = total_before + proposed_cash
    existing_keys = set(exposure)
    open_before = len(existing_keys)
    is_duplicate = proposed_scope.position_key in existing_keys
    open_after = open_before if is_duplicate else open_before + 1

    missing_keys = tuple(sorted(f"{key[0]}/{key[1]}" for key in existing_keys if key not in scopes))

    def scoped_exposure(predicate: Callable[[RiskScope], bool]) -> Money:
        values = [
            amount for key, amount in exposure.items() if key in scopes and predicate(scopes[key])
        ]
        return _sum_money(values, currency=state.currency)

    event_before = scoped_exposure(lambda scope: scope.event_id == proposed_scope.event_id)
    event_after = event_before + proposed_cash
    city_date_before = scoped_exposure(
        lambda scope: scope.city_date_key == proposed_scope.city_date_key
    )
    city_date_after = city_date_before + proposed_cash
    correlation_items: list[CorrelationExposure] = []
    for group in proposed_scope.all_correlation_groups:
        before = scoped_exposure(lambda scope, target=group: target in scope.all_correlation_groups)
        correlation_items.append(
            CorrelationExposure(
                group=group,
                before=before,
                after=before + proposed_cash,
            )
        )
    correlation = tuple(correlation_items)

    valuation_error = _validate_valuation(
        state,
        valuation,
        evaluated_at=evaluated_at,
        policy=policy,
    )
    realized_today = _realized_pnl_today(
        events,
        currency=state.currency,
        evaluated_at=evaluated_at,
        timezone_name=policy.loss_timezone,
    )
    if valuation_error is None:
        unrealized = _unrealized_pnl(state, valuation)
        current_equity = valuation.equity
        high_water = _high_water_mark(
            events,
            current_equity=current_equity,
            evaluated_at=evaluated_at,
        )
    else:
        unrealized = Money.zero(state.currency)
        current_equity = state.cash
        high_water = current_equity
    daily_pnl = realized_today + unrealized
    daily_loss = Money.of(max(_ZERO, -daily_pnl.amount), state.currency)
    drawdown = Money.of(max(_ZERO, high_water.amount - current_equity.amount), state.currency)

    inputs = _DecisionInputs(
        proposed_scope=proposed_scope,
        proposed_cash=proposed_cash,
        total_before=total_before,
        total_after=total_after,
        event_before=event_before,
        event_after=event_after,
        city_date_before=city_date_before,
        city_date_after=city_date_after,
        correlation=correlation,
        open_before=open_before,
        open_after=open_after,
        realized_today=realized_today,
        unrealized=unrealized,
        daily_pnl=daily_pnl,
        daily_loss=daily_loss,
        current_equity=current_equity,
        high_water=high_water,
        drawdown=drawdown,
    )

    if valuation_error is not None:
        reason = (
            PortfolioRiskRejectionReason.STALE_VALUATION
            if "stale" in valuation_error or "future" in valuation_error
            else PortfolioRiskRejectionReason.VALUATION_MISMATCH
        )
        return _decision(
            status=RiskDecisionStatus.REJECTED,
            reason=reason,
            detail=valuation_error,
            inputs=inputs,
        )
    if missing_keys:
        return _decision(
            status=RiskDecisionStatus.REJECTED,
            reason=PortfolioRiskRejectionReason.MISSING_SCOPE,
            detail="existing exposure is missing durable portfolio risk scope",
            missing_scope_keys=missing_keys,
            inputs=inputs,
        )
    if is_duplicate:
        return _decision(
            status=RiskDecisionStatus.REJECTED,
            reason=PortfolioRiskRejectionReason.DUPLICATE_EXPOSURE,
            detail="position key already has an open position or active BUY intent",
            inputs=inputs,
        )
    if daily_loss.amount >= policy.maximum_daily_loss.amount:
        return _decision(
            status=RiskDecisionStatus.REJECTED,
            reason=PortfolioRiskRejectionReason.DAILY_LOSS,
            detail="realized-today plus current unrealized loss reached the daily limit",
            inputs=inputs,
        )
    if drawdown.amount >= policy.maximum_drawdown.amount:
        return _decision(
            status=RiskDecisionStatus.REJECTED,
            reason=PortfolioRiskRejectionReason.DRAWDOWN,
            detail="current liquidation equity reached the drawdown limit",
            inputs=inputs,
        )
    if open_after > policy.maximum_open_positions:
        return _decision(
            status=RiskDecisionStatus.REJECTED,
            reason=PortfolioRiskRejectionReason.MAX_OPEN_POSITIONS,
            detail="proposed exposure exceeds the maximum open-position count",
            inputs=inputs,
        )
    if total_after.amount > policy.maximum_total_exposure.amount:
        return _decision(
            status=RiskDecisionStatus.REJECTED,
            reason=PortfolioRiskRejectionReason.TOTAL_EXPOSURE,
            detail="proposed exposure exceeds the total portfolio cap",
            inputs=inputs,
        )
    if event_after.amount > policy.maximum_event_exposure.amount:
        return _decision(
            status=RiskDecisionStatus.REJECTED,
            reason=PortfolioRiskRejectionReason.EVENT_EXPOSURE,
            detail="proposed exposure exceeds the event cap",
            inputs=inputs,
        )
    if city_date_after.amount > policy.maximum_city_date_exposure.amount:
        return _decision(
            status=RiskDecisionStatus.REJECTED,
            reason=PortfolioRiskRejectionReason.CITY_DATE_EXPOSURE,
            detail="proposed exposure exceeds the city/date cap",
            inputs=inputs,
        )
    for item in correlation:
        if item.after.amount > policy.maximum_correlation_group_exposure.amount:
            return _decision(
                status=RiskDecisionStatus.REJECTED,
                reason=PortfolioRiskRejectionReason.CORRELATION_EXPOSURE,
                detail=f"proposed exposure exceeds correlation cap for {item.group}",
                inputs=inputs,
            )

    return _decision(
        status=RiskDecisionStatus.APPROVED,
        reason=None,
        detail=None,
        inputs=inputs,
    )
