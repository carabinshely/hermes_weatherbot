"""Deterministic Open-Meteo Single Runs sampling for calibration.

The operational Open-Meteo forecast endpoint is continuously stitched from the newest
available model runs. Calibration instead needs one reproducible point-in-time forecast
per city/target-date/horizon. For the current U.S. market set, v1 uses the previous UTC
calendar day's 18Z ECMWF IFS 0.25° run and a 00:15 market-local decision time.
"""

from __future__ import annotations

import hashlib
import json
import math
import urllib.parse
import urllib.request
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal, InvalidOperation
from typing import cast
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from weatherbot.forecasting.calibration import CalibrationError
from weatherbot.forecasting.calibration_data import (
    ForecastCalibrationEvidence,
    ForecastCaptureMethod,
)
from weatherbot.forecasting.model import DailyHighForecast, ForecastSource

PRODUCTION_FORECAST_CONTRACT_ID = (
    "open-meteo:ecmwf_ifs025:daily-temperature_2m_max:bias-corrected:market-local-day:v1"
)
SINGLE_RUN_CAPTURE_CONTRACT_ID = (
    "open-meteo:single-runs:ecmwf_ifs025:hourly-temperature_2m:"
    "bias-corrected:market-local-day-max:v1"
)
_SINGLE_RUN_HOST = "single-runs-api.open-meteo.com"
_SINGLE_RUN_PATH = "/v1/forecast"
_MIN_SAFE_RUN_AGE = timedelta(hours=8, minutes=10)
_DEFAULT_TIMEOUT_SECONDS = 30.0
_DEFAULT_HORIZONS = (0, 1, 2)


@dataclass(frozen=True, slots=True)
class CalibrationLocation:
    city: str
    climate_region: str
    latitude: Decimal
    longitude: Decimal
    market_timezone: str

    def __post_init__(self) -> None:
        city = self.city.strip().lower()
        region = self.climate_region.strip().lower()
        if not city:
            raise CalibrationError("calibration location city must not be blank")
        if not region:
            raise CalibrationError("calibration location climate region must not be blank")
        latitude = _decimal(self.latitude, label="calibration latitude")
        longitude = _decimal(self.longitude, label="calibration longitude")
        if not Decimal("-90") <= latitude <= Decimal("90"):
            raise CalibrationError("calibration latitude is outside [-90, 90]")
        if not Decimal("-180") <= longitude <= Decimal("180"):
            raise CalibrationError("calibration longitude is outside [-180, 180]")
        try:
            timezone = ZoneInfo(self.market_timezone.strip())
        except ZoneInfoNotFoundError as exc:
            raise CalibrationError(
                f"invalid calibration market timezone: {self.market_timezone!r}"
            ) from exc
        object.__setattr__(self, "city", city)
        object.__setattr__(self, "climate_region", region)
        object.__setattr__(self, "latitude", latitude)
        object.__setattr__(self, "longitude", longitude)
        object.__setattr__(self, "market_timezone", timezone.key)


@dataclass(frozen=True, slots=True)
class CalibrationForecastSamplingPolicy:
    run_cycle_hour_utc: int = 18
    decision_local_time: time = time(hour=0, minute=15)
    min_safe_run_age: timedelta = _MIN_SAFE_RUN_AGE
    horizons: tuple[int, ...] = _DEFAULT_HORIZONS

    def __post_init__(self) -> None:
        if isinstance(self.run_cycle_hour_utc, bool):
            raise CalibrationError("run cycle hour must be an integer")
        if self.run_cycle_hour_utc not in {0, 6, 12, 18}:
            raise CalibrationError("ECMWF run cycle must be one of 00, 06, 12, or 18 UTC")
        if self.min_safe_run_age < timedelta(0):
            raise CalibrationError("minimum safe run age must not be negative")
        horizons = tuple(self.horizons)
        if not horizons:
            raise CalibrationError("calibration forecast horizons must not be empty")
        if len(horizons) != len(set(horizons)):
            raise CalibrationError("calibration forecast horizons must be unique")
        if any(isinstance(value, bool) or not 0 <= value <= 7 for value in horizons):
            raise CalibrationError("calibration forecast horizons must be integer days in [0, 7]")
        object.__setattr__(self, "horizons", tuple(sorted(horizons)))


