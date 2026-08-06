"""Immutable authoritative weather observations and revision provenance."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from urllib.parse import urlparse
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from weatherbot.domain.errors import InvariantViolation
from weatherbot.domain.model import MarketId, require_aware

_SHA256 = re.compile(r"^[0-9a-f]{64}$")


def _require_text(value: str, *, label: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{label} must not be blank")
    return normalized


def _require_url(value: str, *, label: str) -> str:
    normalized = _require_text(value, label=label)
    parsed = urlparse(normalized)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError(f"{label} must be an HTTP(S) URL")
    return normalized


def _require_hash(value: str, *, label: str) -> str:
    normalized = _require_text(value, label=label).lower()
    if not _SHA256.fullmatch(normalized):
        raise ValueError(f"{label} must be a lowercase SHA-256 digest")
    return normalized


class ObservationEvidenceStatus(StrEnum):
    PROVISIONAL = "provisional"
    FINAL = "final"
    REVISED = "revised"

    @property
    def learning_eligible(self) -> bool:
        return self in {self.FINAL, self.REVISED}


@dataclass(frozen=True, slots=True)
class WeatherObservationEvidence:
    """One immutable source version of an observed daily temperature."""

    market_id: MarketId
    source_name: str
    source_url: str
    station_id: str
    measurement_basis: str
    market_date: date
    market_timezone: str
    temperature: Decimal
    unit: str
    retrieved_at: datetime
    source_timestamp: datetime | None
    source_revision: str
    status: ObservationEvidenceStatus
    payload_hash: str
    supersedes_payload_hash: str | None = None

    def __post_init__(self) -> None:
        if not str(self.market_id).strip():
            raise ValueError("market_id must not be blank")
        source_name = _require_text(self.source_name, label="source_name")
        source_url = _require_url(self.source_url, label="source_url")
        station_id = _require_text(self.station_id, label="station_id")
        measurement_basis = _require_text(
            self.measurement_basis,
            label="measurement_basis",
        )
        timezone_name = _require_text(self.market_timezone, label="market_timezone")
        try:
            ZoneInfo(timezone_name)
        except ZoneInfoNotFoundError as exc:
            raise ValueError(f"unknown IANA timezone: {timezone_name!r}") from exc
        try:
            temperature = Decimal(self.temperature)
        except (InvalidOperation, ValueError) as exc:
            raise ValueError("temperature must be decimal") from exc
        if not temperature.is_finite():
            raise ValueError("temperature must be finite")
        unit = _require_text(self.unit, label="unit").upper()
        if unit not in {"C", "F"}:
            raise ValueError("unit must be C or F")
        retrieved_at = self.retrieved_at
        require_aware(retrieved_at, label="retrieved_at")
        source_timestamp = self.source_timestamp
        if source_timestamp is not None:
            require_aware(source_timestamp, label="source_timestamp")
            local_date = source_timestamp.astimezone(ZoneInfo(timezone_name)).date()
            if local_date != self.market_date:
                raise InvariantViolation(
                    "source timestamp local date differs from the market date"
                )
        source_revision = _require_text(
            self.source_revision,
            label="source_revision",
        )
        payload_hash = _require_hash(self.payload_hash, label="payload_hash")
        supersedes = self.supersedes_payload_hash
        if supersedes is not None:
            supersedes = _require_hash(
                supersedes,
                label="supersedes_payload_hash",
            )
            if supersedes == payload_hash:
                raise InvariantViolation("an observation revision cannot supersede itself")
        if self.status is ObservationEvidenceStatus.REVISED and supersedes is None:
            raise InvariantViolation("revised observation requires a superseded payload hash")
        if self.status is not ObservationEvidenceStatus.REVISED and supersedes is not None:
            raise InvariantViolation(
                "only revised observations may name a superseded payload hash"
            )

        object.__setattr__(self, "source_name", source_name)
        object.__setattr__(self, "source_url", source_url)
        object.__setattr__(self, "station_id", station_id)
        object.__setattr__(self, "measurement_basis", measurement_basis)
        object.__setattr__(self, "market_timezone", timezone_name)
        object.__setattr__(self, "temperature", temperature)
        object.__setattr__(self, "unit", unit)
        object.__setattr__(self, "retrieved_at", retrieved_at)
        object.__setattr__(self, "source_timestamp", source_timestamp)
        object.__setattr__(self, "source_revision", source_revision)
        object.__setattr__(self, "payload_hash", payload_hash)
        object.__setattr__(self, "supersedes_payload_hash", supersedes)

    @property
    def learning_eligible(self) -> bool:
        return self.status.learning_eligible
