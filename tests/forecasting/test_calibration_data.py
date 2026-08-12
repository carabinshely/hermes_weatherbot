from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

from weatherbot.domain import MarketId, ObservationEvidenceStatus, WeatherObservationEvidence
from weatherbot.forecasting.calibration import CalibrationError
from weatherbot.forecasting.calibration_data import (
    ArchiveParityPolicy,
    ForecastCalibrationEvidence,
    ForecastCaptureMethod,
    build_calibration_dataset,
    calibration_sample_from_evidence,
    compare_archive_parity,
    parity_report_json,
    parse_dataset_jsonl,
    write_calibration_dataset,
)
from weatherbot.forecasting.model import DailyHighForecast, ForecastSource

_PRODUCTION_CONTRACT = "open-meteo:ecmwf_ifs025:daily-high:bias-corrected:v1"
_ARCHIVE_CONTRACT = "open-meteo:previous-runs:ecmwf_ifs025:hourly-dplus:v1"
_OBSERVATION_CONTRACT = "declared-resolution-station:daily-high:v1"


def _forecast(
    market_date: date,
    *,
    temperature_f: str,
    lead_days: int = 1,
    capture_contract: str = _PRODUCTION_CONTRACT,
    capture_method: ForecastCaptureMethod = ForecastCaptureMethod.PRODUCTION,
    payload_character: str = "a",
) -> ForecastCalibrationEvidence:
    timezone = ZoneInfo("America/New_York")
    as_of_local = datetime.combine(
        market_date - timedelta(days=lead_days),
        time(hour=12),
        timezone,
    )
    as_of_utc = as_of_local.astimezone(UTC)
    valid_from = datetime.combine(market_date, time.min, timezone).astimezone(UTC)
    valid_until = datetime.combine(market_date + timedelta(days=1), time.min, timezone).astimezone(
        UTC
    )
    retrieved = as_of_utc if capture_method is ForecastCaptureMethod.PRODUCTION else datetime(
        2026, 8, 12, 10, tzinfo=UTC
    )
    forecast = DailyHighForecast(
        temperature_f=Decimal(temperature_f),
        market_date=market_date,
        market_timezone="America/New_York",
        source=ForecastSource.OPEN_METEO_ECMWF_IFS025,
        snapshot_issued_at_utc=as_of_utc,
        valid_from_utc=valid_from,
        valid_until_utc=valid_until,
        retrieved_at_utc=retrieved,
        model_run_initialized_at_utc=as_of_utc - timedelta(hours=6),
    )
    return ForecastCalibrationEvidence(
        city="nyc",
        climate_region="northeast",
        forecast=forecast,
        forecast_as_of_utc=as_of_utc,
        lead_days=lead_days,
        source_contract_id=_PRODUCTION_CONTRACT,
        capture_contract_id=capture_contract,
        capture_method=capture_method,
        source_url="https://api.open-meteo.com/v1/forecast",
        latitude=Decimal("40.7128"),
        longitude=Decimal("-74.0060"),
        bias_correction=True,
        payload_sha256=payload_character * 64,
    )


def _observation(
    market_date: date,
    *,
    temperature: str = "30",
    unit: str = "C",
    payload_character: str = "b",
) -> WeatherObservationEvidence:
    return WeatherObservationEvidence(
        market_id=MarketId(f"nyc-{market_date.isoformat()}"),
        source_name="Weather Underground daily history",
        source_url="https://www.wunderground.com/history/daily/us/ny/new-york-city/KLGA",
        station_id="KLGA",
        measurement_basis="finalized daily high temperature",
        market_date=market_date,
        market_timezone="America/New_York",
        temperature=Decimal(temperature),
        unit=unit,
        retrieved_at=datetime.combine(
            market_date + timedelta(days=1),
            time(hour=8),
            tzinfo=UTC,
        ),
        source_timestamp=None,
        source_revision="final-v1",
        status=ObservationEvidenceStatus.FINAL,
        payload_hash=payload_character * 64,
    )


def _passing_parity_report() -> object:
    dates = tuple(date(2026, 7, day) for day in range(1, 5))
    references = tuple(
        _forecast(day, temperature_f=str(80 + index), payload_character=chr(ord("a") + index))
        for index, day in enumerate(dates)
    )
    candidates = tuple(
        _forecast(
            day,
            temperature_f=str(Decimal(80 + index) + Decimal("0.1")),
            capture_contract=_ARCHIVE_CONTRACT,
            capture_method=ForecastCaptureMethod.PREVIOUS_RUNS,
            payload_character=chr(ord("e") + index),
        )
        for index, day in enumerate(dates)
    )
    return compare_archive_parity(
        references,
        candidates,
        policy=ArchiveParityPolicy(
            min_pairs=4,
            min_reference_coverage=1.0,
            max_mae_f=0.25,
            max_abs_error_f=0.25,
        ),
    )


def test_observation_is_normalized_to_fahrenheit_without_losing_truth_provenance() -> None:
    market_date = date(2026, 7, 10)
    sample = calibration_sample_from_evidence(_forecast(market_date, temperature_f="85"), _observation(market_date))

    assert sample.observed_temperature_f == Decimal("86")
    assert sample.station_id == "KLGA"
    assert sample.measurement_basis == "finalized daily high temperature"
    assert sample.observation_payload_sha256 == "b" * 64