_DEFAULT_POLICY = CalibrationForecastSamplingPolicy()


@dataclass(frozen=True, slots=True)
class OpenMeteoSingleRunCapture:
    run_initialized_at_utc: datetime
    decision_time_utc: datetime
    source_url: str
    raw_payload_sha256: str
    forecasts: tuple[ForecastCalibrationEvidence, ...]

    def __post_init__(self) -> None:
        if (
            self.run_initialized_at_utc.tzinfo is None
            or self.run_initialized_at_utc.utcoffset() is None
        ):
            raise CalibrationError("single-run initialization must be timezone-aware")
        if self.decision_time_utc.tzinfo is None or self.decision_time_utc.utcoffset() is None:
            raise CalibrationError("calibration decision time must be timezone-aware")
        digest = self.raw_payload_sha256.lower()
        if len(digest) != 64 or any(character not in "0123456789abcdef" for character in digest):
            raise CalibrationError("single-run payload hash must be SHA-256")
        if not self.forecasts:
            raise CalibrationError("single-run capture must contain forecasts")
        object.__setattr__(
            self,
            "run_initialized_at_utc",
            self.run_initialized_at_utc.astimezone(UTC),
        )
        object.__setattr__(
            self,
            "decision_time_utc",
            self.decision_time_utc.astimezone(UTC),
        )
        object.__setattr__(self, "raw_payload_sha256", digest)


def _decimal(value: object, *, label: str) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (Decimal, int, str, float)):
        raise CalibrationError(f"{label} must be decimal")
    try:
        result = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise CalibrationError(f"{label} must be decimal") from exc
    if not result.is_finite():
        raise CalibrationError(f"{label} must be finite")
    return result


