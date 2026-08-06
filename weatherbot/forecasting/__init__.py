"""Forecast and observation provenance for signal generation."""

from weatherbot.forecasting.model import (
    DailyHighForecast,
    ForecastSource,
    ObservationSource,
    TemperatureObservation,
    WeatherInputError,
    WeatherInputSnapshot,
)
from weatherbot.forecasting.providers import (
    parse_aviation_weather_metar,
    parse_open_meteo_daily_highs,
)

__all__ = [
    "DailyHighForecast",
    "ForecastSource",
    "ObservationSource",
    "TemperatureObservation",
    "WeatherInputError",
    "WeatherInputSnapshot",
    "parse_aviation_weather_metar",
    "parse_open_meteo_daily_highs",
]
