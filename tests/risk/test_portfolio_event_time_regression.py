from __future__ import annotations

from datetime import timedelta

from tests.risk.portfolio_helpers import (
    MARKET_B,
    NOW,
    YES_B,
    fill,
    filled_position_events,
    policy,
    risk_scope,
    sell_intent_created,
    state_for,
    submitted,
    valuation_for,
    valuation_recorded,
)
from weatherbot.domain import LedgerEvent, Money
from weatherbot.risk import (
    PortfolioRiskRejectionReason,
    evaluate_portfolio_risk,
)


def test_future_dated_event_does_not_hide_later_appended_realized_loss() -> None:
    base = filled_position_events()
    base_state = state_for(base)
    future_valuation = valuation_recorded(
        valuation_for(
            base_state,
            assembled_at=NOW + timedelta(seconds=1),
        ),
        suffix="future-before-sell",
    )
    sell = sell_intent_created(
        decision_id="older-sell-after-future-event",
        quantity="5",
        limit_price="0.30",
    )
    events: tuple[LedgerEvent, ...] = (
        *base,
        future_valuation,
        sell,
        submitted(sell, suffix="older-sell"),
        fill(
            sell,
            suffix="older-sell",
            quantity="5",
            price="0.30",
        ),
    )
    state = state_for(events)

    decision = evaluate_portfolio_risk(
        state=state,
        events=events,
        proposed_scope=risk_scope(
            MARKET_B,
            YES_B,
            event_id="event-b",
            city_key="boston",
        ),
        proposed_cash=Money.of("4"),
        valuation=valuation_for(state),
        policy=policy(daily_loss="0.5"),
        evaluated_at=NOW,
    )

    assert decision.realized_pnl_today == Money.of("-0.55")
    assert decision.daily_loss == Money.of("0.55")
    assert decision.rejection_reason is PortfolioRiskRejectionReason.DAILY_LOSS