def _mapping(value: object, *, label: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise CalibrationError(f"{label} must be an object")
    return cast(Mapping[str, object], value)


def _sequence(value: object, *, label: str) -> Sequence[object]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        raise CalibrationError(f"{label} must be an array")
    return cast(Sequence[object], value)


def calibration_run_for_market_day(
    market_day: date,
    *,
    policy: CalibrationForecastSamplingPolicy = _DEFAULT_POLICY,
) -> datetime:
    """Return the deterministic model run used for one U.S. market-local decision day."""

    return datetime.combine(
        market_day - timedelta(days=1),
        time(hour=policy.run_cycle_hour_utc),
        UTC,
    )


def calibration_decision_time(
    market_day: date,
    market_timezone: str,
    *,
    policy: CalibrationForecastSamplingPolicy = _DEFAULT_POLICY,
) -> datetime:
    timezone = ZoneInfo(market_timezone)
    decision = datetime.combine(
        market_day,
        policy.decision_local_time,
        timezone,
    ).astimezone(UTC)
    run = calibration_run_for_market_day(market_day, policy=policy)
    run_age = decision - run
    if run_age < policy.min_safe_run_age:
        raise CalibrationError(
            "calibration decision time is too close to model initialization for the "
            "availability policy"
        )
    return decision


def single_run_url(
    location: CalibrationLocation,
    *,
    run_initialized_at_utc: datetime,
) -> str:
    run = run_initialized_at_utc.astimezone(UTC)
    params = {
        "latitude": format(location.latitude, "f"),
        "longitude": format(location.longitude, "f"),
        "hourly": "temperature_2m",
        "temperature_unit": "fahrenheit",
        "timezone": location.market_timezone,
        "models": "ecmwf_ifs025",
        "bias_correction": "true",
        "run": run.strftime("%Y-%m-%dT%H:%M"),
    }
    return f"https://{_SINGLE_RUN_HOST}{_SINGLE_RUN_PATH}?{urllib.parse.urlencode(params)}"


def _validate_source_url(
    source_url: str,
    *,
    location: CalibrationLocation,
    run_initialized_at_utc: datetime,
) -> None:
    parsed = urllib.parse.urlparse(source_url)
    if (
        parsed.scheme != "https"
        or parsed.hostname != _SINGLE_RUN_HOST
        or parsed.path != _SINGLE_RUN_PATH
    ):
        raise CalibrationError("single-run source URL is not the canonical Open-Meteo endpoint")
    query = urllib.parse.parse_qs(parsed.query, keep_blank_values=True)
    expected = urllib.parse.parse_qs(
        urllib.parse.urlparse(
            single_run_url(location, run_initialized_at_utc=run_initialized_at_utc)
        ).query,
        keep_blank_values=True,
    )
    if query != expected:
        raise CalibrationError(
            "single-run source URL parameters differ from the calibration contract"
        )


def _parse_local_hour(value: object) -> tuple[date, str]:
    if not isinstance(value, str):
        raise CalibrationError("Open-Meteo hourly timestamp must be text")
    try:
        timestamp = datetime.fromisoformat(value)
    except ValueError as exc:
        raise CalibrationError(f"invalid Open-Meteo hourly timestamp: {value!r}") from exc
    if timestamp.tzinfo is not None:
        raise CalibrationError("Open-Meteo local hourly timestamps must not contain an offset")
    return timestamp.date(), value


def parse_single_run_daily_highs(
    raw_payload: bytes,
    *,
    source_url: str,
    location: CalibrationLocation,
    market_day: date,
    run_initialized_at_utc: datetime,
    retrieved_at_utc: datetime,
    policy: CalibrationForecastSamplingPolicy = _DEFAULT_POLICY,
) -> OpenMeteoSingleRunCapture:
    """Normalize one archived model run into D+0/D+1/D+2 local daily-high forecasts."""

    run = run_initialized_at_utc.astimezone(UTC)
    expected_run = calibration_run_for_market_day(market_day, policy=policy)
    if run != expected_run:
        raise CalibrationError(
            f"single-run initialization mismatch: expected {expected_run.isoformat()}, "
            f"got {run.isoformat()}"
        )
    decision = calibration_decision_time(
        market_day,
        location.market_timezone,
        policy=policy,
    )
    _validate_source_url(
        source_url,
        location=location,
        run_initialized_at_utc=run,
    )
    if retrieved_at_utc.tzinfo is None or retrieved_at_utc.utcoffset() is None:
        raise CalibrationError("single-run retrieval time must be timezone-aware")
    retrieved = retrieved_at_utc.astimezone(UTC)
    if retrieved < run:
        raise CalibrationError("single-run retrieval cannot predate model initialization")

    try:
        decoded: object = json.loads(raw_payload)
    except json.JSONDecodeError as exc:
        raise CalibrationError("Open-Meteo single-run response is not valid JSON") from exc
    payload = _mapping(decoded, label="Open-Meteo single-run payload")
    if payload.get("timezone") != location.market_timezone:
        raise CalibrationError(
            "Open-Meteo single-run timezone differs from the calibration contract"
        )
    units = _mapping(payload.get("hourly_units"), label="Open-Meteo hourly units")
    if units.get("temperature_2m") not in {"°F", "°Fahrenheit", "F"}:
        raise CalibrationError("Open-Meteo single-run temperature unit is not Fahrenheit")
    hourly = _mapping(payload.get("hourly"), label="Open-Meteo hourly payload")
    times = _sequence(hourly.get("time"), label="Open-Meteo hourly times")
    temperatures = _sequence(
        hourly.get("temperature_2m"),
        label="Open-Meteo hourly temperatures",
    )
    if len(times) != len(temperatures):
        raise CalibrationError(
            "Open-Meteo hourly timestamps and temperatures have different lengths"
        )

    by_date: dict[date, list[Decimal]] = defaultdict(list)
    seen_timestamps: set[str] = set()
    for timestamp_raw, temperature_raw in zip(times, temperatures, strict=True):
        local_date, timestamp_text = _parse_local_hour(timestamp_raw)
        if timestamp_text in seen_timestamps:
            raise CalibrationError("Open-Meteo single-run contains a duplicate hourly timestamp")
        seen_timestamps.add(timestamp_text)
        if temperature_raw is None:
            continue
        by_date[local_date].append(
            _decimal(
                temperature_raw,
                label="Open-Meteo hourly temperature",
            )
        )

    payload_hash = hashlib.sha256(raw_payload).hexdigest()
    timezone = ZoneInfo(location.market_timezone)
    forecasts: list[ForecastCalibrationEvidence] = []
    for horizon in policy.horizons:
        target_date = market_day + timedelta(days=horizon)
        values = by_date.get(target_date, [])
        if len(values) < 20:
            raise CalibrationError(
                "Open-Meteo single-run has insufficient local-day temperature coverage "
                f"for {target_date}: {len(values)} values"
            )
        valid_from = datetime.combine(target_date, time.min, timezone).astimezone(UTC)
        valid_until = datetime.combine(
            target_date + timedelta(days=1),
            time.min,
            timezone,
        ).astimezone(UTC)
        forecast = DailyHighForecast(
            temperature_f=max(values),
            market_date=target_date,
            market_timezone=location.market_timezone,
            source=ForecastSource.OPEN_METEO_ECMWF_IFS025,
            snapshot_issued_at_utc=run,
            valid_from_utc=valid_from,
            valid_until_utc=valid_until,
            retrieved_at_utc=retrieved,
            model_run_initialized_at_utc=run,
        )
        forecasts.append(
            ForecastCalibrationEvidence(
                city=location.city,
                climate_region=location.climate_region,
                forecast=forecast,
                forecast_as_of_utc=decision,
                lead_days=horizon,
                source_contract_id=PRODUCTION_FORECAST_CONTRACT_ID,
                capture_contract_id=SINGLE_RUN_CAPTURE_CONTRACT_ID,
                capture_method=ForecastCaptureMethod.SINGLE_RUN,
                source_url=source_url,
                latitude=location.latitude,
                longitude=location.longitude,
                bias_correction=True,
                payload_sha256=payload_hash,
            )
        )

    return OpenMeteoSingleRunCapture(
        run_initialized_at_utc=run,
        decision_time_utc=decision,
        source_url=source_url,
        raw_payload_sha256=payload_hash,
        forecasts=tuple(forecasts),
    )


def fetch_single_run_daily_highs(
    location: CalibrationLocation,
    *,
    market_day: date,
    retrieved_at_utc: datetime | None = None,
    timeout_seconds: float = _DEFAULT_TIMEOUT_SECONDS,
    policy: CalibrationForecastSamplingPolicy = _DEFAULT_POLICY,
) -> OpenMeteoSingleRunCapture:
    """Fetch the deterministic archived run for one market-local decision day."""

    if not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
        raise CalibrationError("single-run timeout must be finite and positive")
    run = calibration_run_for_market_day(market_day, policy=policy)
    url = single_run_url(location, run_initialized_at_utc=run)
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "hermes-weatherbot-calibration/1"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            raw_payload = response.read()
            final_url = response.geturl()
    except OSError as exc:
        raise CalibrationError(f"failed to fetch Open-Meteo single run: {exc}") from exc
    retrieved = datetime.now(UTC) if retrieved_at_utc is None else retrieved_at_utc
    return parse_single_run_daily_highs(
        raw_payload,
        source_url=final_url,
        location=location,
        market_day=market_day,
        run_initialized_at_utc=run,
        retrieved_at_utc=retrieved,
        policy=policy,
    )
