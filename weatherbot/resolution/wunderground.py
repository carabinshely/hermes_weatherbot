"""Authoritative Weather Underground public daily-history evidence.

This adapter intentionally consumes only the canonical public HTML history page. It does
not depend on undocumented frontend APIs. The page embeds station identity and one JSON
observation series; the parser validates those against the market contract and derives
the finalized whole-degree daily high from the station observations.
"""

from __future__ import annotations

import hashlib
import json
import math
import urllib.request
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, time
from decimal import Decimal, InvalidOperation
from html.parser import HTMLParser
from typing import cast
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

from weatherbot.domain import MarketId, ObservationEvidenceStatus, WeatherObservationEvidence

_NORMALIZED_SCHEMA_VERSION = 1
_SOURCE_NAME = "Weather Underground public daily history"
_MEASUREMENT_BASIS = (
    "finalized daily high temperature from the public station daily-history observation series"
)
_ALLOWED_HOSTS = {"www.wunderground.com", "wunderground.com"}


class WeatherUndergroundHistoryError(ValueError):
    """Raised when a public history page cannot be treated as authoritative evidence."""


@dataclass(frozen=True, slots=True)
class WeatherUndergroundCoveragePolicy:
    """Minimum station-series coverage required before calling a daily high final."""

    min_observations: int = 18
    latest_allowed_first_local_time: time = time(hour=2)
    earliest_allowed_last_local_time: time = time(hour=22)

    def __post_init__(self) -> None:
        if isinstance(self.min_observations, bool):
            raise WeatherUndergroundHistoryError("min_observations must be an integer")
        if self.min_observations < 2:
            raise WeatherUndergroundHistoryError("min_observations must be at least two")


_DEFAULT_COVERAGE_POLICY = WeatherUndergroundCoveragePolicy()


@dataclass(frozen=True, slots=True)
class WeatherUndergroundObservation:
    timestamp_utc: datetime
    temperature_f: Decimal

    def __post_init__(self) -> None:
        timestamp = self.timestamp_utc
        if timestamp.tzinfo is None or timestamp.utcoffset() is None:
            raise WeatherUndergroundHistoryError("observation timestamp must be timezone-aware")
        temperature = Decimal(self.temperature_f)
        if not temperature.is_finite():
            raise WeatherUndergroundHistoryError("observation temperature must be finite")
        object.__setattr__(self, "timestamp_utc", timestamp.astimezone(UTC))
        object.__setattr__(self, "temperature_f", temperature)


@dataclass(frozen=True, slots=True)
class WeatherUndergroundDailyHistoryCapture:
    evidence: WeatherObservationEvidence
    final_url: str
    raw_page_sha256: str
    normalized_payload_sha256: str
    observation_count: int
    first_observation_utc: datetime
    last_observation_utc: datetime
    high_observation_utc: datetime

    def __post_init__(self) -> None:
        for label, digest in (
            ("raw_page_sha256", self.raw_page_sha256),
            ("normalized_payload_sha256", self.normalized_payload_sha256),
        ):
            if len(digest) != 64 or any(
                character not in "0123456789abcdef" for character in digest
            ):
                raise WeatherUndergroundHistoryError(f"{label} must be a SHA-256 digest")
        if self.evidence.payload_hash != self.normalized_payload_sha256:
            raise WeatherUndergroundHistoryError(
                "evidence payload hash must equal normalized Weather Underground payload hash"
            )
        if self.observation_count < 1:
            raise WeatherUndergroundHistoryError("observation_count must be positive")


class _HistoryPageParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.airport_bodies: list[dict[str, str]] = []
        self.json_scripts: list[str] = []
        self._capture_json = False
        self._json_parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        normalized = {key.lower(): value or "" for key, value in attrs}
        if tag.lower() == "airport-body":
            self.airport_bodies.append(normalized)
        if tag.lower() == "script" and normalized.get("type", "").lower() == "application/json":
            if self._capture_json:
                raise WeatherUndergroundHistoryError("nested application/json script blocks")
            self._capture_json = True
            self._json_parts = []

    def handle_data(self, data: str) -> None:
        if self._capture_json:
            self._json_parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "script" and self._capture_json:
            self.json_scripts.append("".join(self._json_parts))
            self._capture_json = False
            self._json_parts = []


