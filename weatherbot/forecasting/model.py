"""Point-in-time weather inputs used to generate trading signals."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


class WeatherInputError(ValueError):
    """Raised when forecast or observation provenance is invalid."""


def _decimal(value: Decimal | int | str | float, *, label: str) -> Decimal:
    if isinstance(value, bool):
        raise WeatherInputError(f"{label} must be decimal")
    try:
        result = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise WeatherInputError(f"{label} must be decimal") from exc
    if not result.is_finite():
        raise WeatherInputError(f"{label} must be finite")
    return result


def _utc(value: datetime, *, label: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise WeatherInputError(f"{label} must be timezone-aware")
    return value.astimezone(UTC)


def _timezone(value: str) -> ZoneInfo:
    normalized = value.strip()
    if not normalized:
        raise WeatherInputError("market timezone must not be blank")
    try:
        return ZoneInfo(normalized)
    except ZoneInfoNotFoundError as exc:
        raise WeatherInputError(f"unknown IANA timezone: {normalized!r}") from exc


def _age_seconds(*, reference: datetime, datum_time: datetime, label: str) -> float:
    reference_utc = _utc(reference, label="age reference")
    datum_utc = _utc(datum_time, label=label)
    age = reference_utc - datum_utc
    if age < -timedelta(seconds=5):
        raise WeatherInputError(f"{label} is unexpectedly in the future")
    return max(0.0, age.total_seconds())


class ForecastSource(StrEnum):
    OPEN_METEO_ECMWF_IFS025 = "open_meteo_ecmwf_ifs025"


class ObservationSource(StrEnum):
    AVIATION_WEATHER_METAR = "aviation_weather_metar"


@dataclass(frozen=True, slots=True)
class DailyHighForecast:
    """One model forecast for the maximum temperature of a local calendar day."""

    temperature_f: Decimal
    market_date: date
    market_timezone: str
    source: ForecastSource
    snapshot_issued_at_utc: datetime
    valid_from_utc: datetime
    valid_until_utc: datetime
    retrieved_at_utc: datetime
    model_run_initialized_at_utc: datetime | None = None

    def __post_init__(self) -> None:
        temperature = _decimal(self.temperature_f, label="forecast temperature")
        timezone = _timezone(self.market_timezone)
        issued = _utc(self.snapshot_issued_at_utc, label="forecast issue time")
        valid_from = _utc(self.valid_from_utc, label="forecast valid-from time")
        valid_until = _utc(self.valid_until_utc, label="forecast valid-until time")
        retrieved = _utc(self.retrieved_at_utc, label="forecast retrieval time")
        model_run = self.model_run_initialized_at_utc
        if model_run is not None:
            model_run = _utc(model_run, label="forecast model-run initialization time")
            if model_run > retrieved + timedelta(seconds=5):
                raise WeatherInputError("forecast model run is later than retrieval time")
        if issued > retrieved + timedelta(seconds=5):
            raise WeatherInputError("forecast issue time is later than retrieval time")
        if valid_from >= valid_until:
            raise WeatherInputError("forecast valid interval must be positive")

        expected_from = datetime.combine(self.market_date, time.min, timezone).astimezone(UTC)
        expected_until = datetime.combine(
            self.market_date + timedelta(days=1), time.min, timezone
        ).astimezone(UTC)
        if valid_from != expected_from or valid_until != expected_until:
            raise WeatherInputError(
                "daily-high forecast validity must cover exactly one market-local day"
            )

        object.__setattr__(self, "temperature_f", temperature)
        object.__setattr__(self, "market_timezone", timezone.key)
        object.__setattr__(self, "snapshot_issued_at_utc", issued)
        object.__setattr__(self, "valid_from_utc", valid_from)
        object.__setattr__(self, "valid_until_utc", valid_until)
        object.__setattr__(self, "retrieved_at_utc", retrieved)
        object.__setattr__(self, "model_run_initialized_at_utc", model_run)

    def age_seconds_at(self, reference: datetime) -> float:
        return _age_seconds(
            reference=reference,
            datum_time=self.snapshot_issued_at_utc,
            label="forecast issue time",
        )


@dataclass(frozen=True, slots=True)
class TemperatureObservation:
    """One instantaneous station observation, never a daily-high forecast."""

    temperature_f: Decimal
    station_id: str
    market_timezone: str
    source: ObservationSource
    issued_at_utc: datetime
    valid_at_utc: datetime
    retrieved_at_utc: datetime
    provider_received_at_utc: datetime | None = None

    def __post_init__(self) -> None:
        temperature = _decimal(self.temperature_f, label="observation temperature")
        station = self.station_id.strip().upper()
        if not station:
            raise WeatherInputError("observation station must not be blank")
        timezone = _timezone(self.market_timezone)
        issued = _utc(self.issued_at_utc, label="observation issue time")
        valid = _utc(self.valid_at_utc, label="observation valid time")
        retrieved = _utc(self.retrieved_at_utc, label="observation retrieval time")
        provider_received = self.provider_received_at_utc
        if provider_received is not None:
            provider_received = _utc(
                provider_received,
                label="observation provider-received time",
            )
        for label, value in (
            ("observation issue time", issued),
            ("observation valid time", valid),
            ("observation provider-received time", provider_received),
        ):
            if value is not None and value > retrieved + timedelta(minutes=5):
                raise WeatherInputError(f"{label} is later than retrieval time")

        object.__setattr__(self, "temperature_f", temperature)
        object.__setattr__(self, "station_id", station)
        object.__setattr__(self, "market_timezone", timezone.key)
        object.__setattr__(self, "issued_at_utc", issued)
        object.__setattr__(self, "valid_at_utc", valid)
        object.__setattr__(self, "retrieved_at_utc", retrieved)
        object.__setattr__(self, "provider_received_at_utc", provider_received)

    @property
    def market_date(self) -> date:
        return self.valid_at_utc.astimezone(_timezone(self.market_timezone)).date()

    def age_seconds_at(self, reference: datetime) -> float:
        return _age_seconds(
            reference=reference,
            datum_time=self.valid_at_utc,
            label="observation valid time",
        )


WeatherMetadataValue = str | float | None


@dataclass(frozen=True, slots=True)
class WeatherInputSnapshot:
    """Forecast plus optional observation available at one signal decision time."""

    forecast: DailyHighForecast
    observation: TemperatureObservation | None
    assembled_at_utc: datetime

    def __post_init__(self) -> None:
        assembled = _utc(self.assembled_at_utc, label="weather snapshot assembly time")
        if assembled + timedelta(seconds=5) < self.forecast.retrieved_at_utc:
            raise WeatherInputError("weather snapshot predates its forecast retrieval")
        observation = self.observation
        if observation is not None:
            if observation.market_timezone != self.forecast.market_timezone:
                raise WeatherInputError("forecast and observation timezones do not match")
            if observation.market_date != self.forecast.market_date:
                raise WeatherInputError("observation local date does not match forecast market date")
            if assembled + timedelta(seconds=5) < observation.retrieved_at_utc:
                raise WeatherInputError("weather snapshot predates its observation retrieval")
        object.__setattr__(self, "assembled_at_utc", assembled)

    @property
    def signal_temperature_f(self) -> Decimal:
        """The daily-high signal input; an observation can never replace this value."""
        return self.forecast.temperature_f

    def signal_metadata(self, *, generated_at_utc: datetime) -> dict[str, WeatherMetadataValue]:
        generated = _utc(generated_at_utc, label="signal generation time")
        if generated + timedelta(seconds=5) < self.assembled_at_utc:
            raise WeatherInputError("signal generation predates weather snapshot assembly")
        observation = self.observation
        return {
            "forecast_temperature_f": float(self.forecast.temperature_f),
            "forecast_source": self.forecast.source.value,
            "forecast_snapshot_issued_at_utc": self.forecast.snapshot_issued_at_utc.isoformat(),
            "forecast_model_run_initialized_at_utc": (
                None
                if self.forecast.model_run_initialized_at_utc is None
                else self.forecast.model_run_initialized_at_utc.isoformat()
            ),
            "forecast_valid_from_utc": self.forecast.valid_from_utc.isoformat(),
            "forecast_valid_until_utc": self.forecast.valid_until_utc.isoformat(),
            "forecast_retrieved_at_utc": self.forecast.retrieved_at_utc.isoformat(),
            "forecast_age_seconds": self.forecast.age_seconds_at(generated),
            "observation_temperature_f": (
                None if observation is None else float(observation.temperature_f)
            ),
            "observation_source": None if observation is None else observation.source.value,
            "observation_station": None if observation is None else observation.station_id,
            "observation_issued_at_utc": (
                None if observation is None else observation.issued_at_utc.isoformat()
            ),
            "observation_valid_at_utc": (
                None if observation is None else observation.valid_at_utc.isoformat()
            ),
            "observation_provider_received_at_utc": (
                None
                if observation is None or observation.provider_received_at_utc is None
                else observation.provider_received_at_utc.isoformat()
            ),
            "observation_retrieved_at_utc": (
                None if observation is None else observation.retrieved_at_utc.isoformat()
            ),
            "observation_age_seconds": (
                None if observation is None else observation.age_seconds_at(generated)
            ),
        }
