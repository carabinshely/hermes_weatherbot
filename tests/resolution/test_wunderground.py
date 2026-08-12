from __future__ import annotations

import json
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

from weatherbot.domain import MarketId
from weatherbot.resolution.wunderground import (
    WeatherUndergroundCoveragePolicy,
    WeatherUndergroundHistoryError,
    parse_wunderground_daily_history_html,
)

_SOURCE_URL = (
    "https://www.wunderground.com/history/daily/us/ny/new-york-city/KLGA/date/2026-7-18"
)
_MARKET_DATE = date(2026, 7, 18)
_TIMEZONE = "America/New_York"


def _observations(
    *,
    count: int = 24,
    first_hour: int = 0,
    fractional_high: bool = False,
) -> list[dict[str, object]]:
    timezone = ZoneInfo(_TIMEZONE)
    values: list[dict[str, object]] = []
    for index in range(count):
        local = datetime.combine(
            _MARKET_DATE,
            time(hour=first_hour, minute=51),
            timezone,
        ) + timedelta(hours=index)
        temperature: int | float = 48 + min(index, 13)
        if index > 13:
            temperature = 61 - min(index - 13, 8)
        if fractional_high and index == 13:
            temperature = 61.5
        values.append(
            {
                "ts": int(local.astimezone(UTC).timestamp() * 1000),
                "temp": temperature,
                "dewPt": 40,
                "hum": 60,
            }
        )
    return values


def _html(
    observations: list[dict[str, object]],
    *,
    station: str = "KLGA",
    market_date_text: str = "2026-7-18",
    timezone: str = _TIMEZONE,
    volatile_marker: str = "first",
    duplicate_series: bool = False,
) -> bytes:
    series = json.dumps(observations, separators=(",", ":"))
    duplicate = f'<script type="application/json">{series}</script>' if duplicate_series else ""
    return f"""
    <!doctype html>
    <html>
      <head>
        <script type="application/json">{{"volatile":"{volatile_marker}"}}</script>
      </head>
      <body>
        <airport-body
          data-mode="daily"
          data-date="{market_date_text}"
          data-time-zone="{timezone}"
          data-location-id="{station}:9:US"
          data-icao-code="{station}"
          data-country-code="US"
        ></airport-body>
        <script type="application/json">{series}</script>
        {duplicate}
      </body>
    </html>
    """.encode()


def _parse(raw_html: bytes, **kwargs: object):
    parameters = {
        "source_url": _SOURCE_URL,
        "retrieved_at_utc": datetime(2026, 7, 20, 12, tzinfo=UTC),
        "market_id": MarketId("wu-klga-2026-07-18"),
        "station_id": "KLGA",
        "market_date": _MARKET_DATE,
        "market_timezone": _TIMEZONE,
    }
    parameters.update(kwargs)
    return parse_wunderground_daily_history_html(raw_html, **parameters)


def test_public_history_page_becomes_final_authoritative_evidence() -> None:
    capture = _parse(_html(_observations()))

    assert capture.evidence.station_id == "KLGA"
    assert capture.evidence.market_date == _MARKET_DATE
    assert capture.evidence.market_timezone == _TIMEZONE
    assert capture.evidence.temperature == Decimal("61")
    assert capture.evidence.unit == "F"
    assert capture.evidence.learning_eligible
    assert capture.observation_count == 24
    assert capture.evidence.payload_hash == capture.normalized_payload_sha256
    assert capture.raw_page_sha256 != capture.normalized_payload_sha256
    assert capture.high_observation_utc.astimezone(ZoneInfo(_TIMEZONE)).hour == 13


def test_normalized_weather_hash_ignores_unrelated_page_churn() -> None:
    observations = _observations()
    first = _parse(_html(observations, volatile_marker="ad-config-a"))
    second = _parse(_html(observations, volatile_marker="ad-config-b"))

    assert first.raw_page_sha256 != second.raw_page_sha256
    assert first.normalized_payload_sha256 == second.normalized_payload_sha256
    assert first.evidence.source_revision == second.evidence.source_revision


@pytest.mark.parametrize(
    ("html_kwargs", "message"),
    (
        ({"station": "KJFK"}, "station KLGA"),
        ({"market_date_text": "2026-7-17"}, "market date mismatch"),
        ({"timezone": "America/Chicago"}, "timezone mismatch"),
    ),
)
def test_source_identity_mismatch_fails_closed(
    html_kwargs: dict[str, str],
    message: str,
) -> None:
    with pytest.raises(WeatherUndergroundHistoryError, match=message):
        _parse(_html(_observations(), **html_kwargs))


def test_current_market_day_is_not_treated_as_final_history() -> None:
    with pytest.raises(WeatherUndergroundHistoryError, match="not final"):
        _parse(
            _html(_observations()),
            retrieved_at_utc=datetime(2026, 7, 18, 12, tzinfo=UTC),
        )


def test_incomplete_station_day_is_rejected() -> None:
    with pytest.raises(WeatherUndergroundHistoryError, match="insufficient"):
        _parse(
            _html(_observations(count=10)),
            coverage_policy=WeatherUndergroundCoveragePolicy(min_observations=18),
        )


def test_series_that_begins_too_late_is_rejected_even_with_enough_rows() -> None:
    with pytest.raises(WeatherUndergroundHistoryError, match="begins too late"):
        _parse(
            _html(_observations(count=21, first_hour=3)),
            coverage_policy=WeatherUndergroundCoveragePolicy(min_observations=18),
        )


def test_fractional_final_high_is_not_settlement_ready() -> None:
    with pytest.raises(WeatherUndergroundHistoryError, match="whole-degree"):
        _parse(_html(_observations(fractional_high=True)))


def test_multiple_embedded_observation_series_are_rejected_as_ambiguous() -> None:
    with pytest.raises(WeatherUndergroundHistoryError, match="exactly one embedded"):
        _parse(_html(_observations(), duplicate_series=True))


def test_official_history_url_is_required() -> None:
    with pytest.raises(WeatherUndergroundHistoryError, match="daily-history URL"):
        _parse(
            _html(_observations()),
            source_url="https://www.wunderground.com/weather/us/ny/new-york-city/KLGA",
        )