def _canonical_json_bytes(value: Mapping[str, object]) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def _require_wunderground_history_url(value: str, *, label: str) -> str:
    normalized = value.strip()
    parsed = urlparse(normalized)
    if parsed.scheme != "https" or parsed.hostname not in _ALLOWED_HOSTS:
        raise WeatherUndergroundHistoryError(
            f"{label} must use the public Weather Underground HTTPS host"
        )
    if "/history/daily/" not in parsed.path:
        raise WeatherUndergroundHistoryError(
            f"{label} must be a Weather Underground daily-history URL"
        )
    return normalized


def _parse_page_date(value: str) -> date:
    try:
        year_text, month_text, day_text = value.split("-")
        return date(int(year_text), int(month_text), int(day_text))
    except (TypeError, ValueError) as exc:
        raise WeatherUndergroundHistoryError(
            f"invalid Weather Underground page date: {value!r}"
        ) from exc


def _number(value: object, *, label: str) -> Decimal:
    if isinstance(value, bool):
        raise WeatherUndergroundHistoryError(f"{label} must be numeric")
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise WeatherUndergroundHistoryError(f"{label} must be numeric") from exc
    if not result.is_finite():
        raise WeatherUndergroundHistoryError(f"{label} must be finite")
    return result


def _integer(value: object, *, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise WeatherUndergroundHistoryError(f"{label} must be an integer")
    return value


def _extract_airport_body(
    parser: _HistoryPageParser,
    *,
    station_id: str,
    market_date: date,
    market_timezone: str,
) -> Mapping[str, str]:
    station = station_id.strip().upper()
    matches = [
        attrs
        for attrs in parser.airport_bodies
        if attrs.get("data-icao-code", "").upper() == station
    ]
    if len(matches) != 1:
        raise WeatherUndergroundHistoryError(
            f"expected exactly one airport-body for station {station}, found {len(matches)}"
        )
    attrs = matches[0]
    if attrs.get("data-mode", "").lower() != "daily":
        raise WeatherUndergroundHistoryError(
            "Weather Underground page is not in daily-history mode"
        )
    page_timezone = attrs.get("data-time-zone", "")
    if page_timezone != market_timezone:
        raise WeatherUndergroundHistoryError(
            f"Weather Underground timezone mismatch: expected {market_timezone}, got {page_timezone}"
        )
    page_date = _parse_page_date(attrs.get("data-date", ""))
    if page_date != market_date:
        raise WeatherUndergroundHistoryError(
            f"Weather Underground market date mismatch: expected {market_date}, got {page_date}"
        )
    location_id = attrs.get("data-location-id", "")
    if not location_id.upper().startswith(f"{station}:"):
        raise WeatherUndergroundHistoryError(
            "Weather Underground location ID disagrees with station ID"
        )
    return attrs


def _observation_json_candidate(value: object) -> Sequence[Mapping[str, object]] | None:
    if not isinstance(value, list) or not value:
        return None
    rows: list[Mapping[str, object]] = []
    for item in cast(list[object], value):
        if not isinstance(item, Mapping):
            return None
        row = cast(Mapping[str, object], item)
        if "ts" not in row or "temp" not in row:
            return None
        rows.append(row)
    return tuple(rows)


def _extract_observation_rows(parser: _HistoryPageParser) -> Sequence[Mapping[str, object]]:
    candidates: list[Sequence[Mapping[str, object]]] = []
    for script in parser.json_scripts:
        if not script.strip():
            continue
        try:
            decoded: object = json.loads(script)
        except json.JSONDecodeError:
            continue
        candidate = _observation_json_candidate(decoded)
        if candidate is not None:
            candidates.append(candidate)
    if len(candidates) != 1:
        raise WeatherUndergroundHistoryError(
            f"expected exactly one embedded station observation series, found {len(candidates)}"
        )
    return candidates[0]


def _normalize_observations(
    rows: Sequence[Mapping[str, object]],
    *,
    market_date: date,
    market_timezone: str,
) -> tuple[WeatherUndergroundObservation, ...]:
    timezone = ZoneInfo(market_timezone)
    observations: list[WeatherUndergroundObservation] = []
    seen_timestamps: set[datetime] = set()
    for row in rows:
        temperature_raw = row.get("temp")
        if temperature_raw is None:
            continue
        timestamp_ms = _integer(row.get("ts"), label="Weather Underground observation timestamp")
        timestamp_utc = datetime.fromtimestamp(timestamp_ms / 1000, tz=UTC)
        local_date = timestamp_utc.astimezone(timezone).date()
        if local_date != market_date:
            raise WeatherUndergroundHistoryError(
                "Weather Underground observation series contains a timestamp outside the requested local day"
            )
        if timestamp_utc in seen_timestamps:
            raise WeatherUndergroundHistoryError(
                "duplicate Weather Underground observation timestamp"
            )
        seen_timestamps.add(timestamp_utc)
        observations.append(
            WeatherUndergroundObservation(
                timestamp_utc=timestamp_utc,
                temperature_f=_number(temperature_raw, label="Weather Underground temperature"),
            )
        )
    if not observations:
        raise WeatherUndergroundHistoryError("Weather Underground observation series is empty")
    return tuple(sorted(observations, key=lambda item: item.timestamp_utc))


def _validate_coverage(
    observations: Sequence[WeatherUndergroundObservation],
    *,
    market_timezone: str,
    policy: WeatherUndergroundCoveragePolicy,
) -> None:
    if len(observations) < policy.min_observations:
        raise WeatherUndergroundHistoryError(
            f"insufficient Weather Underground observations: {len(observations)} < {policy.min_observations}"
        )
    timezone = ZoneInfo(market_timezone)
    first_local = observations[0].timestamp_utc.astimezone(timezone).timetz().replace(tzinfo=None)
    last_local = observations[-1].timestamp_utc.astimezone(timezone).timetz().replace(tzinfo=None)
    if first_local > policy.latest_allowed_first_local_time:
        raise WeatherUndergroundHistoryError(
            f"Weather Underground series begins too late in the local day: {first_local}"
        )
    if last_local < policy.earliest_allowed_last_local_time:
        raise WeatherUndergroundHistoryError(
            f"Weather Underground series ends too early in the local day: {last_local}"
        )


def _normalized_payload(
    *,
    station_id: str,
    market_date: date,
    market_timezone: str,
    observations: Sequence[WeatherUndergroundObservation],
) -> dict[str, object]:
    return {
        "schema_version": _NORMALIZED_SCHEMA_VERSION,
        "source": "weather-underground-public-daily-history-html",
        "station_id": station_id.strip().upper(),
        "market_date": market_date.isoformat(),
        "market_timezone": market_timezone,
        "observations": [
            {
                "timestamp_utc": item.timestamp_utc.isoformat(),
                "temperature_f": format(item.temperature_f, "f"),
            }
            for item in observations
        ],
    }


def parse_wunderground_daily_history_html(
    raw_html: bytes,
    *,
    source_url: str,
    retrieved_at_utc: datetime,
    market_id: MarketId,
    station_id: str,
    market_date: date,
    market_timezone: str,
    coverage_policy: WeatherUndergroundCoveragePolicy = _DEFAULT_COVERAGE_POLICY,
) -> WeatherUndergroundDailyHistoryCapture:
    """Parse one canonical public Weather Underground daily-history page."""

    final_url = _require_wunderground_history_url(source_url, label="source_url")
    if retrieved_at_utc.tzinfo is None or retrieved_at_utc.utcoffset() is None:
        raise WeatherUndergroundHistoryError("retrieved_at_utc must be timezone-aware")
    retrieved = retrieved_at_utc.astimezone(UTC)
    timezone = ZoneInfo(market_timezone)
    if market_date >= retrieved.astimezone(timezone).date():
        raise WeatherUndergroundHistoryError(
            "daily history is not final until the market-local day has ended"
        )
    try:
        text = raw_html.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise WeatherUndergroundHistoryError("Weather Underground page is not UTF-8 HTML") from exc

    parser = _HistoryPageParser()
    parser.feed(text)
    parser.close()
    _extract_airport_body(
        parser,
        station_id=station_id,
        market_date=market_date,
        market_timezone=market_timezone,
    )
    rows = _extract_observation_rows(parser)
    observations = _normalize_observations(
        rows,
        market_date=market_date,
        market_timezone=market_timezone,
    )
    _validate_coverage(
        observations,
        market_timezone=market_timezone,
        policy=coverage_policy,
    )

    daily_high = max(item.temperature_f for item in observations)
    if daily_high != daily_high.to_integral_value():
        raise WeatherUndergroundHistoryError(
            f"Weather Underground finalized high is not a whole-degree value: {daily_high}"
        )
    high_observation = min(
        (item for item in observations if item.temperature_f == daily_high),
        key=lambda item: item.timestamp_utc,
    )
    normalized = _normalized_payload(
        station_id=station_id,
        market_date=market_date,
        market_timezone=market_timezone,
        observations=observations,
    )
    normalized_hash = hashlib.sha256(_canonical_json_bytes(normalized)).hexdigest()
    raw_page_hash = hashlib.sha256(raw_html).hexdigest()
    evidence = WeatherObservationEvidence(
        market_id=market_id,
        source_name=_SOURCE_NAME,
        source_url=final_url,
        station_id=station_id.strip().upper(),
        measurement_basis=_MEASUREMENT_BASIS,
        market_date=market_date,
        market_timezone=market_timezone,
        temperature=daily_high,
        unit="F",
        retrieved_at=retrieved,
        source_timestamp=high_observation.timestamp_utc,
        source_revision=f"public-history-html-v1:{normalized_hash[:16]}",
        status=ObservationEvidenceStatus.FINAL,
        payload_hash=normalized_hash,
    )
    return WeatherUndergroundDailyHistoryCapture(
        evidence=evidence,
        final_url=final_url,
        raw_page_sha256=raw_page_hash,
        normalized_payload_sha256=normalized_hash,
        observation_count=len(observations),
        first_observation_utc=observations[0].timestamp_utc,
        last_observation_utc=observations[-1].timestamp_utc,
        high_observation_utc=high_observation.timestamp_utc,
    )


def fetch_wunderground_daily_history(
    *,
    source_url: str,
    market_id: MarketId,
    station_id: str,
    market_date: date,
    market_timezone: str,
    timeout_seconds: float = 30.0,
    coverage_policy: WeatherUndergroundCoveragePolicy = _DEFAULT_COVERAGE_POLICY,
) -> WeatherUndergroundDailyHistoryCapture:
    """Fetch and parse one public Weather Underground daily-history page."""

    requested_url = _require_wunderground_history_url(source_url, label="source_url")
    if not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
        raise WeatherUndergroundHistoryError("timeout_seconds must be finite and positive")
    request = urllib.request.Request(
        requested_url,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/151.0 Safari/537.36"
            ),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            raw_html = response.read()
            final_url = response.geturl()
    except OSError as exc:
        raise WeatherUndergroundHistoryError(
            f"failed to fetch Weather Underground daily history: {exc}"
        ) from exc
    final_url = _require_wunderground_history_url(final_url, label="final_url")
    return parse_wunderground_daily_history_html(
        raw_html,
        source_url=final_url,
        retrieved_at_utc=datetime.now(UTC),
        market_id=market_id,
        station_id=station_id,
        market_date=market_date,
        market_timezone=market_timezone,
        coverage_policy=coverage_policy,
    )
