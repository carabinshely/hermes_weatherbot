from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from weatherbot.forecasting.calibration import (
    CalibratedTemperatureModel,
    CalibrationArtifact,
    CalibrationDiagnostics,
    CalibrationError,
    CalibrationGroup,
    CalibrationGroupKey,
    CalibrationSample,
    DistributionKind,
    EmpiricalResidualDistribution,
    GroupLevel,
    NormalResidualDistribution,
    Season,
    calibration_artifact_from_json,
)
from weatherbot.forecasting.model import ForecastSource
from weatherbot.markets import TemperatureBucket, TemperatureMarketPartition, TemperatureUnit

_SOURCE = ForecastSource.OPEN_METEO_ECMWF_IFS025
_HASH_A = "a" * 64
_HASH_B = "b" * 64
_DIAGNOSTICS = CalibrationDiagnostics(jarque_bera=0.2, normality_p_value=0.9)


def group(
    *,
    level: GroupLevel,
    sample_count: int,
    distribution: NormalResidualDistribution | EmpiricalResidualDistribution,
    city: str | None = None,
    region: str | None = None,
    lead_days: int | None = None,
    season: Season | None = None,
) -> CalibrationGroup:
    return CalibrationGroup(
        key=CalibrationGroupKey(
            level=level,
            forecast_source=_SOURCE,
            city=city,
            climate_region=region,
            lead_days=lead_days,
            season=season,
        ),
        sample_count=sample_count,
        distribution=distribution,
        training_end=date(2025, 12, 31),
        diagnostics=_DIAGNOSTICS,
    )


def artifact(*groups: CalibrationGroup, minimum: int = 30) -> CalibrationArtifact:
    return CalibrationArtifact(
        model_version="ecmwf-us-v1",
        created_at_utc=datetime(2026, 8, 12, 10, tzinfo=UTC),
        forecast_contract_id="open-meteo:ecmwf_ifs025:daily-high:v1",
        observation_contract_id="declared-resolution-station:daily-high:v1",
        training_start=date(2024, 1, 1),
        training_end=date(2025, 12, 31),
        validation_start=date(2026, 1, 1),
        validation_end=date(2026, 7, 31),
        dataset_sha256="c" * 64,
        min_sample_count=minimum,
        groups=groups,
    )


def test_season_uses_market_local_calendar_date() -> None:
    assert Season.for_date(date(2026, 1, 15)) is Season.DJF
    assert Season.for_date(date(2026, 4, 15)) is Season.MAM
    assert Season.for_date(date(2026, 7, 15)) is Season.JJA
    assert Season.for_date(date(2026, 10, 15)) is Season.SON


def test_sample_requires_point_in_time_forecast_before_final_observation() -> None:
    with pytest.raises(CalibrationError, match="predate"):
        CalibrationSample(
            city="nyc",
            climate_region="northeast",
            forecast_source=_SOURCE,
            market_date=date(2026, 8, 12),
            lead_days=1,
            forecast_temperature_f=Decimal("80"),
            observed_temperature_f=Decimal("82"),
            forecast_as_of_utc=datetime(2026, 8, 13, 6, tzinfo=UTC),
            observation_finalized_at_utc=datetime(2026, 8, 13, 6, tzinfo=UTC),
            observation_source="Weather Underground daily history",
            station_id="KLGA",
            measurement_basis="finalized daily high temperature",
            forecast_payload_sha256=_HASH_A,
            observation_payload_sha256=_HASH_B,
        )


def test_bias_zero_normal_matches_existing_bucket_gaussian_math() -> None:
    distribution = NormalResidualDistribution(Decimal("0"), Decimal("2"))
    calibration_group = group(
        level=GroupLevel.CITY_SOURCE_LEAD_SEASON,
        sample_count=100,
        distribution=distribution,
        city="nyc",
        lead_days=1,
        season=Season.JJA,
    )
    model = CalibratedTemperatureModel(artifact(calibration_group))
    bucket = TemperatureBucket.bounded(80, 80, TemperatureUnit.FAHRENHEIT)

    estimate = model.probability(
        city="NYC",
        climate_region="northeast",
        forecast_source=_SOURCE,
        market_date=date(2026, 8, 12),
        lead_days=1,
        forecast_temperature_f=Decimal("80"),
        bucket=bucket,
    )

    assert estimate.probability == pytest.approx(bucket.probability(Decimal("80"), Decimal("2")))
    assert estimate.distribution_type is DistributionKind.NORMAL
    assert estimate.fallback_level is GroupLevel.CITY_SOURCE_LEAD_SEASON


def test_empirical_distribution_drives_bucket_mass_without_sigma() -> None:
    calibration_group = group(
        level=GroupLevel.CITY_SOURCE_LEAD_SEASON,
        sample_count=40,
        distribution=EmpiricalResidualDistribution(
            (Decimal("-1"), Decimal("0"), Decimal("0"), Decimal("1"))
        ),
        city="nyc",
        lead_days=0,
        season=Season.JJA,
    )
    model = CalibratedTemperatureModel(artifact(calibration_group))
    bucket = TemperatureBucket.bounded(80, 80, TemperatureUnit.FAHRENHEIT)

    estimate = model.probability(
        city="nyc",
        climate_region="northeast",
        forecast_source=_SOURCE,
        market_date=date(2026, 8, 12),
        lead_days=0,
        forecast_temperature_f=80,
        bucket=bucket,
    )

    assert estimate.probability == pytest.approx(0.5)
    assert estimate.distribution_type is DistributionKind.EMPIRICAL


