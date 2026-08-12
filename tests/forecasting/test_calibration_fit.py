from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest

from weatherbot.forecasting.calibration import CalibrationError, CalibrationSample
from weatherbot.forecasting.calibration_fit import (
    fit_calibration_artifact,
    validate_calibration_samples,
)
from weatherbot.forecasting.model import ForecastSource

_SOURCE = ForecastSource.OPEN_METEO_ECMWF_IFS025


def sample(day: date, *, residual: int, lead_days: int = 1) -> CalibrationSample:
    forecast = Decimal("80")
    as_of = datetime(day.year, day.month, day.day, 6, tzinfo=UTC) - timedelta(days=lead_days)
    finalized = datetime(day.year, day.month, day.day, 23, tzinfo=UTC) + timedelta(hours=8)
    material = f"{day.isoformat()}-{lead_days}"
    forecast_hash = (material.encode().hex() + "a" * 64)[:64]
    observation_hash = (material[::-1].encode().hex() + "b" * 64)[:64]
    return CalibrationSample(
        city="nyc",
        climate_region="northeast",
        forecast_source=_SOURCE,
        market_date=day,
        lead_days=lead_days,
        forecast_temperature_f=forecast,
        observed_temperature_f=forecast + Decimal(residual),
        forecast_as_of_utc=as_of,
        observation_finalized_at_utc=finalized,
        observation_source="Weather Underground daily history",
        station_id="KLGA",
        measurement_basis="finalized daily high temperature",
        forecast_payload_sha256=forecast_hash,
        observation_payload_sha256=observation_hash,
    )


def training_and_holdout() -> tuple[CalibrationSample, ...]:
    start = date(2025, 6, 1)
    values: list[CalibrationSample] = []
    for index in range(60):
        values.append(sample(start + timedelta(days=index), residual=(2, 3, 4)[index % 3]))
    validation_start = date(2026, 6, 1)
    for index in range(18):
        values.append(
            sample(validation_start + timedelta(days=index), residual=(2, 3, 4)[index % 3])
        )
    return tuple(values)


def test_fit_uses_only_pre_cutoff_samples_and_evaluates_untouched_holdout() -> None:
    result = fit_calibration_artifact(
        training_and_holdout(),
        model_version="biased-forecast-v1",
        created_at_utc=datetime(2026, 8, 12, tzinfo=UTC),
        forecast_contract_id="open-meteo:ecmwf_ifs025:daily-high:v1",
        observation_contract_id="declared-resolution-station:daily-high:v1",
        training_start=date(2025, 6, 1),
        training_end=date(2025, 7, 30),
        validation_start=date(2026, 6, 1),
        validation_end=date(2026, 6, 18),
        dataset_sha256="c" * 64,
        min_sample_count=30,
    )

    assert result.artifact.training_end == date(2025, 7, 30)
    assert result.artifact.validation_start == date(2026, 6, 1)
    assert all(
        group.training_end == result.artifact.training_end for group in result.artifact.groups
    )
    assert max(group.sample_count for group in result.artifact.groups) == 60
    assert result.validation.sample_count == 18
    assert result.validation.forecast_bias_f == pytest.approx(3.0)
    assert result.validation.mean_log_score < result.validation.baseline_mean_log_score
    assert (
        result.validation.mean_ranked_probability_score
        < result.validation.baseline_mean_ranked_probability_score
    )
    assert result.validation.reliability_bins


def test_distribution_selection_diagnostics_are_training_only_and_reproducible() -> None:
    kwargs = dict(
        model_version="repeatable-v1",
        created_at_utc=datetime(2026, 8, 12, tzinfo=UTC),
        forecast_contract_id="forecast-v1",
        observation_contract_id="observation-v1",
        training_start=date(2025, 6, 1),
        training_end=date(2025, 7, 30),
        validation_start=date(2026, 6, 1),
        validation_end=date(2026, 6, 18),
        dataset_sha256="d" * 64,
        min_sample_count=30,
    )

    first = fit_calibration_artifact(training_and_holdout(), **kwargs)
    second = fit_calibration_artifact(tuple(reversed(training_and_holdout())), **kwargs)

    assert first.artifact.artifact_sha256 == second.artifact.artifact_sha256
    assert first.validation == second.validation
    assert all(group.diagnostics.normality_p_value >= 0 for group in first.artifact.groups)
    assert all(
        group.diagnostics.empirical_selection_crps is not None for group in first.artifact.groups
    )


def test_duplicate_sample_identity_is_rejected_instead_of_double_counted() -> None:
    duplicated = sample(date(2025, 6, 1), residual=2)

    with pytest.raises(CalibrationError, match="duplicate calibration sample identity"):
        validate_calibration_samples((duplicated, duplicated))


def test_fit_rejects_empty_holdout() -> None:
    only_training = tuple(
        sample(date(2025, 6, 1) + timedelta(days=index), residual=2) for index in range(40)
    )

    with pytest.raises(CalibrationError, match="untouched holdout"):
        fit_calibration_artifact(
            only_training,
            model_version="no-holdout",
            created_at_utc=datetime(2026, 8, 12, tzinfo=UTC),
            forecast_contract_id="forecast-v1",
            observation_contract_id="observation-v1",
            training_start=date(2025, 6, 1),
            training_end=date(2025, 7, 31),
            validation_start=date(2026, 6, 1),
            validation_end=date(2026, 6, 30),
            dataset_sha256="e" * 64,
            min_sample_count=30,
        )
