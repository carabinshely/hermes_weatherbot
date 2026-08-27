"""Read-only weather acquisition for the public producer runtime."""

from __future__ import annotations

import time
from collections.abc import Sequence
from datetime import UTC, date, datetime
from typing import cast

import requests

from weatherbot.forecasting.model import (
    DailyHighForecast,
    TemperatureObservation,
    WeatherInputError,
    WeatherInputSnapshot,
)
from weatherbot.forecasting.providers import (
    parse_aviation_weather_metar,
    parse_open_meteo_daily_highs,
)
from weatherbot.markets import MarketCalendar
from weatherbot.producer.catalog import LOCATIONS


def fetch_ecmwf_daily_highs(
    city_slug: str,
    requested_dates: Sequence[date],
) -> dict[date, DailyHighForecast]:
    """Fetch the same public ECMWF daily-high source without legacy/trading imports."""
    location = LOCATIONS[city_slug]
    url = "https://api.open-meteo.com/v1/forecast"
    params = {
        "latitude": location.latitude,
        "longitude": location.longitude,
        "daily": "temperature_2m_max",
        "temperature_unit": "fahrenheit",
        "forecast_days": 7,
        "timezone": location.market_timezone,
        "models": "ecmwf_ifs025",
        "bias_correction": "true",
    }
    last_error: Exception | None = None
    for attempt in range(3):
        try:
            response = requests.get(url, params=params, timeout=(5, 10))
            response.raise_for_status()
            payload: object = response.json()
            if not isinstance(payload, dict):
                raise WeatherInputError("Open-Meteo response must be an object")
            retrieved_at = datetime.now(UTC)
            # Deliberately do not invent model_run_initialized_at_utc. Until #50 can
            # prove the exact calibrated run, CalibratedProbabilityRuntime stays fail-closed.
            return dict(
                parse_open_meteo_daily_highs(
                    cast(dict[str, object], payload),
                    requested_dates=requested_dates,
                    market_timezone=location.market_timezone,
                    retrieved_at_utc=retrieved_at,
                )
            )
        except (requests.RequestException, ValueError, WeatherInputError) as exc:
            last_error = exc
            if attempt < 2:
                time.sleep(2)
    if last_error is not None:
        raise WeatherInputError(f"ECMWF fetch failed for {city_slug}: {last_error}") from last_error
    return {}


def fetch_metar(city_slug: str) -> TemperatureObservation | None:
    """Fetch the latest instantaneous METAR observation; failures never replace the forecast."""
    location = LOCATIONS[city_slug]
    url = "https://aviationweather.gov/api/data/metar"
    try:
        response = requests.get(
            url,
            params={"ids": location.station_id, "format": "json"},
            timeout=(5, 8),
        )
        response.raise_for_status()
        payload: object = response.json()
        return parse_aviation_weather_metar(
            payload,
            station_id=location.station_id,
            market_timezone=location.market_timezone,
            retrieved_at_utc=datetime.now(UTC),
        )
    except (requests.RequestException, ValueError, WeatherInputError):
        return None


def fetch_weather_snapshots(
    city_slug: str,
    requested_dates: Sequence[date],
    *,
    now: datetime,
) -> dict[date, WeatherInputSnapshot]:
    location = LOCATIONS[city_slug]
    forecasts = fetch_ecmwf_daily_highs(city_slug, requested_dates)
    local_date = MarketCalendar(location.market_timezone).local_date(now)
    observation = fetch_metar(city_slug) if local_date in requested_dates else None
    snapshots: dict[date, WeatherInputSnapshot] = {}
    for market_date, forecast in forecasts.items():
        matching_observation = (
            observation
            if observation is not None and observation.market_date == forecast.market_date
            else None
        )
        snapshots[market_date] = WeatherInputSnapshot(
            forecast=forecast,
            observation=matching_observation,
            assembled_at_utc=datetime.now(UTC),
        )
    return snapshots
