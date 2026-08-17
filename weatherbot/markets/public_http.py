"""Read-only Polymarket HTTP acquisition for Hermes producer/runtime research."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

import requests

from weatherbot.markets import (
    ConditionId,
    GammaBinaryMarket,
    GammaMarketError,
    OrderBookError,
    OrderBookSnapshot,
    OutcomeTokenId,
    TemperatureBucket,
    TemperatureMarketPartition,
    parse_gamma_binary_market,
    parse_order_book,
    parse_temperature_bucket,
)

CLOB_HOST = "https://clob.polymarket.com"
GAMMA_HOST = "https://gamma-api.polymarket.com"


@dataclass(frozen=True, slots=True)
class ParsedTemperatureMarket:
    market: GammaBinaryMarket
    bucket: TemperatureBucket
    volume: Decimal


def fetch_temperature_event(
    city_slug: str,
    month: str,
    day: int,
    year: int,
) -> dict[str, Any] | None:
    slug = f"highest-temperature-in-{city_slug}-on-{month}-{day}-{year}"
    response = requests.get(f"{GAMMA_HOST}/events", params={"slug": slug}, timeout=(5, 8))
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, list):
        raise GammaMarketError("Gamma event response must be an array")
    if not payload:
        return None
    event = payload[0]
    if not isinstance(event, dict):
        raise GammaMarketError("Gamma event entry must be an object")
    return event


def fetch_token_order_book(
    condition_id: ConditionId,
    token_id: OutcomeTokenId,
) -> OrderBookSnapshot:
    response = requests.get(
        f"{CLOB_HOST}/book",
        params={"token_id": str(token_id)},
        timeout=(3, 6),
    )
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict):
        raise OrderBookError("CLOB order-book response must be an object")
    return parse_order_book(
        payload,
        expected_condition_id=condition_id,
        expected_token_id=token_id,
    )


def parse_api_datetime(value: object, *, label: str) -> datetime | None:
    if value in (None, ""):
        return None
    if not isinstance(value, str):
        raise GammaMarketError(f"{label} must be an ISO-8601 string")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise GammaMarketError(f"{label} must be ISO-8601") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise GammaMarketError(f"{label} must include a timezone")
    return parsed.astimezone(UTC)


def hours_to_resolution(end_date: object, *, now: datetime) -> Decimal:
    if not isinstance(end_date, str) or not end_date.strip():
        raise GammaMarketError("event endDate must be a non-blank ISO-8601 string")
    parsed = parse_api_datetime(end_date, label="event.endDate")
    assert parsed is not None
    try:
        return max(
            Decimal("0"),
            Decimal(str((parsed - now).total_seconds())) / Decimal("3600"),
        )
    except (InvalidOperation, ValueError) as exc:
        raise GammaMarketError("event resolution horizon is invalid") from exc


def parse_temperature_markets(
    event: dict[str, Any],
) -> tuple[tuple[ParsedTemperatureMarket, ...], TemperatureMarketPartition]:
    parsed: list[ParsedTemperatureMarket] = []
    raw_markets = event.get("markets", [])
    if not isinstance(raw_markets, list):
        raise GammaMarketError("event.markets must be an array")
    for raw_market in raw_markets:
        if not isinstance(raw_market, dict):
            raise GammaMarketError("event market entry must be an object")
        market = parse_gamma_binary_market(raw_market)
        bucket = parse_temperature_bucket(market.question)
        try:
            volume = Decimal(str(raw_market.get("volume", 0)))
        except (InvalidOperation, ValueError) as exc:
            raise GammaMarketError(f"market {market.identity.market_id} has invalid volume") from exc
        if not volume.is_finite() or volume < 0:
            raise GammaMarketError(f"market {market.identity.market_id} has invalid volume")
        parsed.append(ParsedTemperatureMarket(market=market, bucket=bucket, volume=volume))
    partition = TemperatureMarketPartition(tuple(item.bucket for item in parsed))
    return tuple(parsed), partition
