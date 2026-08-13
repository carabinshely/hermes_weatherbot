from __future__ import annotations

from datetime import UTC, date, datetime

from weatherbot.forecasting.archive import calibration_run_for_market_day


def test_june_10_18z_run_maps_to_june_11_decision_day() -> None:
    assert calibration_run_for_market_day(date(2026, 6, 11)) == datetime(
        2026, 6, 10, 18, tzinfo=UTC
    )
