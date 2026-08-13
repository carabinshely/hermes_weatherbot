from __future__ import annotations

import hashlib
import json
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from weatherbot.forecasting.calibration import CalibrationSample, calibration_artifact_from_json
from weatherbot.forecasting.calibration_data import (
    CalibrationDataset,
    CalibrationDatasetManifest,
    CalibrationDatasetRecord,
    ForecastCaptureMethod,
    write_calibration_dataset,
)
from weatherbot.forecasting.calibration_v3_train import (
    EvaluationKind,
    train_v3_calibration_dataset,
    write_v3_training_output,
)
from weatherbot.forecasting.model import ForecastSource

_SOURCE = ForecastSource.OPEN_METEO_ECMWF_IFS025
_FORECAST_CONTRACT = "fixture:forecast:v1"
_OBSERVATION_CONTRACT = "fixture:observation:v1"


def _record(day: date, index: int) -> CalibrationDatasetRecord:
    forecast = Decimal("70") + Decimal(index % 3)
    residual = Decimal((-2, -1, 1, 2)[index % 4])
    material = f"v3-{index}-{day.isoformat()}"
    sample = CalibrationSample(
        city="nyc",
        climate_region="northeast",
        forecast_source=_SOURCE,
        market_date=day,
        lead_days=0,
        forecast_temperature_f=forecast,
        observed_temperature_f=forecast + residual,
        forecast_as_of_utc=datetime.combine(day - timedelta(days=1), datetime.min.time(), UTC),
        observation_finalized_at_utc=datetime.combine(
            day + timedelta(days=1), datetime.min.time(), UTC
        ),
        observation_source="synthetic finalized observation",
        station_id="KLGA",
        measurement_basis="synthetic finalized daily high",
        forecast_payload_sha256=hashlib.sha256(f"forecast-{material}".encode()).hexdigest(),
        observation_payload_sha256=hashlib.sha256(
            f"observation-{material}".encode()
        ).hexdigest(),
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
        forecast_provenance_sha256=hashlib.sha256(f"provenance-{material}".encode()).hexdigest(),
    )


def _write_dataset(tmp_path: Path) -> tuple[Path, Path]:
    start = date(2026, 6, 1)
    records = tuple(_record(start + timedelta(days=index), index) for index in range(30))
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
    dataset = CalibrationDataset(
        records=records,
        manifest=CalibrationDatasetManifest(
            forecast_contract_id=_FORECAST_CONTRACT,
            observation_contract_id=_OBSERVATION_CONTRACT,
            record_count=len(records),
            start_date=records[0].sample.market_date,
            end_date=records[-1].sample.market_date,
            capture_contract_ids=(_FORECAST_CONTRACT,),
            parity_report_sha256s=(),
            dataset_sha256=hashlib.sha256(jsonl.encode()).hexdigest(),
        ),
    )
    records_path = tmp_path / "dataset.jsonl"
    manifest_path = tmp_path / "manifest.json"
    write_calibration_dataset(dataset, records_path=records_path, manifest_path=manifest_path)
    return records_path, manifest_path


def test_v3_training_report_is_deterministic_and_labeled_development(tmp_path: Path) -> None:
    records_path, manifest_path = _write_dataset(tmp_path)
    kwargs = dict(
        records_path=records_path,
        manifest_path=manifest_path,
        evaluation_kind=EvaluationKind.DEVELOPMENT,
        model_version="issue12-v3-dev-fixture",
        created_at_utc=datetime(2026, 8, 10, 12, tzinfo=UTC),
        training_end=date(2026, 6, 25),
        validation_start=date(2026, 6, 26),
        validation_end=date(2026, 6, 30),
        min_sample_count=20,
    )

    first = train_v3_calibration_dataset(**kwargs)
    second = train_v3_calibration_dataset(**kwargs)

    assert first.fit.artifact.artifact_sha256 == second.fit.artifact.artifact_sha256
    assert first.report == second.report
    assert first.report["schema_version"] == 2
    assert first.report["fitting_policy"] == "v3-normal-runtime-v1"
    assert first.report["evaluation_kind"] == "development"
    assert first.report["group_fit_decisions"]
    assert all(
        item["runtime_distribution_type"] == "normal"
        for item in first.report["group_fit_decisions"]
        if item["runtime_eligible"]
    )


def test_v3_written_artifact_round_trips_without_report_policy_in_artifact(tmp_path: Path) -> None:
    records_path, manifest_path = _write_dataset(tmp_path)
    output = train_v3_calibration_dataset(
        records_path=records_path,
        manifest_path=manifest_path,
        evaluation_kind=EvaluationKind.DEVELOPMENT,
        model_version="issue12-v3-dev-fixture",
        created_at_utc=datetime(2026, 8, 10, 12, tzinfo=UTC),
        training_end=date(2026, 6, 25),
        validation_start=date(2026, 6, 26),
        validation_end=date(2026, 6, 30),
        min_sample_count=20,
    )
    artifact_path = tmp_path / "artifact.json"
    report_path = tmp_path / "report.json"

    write_v3_training_output(output, artifact_path=artifact_path, report_path=report_path)

    artifact_text = artifact_path.read_text(encoding="utf-8")
    restored = calibration_artifact_from_json(artifact_text)
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert restored.artifact_sha256 == output.fit.artifact.artifact_sha256
    assert "fitting_policy" not in restored.payload_mapping()
    assert report["fitting_policy"] == "v3-normal-runtime-v1"
    assert report["evaluation_kind"] == "development"
