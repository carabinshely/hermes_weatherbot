from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from weatherbot.markets import (
    ForecastObservation,
    MarketCalendar,
    MarketCalendarError,
    TemperatureUnit,
    index_forecasts,
    qualify_forecast_date,
)


@pytest.mark.parametrize(
    ("timezone_name", "instant", "expected"),
    [
        (
            "America/New_York",
            datetime(2026, 8, 6, 2, 30, tzinfo=UTC),
            date(2026, 8, 5),
        ),
        (
            "America/Chicago",
            datetime(2026, 8, 6, 4, 30, tzinfo=UTC),
            date(2026, 8, 5),
        ),
        (
            "America/Los_Angeles",
            datetime(2026, 8, 6, 6, 30, tzinfo=UTC),
            date(2026, 8, 5),
        ),
    ],
)
def test_utc_midnight_uses_city_local_calendar_day(
    timezone_name: str,
    instant: datetime,
    expected: date,
) -> None:
    assert MarketCalendar(timezone_name).local_date(instant) == expected


def test_candidate_dates_are_generated_from_local_today() -> None:
    instant = datetime(2026, 8, 6, 2, 30, tzinfo=UTC)
    dates = MarketCalendar("America/New_York").candidate_dates(instant, count=4)
    assert dates == (
        date(2026, 8, 5),
        date(2026, 8, 6),
        date(2026, 8, 7),
        date(2026, 8, 8),
    )


@pytest.mark.parametrize(
    ("timezone_name", "before", "after", "expected_dates"),
    [
        (
            "America/New_York",
            datetime(2026, 3, 8, 6, 59, tzinfo=UTC),
            datetime(2026, 3, 8, 7, 1, tzinfo=UTC),
            (date(2026, 3, 8), date(2026, 3, 8)),
        ),
        (
            "America/Chicago",
            datetime(2026, 11, 1, 6, 59, tzinfo=UTC),
            datetime(2026, 11, 1, 7, 1, tzinfo=UTC),
            (date(2026, 11, 1), date(2026, 11, 1)),
        ),
        (
            "America/Los_Angeles",
            datetime(2026, 11, 1, 8, 59, tzinfo=UTC),
            datetime(2026, 11, 1, 9, 1, tzinfo=UTC),
            (date(2026, 11, 1), date(2026, 11, 1)),
        ),
    ],
)
def test_daylight_saving_transitions_preserve_local_date(
    timezone_name: str,
    before: datetime,
    after: datetime,
    expected_dates: tuple[date, date],
) -> None:
    calendar = MarketCalendar(timezone_name)
    assert (calendar.local_date(before), calendar.local_date(after)) == expected_dates


def test_forecast_record_contains_utc_retrieval_and_local_market_date() -> None:
    observation = qualify_forecast_date(
        market_date="2026-08-05",
        market_timezone="America/New_York",
        retrieved_at_utc=datetime(2026, 8, 6, 2, 30, tzinfo=UTC),
        source="ecmwf",
        value=82,
        unit=TemperatureUnit.FAHRENHEIT,
    )
    assert observation.market_date_text == "2026-08-05"
    assert observation.market_timezone == "America/New_York"
    assert observation.retrieved_at_utc.tzinfo is UTC
    assert observation.value == Decimal("82")


def test_source_timestamp_must_match_qualified_local_date() -> None:
    with pytest.raises(MarketCalendarError, match="local date"):
        qualify_forecast_date(
            market_date="2026-08-06",
            market_timezone="America/New_York",
            retrieved_at_utc=datetime(2026, 8, 6, 12, tzinfo=UTC),
            source="metar",
            value=82,
            unit=TemperatureUnit.FAHRENHEIT,
            source_timestamp=datetime(2026, 8, 5, 23, tzinfo=UTC),
        )


def test_unqualified_or_cross_timezone_forecast_join_fails_closed() -> None:
    ny = ForecastObservation(
        market_date=date(2026, 8, 6),
        market_timezone="America/New_York",
        retrieved_at_utc=datetime(2026, 8, 6, 12, tzinfo=UTC),
        source="ecmwf",
        value=Decimal("82"),
        unit=TemperatureUnit.FAHRENHEIT,
    )
    chicago = ForecastObservation(
        market_date=date(2026, 8, 7),
        market_timezone="America/Chicago",
        retrieved_at_utc=datetime(2026, 8, 6, 12, tzinfo=UTC),
        source="ecmwf",
        value=Decimal("80"),
        unit=TemperatureUnit.FAHRENHEIT,
    )
    with pytest.raises(MarketCalendarError, match="different market timezones"):
        index_forecasts((ny, chicago), market_timezone="America/New_York")

    with pytest.raises(MarketCalendarError, match="timezone-aware"):
        MarketCalendar("America/New_York").local_date(datetime(2026, 8, 6, 12))
