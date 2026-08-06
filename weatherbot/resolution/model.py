"""Typed resolution polling and cycle reports."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from enum import StrEnum

from weatherbot.domain import MarketId, MarketResolution
from weatherbot.domain.resolution import MarketResolutionEvidence
from weatherbot.markets import ConditionId, TemperatureBucket


class ResolutionPollStatus(StrEnum):
    PENDING = "pending"
    DELAYED = "delayed"
    DISPUTED = "disputed"
    UNAVAILABLE = "unavailable"
    MALFORMED = "malformed"
    FINAL = "final"
    VOID = "void"

    @property
    def terminal(self) -> bool:
        return self in {self.FINAL, self.VOID}


@dataclass(frozen=True, slots=True)
class ResolutionContext:
    market_id: MarketId
    condition_id: ConditionId
    market_date: date
    market_timezone: str
    bucket: TemperatureBucket
    declared_resolution_source: str


@dataclass(frozen=True, slots=True)
class ResolutionPollResult:
    market_id: MarketId
    status: ResolutionPollStatus
    checked_at: datetime
    reason: str
    evidence: MarketResolutionEvidence | None = None
    resolution: MarketResolution | None = None

    def __post_init__(self) -> None:
        terminal = self.status.terminal
        if terminal != (self.evidence is not None and self.resolution is not None):
            raise ValueError(
                "terminal poll results require evidence and resolution; "
                "non-terminal results must not contain them"
            )
        if self.evidence is not None and self.evidence.market_id != self.market_id:
            raise ValueError("poll evidence belongs to another market")
        if self.resolution is not None and self.resolution.market_id != self.market_id:
            raise ValueError("poll resolution belongs to another market")
        if not self.reason.strip():
            raise ValueError("poll result reason must not be blank")


@dataclass(frozen=True, slots=True)
class ResolutionCycleItem:
    market_id: MarketId
    status: ResolutionPollStatus
    reason: str
    events_appended: int
    positions_settled: int


@dataclass(frozen=True, slots=True)
class ResolutionCycleReport:
    started_at: datetime
    finished_at: datetime
    items: tuple[ResolutionCycleItem, ...]

    @property
    def checked(self) -> int:
        return len(self.items)

    @property
    def resolved(self) -> int:
        return sum(item.status is ResolutionPollStatus.FINAL for item in self.items)

    @property
    def voided(self) -> int:
        return sum(item.status is ResolutionPollStatus.VOID for item in self.items)

    @property
    def settled_positions(self) -> int:
        return sum(item.positions_settled for item in self.items)

    @classmethod
    def empty(cls, at: datetime) -> ResolutionCycleReport:
        return cls(started_at=at, finished_at=at, items=())
