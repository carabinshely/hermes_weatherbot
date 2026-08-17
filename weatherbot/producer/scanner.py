"""Read-only calibrated candidate acquisition for the public Hermes producer."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

import requests

from weatherbot.forecasting import (
    CalibratedProbabilityRuntime,
    CalibrationRuntimeError,
    WeatherInputError,
    WeatherInputSnapshot,
)
from weatherbot.forecasting.calibration import CalibrationError
from weatherbot.forecasting.contracts import (
    CALIBRATION_LEAD_DAYS,
    calibration_runtime_window,
)
from weatherbot.markets import (
    BinaryOutcome,
    ConditionId,
    GammaMarketError,
    MarketCalendar,
    OrderBookError,
    OrderBookSnapshot,
    OutcomeTokenId,
    TemperatureMarketError,
    TemperatureUnit,
)
from weatherbot.markets.public_http import (
    ParsedTemperatureMarket,
    fetch_temperature_event,
    fetch_token_order_book,
    hours_to_resolution,
    parse_api_datetime,
    parse_temperature_markets,
)
from weatherbot.producer.catalog import LOCATIONS, MONTHS
from weatherbot.producer.config import ProducerPolicy
from weatherbot.producer.model import CalibratedMarketCandidate
from weatherbot.producer.sources import fetch_weather_snapshots
from weatherbot.quoting import MarketEventSnapshot

EventFetcher = Callable[[str, str, int, int], dict[str, Any] | None]
BookFetcher = Callable[[ConditionId, OutcomeTokenId], OrderBookSnapshot]


def _event_id(event: dict[str, Any], market_date: str) -> str:
    value = event.get("id") or event.get("slug") or market_date
    normalized = str(value).strip()
    if not normalized:
        raise GammaMarketError("event id must not be blank")
    return normalized


def collect_calibrated_candidates(
    *,
    calibration_runtime: CalibratedProbabilityRuntime,
    policy: ProducerPolicy,
    now: datetime | None = None,
    weather_fetcher: Callable[..., Mapping[date, WeatherInputSnapshot]] = fetch_weather_snapshots,
    event_fetcher: EventFetcher = fetch_temperature_event,
    book_fetcher: BookFetcher = fetch_token_order_book,
) -> tuple[list[CalibratedMarketCandidate], list[str]]:
    """Collect candidates using weather/market reads only; never consult PAPER/execution state."""
    current = (now or datetime.now(UTC)).astimezone(UTC)
    candidates: list[CalibratedMarketCandidate] = []
    errors: list[str] = []

    for city_slug, location in LOCATIONS.items():
        calendar = MarketCalendar(location.market_timezone)
        dates = tuple(calendar.candidate_dates(current, count=len(CALIBRATION_LEAD_DAYS)))
        if not dates:
            continue
        try:
            decision_start, decision_end = calibration_runtime_window(
                target_date=dates[0],
                market_timezone=location.market_timezone,
                lead_days=CALIBRATION_LEAD_DAYS[0],
            )
        except ValueError as exc:
            errors.append(f"{location.name}: invalid calibration decision window: {exc}")
            continue
        if not decision_start <= current < decision_end:
            continue

        try:
            snapshots = dict(weather_fetcher(city_slug, dates, now=current))
        except (WeatherInputError, requests.RequestException, ValueError) as exc:
            errors.append(f"{location.name}: forecast failed: {exc}")
            continue

        for lead_days, market_date in zip(CALIBRATION_LEAD_DAYS, dates, strict=True):
            horizon = f"D+{lead_days}"
            try:
                event = event_fetcher(
                    city_slug,
                    MONTHS[market_date.month - 1],
                    market_date.day,
                    market_date.year,
                )
                if event is None:
                    continue
                retrieved_at = datetime.now(UTC)
                event_snapshot = MarketEventSnapshot(
                    event_id=_event_id(event, market_date.isoformat()),
                    retrieved_at_utc=retrieved_at,
                    source_updated_at_utc=parse_api_datetime(
                        event.get("updatedAt"), label="event.updatedAt"
                    ),
                )
                hours = hours_to_resolution(event.get("endDate"), now=current)
            except (GammaMarketError, ValueError, requests.RequestException) as exc:
                errors.append(f"{location.name} {horizon}: market lookup failed: {exc}")
                continue
            if hours < policy.min_hours or hours > policy.max_hours:
                continue

            weather = snapshots.get(market_date)
            if weather is None:
                continue
            forecast_temp = weather.signal_temperature_f
            if forecast_temp < Decimal("-40") or forecast_temp > Decimal("130"):
                errors.append(f"{location.name} {horizon}: invalid forecast temperature")
                continue

            try:
                parsed_markets, partition = parse_temperature_markets(event)
                if partition.unit is not TemperatureUnit.FAHRENHEIT:
                    raise TemperatureMarketError("US producer expects Fahrenheit markets")
                target_bucket = partition.bucket_for_forecast(float(forecast_temp))
                matches = [item for item in parsed_markets if item.bucket.key == target_bucket.key]
                if len(matches) != 1:
                    raise TemperatureMarketError(
                        f"forecast bucket {target_bucket.label} maps to {len(matches)} markets"
                    )
                selected: ParsedTemperatureMarket = matches[0]
                if selected.volume < policy.min_volume:
                    continue
                selection = selected.market.select(BinaryOutcome.YES)
                book = book_fetcher(selection.condition_id, selection.token_id)
            except (
                GammaMarketError,
                TemperatureMarketError,
                OrderBookError,
                requests.RequestException,
                ValueError,
            ) as exc:
                errors.append(f"{location.name} {horizon}: market rejected: {exc}")
                continue

            try:
                calibrated = calibration_runtime.probability(
                    city=city_slug,
                    climate_region=location.climate_region,
                    lead_days=lead_days,
                    weather=weather,
                    bucket=target_bucket,
                )
            except (CalibrationError, CalibrationRuntimeError) as exc:
                errors.append(f"{location.name} {horizon}: calibration rejected candidate: {exc}")
                continue

            candidates.append(
                CalibratedMarketCandidate(
                    city_slug=city_slug,
                    city_name=location.name,
                    horizon=horizon,
                    market_date=market_date,
                    market_timezone=location.market_timezone,
                    event_id=event_snapshot.event_id,
                    market_id=str(selection.market_id),
                    condition_id=str(selection.condition_id),
                    outcome=selection.outcome.value,
                    token_id=str(selection.token_id),
                    question=selected.market.question,
                    bucket=target_bucket,
                    volume=selected.volume,
                    weather=weather,
                    event=event_snapshot,
                    decision_book=book,
                    calibrated=calibrated,
                )
            )

    return candidates, errors
