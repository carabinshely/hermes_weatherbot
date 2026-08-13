from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest

import weatherbot.forecasting.calibration as calibration_module
from weatherbot.forecasting.calibration import (
    CalibratedTemperatureModel,
    CalibrationArtifact,
    CalibrationDiagnostics,
    CalibrationError,
    CalibrationGroup,
    CalibrationGroupKey,
    CalibrationSample,
    DistributionKind,
    GroupLevel,
    NormalResidualDistribution,
    Season,
)
from weatherbot.forecasting.calibration_v3_fit import (
    V3CalibrationFitResult,
    fit_v3_calibration_artifact,
)
from weatherbot.forecasting.model import ForecastSource
from weatherbot.markets import TemperatureBucket, TemperatureUnit

_SOURCE = ForecastSource.OPEN_METEO_ECMWF_IFS025


def sample(
    city: str,
    day: date,
    *,
    residual: int,
    region: str = "north",
    lead_days: int = 1,
) -> CalibrationSample:
    forecast = Decimal("80")
    material = f"{city}-{day.isoformat()}-{lead_days}"
    return CalibrationSample(
        city=city,
        climate_region=region,
        forecast_source=_SOURCE,
        market_date=day,
        lead_days=lead_days,
        forecast_temperature_f=forecast,
        observed_temperature_f=forecast + Decimal(residual),
        forecast_as_of_utc=datetime.combine(
            day - timedelta(days=lead_days),
            datetime.min.time(),
            UTC,
        ),
        observation_finalized_at_utc=datetime.combine(
            day + timedelta(days=1),
            datetime.min.time(),
            UTC,
        ),
        observation_source="synthetic finalized observation",
        station_id=city.upper(),
        measurement_basis="synthetic finalized daily high",
        forecast_payload_sha256=(material.encode().hex() + "a" * 64)[:64],
        observation_payload_sha256=(material[::-1].encode().hex() + "b" * 64)[:64],
    )


def fit(
    samples: tuple[CalibrationSample, ...],
    *,
    minimum: int = 20,
) -> V3CalibrationFitResult:
    return fit_v3_calibration_artifact(
        samples,
        model_version="issue12-v3-fixture",
        created_at_utc=datetime(2026, 8, 10, 12, tzinfo=UTC),
        forecast_contract_id="fixture-forecast-v1",
        observation_contract_id="fixture-observation-v1",
        training_start=date(2026, 6, 1),
        training_end=date(2026, 7, 10),
        validation_start=date(2026, 7, 11),
        validation_end=date(2026, 7, 15),
        dataset_sha256="c" * 64,
        min_sample_count=minimum,
    )


def test_v3_keeps_empirical_diagnostics_but_emits_only_normal_groups() -> None:
    start = date(2026, 6, 1)
    values: list[CalibrationSample] = []
    for index in range(40):
        residual = -5 if index % 2 == 0 else 5
        values.append(sample("nyc", start + timedelta(days=index), residual=residual))
    for index in range(5):
        residual = -5 if index % 2 == 0 else 5
        values.append(
            sample(
                "nyc",
                date(2026, 7, 11) + timedelta(days=index),
                residual=residual,
            )
        )

    result = fit(tuple(values))

    assert result.group_fit_decisions
    assert all(
        decision.diagnostics.empirical_selection_crps is not None
        for decision in result.group_fit_decisions
    )
    assert all(
        group.distribution.kind is DistributionKind.NORMAL for group in result.artifact.groups
    )
    assert not any(
        group.distribution.kind is DistributionKind.EMPIRICAL for group in result.artifact.groups
    )


