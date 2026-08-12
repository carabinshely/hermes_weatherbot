from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest

from weatherbot.forecasting.archive import (
    PRODUCTION_FORECAST_CONTRACT_ID,
    SINGLE_RUN_CAPTURE_CONTRACT_ID,
    CalibrationForecastSamplingPolicy,
    CalibrationLocation,
    calibration_decision_time,
    calibration_run_for_market_day,
    parse_single_run_daily_highs,
    single_run_url,
)
from weatherbot.forecasting.calibration import CalibrationError

_LOCATION = CalibrationLocation(
    city="nyc",
    climate_region="northeast",
    latitude=Decimal("40.7772"),
    longitude=Decimal("-73.8726"),
    market_timezone="America/New_York",
)
_MARKET_DAY = date(2026, 7, 1)


def _payload(*, timezone: str = "America/New_York", unit: str = "°F") -> bytes:
    times: list[str] = []
    values: list[float] = []
    for day_offset in range(5):
        day = _MARKET_DAY + timedelta(days=day_offset - 1)
        for hour in range(24):
            times.append(f"{day.isoformat()}T{hour:02d}:00")
            values.append(50.0 + day_offset + min(hour, 15) / 10)
    return json.dumps(
        {
            "latitude": 40.77,
            "longitude": -73.87,
            "timezone": timezone,
            "hourly_units": {"time": "iso8601", "temperature_2m": unit},
            "hourly": {"time": times, "temperature_2m": values},
        },
        separators=(",", ":"),
    ).encode()


def _parse(
    raw_payload: bytes | None = None,
    *,
    source_url: str | None = None,
    run_initialized_at_utc: datetime | None = None,
):
    run = calibration_run_for_market_day(_MARKET_DAY)
    return parse_single_run_daily_highs(
        _payload() if raw_payload is None else raw_payload,
        source_url=(
            single_run_url(_LOCATION, run_initialized_at_utc=run)
            if source_url is None
            else source_url
        ),
        location=_LOCATION,
        market_day=_MARKET_DAY,
        run_initialized_at_utc=run if run_initialized_at_utc is None else run_initialized_at_utc,
        retrieved_at_utc=datetime(2026, 8, 12, 12, tzinfo=UTC),
    )


def test_previous_18z_run_and_local_0015_decision_are_deterministic() -> None:
    run = calibration_run_for_market_day(_MARKET_DAY)
    decision = calibration_decision_time(_MARKET_DAY, "America/New_York")

    assert run == datetime(2026, 6, 30, 18, tzinfo=UTC)
    assert decision == datetime(2026, 7, 1, 4, 15, tzinfo=UTC)
    assert decision - run == timedelta(hours=10, minutes=15)


def test_single_run_normalizes_three_local_calendar_horizons() -> None:
    capture = _parse()

    assert capture.run_initialized_at_utc == datetime(2026, 6, 30, 18, tzinfo=UTC)
    assert capture.decision_time_utc == datetime(2026, 7, 1, 4, 15, tzinfo=UTC)
    assert len(capture.forecasts) == 3
    assert [item.lead_days for item in capture.forecasts] == [0, 1, 2]
    assert [item.forecast.market_date for item in capture.forecasts] == [
        date(2026, 7, 1),
        date(2026, 7, 2),
        date(2026, 7, 3),
    ]
    assert [item.forecast.temperature_f for item in capture.forecasts] == [
        Decimal("52.5"),
        Decimal("53.5"),
        Decimal("54.5"),
    ]
    assert all(item.forecast_as_of_utc == capture.decision_time_utc for item in capture.forecasts)
    assert all(item.source_contract_id == PRODUCTION_FORECAST_CONTRACT_ID for item in capture.forecasts)
    assert all(item.capture_contract_id == SINGLE_RUN_CAPTURE_CONTRACT_ID for item in capture.forecasts)
    assert all(item.bias_correction for item in capture.forecasts)
    assert all(item.payload_sha256 == capture.raw_payload_sha256 for item in capture.forecasts)


def test_single_run_source_url_is_exact_contract_not_just_same_host() -> None:
    run = calibration_run_for_market_day(_MARKET_DAY)
    valid = single_run_url(_LOCATION, run_initialized_at_utc=run)
    changed = valid.replace("bias_correction=true", "bias_correction=false")

    with pytest.raises(CalibrationError, match="parameters differ"):
        _parse(source_url=changed)


def test_single_run_rejects_wrong_model_run() -> None:
    wrong_run = datetime(2026, 7, 1, 0, tzinfo=UTC)

    with pytest.raises(CalibrationError, match="initialization mismatch"):
        _parse(run_initialized_at_utc=wrong_run)


def test_single_run_rejects_wrong_timezone_or_units() -> None:
    with pytest.raises(CalibrationError, match="timezone differs"):
        _parse(_payload(timezone="America/Chicago"))

    with pytest.raises(CalibrationError, match="not Fahrenheit"):
        _parse(_payload(unit="°C"))


def test_single_run_rejects_incomplete_target_day() -> None:
    payload = json.loads(_payload())
    keep = [
        index
        for index, timestamp in enumerate(payload["hourly"]["time"])
        if not timestamp.startswith("2026-07-02") or int(timestamp[11:13]) < 10
    ]
    payload["hourly"]["time"] = [payload["hourly"]["time"][index] for index in keep]
    payload["hourly"]["temperature_2m"] = [
        payload["hourly"]["temperature_2m"][index] for index in keep
    ]

    with pytest.raises(CalibrationError, match="insufficient local-day"):
        _parse(json.dumps(payload).encode())


def test_sampling_policy_rejects_decision_too_close_to_run() -> None:
    policy = CalibrationForecastSamplingPolicy(
        run_cycle_hour_utc=18,
        decision_local_time=datetime.strptime("18:30", "%H:%M").time(),
        min_safe_run_age=timedelta(hours=8),
    )

    with pytest.raises(CalibrationError, match="too close"):
        calibration_decision_time(_MARKET_DAY, "America/New_York", policy=policy)
