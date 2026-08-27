from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from weatherbot.forecasting.calibration import (
    CalibratedTemperatureModel,
    CalibrationArtifact,
    CalibrationDiagnostics,
    CalibrationGroup,
    CalibrationGroupKey,
    EmpiricalResidualDistribution,
    GroupLevel,
)
from weatherbot.forecasting.calibration_fit import empirical_crps
from weatherbot.forecasting.model import ForecastSource
from weatherbot.markets import TemperatureBucket, TemperatureUnit


def _model(residuals: tuple[Decimal, ...]) -> CalibratedTemperatureModel:
    source = ForecastSource.OPEN_METEO_ECMWF_IFS025
    group = CalibrationGroup(
        key=CalibrationGroupKey(GroupLevel.SOURCE, source),
        sample_count=len(residuals),
        distribution=EmpiricalResidualDistribution(residuals),
        training_end=date(2025, 12, 31),
        diagnostics=CalibrationDiagnostics(jarque_bera=0.0, normality_p_value=1.0),
    )
    return CalibratedTemperatureModel(
        CalibrationArtifact(
            model_version="empirical-boundary-v1",
            created_at_utc=datetime(2026, 8, 12, tzinfo=UTC),
            forecast_contract_id="forecast-v1",
            observation_contract_id="observation-v1",
            training_start=date(2025, 1, 1),
            training_end=date(2025, 12, 31),
            validation_start=date(2026, 1, 1),
            validation_end=date(2026, 7, 31),
            dataset_sha256="a" * 64,
            min_sample_count=2,
            groups=(group,),
        )
    )


def test_empirical_atoms_on_half_degree_boundary_belong_to_upper_bucket() -> None:
    model = _model((Decimal("0.5"), Decimal("0.5"), Decimal("1.5")))
    source = ForecastSource.OPEN_METEO_ECMWF_IFS025

    lower = model.probability(
        city="nyc",
        climate_region="northeast",
        forecast_source=source,
        market_date=date(2026, 7, 1),
        lead_days=1,
        forecast_temperature_f=80,
        bucket=TemperatureBucket.bounded(80, 80, TemperatureUnit.FAHRENHEIT),
    ).probability
    upper = model.probability(
        city="nyc",
        climate_region="northeast",
        forecast_source=source,
        market_date=date(2026, 7, 1),
        lead_days=1,
        forecast_temperature_f=80,
        bucket=TemperatureBucket.bounded(81, 81, TemperatureUnit.FAHRENHEIT),
    ).probability

    assert lower == pytest.approx(0.0)
    assert upper == pytest.approx(2 / 3)


def test_fastempirical_crps_matches_direct_pairwise_definition() -> None:
    distribution = EmpiricalResidualDistribution(
        (Decimal("-2"), Decimal("-1"), Decimal("1"), Decimal("4"))
    )
    observed = Decimal("0.5")
    values = distribution.residuals_f
    first = sum(abs(float(value - observed)) for value in values) / len(values)
    pairwise = sum(abs(float(left - right)) for left in values for right in values)
    direct = first - pairwise / (2 * len(values) * len(values))

    assert empirical_crps(distribution, observed) == pytest.approx(direct)
