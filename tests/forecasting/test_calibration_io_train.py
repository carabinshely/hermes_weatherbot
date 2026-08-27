from __future__ import annotations

import hashlib
import json
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from weatherbot.forecasting.calibration import (
    CalibrationError,
    CalibrationSample,
    calibration_artifact_from_json,
)
from weatherbot.forecasting.calibration_data import (
    CalibrationDataset,
    CalibrationDatasetManifest,
    CalibrationDatasetRecord,
    ForecastCaptureMethod,
    write_calibration_dataset,
)
from weatherbot.forecasting.calibration_io import load_calibration_dataset
from weatherbot.forecasting.calibration_train import (
    train_calibration_dataset,
    write_training_output,
)
from weatherbot.forecasting.model import ForecastSource

_FORECAST_CONTRACT = "test:forecast:v1"
_OBSERVATION_CONTRACT = "test:observation:v1"


def _record(day: date, index: int) -> CalibrationDatasetRecord:
    forecast = Decimal("70") + Decimal(index % 5)
    residual = Decimal((index % 4) - 1)
    sample = CalibrationSample(
        city="nyc",
        climate_region="northeast",
        forecast_source=ForecastSource.OPEN_METEO_ECMWF_IFS025,
        market_date=day,
        lead_days=0,
        forecast_temperature_f=forecast,
        observed_temperature_f=forecast + residual,
        forecast_as_of_utc=datetime.combine(day - timedelta(days=1), datetime.min.time(), UTC),
        observation_finalized_at_utc=datetime.combine(
            day + timedelta(days=1),
            datetime.min.time(),
            UTC,
        ),
        observation_source="Weather Underground test fixture",
        station_id="KLGA",
        measurement_basis="finalized daily high",
        forecast_payload_sha256=hashlib.sha256(f"forecast-{index}".encode()).hexdigest(),
        observation_payload_sha256=hashlib.sha256(f"observation-{index}".encode()).hexdigest(),
    )
    return CalibrationDatasetRecord(
        sample=sample,
        forecast_contract_id=_FORECAST_CONTRACT,
        observation_contract_id=_OBSERVATION_CONTRACT,
        forecast_capture_contract_id=_FORECAST_CONTRACT,
        forecast_capture_method=ForecastCaptureMethod.PRODUCTION,
        forecast_source_url="https://example.test/forecast",
        forecast_latitude=Decimal("40.7772"),
        forecast_longitude=Decimal("-73.8726"),
        forecast_bias_correction=True,
        forecast_provenance_sha256=hashlib.sha256(f"provenance-{index}".encode()).hexdigest(),
    )


def _dataset(start: date = date(2026, 6, 1), count: int = 14) -> CalibrationDataset:
    records = tuple(_record(start + timedelta(days=index), index) for index in range(count))
    jsonl = "".join(
        json.dumps(
            record.to_mapping(),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        )
        + "\n"
        for record in records
    )
    manifest = CalibrationDatasetManifest(
        forecast_contract_id=_FORECAST_CONTRACT,
        observation_contract_id=_OBSERVATION_CONTRACT,
        record_count=len(records),
        start_date=records[0].sample.market_date,
        end_date=records[-1].sample.market_date,
        capture_contract_ids=(_FORECAST_CONTRACT,),
        parity_report_sha256s=(),
        dataset_sha256=hashlib.sha256(jsonl.encode()).hexdigest(),
    )
    return CalibrationDataset(records=records, manifest=manifest)


def _write_dataset(tmp_path: Path) -> tuple[Path, Path, CalibrationDataset]:
    dataset = _dataset()
    records_path = tmp_path / "dataset.jsonl"
    manifest_path = tmp_path / "manifest.json"
    write_calibration_dataset(
        dataset,
        records_path=records_path,
        manifest_path=manifest_path,
    )
    return records_path, manifest_path, dataset


