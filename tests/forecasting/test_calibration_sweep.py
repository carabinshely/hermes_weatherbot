from __future__ import annotations

import hashlib
import json
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import cast

import pytest

from weatherbot.forecasting import calibration_sweep
from weatherbot.forecasting.calibration import CalibrationSample
from weatherbot.forecasting.calibration_build import (
    CachedHttpCapture,
    DEFAULT_MARKETS,
    ImmutableHttpCache,
)
from weatherbot.forecasting.calibration_data import (
    ArchiveParityReport,
    CalibrationDataset,
    CalibrationDatasetManifest,
    CalibrationDatasetRecord,
    ForecastCaptureMethod,
)
from weatherbot.forecasting.calibration_sweep import (
    CalibrationSweepExclusion,
    CalibrationSweepReport,
    collect_calibration_sweep,
    merge_calibration_datasets,
)
from weatherbot.forecasting.model import ForecastSource
from weatherbot.resolution.wunderground import WeatherUndergroundHistoryError

_FORECAST_CONTRACT = "test:forecast:v1"
_OBSERVATION_CONTRACT = "test:observation:v1"
_CAPTURE_CONTRACT = "test:capture:v1"
_PARITY_SHA = hashlib.sha256(b"parity").hexdigest()


def _record(day: date, lead_days: int, index: int) -> CalibrationDatasetRecord:
    forecast = Decimal("70") + Decimal(lead_days)
    sample = CalibrationSample(
        city="nyc",
        climate_region="northeast",
        forecast_source=ForecastSource.OPEN_METEO_ECMWF_IFS025,
        market_date=day,
        lead_days=lead_days,
        forecast_temperature_f=forecast,
        observed_temperature_f=forecast + Decimal("1"),
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
        observation_source="Weather Underground test fixture",
        station_id="KLGA",
        measurement_basis="finalized daily high",
        forecast_payload_sha256=hashlib.sha256(f"forecast-{index}".encode()).hexdigest(),
        observation_payload_sha256=hashlib.sha256(f"observation-{day}".encode()).hexdigest(),
    )
    return CalibrationDatasetRecord(
        sample=sample,
        forecast_contract_id=_FORECAST_CONTRACT,
        observation_contract_id=_OBSERVATION_CONTRACT,
        forecast_capture_contract_id=_CAPTURE_CONTRACT,
        forecast_capture_method=ForecastCaptureMethod.SINGLE_RUN,
        forecast_source_url="https://example.test/forecast",
        forecast_latitude=Decimal("40.7772"),
        forecast_longitude=Decimal("-73.8726"),
        forecast_bias_correction=True,
        forecast_provenance_sha256=hashlib.sha256(f"provenance-{index}".encode()).hexdigest(),
    )


def _dataset(day: date, seed: int) -> CalibrationDataset:
    records = tuple(_record(day, lead_days, seed + lead_days) for lead_days in (0, 1, 2))
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
    return CalibrationDataset(
        records=records,
        manifest=CalibrationDatasetManifest(
            forecast_contract_id=_FORECAST_CONTRACT,
            observation_contract_id=_OBSERVATION_CONTRACT,
            record_count=3,
            start_date=day,
            end_date=day,
            capture_contract_ids=(_CAPTURE_CONTRACT,),
            parity_report_sha256s=(_PARITY_SHA,),
            dataset_sha256=hashlib.sha256(jsonl.encode()).hexdigest(),
        ),
    )


class _FakeCache:
    def __init__(self) -> None:
        self.payload = b"frozen Weather Underground page"

    def get(self, **_: object) -> CachedHttpCapture:
        return CachedHttpCapture(
            requested_url=(
                "https://www.wunderground.com/history/daily/us/ny/new-york-city/KLGA/"
                "date/2026-06-02"
            ),
            final_url=(
                "https://www.wunderground.com/history/daily/us/ny/new-york-city/KLGA/"
                "date/2026-06-02"
            ),
            retrieved_at_utc=datetime(2026, 8, 13, 10, tzinfo=UTC),
            payload_sha256=hashlib.sha256(self.payload).hexdigest(),
            payload=self.payload,
        )


