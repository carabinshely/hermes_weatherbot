from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

import pytest

from tests.risk.portfolio_helpers import (
    MARKET_A,
    NOW,
    YES_A,
    filled_position_events,
    opened,
    risk_scope,
    scope_registered,
    state_for,
    valuation_for,
    valuation_recorded,
)
from weatherbot.domain import (
    DuplicateEventConflict,
    InvariantViolation,
    LedgerEvent,
    Money,
    PortfolioValuation,
    PositionValuation,
    RiskScopeRegistered,
    apply_event,
    replay,
    risk_scope_event_id,
)
from weatherbot.persistence.codec import decode_event, encode_event


@pytest.mark.parametrize(
    "event",
    (
        scope_registered(risk_scope()),
        valuation_recorded(
            valuation_for(state_for((opened(),)), assembled_at=NOW),
            suffix="codec",
        ),
    ),
)
def test_portfolio_risk_events_round_trip_without_schema_bump(event: LedgerEvent) -> None:
    encoded = encode_event(event)
    decoded = decode_event(encoded.payload_json)

    assert decoded == event
    assert encode_event(decoded).payload_json == encoded.payload_json
    assert encoded.schema_version == 1


def test_risk_scope_for_same_position_key_is_immutable() -> None:
    first = risk_scope(event_id="event-a", city_key="new-york")
    second = risk_scope(event_id="event-b", city_key="new-york")
    state = replay((opened(), scope_registered(first)))
    conflicting = RiskScopeRegistered(
        event_id=risk_scope_event_id(second),
        occurred_at=NOW,
        scope=second,
    )

    with pytest.raises(DuplicateEventConflict, match="event identifier"):
        apply_event(state, conflicting)


def test_replay_rejects_portfolio_valuation_that_does_not_reconcile_equity() -> None:
    bad = PortfolioValuation(
        positions=(),
        equity=Money.of("99"),
        assembled_at=NOW,
        source="bad-equity",
    )
    event = valuation_recorded(bad, suffix="bad-equity")

    with pytest.raises(InvariantViolation, match="reconcile"):
        replay((opened(), event))


def test_replay_rejects_portfolio_valuation_with_wrong_position_quantity() -> None:
    events = filled_position_events()
    state = state_for(events)
    wrong = PositionValuation(
        market_id=MARKET_A,
        outcome_id=YES_A,
        quantity=Decimal("9"),
        liquidation_value=Money.of("4.10"),
        observed_at=NOW - timedelta(seconds=1),
    )
    valuation = PortfolioValuation(
        positions=(wrong,),
        equity=state.cash + wrong.liquidation_value,
        assembled_at=NOW,
        source="bad-quantity",
    )

    with pytest.raises(InvariantViolation, match="quantity"):
        replay((*events, valuation_recorded(valuation, suffix="bad-quantity")))
