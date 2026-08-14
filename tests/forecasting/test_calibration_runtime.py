from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from tests.quoting.helpers import weather_snapshot
from weatherbot.forecasting.archive import PRODUCTION_FORECAST_CONTRACT_ID
from weatherbot.forecasting.calibration import (
    CalibrationArtifact,
    CalibrationDiagnostics,
    CalibrationGroup,
    CalibrationGroupKey,
    GroupLevel,
    NormalResidualDistribution,
)
from weatherbot.forecasting.contracts import (
    CALIBRATION_LEAD_DAYS,
    OBSERVATION_CONTRACT_ID,
    expected_calibration_model_run,
)
from weatherbot.forecasting.model import (
    DailyHighForecast,
    ForecastSource,
    WeatherInputSnapshot,
)
from weatherbot.forecasting.runtime import (
    CalibrationApprovalError,
    CalibrationCompatibilityError,
    CalibrationUnavailable,
    load_calibrated_probability_runtime,
)
from weatherbot.markets import TemperatureBucket, TemperatureUnit

_SOURCE = ForecastSource.OPEN_METEO_ECMWF_IFS025
_REJECTED_V1 = "aff6f9c1e8f6971104e6640abcd4306bc68e84116b9a52d9d9ee993ea468cc07"
_REJECTED_V2 = "d0f09ff723fab5bc250e824bb2edc66f96730575c5b569778432b8f5b5eefbdc"


def _artifact() -> CalibrationArtifact:
    group = CalibrationGroup(
        key=CalibrationGroupKey(level=GroupLevel.SOURCE, forecast_source=_SOURCE),
        sample_count=60,
        distribution=NormalResidualDistribution(Decimal("0.5"), Decimal("2.5")),
        training_end=date(2026, 8, 10),
        diagnostics=CalibrationDiagnostics(jarque_bera=0.2, normality_p_value=0.9),
    )
    return CalibrationArtifact(
        model_version="issue12-v3-fixture",
        created_at_utc=datetime(2026, 8, 11, 10, tzinfo=UTC),
        forecast_contract_id=PRODUCTION_FORECAST_CONTRACT_ID,
        observation_contract_id=OBSERVATION_CONTRACT_ID,
        training_start=date(2026, 4, 5),
        training_end=date(2026, 8, 10),
        validation_start=date(2026, 8, 11),
        validation_end=date(2026, 8, 24),
        dataset_sha256="c" * 64,
        min_sample_count=30,
        groups=(group,),
    )


def _approval(
    *,
    artifact_sha256: str,
    model_version: str = "issue12-v3-fixture",
    artifact_path: str | None = None,
    decision: str = "accepted",
    forecast_contract_id: str = PRODUCTION_FORECAST_CONTRACT_ID,
    observation_contract_id: str = OBSERVATION_CONTRACT_ID,
) -> dict[str, object]:
    return {
        "schema_version": 1,
        "decision": decision,
        "model_version": model_version,
        "artifact_path": artifact_path or f"artifacts/calibration/accepted/{artifact_sha256}.json",
        "artifact_sha256": artifact_sha256,
        "forecast_contract_id": forecast_contract_id,
        "observation_contract_id": observation_contract_id,
        "acceptance_reference": "issue-49",
        "accepted_at_utc": "2026-08-26T10:00:00Z",
    }


def _write_approval(root: Path, payload: object) -> None:
    path = root / "config/calibration-approval.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def _approved_repository(root: Path) -> tuple[CalibrationArtifact, Path]:
    artifact = _artifact()
    artifact_path = root / f"artifacts/calibration/accepted/{artifact.artifact_sha256}.json"
    artifact_path.parent.mkdir(parents=True, exist_ok=True)
    artifact_path.write_text(artifact.to_json(), encoding="utf-8")
    _write_approval(root, _approval(artifact_sha256=artifact.artifact_sha256))
    return artifact, artifact_path


def test_missing_approval_manifest_fails_closed(tmp_path: Path) -> None:
    with pytest.raises(CalibrationUnavailable, match="no accepted calibration approval"):
        load_calibrated_probability_runtime(repository_root=tmp_path)


def test_manifest_must_be_strict_json_object(tmp_path: Path) -> None:
    path = tmp_path / "config/calibration-approval.json"
    path.parent.mkdir(parents=True)
    path.write_text("not-json", encoding="utf-8")
    with pytest.raises(CalibrationApprovalError, match="valid JSON"):
        load_calibrated_probability_runtime(repository_root=tmp_path)

    path.write_text("[]", encoding="utf-8")
    with pytest.raises(CalibrationApprovalError, match="must be an object"):
        load_calibrated_probability_runtime(repository_root=tmp_path)


def test_unaccepted_decision_fails_closed(tmp_path: Path) -> None:
    _write_approval(tmp_path, _approval(artifact_sha256="a" * 64, decision="rejected"))
    with pytest.raises(CalibrationUnavailable, match="not accepted"):
        load_calibrated_probability_runtime(repository_root=tmp_path)


