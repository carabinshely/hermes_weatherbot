"""Timezone-qualified market calendars and forecast observations."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from weatherbot.markets.temperature import TemperatureUnit


class MarketCalendarError(ValueError):
    """Raised when market dates or timestamps are missing timezone context."""


def require_aware(value: datetime, *, label: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise MarketCalendarError(f"{label} must be timezone-aware")
    return value


@dataclass(frozen=True, slots=True)
class MarketCalendar:
    timezone_name: str

    def __post_init__(self) -> None:
        normalized = self.timezone_name.strip()
        if not normalized:
            raise MarketCalendarError("timezone name must not be blank")
        try:
            ZoneInfo(normalized)
        except ZoneInfoNotFoundError as exc:
            raise MarketCalendarError(f"unknown IANA timezone: {normalized!r}") from exc
        object.__setattr__(self, "timezone_name", normalized)

    @property
    def timezone(self) -> ZoneInfo:
        return ZoneInfo(self.timezone_name)

    def local_datetime(self, instant: datetime) -> datetime:
        return require_aware(instant, label="instant").astimezone(self.timezone)

    def local_date(self, instant: datetime) -> date:
        return self.local_datetime(instant).date()

    def candidate_dates(self, instant: datetime, *, count: int = 4) -> tuple[date, ...]:
        if count <= 0:
            raise MarketCalendarError("candidate date count must be positive")
        today = self.local_date(instant)
        return tuple(today + timedelta(days=offset) for offset in range(count))

    def parse_market_date(self, value: str) -> date:
        try:
            return date.fromisoformat(value)
        except ValueError as exc:
            raise MarketCalendarError(f"market date is not ISO-8601: {value!r}") from exc


@dataclass(frozen=True, slots=True)
class ForecastObservation:
    market_date: date
    market_timezone: str
    retrieved_at_utc: datetime
    source: str
    value: Decimal
    unit: TemperatureUnit
    source_timestamp: datetime | None = None

    def __post_init__(self) -> None:
        source = self.source.strip()
        if not source:
            raise MarketCalendarError("forecast source must not be blank")
        calendar = MarketCalendar(self.market_timezone)
        retrieved = require_aware(self.retrieved_at_utc, label="retrieved_at_utc")
        if retrieved.utcoffset() != timedelta(0):
            retrieved = retrieved.astimezone(ZoneInfo("UTC"))
        if self.source_timestamp is not None:
            source_timestamp = require_aware(
                self.source_timestamp,
                label="source_timestamp",
            )
            if calendar.local_date(source_timestamp) != self.market_date:
                raise MarketCalendarError(
                    "source timestamp local date does not match qualified market date"
                )
        object.__setattr__(self, "source", source)
        object.__setattr__(self, "retrieved_at_utc", retrieved)
        object.__setattr__(self, "value", Decimal(str(self.value)))

    @property
    def market_date_text(self) -> str:
        return self.market_date.isoformat()


def qualify_forecast_date(
    *,
    market_date: str,
    market_timezone: str,
    retrieved_at_utc: datetime,
    source: str,
    value: Decimal | int | str | float,
    unit: TemperatureUnit,
    source_timestamp: datetime | None = None,
) -> ForecastObservation:
    calendar = MarketCalendar(market_timezone)
    return ForecastObservation(
        market_date=calendar.parse_market_date(market_date),
        market_timezone=calendar.timezone_name,
        retrieved_at_utc=retrieved_at_utc,
        source=source,
        value=Decimal(str(value)),
        unit=unit,
        source_timestamp=source_timestamp,
    )


def index_forecasts(
    observations: tuple[ForecastObservation, ...],
    *,
    market_timezone: str,
) -> dict[date, ForecastObservation]:
    calendar = MarketCalendar(market_timezone)
    indexed: dict[date, ForecastObservation] = {}
    for observation in observations:
        if observation.market_timezone != calendar.timezone_name:
            raise MarketCalendarError(
                "forecast observations from different market timezones cannot be joined"
            )
        if observation.market_date in indexed:
            raise MarketCalendarError(
                f"duplicate forecast for market date {observation.market_date}"
            )
        indexed[observation.market_date] = observation
    return indexed
