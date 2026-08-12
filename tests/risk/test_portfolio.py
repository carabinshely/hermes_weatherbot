from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from tests.risk.portfolio_helpers import (
    MARKET_A,
    MARKET_B,
    NOW,
    YES_A,
    YES_B,
    buy_intent_created,
    fill,
    filled_position_events,
    opened,
    policy,
    risk_scope,
    scope_registered,
    sell_intent_created,
    state_for,
    submitted,
    valuation_for,
    valuation_recorded,
)
from weatherbot.domain import (
    LedgerEvent,
    Money,
    PortfolioValuation,
    PositionValuation,
    RiskDecisionStatus,
    RiskScope,
)
from weatherbot.risk import (
    PortfolioRiskDecision,
    PortfolioRiskPolicy,
    PortfolioRiskRejectionReason,
    evaluate_portfolio_risk,
)


def evaluate(
    *,
    events: tuple[LedgerEvent, ...],
    proposed_scope: RiskScope | None = None,
    proposed_cash: str = "4",
    selected_policy: PortfolioRiskPolicy | None = None,
    valuation: PortfolioValuation | None = None,
) -> PortfolioRiskDecision:
    state = state_for(events)
    return evaluate_portfolio_risk(
        state=state,
        events=events,
        proposed_scope=proposed_scope
        or risk_scope(MARKET_B, YES_B, event_id="event-b", city_key="boston"),
        proposed_cash=Money.of(proposed_cash),
        valuation=valuation or valuation_for(state),
        policy=selected_policy or policy(),
        evaluated_at=NOW,
    )


def test_baseline_portfolio_risk_approves_and_is_auditable() -> None:
    events: tuple[LedgerEvent, ...] = (opened(),)

    decision = evaluate(events=events)

    assert decision.status is RiskDecisionStatus.APPROVED
    assert decision.total_exposure_before == Money.zero()
    assert decision.total_exposure_after == Money.of("4")
    assert decision.open_positions_before == 0
    assert decision.open_positions_after == 1
    assert decision.daily_loss == Money.zero()
    assert decision.drawdown == Money.zero()
    assert decision.metadata()["portfolio_risk_total_exposure_after"] == "4.000000"


def test_duplicate_active_buy_intent_is_rejected() -> None:
    scope = risk_scope()
    intent = buy_intent_created()
    events: tuple[LedgerEvent, ...] = (opened(), scope_registered(scope), intent)

    decision = evaluate(events=events, proposed_scope=scope)

    assert decision.status is RiskDecisionStatus.REJECTED
    assert decision.rejection_reason is PortfolioRiskRejectionReason.DUPLICATE_EXPOSURE
    assert decision.total_exposure_before == Money.of("4")


def test_duplicate_open_position_is_rejected() -> None:
    scope = risk_scope()
    events = filled_position_events(scope)

    decision = evaluate(events=events, proposed_scope=scope)

    assert decision.status is RiskDecisionStatus.REJECTED
    assert decision.rejection_reason is PortfolioRiskRejectionReason.DUPLICATE_EXPOSURE
    assert decision.total_exposure_before == Money.of("4.10")


def test_total_exposure_cap_rejects_new_entry() -> None:
    events = filled_position_events()

    decision = evaluate(events=events, selected_policy=policy(total="8"))

    assert decision.rejection_reason is PortfolioRiskRejectionReason.TOTAL_EXPOSURE
    assert decision.total_exposure_after == Money.of("8.10")


def test_event_exposure_cap_rejects_related_market() -> None:
    existing_scope = risk_scope(event_id="shared-event")
    events = filled_position_events(existing_scope)
    proposed = risk_scope(
        MARKET_B,
        YES_B,
        event_id="shared-event",
        city_key="boston",
    )

    decision = evaluate(
        events=events,
        proposed_scope=proposed,
        selected_policy=policy(event="8"),
    )

    assert decision.rejection_reason is PortfolioRiskRejectionReason.EVENT_EXPOSURE
    assert decision.event_exposure_after == Money.of("8.10")


def test_city_date_exposure_cap_rejects_same_city_and_date() -> None:
    existing_scope = risk_scope(event_id="event-a")
    events = filled_position_events(existing_scope)
    proposed = risk_scope(
        MARKET_B,
        YES_B,
        event_id="event-b",
        city_key="new-york",
        market_date=date(2026, 1, 3),
    )

    decision = evaluate(
        events=events,
        proposed_scope=proposed,
        selected_policy=policy(city_date="8"),
    )

    assert decision.rejection_reason is PortfolioRiskRejectionReason.CITY_DATE_EXPOSURE
    assert decision.city_date_exposure_after == Money.of("8.10")


def test_same_market_date_is_an_automatic_correlation_group() -> None:
    events = filled_position_events(risk_scope(event_id="event-a"))
    proposed = risk_scope(
        MARKET_B,
        YES_B,
        event_id="event-b",
        city_key="boston",
        market_date=date(2026, 1, 3),
    )

    decision = evaluate(
        events=events,
        proposed_scope=proposed,
        selected_policy=policy(correlation="8"),
    )

    assert decision.rejection_reason is PortfolioRiskRejectionReason.CORRELATION_EXPOSURE
    date_group = decision.correlation_map["date:2026-01-03"]
    assert date_group.before == Money.of("4.10")
    assert date_group.after == Money.of("8.10")


