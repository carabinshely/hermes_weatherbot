"""Revision-safe ingestion and learning joins for observed temperatures."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from weatherbot.domain import (
    EventId,
    LedgerState,
    MarketId,
    WeatherObservationEvidence,
    WeatherObservationRecorded,
)
from weatherbot.domain.resolution import MarketResolutionEvidence
from weatherbot.persistence import SQLiteEventStore


def _event_id(evidence: WeatherObservationEvidence) -> EventId:
    material = (
        f"{evidence.market_id}\n{evidence.payload_hash}\n"
        f"{evidence.source_revision}"
    ).encode("utf-8")
    return EventId(f"weather_observation_{hashlib.sha256(material).hexdigest()}")


@dataclass(slots=True)
class ObservationRecorder:
    store: SQLiteEventStore

    def record(self, evidence: WeatherObservationEvidence) -> bool:
        result = self.store.append(
            WeatherObservationRecorded(
                event_id=_event_id(evidence),
                occurred_at=evidence.retrieved_at,
                evidence=evidence,
            )
        )
        return result.appended


def latest_learning_observation(
    state: LedgerState,
    market_id: MarketId,
) -> WeatherObservationEvidence | None:
    candidates = [
        evidence
        for evidence in state.weather_observations.get(market_id, ())
        if evidence.learning_eligible
    ]
    if not candidates:
        return None
    return max(
        candidates,
        key=lambda evidence: (
            evidence.retrieved_at,
            evidence.source_revision,
            evidence.payload_hash,
        ),
    )


@dataclass(frozen=True, slots=True)
class VerifiedLearningOutcome:
    settlement: MarketResolutionEvidence
    observation: WeatherObservationEvidence

    def __post_init__(self) -> None:
        if not self.settlement.learning_eligible:
            raise ValueError("learning outcome requires verified non-void settlement")
        if not self.observation.learning_eligible:
            raise ValueError("learning outcome requires a final observed temperature")
        if self.settlement.market_id != self.observation.market_id:
            raise ValueError("settlement and observation belong to different markets")
        if self.settlement.market_date != self.observation.market_date:
            raise ValueError("settlement and observation use different market dates")
        if self.settlement.market_timezone != self.observation.market_timezone:
            raise ValueError("settlement and observation use different market timezones")
        if (
            self.settlement.declared_resolution_source.rstrip("/")
            != self.observation.source_url.rstrip("/")
        ):
            raise ValueError("observation source differs from the market resolution source")

    @property
    def observed_temperature(self):
        return self.observation.temperature

    @property
    def observed_unit(self) -> str:
        return self.observation.unit

    @property
    def winning_outcome_ids(self) -> tuple[str, ...]:
        return tuple(
            str(payout.outcome_id)
            for payout in self.settlement.payouts
            if payout.payout == 1
        )


def eligible_learning_outcomes(state: LedgerState) -> tuple[VerifiedLearningOutcome, ...]:
    outcomes: list[VerifiedLearningOutcome] = []
    for market_id, settlement in sorted(
        state.resolution_evidence.items(),
        key=lambda item: str(item[0]),
    ):
        if not settlement.learning_eligible:
            continue
        observation = latest_learning_observation(state, market_id)
        if observation is None:
            continue
        try:
            outcomes.append(
                VerifiedLearningOutcome(
                    settlement=settlement,
                    observation=observation,
                )
            )
        except ValueError:
            continue
    return tuple(outcomes)


def payload_sha256(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def parse_optional_timestamp(value: str | None) -> datetime | None:
    if value is None:
        return None
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("source timestamp must be timezone-aware")
    return parsed