def test_sparse_city_group_falls_back_to_region_deterministically() -> None:
    city_group = group(
        level=GroupLevel.CITY_SOURCE_LEAD_SEASON,
        sample_count=29,
        distribution=NormalResidualDistribution(Decimal("0"), Decimal("1")),
        city="nyc",
        lead_days=1,
        season=Season.JJA,
    )
    region_group = group(
        level=GroupLevel.REGION_SOURCE_LEAD_SEASON,
        sample_count=60,
        distribution=NormalResidualDistribution(Decimal("1"), Decimal("2")),
        region="northeast",
        lead_days=1,
        season=Season.JJA,
    )
    model = CalibratedTemperatureModel(artifact(city_group, region_group))

    estimate = model.probability(
        city="nyc",
        climate_region="northeast",
        forecast_source=_SOURCE,
        market_date=date(2026, 8, 12),
        lead_days=1,
        forecast_temperature_f=80,
        bucket=TemperatureBucket.upper_tail(80, TemperatureUnit.FAHRENHEIT),
    )

    assert estimate.fallback_level is GroupLevel.REGION_SOURCE_LEAD_SEASON
    assert estimate.calibration_sample_count == 60
    assert "region=northeast" in estimate.calibration_group_key


def test_missing_evidence_fails_closed_instead_of_using_global_sigma() -> None:
    only_other_horizon = group(
        level=GroupLevel.SOURCE_LEAD,
        sample_count=100,
        distribution=NormalResidualDistribution(Decimal("0"), Decimal("2")),
        lead_days=3,
    )
    model = CalibratedTemperatureModel(artifact(only_other_horizon))

    with pytest.raises(CalibrationError, match="minimum evidence"):
        model.probability(
            city="nyc",
            climate_region="northeast",
            forecast_source=_SOURCE,
            market_date=date(2026, 8, 12),
            lead_days=1,
            forecast_temperature_f=80,
            bucket=TemperatureBucket.upper_tail(80, TemperatureUnit.FAHRENHEIT),
        )


def test_calibrated_partition_probabilities_remain_normalized() -> None:
    calibration_group = group(
        level=GroupLevel.SOURCE,
        sample_count=100,
        distribution=NormalResidualDistribution(Decimal("0.5"), Decimal("2.25")),
    )
    model = CalibratedTemperatureModel(artifact(calibration_group))
    partition = TemperatureMarketPartition(
        (
            TemperatureBucket.lower_tail(78, TemperatureUnit.FAHRENHEIT),
            TemperatureBucket.bounded(79, 79, TemperatureUnit.FAHRENHEIT),
            TemperatureBucket.bounded(80, 80, TemperatureUnit.FAHRENHEIT),
            TemperatureBucket.bounded(81, 81, TemperatureUnit.FAHRENHEIT),
            TemperatureBucket.upper_tail(82, TemperatureUnit.FAHRENHEIT),
        )
    )

    probabilities = [
        model.probability(
            city="nyc",
            climate_region="northeast",
            forecast_source=_SOURCE,
            market_date=date(2026, 8, 12),
            lead_days=1,
            forecast_temperature_f=80,
            bucket=bucket,
        ).probability
        for bucket in partition.buckets
    ]

    assert sum(probabilities) == pytest.approx(1.0, abs=1e-12)


def test_artifact_round_trip_is_checksummed_and_tamper_evident() -> None:
    calibration_group = group(
        level=GroupLevel.SOURCE,
        sample_count=100,
        distribution=EmpiricalResidualDistribution(
            (Decimal("-2"), Decimal("-1"), Decimal("0"), Decimal("1"), Decimal("2"))
        ),
    )
    original = artifact(calibration_group)
    serialized = original.to_json()

    restored = calibration_artifact_from_json(serialized)

    assert restored == original
    assert restored.artifact_sha256 == original.artifact_sha256

    tampered = serialized.replace('"model_version": "ecmwf-us-v1"', '"model_version": "tampered"')
    with pytest.raises(CalibrationError, match="checksum"):
        calibration_artifact_from_json(tampered)


def test_artifact_rejects_overlapping_training_and_validation() -> None:
    calibration_group = group(
        level=GroupLevel.SOURCE,
        sample_count=100,
        distribution=NormalResidualDistribution(Decimal("0"), Decimal("2")),
    )

    with pytest.raises(CalibrationError, match="validation must start"):
        CalibrationArtifact(
            model_version="bad",
            created_at_utc=datetime(2026, 8, 12, tzinfo=UTC),
            forecast_contract_id="forecast",
            observation_contract_id="observation",
            training_start=date(2024, 1, 1),
            training_end=date(2026, 1, 1),
            validation_start=date(2026, 1, 1),
            validation_end=date(2026, 7, 1),
            dataset_sha256="c" * 64,
            min_sample_count=30,
            groups=(calibration_group,),
        )