@pytest.mark.parametrize("digest", (_REJECTED_V1, _REJECTED_V2))
def test_known_rejected_artifacts_cannot_be_approved(tmp_path: Path, digest: str) -> None:
    _write_approval(tmp_path, _approval(artifact_sha256=digest))
    with pytest.raises(CalibrationApprovalError, match="known rejected"):
        load_calibrated_probability_runtime(repository_root=tmp_path)


@pytest.mark.parametrize(
    "artifact_path",
    (
        "/tmp/model.json",
        "artifacts/calibration/accepted/../model.json",
        "artifacts/calibration/unreviewed/model.json",
    ),
)
def test_approval_artifact_path_cannot_escape_reviewed_directory(
    tmp_path: Path, artifact_path: str
) -> None:
    _write_approval(
        tmp_path,
        _approval(artifact_sha256="a" * 64, artifact_path=artifact_path),
    )
    with pytest.raises(CalibrationApprovalError, match="artifact_path"):
        load_calibrated_probability_runtime(repository_root=tmp_path)


def test_artifact_filename_is_content_addressed(tmp_path: Path) -> None:
    _write_approval(
        tmp_path,
        _approval(
            artifact_sha256="a" * 64,
            artifact_path="artifacts/calibration/accepted/not-the-sha.json",
        ),
    )
    with pytest.raises(CalibrationApprovalError, match="filename"):
        load_calibrated_probability_runtime(repository_root=tmp_path)


def test_missing_approved_artifact_fails_closed(tmp_path: Path) -> None:
    _write_approval(tmp_path, _approval(artifact_sha256="a" * 64))
    with pytest.raises(CalibrationUnavailable, match="artifact is missing"):
        load_calibrated_probability_runtime(repository_root=tmp_path)


def test_corrupt_approved_artifact_fails_closed(tmp_path: Path) -> None:
    path = tmp_path / f"artifacts/calibration/accepted/{'a' * 64}.json"
    path.parent.mkdir(parents=True)
    path.write_text("{broken", encoding="utf-8")
    _write_approval(tmp_path, _approval(artifact_sha256="a" * 64))
    with pytest.raises(CalibrationApprovalError, match="strict loading"):
        load_calibrated_probability_runtime(repository_root=tmp_path)


def test_manifest_and_artifact_identity_must_match(tmp_path: Path) -> None:
    artifact, path = _approved_repository(tmp_path)
    approval = _approval(
        artifact_sha256=artifact.artifact_sha256,
        model_version="different-version",
    )
    _write_approval(tmp_path, approval)
    with pytest.raises(CalibrationApprovalError, match="model version"):
        load_calibrated_probability_runtime(repository_root=tmp_path)
    assert path.is_file()


def test_runtime_contracts_must_match_production(tmp_path: Path) -> None:
    artifact = _artifact()
    _write_approval(
        tmp_path,
        _approval(
            artifact_sha256=artifact.artifact_sha256,
            forecast_contract_id="another-forecast-contract",
        ),
    )
    with pytest.raises(CalibrationCompatibilityError, match="forecast contract"):
        load_calibrated_probability_runtime(repository_root=tmp_path)


def test_valid_approval_loads_one_runtime_and_preserves_probability_provenance(
    tmp_path: Path,
) -> None:
    artifact, _ = _approved_repository(tmp_path)
    runtime = load_calibrated_probability_runtime(repository_root=tmp_path)
    weather = weather_snapshot(
        issued_at=datetime(2026, 8, 6, 4, 16, tzinfo=UTC),
        model_run_initialized_at_utc=expected_calibration_model_run(
            target_date=date(2026, 8, 6), lead_days=0
        ),
    )
    bucket = TemperatureBucket.bounded(86, 86, TemperatureUnit.FAHRENHEIT)

    result = runtime.probability(
        city="chicago",
        climate_region="ohio_valley",
        lead_days=0,
        weather=weather,
        bucket=bucket,
    )
    direct = runtime.model.probability(
        city="chicago",
        climate_region="ohio_valley",
        forecast_source=weather.forecast.source,
        market_date=weather.forecast.market_date,
        lead_days=0,
        forecast_temperature_f=weather.signal_temperature_f,
        bucket=bucket,
    )

    assert result.model_probability == Decimal(str(direct.probability))
    assert result.model_version == artifact.model_version
    assert result.artifact_sha256 == artifact.artifact_sha256
    assert result.city_slug == "chicago"
    assert result.climate_region == "ohio_valley"
    assert result.lead_days == 0
    assert result.forecast_source == weather.forecast.source.value
    assert result.calibration_group_key == direct.calibration_group_key
    assert result.fallback_level == GroupLevel.SOURCE.value
    assert result.distribution_type == "normal"
    assert result.calibration_sample_count == 60
    assert result.training_cutoff == date(2026, 8, 10)
    assert result.audit_metadata() == {
        "model_probability": format(result.model_probability, "f"),
        "model_version": result.model_version,
        "artifact_sha256": result.artifact_sha256,
        "city_slug": "chicago",
        "climate_region": "ohio_valley",
        "lead_days": 0,
        "forecast_source": result.forecast_source,
        "calibration_group_key": result.calibration_group_key,
        "fallback_level": result.fallback_level,
        "distribution_type": result.distribution_type,
        "calibration_sample_count": 60,
        "training_cutoff": "2026-08-10",
    }

    fingerprint = result.calibration_fingerprint()
    assert fingerprint == result.calibration_fingerprint()
    assert replace(result, artifact_sha256="f" * 64).calibration_fingerprint() != fingerprint
    assert replace(result, model_probability=Decimal("0.5")).calibration_fingerprint() != fingerprint
    assert replace(result, lead_days=1).calibration_fingerprint() != fingerprint


