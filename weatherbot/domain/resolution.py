"""Immutable evidence supporting authoritative market resolution events."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime
from enum import StrEnum
from urllib.parse import urlparse
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from weatherbot.domain.errors import InvariantViolation
from weatherbot.domain.model import MarketId, OutcomePayout, require_aware

_CONDITION_ID = re.compile(r"^0x[0-9a-f]{64}$")
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


class ResolutionEvidenceStatus(StrEnum):
    VERIFIED = "verified"
    VOID = "void"


@dataclass(frozen=True, slots=True)
class MarketResolutionEvidence:
    """Canonical proof retained alongside a financial market resolution."""

    market_id: MarketId
    condition_id: str
    source_name: str
    source_url: str
    declared_resolution_source: str
    retrieved_at: datetime
    finalized_at: datetime
    market_date: date
    market_timezone: str
    status: ResolutionEvidenceStatus
    resolution_value: str
    payouts: tuple[OutcomePayout, ...]
    payload_hash: str

    def __post_init__(self) -> None:
        if not str(self.market_id).strip():
            raise ValueError("market_id must not be blank")
        condition_id = _require_text(self.condition_id, label="condition_id").lower()
        if not _CONDITION_ID.fullmatch(condition_id):
            raise ValueError("condition_id must be a 32-byte hexadecimal identifier")
        source_name = _require_text(self.source_name, label="source_name")
        source_url = _require_url(self.source_url, label="source_url")
        declared_source = _require_url(
            self.declared_resolution_source,
            label="declared_resolution_source",
        )
        retrieved_at = require_aware(self.retrieved_at, label="retrieved_at")
        finalized_at = require_aware(self.finalized_at, label="finalized_at")
        if finalized_at > retrieved_at:
            raise InvariantViolation("resolution finalization cannot occur after retrieval")
        timezone_name = _require_text(self.market_timezone, label="market_timezone")
        try:
            ZoneInfo(timezone_name)
        except ZoneInfoNotFoundError as exc:
            raise ValueError(f"unknown IANA timezone: {timezone_name!r}") from exc
        resolution_value = _require_text(
            self.resolution_value,
            label="resolution_value",
        )
        payload_hash = _require_text(self.payload_hash, label="payload_hash").lower()
        if not _SHA256.fullmatch(payload_hash):
            raise ValueError("payload_hash must be a lowercase SHA-256 digest")
        if len(self.payouts) != 2:
            raise InvariantViolation("binary resolution evidence requires exactly two payouts")
        outcome_ids = [payout.outcome_id for payout in self.payouts]
        if len(set(outcome_ids)) != 2:
            raise InvariantViolation("resolution evidence contains duplicate outcome IDs")
        payout_values = tuple(payout.payout for payout in self.payouts)
        if sum(payout_values) != 1:
            raise InvariantViolation("binary resolution payouts must sum exactly to one")
        is_void = payout_values == (payout_values[0], payout_values[0])
        if self.status is ResolutionEvidenceStatus.VOID:
            if not is_void or payout_values[0] != 0.5:
                raise InvariantViolation("void evidence requires a 0.5/0.5 payout vector")
        elif sorted(payout_values) != [0, 1]:
            raise InvariantViolation("verified evidence requires a 1/0 payout vector")

        object.__setattr__(self, "condition_id", condition_id)
        object.__setattr__(self, "source_name", source_name)
        object.__setattr__(self, "source_url", source_url)
        object.__setattr__(self, "declared_resolution_source", declared_source)
        object.__setattr__(self, "retrieved_at", retrieved_at)
        object.__setattr__(self, "finalized_at", finalized_at)
        object.__setattr__(self, "market_timezone", timezone_name)
        object.__setattr__(self, "resolution_value", resolution_value)
        object.__setattr__(self, "payload_hash", payload_hash)

    @property
    def learning_eligible(self) -> bool:
        return self.status is ResolutionEvidenceStatus.VERIFIED
