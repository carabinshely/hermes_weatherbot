from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from weatherbot.forecasting import (
    WeatherInputError,
    parse_aviation_weather_metar,
    parse_open_meteo_daily_highs,
)


def test_open_meteo_parser_keeps_daily_highs_as_forecasts() -> None:
    retrieved = datetime(2026, 8, 6, 10, tzinfo=UTC)
    forecasts = parse_open_meteo_daily_highs(
        {
            "timezone": "America/New_York",
            "daily_units": {"temperature_2m_max": "°F"},
            "daily": {
                "time": ["2026-08-06", "2026-08-07"],
                "temperature_2m_max": [86.0, 88.0],
            },
        },
        requested_dates=[date(2026, 8, 6), date(2026, 8, 7)],
        market_timezone="America/New_York",
        retrieved_at_utc=retrieved,
    )

    assert forecasts[date(2026, 8, 6)].temperature_f == Decimal("86.0")
    assert forecasts[date(2026, 8, 6)].snapshot_issued_at_utc == retrieved
    assert forecasts[date(2026, 8, 6)].model_run_initialized_at_utc is None


def test_open_meteo_parser_rejects_non_fahrenheit_payload() -> None:
    with pytest.raises(WeatherInputError, match="Fahrenheit"):
        parse_open_meteo_daily_highs(
            {
                "timezone": "America/New_York",
                "daily_units": {"temperature_2m_max": "°C"},
                "daily": {"time": ["2026-08-06"], "temperature_2m_max": [30]},
            },
            requested_dates=[date(2026, 8, 6)],
            market_timezone="America/New_York",
            retrieved_at_utc=datetime(2026, 8, 6, 10, tzinfo=UTC),
        )


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        (
            {
                "daily_units": {"temperature_2m_max": "°F"},
                "daily": {"time": ["2026-08-06"], "temperature_2m_max": [86]},
            },
            "timezone",
        ),
        (
            {
                "timezone": "America/New_York",
                "daily": {"time": ["2026-08-06"], "temperature_2m_max": [86]},
            },
            "daily_units",
        ),
    ],
)
def test_open_meteo_parser_requires_timezone_and_unit_provenance(
    payload: dict[str, object],
    message: str,
) -> None:
    with pytest.raises(WeatherInputError, match=message):
        parse_open_meteo_daily_highs(
            payload,
            requested_dates=[date(2026, 8, 6)],
            market_timezone="America/New_York",
            retrieved_at_utc=datetime(2026, 8, 6, 10, tzinfo=UTC),
        )


def test_metar_parser_preserves_valid_issue_receipt_and_retrieval_times() -> None:
    retrieved = datetime(2026, 8, 6, 14, 10, tzinfo=UTC)
    observation = parse_aviation_weather_metar(
        [
            {
                "icaoId": "KLGA",
                "obsTime": 1786024800,
                "reportTime": "2026-08-06T14:01:00Z",
                "receiptTime": "2026-08-06T14:02:00Z",
                "temp": 20,
            }
        ],
        station_id="KLGA",
        market_timezone="America/New_York",
        retrieved_at_utc=retrieved,
    )

    assert observation is not None
    assert observation.temperature_f == Decimal("68")
    assert observation.valid_at_utc == datetime.fromtimestamp(1786024800, tz=UTC)
    assert observation.issued_at_utc == datetime(2026, 8, 6, 14, 1, tzinfo=UTC)
    assert observation.provider_received_at_utc == datetime(2026, 8, 6, 14, 2, tzinfo=UTC)
    assert observation.retrieved_at_utc == retrieved


def test_metar_parser_selects_latest_matching_station() -> None:
    observation = parse_aviation_weather_metar(
        [
            {"icaoId": "KJFK", "obsTime": 1786024500, "temp": 25},
            {"icaoId": "KLGA", "obsTime": 1786024200, "temp": 18},
            {"icaoId": "KLGA", "obsTime": 1786024800, "temp": 20},
        ],
        station_id="KLGA",
        market_timezone="America/New_York",
        retrieved_at_utc=datetime(2026, 8, 6, 15, tzinfo=UTC),
    )

    assert observation is not None
    assert observation.valid_at_utc == datetime.fromtimestamp(1786024800, tz=UTC)
    assert observation.temperature_f == Decimal("68")


def test_empty_metar_payload_is_optional() -> None:
    assert (
        parse_aviation_weather_metar(
            [],
            station_id="KLGA",
            market_timezone="America/New_York",
            retrieved_at_utc=datetime(2026, 8, 6, 15, tzinfo=UTC),
        )
        is None
    )
