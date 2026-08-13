from __future__ import annotations

import hashlib
from datetime import UTC, date, datetime
from pathlib import Path

from weatherbot.forecasting.calibration_sparse_sweep import (
    CalibrationSourceGap,
    CalibrationSparseReport,
    load_unavailable_run_registry,
    missing_horizons,
)

_EVIDENCE = Path("tests/fixtures/forecasting/open_meteo_unavailable_runs_2026-08-13.json")
_DATASET_SHA = hashlib.sha256(b"dataset").hexdigest()


def test_unavailable_run_registry_loads_frozen_provider_evidence() -> None:
    registry = load_unavailable_run_registry(_EVIDENCE)

    run = datetime(2026, 6, 10, 18, tzinfo=UTC)
    assert set(registry) == {run}
    evidence = registry[run]
    assert evidence.workflow_run == 31700087146
    assert evidence.response_body_sha256 == (
        "51951f13cbca15a5ae3c6163ac7c4cd046f92e5b4781098cea74d29dd3c2e8d6"
    )
    assert evidence.reason.endswith("run: 2026-06-10T18:00Z")


def test_missing_run_excludes_only_matching_horizon_per_target_day() -> None:
    registry = load_unavailable_run_registry(_EVIDENCE)

    assert set(missing_horizons(date(2026, 6, 10), registry)) == {0}
    assert set(missing_horizons(date(2026, 6, 11), registry)) == {1}
    assert set(missing_horizons(date(2026, 6, 12), registry)) == {2}
    assert missing_horizons(date(2026, 6, 13), registry) == {}


def test_sparse_report_counts_observation_days_and_forecast_samples_separately() -> None:
    observation_gap = CalibrationSourceGap(
        city="nyc",
        station_id="KLGA",
        market_date=date(2026, 6, 1),
        lead_days=None,
        source_class="observation",
        source_url="https://www.wunderground.com/history/daily/example",
        evidence_source_url="https://www.wunderground.com/history/daily/example",
        raw_payload_sha256=hashlib.sha256(b"observation").hexdigest(),
        reason_code="weather_underground_coverage",
        reason_detail="insufficient Weather Underground observations: 17",
    )
    forecast_gap = CalibrationSourceGap(
        city="nyc",
        station_id="KLGA",
        market_date=date(2026, 6, 10),
        lead_days=0,
        source_class="forecast",
        source_url="https://single-runs-api.open-meteo.com/v1/forecast?example=1",
        evidence_source_url="https://single-runs-api.open-meteo.com/v1/forecast?example=1",
        raw_payload_sha256=hashlib.sha256(b"forecast-gap").hexdigest(),
        reason_code="open_meteo_model_run_unavailable",
        reason_detail=(
            "The requested model run is not available. Model: ecmwf_ifs025, run: 2026-06-10T18:00Z"
        ),
        run_initialized_at_utc=datetime(2026, 6, 10, 18, tzinfo=UTC),
    )
    report = CalibrationSparseReport(
        requested_start_date=date(2026, 6, 1),
        requested_end_date=date(2026, 6, 2),
        cities=("nyc",),
        expected_record_count=6,
        collected_record_count=2,
        exclusions=(observation_gap, forecast_gap),
        dataset_sha256=_DATASET_SHA,
    )

    assert report.excluded_date_count == 1
    assert report.forecast_gap_sample_count == 1
    assert report.excluded_record_count == 4
