from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

from weatherbot.forecasting import (
    DailyHighForecast,
    ForecastSource,
    ObservationSource,
    TemperatureObservation,
    WeatherInputError,
    WeatherInputSnapshot,
)


def forecast(*, market_date: date = date(2026, 8, 6)) -> DailyHighForecast:
    timezone = ZoneInfo("America/New_York")
    issued = datetime(2026, 8, 6, 10, 0, tzinfo=UTC)
    return DailyHighForecast(
        temperature_f=Decimal("86"),
        market_date=market_date,
        market_timezone=timezone.key,
        source=ForecastSource.OPEN_METEO_ECMWF_IFS025,
        snapshot_issued_at_utc=issued,
        valid_from_utc=datetime.combine(market_date, time.min, timezone).astimezone(UTC),
        valid_until_utc=datetime.combine(
            market_date + timedelta(days=1), time.min, timezone
        ).astimezone(UTC),
        retrieved_at_utc=issued,
    )


def observation(local_hour: int) -> TemperatureObservation:
    timezone = ZoneInfo("America/New_York")
    valid = datetime(2026, 8, 6, local_hour, 0, tzinfo=timezone).astimezone(UTC)
    return TemperatureObservation(
        temperature_f=Decimal("74"),
        station_id="KLGA",
        market_timezone=timezone.key,
        source=ObservationSource.AVIATION_WEATHER_METAR,
        issued_at_utc=valid + timedelta(minutes=2),
        valid_at_utc=valid,
        provider_received_at_utc=valid + timedelta(minutes=3),
        retrieved_at_utc=valid + timedelta(minutes=5),
    )


@pytest.mark.parametrize("local_hour", [9, 15, 21])
def test_morning_afternoon_and_evening_metar_never_replace_daily_high(local_hour: int) -> None:
    daily = forecast()
    metar = observation(local_hour)
    generated = max(daily.retrieved_at_utc, metar.retrieved_at_utc) + timedelta(minutes=1)
    snapshot = WeatherInputSnapshot(
        forecast=daily,
        observation=metar,
        assembled_at_utc=generated,
    )

    assert snapshot.signal_temperature_f == Decimal("86")
    assert snapshot.observation is not None
    assert snapshot.observation.temperature_f == Decimal("74")
    metadata = snapshot.signal_metadata(generated_at_utc=generated)
    assert metadata["forecast_temperature_f"] == 86.0
    assert metadata["observation_temperature_f"] == 74.0
    assert metadata["forecast_source"] == "open_meteo_ecmwf_ifs025"
    assert metadata["observation_source"] == "aviation_weather_metar"


def test_missing_observation_does_not_change_forecast_semantics() -> None:
    daily = forecast()
    snapshot = WeatherInputSnapshot(
        forecast=daily,
        observation=None,
        assembled_at_utc=daily.retrieved_at_utc,
    )

    assert snapshot.signal_temperature_f == daily.temperature_f
    metadata = snapshot.signal_metadata(generated_at_utc=daily.retrieved_at_utc)
    assert metadata["observation_temperature_f"] is None
    assert metadata["observation_valid_at_utc"] is None


def test_observation_from_different_local_day_is_rejected() -> None:
    daily = forecast(market_date=date(2026, 8, 7))

    with pytest.raises(WeatherInputError, match="local date"):
        WeatherInputSnapshot(
            forecast=daily,
            observation=observation(21),
            assembled_at_utc=daily.retrieved_at_utc + timedelta(days=1),
        )


def test_daily_valid_window_respects_dst_day_length() -> None:
    timezone = ZoneInfo("America/New_York")
    market_date = date(2026, 11, 1)
    valid_from = datetime.combine(market_date, time.min, timezone).astimezone(UTC)
    valid_until = datetime.combine(market_date + timedelta(days=1), time.min, timezone).astimezone(
        UTC
    )

    daily = DailyHighForecast(
        temperature_f=Decimal("60"),
        market_date=market_date,
        market_timezone=timezone.key,
        source=ForecastSource.OPEN_METEO_ECMWF_IFS025,
        snapshot_issued_at_utc=datetime(2026, 10, 31, 12, tzinfo=UTC),
        valid_from_utc=valid_from,
        valid_until_utc=valid_until,
        retrieved_at_utc=datetime(2026, 10, 31, 12, tzinfo=UTC),
    )

    assert daily.valid_until_utc - daily.valid_from_utc == timedelta(hours=25)