def test_merge_calibration_datasets_preserves_canonical_order_and_provenance() -> None:
    later = _dataset(date(2026, 6, 3), 30)
    earlier = _dataset(date(2026, 6, 1), 10)

    merged = merge_calibration_datasets((later, earlier))

    assert merged.manifest.record_count == 6
    assert merged.manifest.start_date == date(2026, 6, 1)
    assert merged.manifest.end_date == date(2026, 6, 3)
    assert merged.manifest.capture_contract_ids == (_CAPTURE_CONTRACT,)
    assert merged.manifest.parity_report_sha256s == (_PARITY_SHA,)
    assert [record.sample.market_date for record in merged.records] == [
        date(2026, 6, 1),
        date(2026, 6, 1),
        date(2026, 6, 1),
        date(2026, 6, 3),
        date(2026, 6, 3),
        date(2026, 6, 3),
    ]
    assert hashlib.sha256(merged.to_jsonl().encode()).hexdigest() == merged.manifest.dataset_sha256


def test_sparse_sweep_excludes_only_coverage_deficient_station_day(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    excluded_day = date(2026, 6, 2)

    def fake_collect(**kwargs: object) -> CalibrationDataset:
        day = cast(date, kwargs["start_date"])
        if day == excluded_day:
            raise WeatherUndergroundHistoryError(
                "Weather Underground series begins too late in the local day: 02:51:00"
            )
        return _dataset(day, day.day * 10)

    monkeypatch.setattr(calibration_sweep, "collect_calibration_dataset", fake_collect)
    cache = cast(ImmutableHttpCache, _FakeCache())
    parity_report = cast(ArchiveParityReport, object())

    dataset, report = collect_calibration_sweep(
        start_date=date(2026, 6, 1),
        end_date=date(2026, 6, 3),
        cache=cache,
        markets=(DEFAULT_MARKETS[0],),
        parity_report=parity_report,
        now_utc=datetime(2026, 8, 13, 10, tzinfo=UTC),
    )

    assert dataset.manifest.record_count == 6
    assert report.expected_record_count == 9
    assert report.collected_record_count == 6
    assert report.excluded_record_count == 3
    assert len(report.exclusions) == 1
    exclusion = report.exclusions[0]
    assert exclusion.market_date == excluded_day
    assert exclusion.city == "nyc"
    assert exclusion.station_id == "KLGA"
    assert exclusion.reason_code == "weather_underground_coverage"
    assert exclusion.reason_detail.endswith("02:51:00")
    assert exclusion.raw_payload_sha256 == hashlib.sha256(cache.payload).hexdigest()
    assert report.dataset_sha256 == dataset.manifest.dataset_sha256


def test_sparse_sweep_does_not_hide_noncoverage_weather_underground_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_collect(**_: object) -> CalibrationDataset:
        raise WeatherUndergroundHistoryError("Weather Underground station KLGA identity mismatch")

    monkeypatch.setattr(calibration_sweep, "collect_calibration_dataset", fake_collect)
    cache = cast(ImmutableHttpCache, _FakeCache())
    parity_report = cast(ArchiveParityReport, object())

    with pytest.raises(WeatherUndergroundHistoryError, match="identity mismatch"):
        collect_calibration_sweep(
            start_date=date(2026, 6, 1),
            end_date=date(2026, 6, 1),
            cache=cache,
            markets=(DEFAULT_MARKETS[0],),
            parity_report=parity_report,
            now_utc=datetime(2026, 8, 13, 10, tzinfo=UTC),
        )


def test_sweep_report_checksum_covers_exclusion_provenance() -> None:
    exclusion = CalibrationSweepExclusion(
        city="nyc",
        station_id="KLGA",
        market_date=date(2026, 6, 2),
        source_url=(
            "https://www.wunderground.com/history/daily/us/ny/new-york-city/KLGA/"
            "date/2026-06-02"
        ),
        raw_payload_sha256=hashlib.sha256(b"page").hexdigest(),
        reason_code="weather_underground_coverage",
        reason_detail="Weather Underground series begins too late in the local day: 02:51:00",
    )
    report = CalibrationSweepReport(
        requested_start_date=date(2026, 6, 1),
        requested_end_date=date(2026, 6, 2),
        cities=("nyc",),
        expected_record_count=6,
        collected_record_count=3,
        exclusions=(exclusion,),
        dataset_sha256=hashlib.sha256(b"dataset").hexdigest(),
    )
    decoded = json.loads(report.to_json())

    assert decoded["excluded_date_count"] == 1
    assert decoded["excluded_record_count"] == 3
    assert decoded["report_sha256"] == report.report_sha256