def test_v3_omits_zero_variance_specific_group_and_uses_broader_fallback() -> None:
    start = date(2026, 6, 1)
    values: list[CalibrationSample] = []
    for index in range(40):
        day = start + timedelta(days=index)
        values.append(sample("nyc", day, residual=0))
        values.append(sample("bos", day, residual=(-2, -1, 1, 2)[index % 4]))
    for index in range(5):
        day = date(2026, 7, 11) + timedelta(days=index)
        values.append(sample("nyc", day, residual=0))
        values.append(sample("bos", day, residual=(-2, -1, 1, 2)[index % 4]))

    result = fit(tuple(values))
    nyc_key = CalibrationGroupKey(
        GroupLevel.CITY_SOURCE_LEAD_SEASON,
        _SOURCE,
        city="nyc",
        lead_days=1,
        season=Season.JJA,
    )
    decision = next(item for item in result.group_fit_decisions if item.key == nyc_key)
    assert decision.runtime_eligible is False
    assert decision.omission_reason == "normal_fit_unavailable"
    assert all(group.key != nyc_key for group in result.artifact.groups)

    estimate = CalibratedTemperatureModel(result.artifact).probability(
        city="nyc",
        climate_region="north",
        forecast_source=_SOURCE,
        market_date=date(2026, 7, 12),
        lead_days=1,
        forecast_temperature_f=80,
        bucket=TemperatureBucket.bounded(
            80,
            80,
            TemperatureUnit.FAHRENHEIT,
        ),
    )
    assert estimate.fallback_level is GroupLevel.REGION_SOURCE_LEAD_SEASON
    assert estimate.distribution_type is DistributionKind.NORMAL


def test_v3_fails_closed_when_every_eligible_group_has_zero_variance() -> None:
    values: list[CalibrationSample] = []
    for index in range(40):
        values.append(
            sample(
                "nyc",
                date(2026, 6, 1) + timedelta(days=index),
                residual=0,
            )
        )
    for index in range(5):
        values.append(
            sample(
                "nyc",
                date(2026, 7, 11) + timedelta(days=index),
                residual=0,
            )
        )

    with pytest.raises(CalibrationError, match="V3 normal-fit policy"):
        fit(tuple(values))


def test_model_builds_group_index_once_and_does_not_change_artifact_bytes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    diagnostics = CalibrationDiagnostics(
        jarque_bera=0.2,
        normality_p_value=0.9,
    )
    group = CalibrationGroup(
        key=CalibrationGroupKey(GroupLevel.SOURCE, _SOURCE),
        sample_count=100,
        distribution=NormalResidualDistribution(
            Decimal("0.5"),
            Decimal("2.25"),
        ),
        training_end=date(2026, 7, 10),
        diagnostics=diagnostics,
    )
    artifact = CalibrationArtifact(
        model_version="index-fixture",
        created_at_utc=datetime(2026, 8, 10, tzinfo=UTC),
        forecast_contract_id="fixture-forecast-v1",
        observation_contract_id="fixture-observation-v1",
        training_start=date(2026, 6, 1),
        training_end=date(2026, 7, 10),
        validation_start=date(2026, 7, 11),
        validation_end=date(2026, 7, 15),
        dataset_sha256="d" * 64,
        min_sample_count=20,
        groups=(group,),
    )
    before = artifact.to_json()
    calls = 0
    original = calibration_module._build_group_index  # pyright: ignore[reportPrivateUsage]

    def counted(value: CalibrationArtifact) -> Mapping[CalibrationGroupKey, CalibrationGroup]:
        nonlocal calls
        calls += 1
        return original(value)

    monkeypatch.setattr(calibration_module, "_build_group_index", counted)
    model = CalibratedTemperatureModel(artifact)
    assert calls == 1
    for degree in (78, 79, 80, 81, 82):
        model.probability(
            city="nyc",
            climate_region="north",
            forecast_source=_SOURCE,
            market_date=date(2026, 7, 12),
            lead_days=1,
            forecast_temperature_f=80,
            bucket=TemperatureBucket.bounded(
                degree,
                degree,
                TemperatureUnit.FAHRENHEIT,
            ),
        )
    assert calls == 1
    assert artifact.to_json() == before
