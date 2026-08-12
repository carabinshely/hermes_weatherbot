from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

from weatherbot.forecasting.calibration import CalibrationError
from weatherbot.forecasting.calibration_data import (
    ArchiveParityPolicy,
    ForecastCalibrationEvidence,
    ForecastCaptureMethod,
    compare_archive_parity,
)
from weatherbot.forecasting.model import DailyHighForecast, ForecastSource

_EFFECTIVE_CONTRACT = "open-meteo:ecmwf_ifs025:daily-high:v1"
_ARCHIVE_CONTRACT = "open-meteo:single-runs:ecmwf_ifs025:hourly-local-max:v1"
_POLICY = ArchiveParityPolicy(
    min_pairs=2,
    min_reference_coverage=1.0,
    max_mae_f=0.5,
    max_abs_error_f=0.5,
)


def _evidence(
    market_date: date,
    *,
    capture_contract_id: str,
    capture_method: ForecastCaptureMethod,
    latitude: str = "40.7772",
    longitude: str = "-73.8726",
    market_timezone: str = "America/New_York",
    bias_correction: bool = True,
) -> ForecastCalibrationEvidence:
    timezone = ZoneInfo(market_timezone)
    as_of = datetime.combine(market_date - timedelta(days=1), time(hour=12), timezone).astimezone(
        UTC
    )
    valid_from = datetime.combine(market_date, time.min, timezone).astimezone(UTC)
    valid_until = datetime.combine(market_date + timedelta(days=1), time.min, timezone).astimezone(
        UTC
    )
    forecast = DailyHighForecast(
        temperature_f=Decimal("80"),
        market_date=market_date,
        market_timezone=market_timezone,
        source=ForecastSource.OPEN_METEO_ECMWF_IFS025,
        snapshot_issued_at_utc=as_of,
        valid_from_utc=valid_from,
        valid_until_utc=valid_until,
        retrieved_at_utc=as_of if capture_method is ForecastCaptureMethod.PRODUCTION else datetime(
            2026, 8, 12, tzinfo=UTC
        ),
        model_run_initialized_at_utc=as_of - timedelta(hours=6),
    )
    return ForecastCalibrationEvidence(
        city="nyc",
        climate_region="northeast",
        forecast=forecast,
        forecast_as_of_utc=as_of,
        lead_days=1,
        source_contract_id=_EFFECTIVE_CONTRACT,
        capture_contract_id=capture_contract_id,
        capture_method=capture_method,
        source_url=(
            "https://api.open-meteo.com/v1/forecast"
            if capture_method is ForecastCaptureMethod.PRODUCTION
            else "https://single-runs-api.open-meteo.com/v1/forecast"
        ),
        latitude=Decimal(latitude),
        longitude=Decimal(longitude),
        bias_correction=bias_correction,
        payload_sha256=("a" if capture_method is ForecastCaptureMethod.PRODUCTION else "b") * 64,
    )


def _reference_and_candidate() -> tuple[
    tuple[ForecastCalibrationEvidence, ...], tuple[ForecastCalibrationEvidence, ...]
]:
    dates = (date(2026, 7, 1), date(2026, 7, 2))
    reference = tuple(
        _evidence(
            day,
            capture_contract_id=_EFFECTIVE_CONTRACT,
            capture_method=ForecastCaptureMethod.PRODUCTION,
        )
        for day in dates
    )
    candidate = tuple(
        _evidence(
            day,
            capture_contract_id=_ARCHIVE_CONTRACT,
            capture_method=ForecastCaptureMethod.SINGLE_RUN,
        )
        for day in dates
    )
    return reference, candidate


def test_parity_accepts_same_forecast_environment_with_distinct_capture_method() -> None:
    reference, candidate = _reference_and_candidate()

    report = compare_archive_parity(reference, candidate, policy=_POLICY)

    assert report.compatible


@pytest.mark.parametrize(
    ("field", "replacement", "message"),
    (
        ("latitude", "40.8772", "coordinates"),
        ("longitude", "-73.9726", "coordinates"),
        ("market_timezone", "America/Chicago", "timezone"),
        ("bias_correction", False, "bias correction"),
    ),
)
def test_parity_rejects_environment_mismatch(
    field: str,
    replacement: str | bool,
    message: str,
) -> None:
    reference, candidate = _reference_and_candidate()
    first = candidate[0]
    kwargs: dict[str, str | bool] = {field: replacement}
    mismatched = _evidence(
        first.forecast.market_date,
        capture_contract_id=_ARCHIVE_CONTRACT,
        capture_method=ForecastCaptureMethod.SINGLE_RUN,
        **kwargs,
    )
    candidate = (mismatched, candidate[1])

    with pytest.raises(CalibrationError, match=message):
        compare_archive_parity(reference, candidate, policy=_POLICY)


def test_parity_reference_must_be_a_direct_production_capture() -> None:
    reference, candidate = _reference_and_candidate()
    invalid_reference = tuple(
        _evidence(
            item.forecast.market_date,
            capture_contract_id=_EFFECTIVE_CONTRACT,
            capture_method=ForecastCaptureMethod.IMPORTED,
        )
        for item in reference
    )

    with pytest.raises(CalibrationError, match="production capture"):
        compare_archive_parity(invalid_reference, candidate, policy=_POLICY)