def test_archive_capture_is_rejected_without_passing_parity_evidence() -> None:
    market_date = date(2026, 7, 10)
    archive = _forecast(
        market_date,
        temperature_f="85",
        capture_contract=_ARCHIVE_CONTRACT,
        capture_method=ForecastCaptureMethod.PREVIOUS_RUNS,
    )

    with pytest.raises(CalibrationError, match="without passing source parity"):
        build_calibration_dataset(
            ((archive, _observation(market_date)),),
            forecast_contract_id=_PRODUCTION_CONTRACT,
            observation_contract_id=_OBSERVATION_CONTRACT,
        )


def test_passing_archive_parity_allows_reconstruction_and_is_bound_into_manifest() -> None:
    report = _passing_parity_report()
    assert report.compatible
    market_date = date(2026, 7, 10)
    archive = _forecast(
        market_date,
        temperature_f="85",
        capture_contract=_ARCHIVE_CONTRACT,
        capture_method=ForecastCaptureMethod.PREVIOUS_RUNS,
    )

    dataset = build_calibration_dataset(
        ((archive, _observation(market_date)),),
        forecast_contract_id=_PRODUCTION_CONTRACT,
        observation_contract_id=_OBSERVATION_CONTRACT,
        parity_reports=(report,),
    )

    assert dataset.manifest.capture_contract_ids == (_ARCHIVE_CONTRACT,)
    assert dataset.manifest.parity_report_sha256s == (report.report_sha256,)
    assert dataset.manifest.dataset_sha256


def test_failed_parity_blocks_dataset_even_when_report_is_supplied() -> None:
    dates = tuple(date(2026, 7, day) for day in range(1, 5))
    references = tuple(_forecast(day, temperature_f="80") for day in dates)
    candidates = tuple(
        _forecast(
            day,
            temperature_f="85",
            capture_contract=_ARCHIVE_CONTRACT,
            capture_method=ForecastCaptureMethod.PREVIOUS_RUNS,
        )
        for day in dates
    )
    report = compare_archive_parity(
        references,
        candidates,
        policy=ArchiveParityPolicy(
            min_pairs=4,
            min_reference_coverage=1.0,
            max_mae_f=0.5,
            max_abs_error_f=1.0,
        ),
    )
    assert not report.compatible

    with pytest.raises(CalibrationError, match="archive parity failed"):
        build_calibration_dataset(
            ((candidates[0], _observation(dates[0])),),
            forecast_contract_id=_PRODUCTION_CONTRACT,
            observation_contract_id=_OBSERVATION_CONTRACT,
            parity_reports=(report,),
        )


def test_dataset_hash_and_manifest_are_independent_of_input_order(tmp_path: object) -> None:
    from pathlib import Path

    assert isinstance(tmp_path, Path)
    dates = (date(2026, 7, 10), date(2026, 7, 11))
    pairs = tuple(
        (_forecast(day, temperature_f=str(84 + index)), _observation(day))
        for index, day in enumerate(dates)
    )
    forward = build_calibration_dataset(
        pairs,
        forecast_contract_id=_PRODUCTION_CONTRACT,
        observation_contract_id=_OBSERVATION_CONTRACT,
    )
    reverse = build_calibration_dataset(
        reversed(pairs),
        forecast_contract_id=_PRODUCTION_CONTRACT,
        observation_contract_id=_OBSERVATION_CONTRACT,
    )

    assert forward.to_jsonl() == reverse.to_jsonl()
    assert forward.manifest.dataset_sha256 == reverse.manifest.dataset_sha256
    assert forward.manifest.manifest_sha256 == reverse.manifest.manifest_sha256
    assert parse_dataset_jsonl(forward.to_jsonl())

    records_path = tmp_path / "records.jsonl"
    manifest_path = tmp_path / "manifest.json"
    write_calibration_dataset(
        forward,
        records_path=records_path,
        manifest_path=manifest_path,
    )
    assert records_path.read_text(encoding="utf-8") == forward.to_jsonl()
    assert manifest_path.read_text(encoding="utf-8") == forward.manifest.to_json()


def test_duplicate_and_conflicting_dataset_identities_both_fail_closed() -> None:
    market_date = date(2026, 7, 10)
    forecast = _forecast(market_date, temperature_f="85")
    observation = _observation(market_date)

    with pytest.raises(CalibrationError, match="duplicate calibration dataset identity"):
        build_calibration_dataset(
            ((forecast, observation), (forecast, observation)),
            forecast_contract_id=_PRODUCTION_CONTRACT,
            observation_contract_id=_OBSERVATION_CONTRACT,
        )

    conflicting_observation = _observation(
        market_date,
        temperature="31",
        payload_character="c",
    )
    with pytest.raises(CalibrationError, match="conflicting calibration dataset identity"):
        build_calibration_dataset(
            ((forecast, observation), (forecast, conflicting_observation)),
            forecast_contract_id=_PRODUCTION_CONTRACT,
            observation_contract_id=_OBSERVATION_CONTRACT,
        )


def test_parity_report_is_content_addressed_and_records_coverage() -> None:
    report = _passing_parity_report()
    serialized = parity_report_json(report)

    assert report.reference_coverage == pytest.approx(1.0)
    assert report.mae_f == pytest.approx(0.1)
    assert report.report_sha256 in serialized
