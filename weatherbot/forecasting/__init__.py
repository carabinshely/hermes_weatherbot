"""Forecast, observation, and calibrated probability contracts."""

from weatherbot.forecasting.calibration import (
    CalibrationArtifact,
    CalibrationDiagnostics,
    CalibrationError,
    CalibrationGroup,
    CalibrationGroupKey,
    CalibrationSample,
    CalibratedTemperatureModel,
    DistributionKind,
    EmpiricalResidualDistribution,
    GroupLevel,
    NormalResidualDistribution,
    ProbabilityEstimate,
    Season,
    calibration_artifact_from_json,
    load_calibration_artifact,
)
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
    "CalibrationArtifact",
    "CalibrationDiagnostics",
    "CalibrationError",
    "CalibrationGroup",
    "CalibrationGroupKey",
    "CalibrationSample",
    "CalibratedTemperatureModel",
    "DailyHighForecast",
    "DistributionKind",
    "EmpiricalResidualDistribution",
    "ForecastSource",
    "GroupLevel",
    "NormalResidualDistribution",
    "ObservationSource",
    "ProbabilityEstimate",
    "Season",
    "TemperatureObservation",
    "WeatherInputError",
    "WeatherInputSnapshot",
    "calibration_artifact_from_json",
    "load_calibration_artifact",
    "parse_aviation_weather_metar",
    "parse_open_meteo_daily_highs",
]
