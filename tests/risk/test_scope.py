from __future__ import annotations

from tests.risk.portfolio_helpers import risk_scope


def test_risk_scope_automatically_correlates_same_event_and_date() -> None:
    scope = risk_scope(event_id="weather-event-1", groups=("weather-system:storm-a",))

    assert scope.all_correlation_groups == (
        "date:2026-01-03",
        "event:weather-event-1",
        "weather-system:storm-a",
    )