def test_dataset_loader_round_trips_canonical_dataset(tmp_path: Path) -> None:
    records_path, manifest_path, expected = _write_dataset(tmp_path)

    loaded = load_calibration_dataset(records_path, manifest_path)

    assert loaded.to_jsonl() == expected.to_jsonl()
    assert loaded.manifest.to_json() == expected.manifest.to_json()
    assert loaded.samples == expected.samples


def test_dataset_loader_rejects_record_tampering(tmp_path: Path) -> None:
    records_path, manifest_path, _ = _write_dataset(tmp_path)
    text = records_path.read_text(encoding="utf-8")
    records_path.write_text(
        text.replace('"forecast_temperature_f":"70"', '"forecast_temperature_f":"99"', 1),
        encoding="utf-8",
    )

    with pytest.raises(CalibrationError, match="checksum mismatch"):
        load_calibration_dataset(records_path, manifest_path)


def test_dataset_loader_rejects_manifest_tampering(tmp_path: Path) -> None:
    records_path, manifest_path, _ = _write_dataset(tmp_path)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["record_count"] += 1
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(CalibrationError, match="manifest checksum mismatch"):
        load_calibration_dataset(records_path, manifest_path)


def test_training_is_deterministic_for_fixed_inputs(tmp_path: Path) -> None:
    records_path, manifest_path, _ = _write_dataset(tmp_path)
    first = train_calibration_dataset(
        records_path=records_path,
        manifest_path=manifest_path,
        model_version="test-calibration-v1",
        created_at_utc=datetime(2026, 8, 12, 13, tzinfo=UTC),
        training_end=date(2026, 6, 10),
        validation_start=date(2026, 6, 11),
        validation_end=date(2026, 6, 14),
        min_sample_count=5,
    )
    second = train_calibration_dataset(
        records_path=records_path,
        manifest_path=manifest_path,
        model_version="test-calibration-v1",
        created_at_utc=datetime(2026, 8, 12, 13, tzinfo=UTC),
        training_end=date(2026, 6, 10),
        validation_start=date(2026, 6, 11),
        validation_end=date(2026, 6, 14),
        min_sample_count=5,
    )

    assert first.fit.artifact.artifact_sha256 == second.fit.artifact.artifact_sha256
    assert first.fit.validation == second.fit.validation
    assert first.report == second.report
    assert first.fit.validation.sample_count == 4


def test_training_requires_contiguous_final_holdout(tmp_path: Path) -> None:
    records_path, manifest_path, _ = _write_dataset(tmp_path)

    with pytest.raises(CalibrationError, match="calendar day after"):
        train_calibration_dataset(
            records_path=records_path,
            manifest_path=manifest_path,
            model_version="test-calibration-v1",
            created_at_utc=datetime(2026, 8, 12, 13, tzinfo=UTC),
            training_end=date(2026, 6, 9),
            validation_start=date(2026, 6, 11),
            validation_end=date(2026, 6, 14),
            min_sample_count=5,
        )


def test_written_artifact_reloads_and_report_names_fixed_baseline(tmp_path: Path) -> None:
    records_path, manifest_path, _ = _write_dataset(tmp_path)
    output = train_calibration_dataset(
        records_path=records_path,
        manifest_path=manifest_path,
        model_version="test-calibration-v1",
        created_at_utc=datetime(2026, 8, 12, 13, tzinfo=UTC),
        training_end=date(2026, 6, 10),
        validation_start=date(2026, 6, 11),
        validation_end=date(2026, 6, 14),
        min_sample_count=5,
    )
    artifact_path = tmp_path / "artifact.json"
    report_path = tmp_path / "report.json"

    write_training_output(
        output,
        artifact_path=artifact_path,
        report_path=report_path,
    )

    reloaded = calibration_artifact_from_json(artifact_path.read_text(encoding="utf-8"))
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert reloaded.artifact_sha256 == output.fit.artifact.artifact_sha256
    assert report["artifact_sha256"] == reloaded.artifact_sha256
    assert report["baseline_comparison"]["fixed_sigma_f"] == 2.0
    assert "calibrated_better_mean_log_score" in report["baseline_comparison"]
