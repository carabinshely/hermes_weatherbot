"""Strict parsers for public daily forecasts and instantaneous METAR observations."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal, InvalidOperation
from types import MappingProxyType
from typing import cast
from zoneinfo import ZoneInfo

from weatherbot.forecasting.model import (
    DailyHighForecast,
    ForecastSource,
    ObservationSource,
    TemperatureObservation,
    WeatherInputError,
)


def _mapping(value: object, *, label: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise WeatherInputError(f"{label} must be an object")
    return cast(Mapping[str, object], value)


def _sequence(value: object, *, label: str) -> Sequence[object]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        raise WeatherInputError(f"{label} must be an array")
    return cast(Sequence[object], value)


def _text(value: object, *, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise WeatherInputError(f"{label} must be a non-blank string")
    return value.strip()


def _decimal(value: object, *, label: str) -> Decimal:
    if isinstance(value, bool):
        raise WeatherInputError(f"{label} must be decimal")
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise WeatherInputError(f"{label} must be decimal") from exc
    if not result.is_finite():
        raise WeatherInputError(f"{label} must be finite")
    return result


def _aware_utc(value: datetime, *, label: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise WeatherInputError(f"{label} must be timezone-aware")
    return value.astimezone(UTC)


def _timestamp(value: object, *, label: str) -> datetime:
    if isinstance(value, bool) or value is None:
        raise WeatherInputError(f"{label} must be a timestamp")
    if isinstance(value, (int, float, Decimal)):
        try:
            return datetime.fromtimestamp(float(value), tz=UTC)
        except (OverflowError, OSError, ValueError) as exc:
            raise WeatherInputError(f"{label} is outside the timestamp range") from exc
    text = _text(value, label=label)
    try:
        numeric = Decimal(text)
    except InvalidOperation:
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError as exc:
            raise WeatherInputError(f"{label} must be ISO-8601 or Unix seconds") from exc
        return _aware_utc(parsed, label=label)
    try:
        return datetime.fromtimestamp(float(numeric), tz=UTC)
    except (OverflowError, OSError, ValueError) as exc:
        raise WeatherInputError(f"{label} is outside the timestamp range") from exc


def _optional_timestamp(value: object, *, label: str) -> datetime | None:
    if value in (None, ""):
        return None
    return _timestamp(value, label=label)


def parse_open_meteo_daily_highs(
    payload: Mapping[str, object],
    *,
    requested_dates: Sequence[date],
    market_timezone: str,
    retrieved_at_utc: datetime,
    model_run_initialized_at_utc: datetime | None = None,
) -> Mapping[date, DailyHighForecast]:
    """Parse daily maximum temperatures without treating current conditions as forecasts."""
    retrieved = _aware_utc(retrieved_at_utc, label="forecast retrieval time")
    timezone = ZoneInfo(market_timezone)
    response_timezone = payload.get("timezone")
    if response_timezone not in (None, market_timezone):
        raise WeatherInputError(
            f"Open-Meteo timezone {response_timezone!r} does not match {market_timezone!r}"
        )

    daily = _mapping(payload.get("daily"), label="daily")
    raw_dates = _sequence(daily.get("time"), label="daily.time")
    raw_highs = _sequence(
        daily.get("temperature_2m_max"),
        label="daily.temperature_2m_max",
    )
    if len(raw_dates) != len(raw_highs):
        raise WeatherInputError("daily time and maximum-temperature lengths do not match")

    units_value = payload.get("daily_units")
    if units_value is not None:
        units = _mapping(units_value, label="daily_units")
        temperature_unit = units.get("temperature_2m_max")
        if temperature_unit not in {"°F", "F", "fahrenheit"}:
            raise WeatherInputError("daily maximum temperatures must be Fahrenheit")

    requested = set(requested_dates)
    forecasts: dict[date, DailyHighForecast] = {}
    for index, (raw_date, raw_high) in enumerate(zip(raw_dates, raw_highs, strict=True)):
        try:
            valid_date = date.fromisoformat(_text(raw_date, label=f"daily.time[{index}]"))
        except ValueError as exc:
            raise WeatherInputError(f"daily.time[{index}] must be an ISO date") from exc
        if valid_date not in requested:
            continue
        if raw_high is None:
            continue
        if valid_date in forecasts:
            raise WeatherInputError(f"duplicate daily forecast for {valid_date.isoformat()}")
        valid_from = datetime.combine(valid_date, time.min, timezone).astimezone(UTC)
        valid_until = datetime.combine(
            valid_date + timedelta(days=1), time.min, timezone
        ).astimezone(UTC)
        forecasts[valid_date] = DailyHighForecast(
            temperature_f=_decimal(
                raw_high,
                label=f"daily.temperature_2m_max[{index}]",
            ),
            market_date=valid_date,
            market_timezone=market_timezone,
            source=ForecastSource.OPEN_METEO_ECMWF_IFS025,
            snapshot_issued_at_utc=retrieved,
            valid_from_utc=valid_from,
            valid_until_utc=valid_until,
            retrieved_at_utc=retrieved,
            model_run_initialized_at_utc=model_run_initialized_at_utc,
        )

    return MappingProxyType(forecasts)


def parse_aviation_weather_metar(
    payload: object,
    *,
    station_id: str,
    market_timezone: str,
    retrieved_at_utc: datetime,
) -> TemperatureObservation | None:
    """Parse the latest station METAR as an instantaneous observation."""
    retrieved = _aware_utc(retrieved_at_utc, label="observation retrieval time")
    records = _sequence(payload, label="METAR response")
    expected_station = station_id.strip().upper()
    if not expected_station:
        raise WeatherInputError("station_id must not be blank")
    if not records:
        return None

    candidates: list[TemperatureObservation] = []
    for index, raw_record in enumerate(records):
        record = _mapping(raw_record, label=f"METAR[{index}]")
        actual_station = _text(record.get("icaoId"), label=f"METAR[{index}].icaoId").upper()
        if actual_station != expected_station:
            continue
        temperature_c = _decimal(record.get("temp"), label=f"METAR[{index}].temp")
        valid = _timestamp(record.get("obsTime"), label=f"METAR[{index}].obsTime")
        issued = (
            _optional_timestamp(
                record.get("reportTime"),
                label=f"METAR[{index}].reportTime",
            )
            or valid
        )
        provider_received = _optional_timestamp(
            record.get("receiptTime"),
            label=f"METAR[{index}].receiptTime",
        )
        candidates.append(
            TemperatureObservation(
                temperature_f=temperature_c * Decimal("9") / Decimal("5") + Decimal("32"),
                station_id=actual_station,
                market_timezone=market_timezone,
                source=ObservationSource.AVIATION_WEATHER_METAR,
                issued_at_utc=issued,
                valid_at_utc=valid,
                provider_received_at_utc=provider_received,
                retrieved_at_utc=retrieved,
            )
        )

    if not candidates:
        raise WeatherInputError(f"METAR response contains no record for {expected_station}")
    return max(candidates, key=lambda item: item.valid_at_utc)
