from __future__ import annotations

from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest

from tests.quoting.helpers import weather_snapshot
from weatherbot.forecasting.contracts import expected_calibration_model_run
from weatherbot.forecasting.runtime import (
    CalibrationCompatibilityError,
    load_calibrated_probability_runtime,
)
from weatherbot.markets import TemperatureBucket, TemperatureUnit

_ROOT = Path(__file__).resolve().parents[2]
_ARTIFACT_SHA256 = "b5c8ad0d90d248459c1253dfa12f5fdb5bfd7e85b9d36ec415eb2a1e63596550"


def _runtime():
    return load_calibrated_probability_runtime(repository_root=_ROOT)


def _exact_weather():
    return weather_snapshot(
        issued_at=datetime(2026, 8, 6, 4, 16, tzinfo=UTC),
        model_run_initialized_at_utc=expected_calibration_model_run(
            target_date=date(2026, 8, 6), lead_days=0
        ),
    )


def test_repository_accepted_v3_loads_and_preserves_exact_run_provenance() -> None:
    runtime = _runtime()
    result = runtime.probability(
        city="chicago",
        climate_region="ohio_valley",
        lead_days=0,
        weather=_exact_weather(),
        bucket=TemperatureBucket.bounded(86, 86, TemperatureUnit.FAHRENHEIT),
    )

    assert result.model_version == "issue12-v3-final-holdout"
    assert result.artifact_sha256 == _ARTIFACT_SHA256
    assert result.training_cutoff == date(2026, 8, 10)
    assert result.fallback_level == "city_source_lead_season"
    assert result.calibration_sample_count == 70
    assert result.distribution_type == "normal"
    assert 0 < result.model_probability < 1


def test_repository_accepted_v3_rejects_wrong_model_run_vintage() -> None:
    runtime = _runtime()
    weather = _exact_weather()
    wrong_run = replace(
        weather,
        forecast=replace(
            weather.forecast,
            model_run_initialized_at_utc=(
                weather.forecast.model_run_initialized_at_utc + timedelta(hours=6)
            ),
        ),
    )

    with pytest.raises(CalibrationCompatibilityError, match="18Z vintage"):
        runtime.probability(
            city="chicago",
            climate_region="ohio_valley",
            lead_days=0,
            weather=wrong_run,
            bucket=TemperatureBucket.bounded(86, 86, TemperatureUnit.FAHRENHEIT),
        )


def test_repository_accepted_v3_rejects_missing_model_run_provenance() -> None:
    runtime = _runtime()
    weather = _exact_weather()
    missing_run = replace(
        weather,
        forecast=replace(weather.forecast, model_run_initialized_at_utc=None),
    )

    with pytest.raises(CalibrationCompatibilityError, match="cannot be proven"):
        runtime.probability(
            city="chicago",
            climate_region="ohio_valley",
            lead_days=0,
            weather=missing_run,
            bucket=TemperatureBucket.bounded(86, 86, TemperatureUnit.FAHRENHEIT),
        )