def test_runtime_rejects_lead_days_outside_frozen_dataset(tmp_path: Path) -> None:
    _approved_repository(tmp_path)
    runtime = load_calibrated_probability_runtime(repository_root=tmp_path)
    weather = weather_snapshot(
        issued_at=datetime(2026, 8, 6, 4, 16, tzinfo=UTC),
        model_run_initialized_at_utc=expected_calibration_model_run(
            target_date=date(2026, 8, 6), lead_days=0
        ),
    )
    bucket = TemperatureBucket.bounded(86, 86, TemperatureUnit.FAHRENHEIT)

    with pytest.raises(CalibrationCompatibilityError, match="outside calibrated lead set"):
        runtime.probability(
            city="chicago",
            climate_region="ohio_valley",
            lead_days=max(CALIBRATION_LEAD_DAYS) + 1,
            weather=weather,
            bucket=bucket,
        )


def test_runtime_rejects_forecast_outside_calibrated_decision_window(tmp_path: Path) -> None:
    _approved_repository(tmp_path)
    runtime = load_calibrated_probability_runtime(repository_root=tmp_path)
    weather = weather_snapshot(issued_at=datetime(2026, 8, 6, 14, 0, tzinfo=UTC))
    bucket = TemperatureBucket.bounded(86, 86, TemperatureUnit.FAHRENHEIT)

    with pytest.raises(CalibrationCompatibilityError, match="decision window"):
        runtime.probability(
            city="chicago",
            climate_region="ohio_valley",
            lead_days=0,
            weather=weather,
            bucket=bucket,
        )


def test_runtime_rejects_mismatched_model_run_vintage(tmp_path: Path) -> None:
    _approved_repository(tmp_path)
    runtime = load_calibrated_probability_runtime(repository_root=tmp_path)
    base = weather_snapshot(
        issued_at=datetime(2026, 8, 6, 4, 16, tzinfo=UTC),
        model_run_initialized_at_utc=expected_calibration_model_run(
            target_date=date(2026, 8, 6), lead_days=0
        ),
    )
    forecast = base.forecast
    mismatched = DailyHighForecast(
        temperature_f=forecast.temperature_f,
        market_date=forecast.market_date,
        market_timezone=forecast.market_timezone,
        source=forecast.source,
        snapshot_issued_at_utc=forecast.snapshot_issued_at_utc,
        valid_from_utc=forecast.valid_from_utc,
        valid_until_utc=forecast.valid_until_utc,
        retrieved_at_utc=forecast.retrieved_at_utc,
        model_run_initialized_at_utc=expected_calibration_model_run(
            target_date=forecast.market_date,
            lead_days=0,
        )
        + timedelta(hours=6),
    )
    weather = WeatherInputSnapshot(
        forecast=mismatched,
        observation=None,
        assembled_at_utc=base.assembled_at_utc,
    )
    bucket = TemperatureBucket.bounded(86, 86, TemperatureUnit.FAHRENHEIT)

    with pytest.raises(CalibrationCompatibilityError, match="18Z vintage"):
        runtime.probability(
            city="chicago",
            climate_region="ohio_valley",
            lead_days=0,
            weather=weather,
            bucket=bucket,
        )


def test_runtime_rejects_missing_model_run_provenance(tmp_path: Path) -> None:
    _approved_repository(tmp_path)
    runtime = load_calibrated_probability_runtime(repository_root=tmp_path)
    weather = weather_snapshot(issued_at=datetime(2026, 8, 6, 4, 16, tzinfo=UTC))
    bucket = TemperatureBucket.bounded(86, 86, TemperatureUnit.FAHRENHEIT)

    with pytest.raises(CalibrationCompatibilityError, match="cannot be proven"):
        runtime.probability(
            city="chicago",
            climate_region="ohio_valley",
            lead_days=0,
            weather=weather,
            bucket=bucket,
        )
