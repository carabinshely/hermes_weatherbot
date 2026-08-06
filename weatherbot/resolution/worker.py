"""Idempotent resolution and settlement over the durable event ledger."""

from __future__ import annotations

import hashlib
from collections import defaultdict
from collections.abc import Callable, Protocol
from dataclasses import dataclass
from datetime import UTC, datetime

from weatherbot.domain import (
    DomainError,
    EventId,
    LedgerState,
    MarketId,
    MarketResolution,
    MarketResolved,
    MarketResolutionEvidenceRecorded,
    Money,
    Position,
    PositionSettled,
    PositionStatus,
    fingerprint,
)
from weatherbot.persistence import PersistenceError, SQLiteEventStore
from weatherbot.resolution.context import ResolutionContextError
from weatherbot.resolution.model import (
    ResolutionContext,
    ResolutionCycleItem,
    ResolutionCycleReport,
    ResolutionPollResult,
    ResolutionPollStatus,
)


class ResolutionSource(Protocol):
    def poll(
        self,
        context: ResolutionContext,
        *,
        checked_at: datetime | None = None,
    ) -> ResolutionPollResult: ...


class ResolutionContextProvider(Protocol):
    def context_for_market(
        self,
        store: SQLiteEventStore,
        market_id: MarketId,
    ) -> ResolutionContext: ...


def _event_id(prefix: str, *parts: str) -> EventId:
    material = "\n".join((prefix, *parts)).encode("utf-8")
    return EventId(f"{prefix}_{hashlib.sha256(material).hexdigest()}")


def _open_positions_by_market(state: LedgerState) -> dict[MarketId, tuple[Position, ...]]:
    grouped: defaultdict[MarketId, list[Position]] = defaultdict(list)
    for position in state.positions.values():
        if position.status is PositionStatus.OPEN and position.quantity > 0:
            grouped[position.market_id].append(position)
    return {
        market_id: tuple(sorted(items, key=lambda item: str(item.outcome_id)))
        for market_id, items in grouped.items()
    }


def _settlement_events(
    positions: tuple[Position, ...],
    resolution: MarketResolution,
    *,
    occurred_at: datetime,
    identity_material: str,
    currency: str,
) -> tuple[PositionSettled, ...]:
    return tuple(
        PositionSettled(
            event_id=_event_id(
                "position_settled",
                str(position.market_id),
                str(position.outcome_id),
                identity_material,
            ),
            occurred_at=occurred_at,
            market_id=position.market_id,
            outcome_id=position.outcome_id,
            fee=Money.zero(currency),
        )
        for position in positions
    )


@dataclass(slots=True)
class ResolutionWorker:
    store: SQLiteEventStore
    source: ResolutionSource
    context_provider: ResolutionContextProvider
    clock: Callable[[], datetime] = lambda: datetime.now(UTC)

    def _settle_existing(
        self,
        state: LedgerState,
        positions: tuple[Position, ...],
        resolution: MarketResolution,
        now: datetime,
    ) -> ResolutionCycleItem:
        identity = fingerprint(resolution)
        settlements = _settlement_events(
            positions,
            resolution,
            occurred_at=now,
            identity_material=identity,
            currency=state.currency,
        )
        result = self.store.append_many(settlements)
        settled = sum(
            result.state.positions[(position.market_id, position.outcome_id)].status
            is PositionStatus.SETTLED
            for position in positions
        )
        return ResolutionCycleItem(
            market_id=resolution.market_id,
            status=ResolutionPollStatus.FINAL,
            reason="settled positions from an already-recorded market resolution",
            events_appended=len(result.appended_sequences),
            positions_settled=settled,
        )

    def _apply_terminal_poll(
        self,
        state: LedgerState,
        positions: tuple[Position, ...],
        poll: ResolutionPollResult,
        now: datetime,
    ) -> ResolutionCycleItem:
        evidence = poll.evidence
        resolution = poll.resolution
        if evidence is None or resolution is None:
            raise ValueError("terminal resolution poll lacks evidence or payout vector")
        evidence_event = MarketResolutionEvidenceRecorded(
            event_id=_event_id(
                "resolution_evidence",
                str(poll.market_id),
                evidence.payload_hash,
            ),
            occurred_at=now,
            evidence=evidence,
        )
        resolution_event = MarketResolved(
            event_id=_event_id(
                "market_resolved",
                str(poll.market_id),
                evidence.payload_hash,
            ),
            occurred_at=now,
            resolution=resolution,
        )
        settlements = _settlement_events(
            positions,
            resolution,
            occurred_at=now,
            identity_material=evidence.payload_hash,
            currency=state.currency,
        )
        result = self.store.append_many(
            (evidence_event, resolution_event, *settlements)
        )
        settled = sum(
            result.state.positions[(position.market_id, position.outcome_id)].status
            is PositionStatus.SETTLED
            for position in positions
        )
        return ResolutionCycleItem(
            market_id=poll.market_id,
            status=poll.status,
            reason=poll.reason,
            events_appended=len(result.appended_sequences),
            positions_settled=settled,
        )

    def run_once(self) -> ResolutionCycleReport:
        started = self.clock()
        if started.tzinfo is None or started.utcoffset() is None:
            raise ValueError("resolution worker clock must return timezone-aware values")
        state = self.store.load_state()
        positions_by_market = _open_positions_by_market(state)
        items: list[ResolutionCycleItem] = []

        for market_id in sorted(positions_by_market, key=str):
            positions = positions_by_market[market_id]
            try:
                existing = state.resolutions.get(market_id)
                if existing is not None:
                    item = self._settle_existing(state, positions, existing, started)
                else:
                    context = self.context_provider.context_for_market(
                        self.store,
                        market_id,
                    )
                    poll = self.source.poll(context, checked_at=started)
                    if poll.status.terminal:
                        item = self._apply_terminal_poll(
                            state,
                            positions,
                            poll,
                            started,
                        )
                    else:
                        item = ResolutionCycleItem(
                            market_id=market_id,
                            status=poll.status,
                            reason=poll.reason,
                            events_appended=0,
                            positions_settled=0,
                        )
            except (
                DomainError,
                PersistenceError,
                ResolutionContextError,
                KeyError,
                TypeError,
                ValueError,
            ) as exc:
                item = ResolutionCycleItem(
                    market_id=market_id,
                    status=ResolutionPollStatus.MALFORMED,
                    reason=f"resolution failed closed: {exc}",
                    events_appended=0,
                    positions_settled=0,
                )
            items.append(item)
            state = self.store.load_state()

        finished = self.clock()
        if finished.tzinfo is None or finished.utcoffset() is None:
            raise ValueError("resolution worker clock must return timezone-aware values")
        return ResolutionCycleReport(
            started_at=started,
            finished_at=finished,
            items=tuple(items),
        )