def test_explicit_weather_system_group_correlates_different_dates() -> None:
    group = "weather-system:nor-easter-1"
    existing_scope = risk_scope(groups=(group,))
    events = filled_position_events(existing_scope)
    proposed = risk_scope(
        MARKET_B,
        YES_B,
        event_id="event-b",
        city_key="boston",
        market_date=date(2026, 1, 4),
        groups=(group,),
    )

    decision = evaluate(
        events=events,
        proposed_scope=proposed,
        selected_policy=policy(correlation="8"),
    )

    assert decision.rejection_reason is PortfolioRiskRejectionReason.CORRELATION_EXPOSURE
    assert decision.correlation_map[group].after == Money.of("8.10")


def test_open_position_count_cap_counts_unique_exposed_position_keys() -> None:
    events = filled_position_events()

    decision = evaluate(events=events, selected_policy=policy(positions=1))

    assert decision.rejection_reason is PortfolioRiskRejectionReason.MAX_OPEN_POSITIONS
    assert decision.open_positions_before == 1
    assert decision.open_positions_after == 2


def test_existing_exposure_without_durable_scope_fails_closed() -> None:
    intent = buy_intent_created(
        decision_id="legacy-filled",
        quantity="10",
        limit_price="0.50",
        fee_reserve="0.10",
    )
    events: tuple[LedgerEvent, ...] = (
        opened(),
        intent,
        submitted(intent),
        fill(intent, price="0.40", fee="0.10"),
    )

    decision = evaluate(events=events)

    assert decision.rejection_reason is PortfolioRiskRejectionReason.MISSING_SCOPE
    assert decision.missing_scope_keys == (f"{MARKET_A}/{YES_A}",)


def test_stale_portfolio_valuation_fails_closed() -> None:
    events: tuple[LedgerEvent, ...] = (opened(),)
    state = state_for(events)
    stale = valuation_for(state, assembled_at=NOW - timedelta(seconds=31))

    decision = evaluate(events=events, valuation=stale)

    assert decision.rejection_reason is PortfolioRiskRejectionReason.STALE_VALUATION


def test_valuation_quantity_mismatch_fails_closed() -> None:
    events = filled_position_events()
    state = state_for(events)
    wrong_mark = PositionValuation(
        market_id=MARKET_A,
        outcome_id=YES_A,
        quantity=Decimal("9"),
        liquidation_value=Money.of("4.10"),
        observed_at=NOW - timedelta(seconds=1),
    )
    valuation = PortfolioValuation(
        positions=(wrong_mark,),
        equity=state.cash + wrong_mark.liquidation_value,
        assembled_at=NOW,
        source="wrong-quantity-test",
    )

    decision = evaluate(events=events, valuation=valuation)

    assert decision.rejection_reason is PortfolioRiskRejectionReason.VALUATION_MISMATCH
    assert decision.detail is not None
    assert "quantity" in decision.detail


def test_daily_loss_combines_today_realized_and_current_unrealized_pnl() -> None:
    base = filled_position_events()
    sell = sell_intent_created()
    events: tuple[LedgerEvent, ...] = (
        *base,
        sell,
        submitted(sell, suffix="sell"),
        fill(sell, suffix="sell", quantity="5", price="0.30", fee="0"),
    )
    state = state_for(events)
    valuation = valuation_for(
        state,
        liquidation_values={(MARKET_A, YES_A): "1.50"},
    )

    decision = evaluate(
        events=events,
        valuation=valuation,
        selected_policy=policy(daily_loss="1"),
    )

    assert decision.realized_pnl_today == Money.of("-0.55")
    assert decision.unrealized_pnl == Money.of("-0.55")
    assert decision.daily_pnl == Money.of("-1.10")
    assert decision.daily_loss == Money.of("1.10")
    assert decision.rejection_reason is PortfolioRiskRejectionReason.DAILY_LOSS


def test_drawdown_uses_durable_historical_valuation_high_water_mark() -> None:
    base = filled_position_events()
    state = state_for(base)
    high = valuation_for(
        state,
        liquidation_values={(MARKET_A, YES_A): "14.10"},
        assembled_at=NOW - timedelta(minutes=5),
    )
    events: tuple[LedgerEvent, ...] = (*base, valuation_recorded(high, suffix="high-water"))
    current_state = state_for(events)
    current = valuation_for(
        current_state,
        liquidation_values={(MARKET_A, YES_A): "2.00"},
    )

    decision = evaluate(
        events=events,
        valuation=current,
        selected_policy=policy(drawdown="10"),
    )

    assert decision.high_water_mark == Money.of("110")
    assert decision.current_equity == Money.of("97.90")
    assert decision.drawdown == Money.of("12.10")
    assert decision.rejection_reason is PortfolioRiskRejectionReason.DRAWDOWN
