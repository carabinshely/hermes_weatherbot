from __future__ import annotations

from pathlib import Path


def replace_once(path: str, old: str, new: str) -> None:
    file = Path(path)
    content = file.read_text(encoding="utf-8")
    count = content.count(old)
    if count != 1:
        raise SystemExit(f"{path}: expected one marker, found {count}")
    file.write_text(content.replace(old, new, 1), encoding="utf-8")


# Domain event model.
replace_once(
    "weatherbot/domain/events.py",
    "from datetime import datetime\n",
    "from datetime import date, datetime\n",
)
replace_once(
    "weatherbot/domain/events.py",
    "from weatherbot.domain.money import Money, as_decimal, require_nonnegative\n",
    "from weatherbot.domain.money import Money, as_decimal, require_nonnegative\n"
    "from weatherbot.domain.resolution import MarketResolutionEvidence\n",
)
replace_once(
    "weatherbot/domain/events.py",
    '''    if isinstance(value, datetime):
        return value.isoformat()
''',
    '''    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
''',
)
replace_once(
    "weatherbot/domain/events.py",
    '''@dataclass(frozen=True, slots=True, kw_only=True)
class MarketResolved(DomainEvent):
    resolution: MarketResolution


@dataclass(frozen=True, slots=True, kw_only=True)
class PositionSettled(DomainEvent):
''',
    '''@dataclass(frozen=True, slots=True, kw_only=True)
class MarketResolutionEvidenceRecorded(DomainEvent):
    evidence: MarketResolutionEvidence


@dataclass(frozen=True, slots=True, kw_only=True)
class MarketResolved(DomainEvent):
    resolution: MarketResolution


@dataclass(frozen=True, slots=True, kw_only=True)
class PositionSettled(DomainEvent):
''',
)
replace_once(
    "weatherbot/domain/events.py",
    '''    | OrderOutcomeUnknown
    | MarketResolved
    | PositionSettled
''',
    '''    | OrderOutcomeUnknown
    | MarketResolutionEvidenceRecorded
    | MarketResolved
    | PositionSettled
''',
)

# Derived state and evidence invariants.
replace_once(
    "weatherbot/domain/state.py",
    "from weatherbot.domain.money import (\n",
    "from weatherbot.domain.resolution import MarketResolutionEvidence\n"
    "from weatherbot.domain.money import (\n",
)
replace_once(
    "weatherbot/domain/state.py",
    '''def _empty_resolutions() -> Mapping[MarketId, MarketResolution]:
    return {}


def _empty_event_fingerprints() -> Mapping[EventId, str]:
''',
    '''def _empty_resolutions() -> Mapping[MarketId, MarketResolution]:
    return {}


def _empty_resolution_evidence() -> Mapping[MarketId, MarketResolutionEvidence]:
    return {}


def _empty_event_fingerprints() -> Mapping[EventId, str]:
''',
)
replace_once(
    "weatherbot/domain/state.py",
    '''    positions: Mapping[PositionKey, Position] = field(default_factory=_empty_positions)
    resolutions: Mapping[MarketId, MarketResolution] = field(default_factory=_empty_resolutions)
    event_fingerprints: Mapping[EventId, str] = field(default_factory=_empty_event_fingerprints)
''',
    '''    positions: Mapping[PositionKey, Position] = field(default_factory=_empty_positions)
    resolutions: Mapping[MarketId, MarketResolution] = field(default_factory=_empty_resolutions)
    resolution_evidence: Mapping[MarketId, MarketResolutionEvidence] = field(
        default_factory=_empty_resolution_evidence
    )
    event_fingerprints: Mapping[EventId, str] = field(default_factory=_empty_event_fingerprints)
''',
)
replace_once(
    "weatherbot/domain/state.py",
    '''        object.__setattr__(self, "positions", _freeze_mapping(self.positions))
        object.__setattr__(self, "resolutions", _freeze_mapping(self.resolutions))
        object.__setattr__(
''',
    '''        object.__setattr__(self, "positions", _freeze_mapping(self.positions))
        object.__setattr__(self, "resolutions", _freeze_mapping(self.resolutions))
        object.__setattr__(
            self,
            "resolution_evidence",
            _freeze_mapping(self.resolution_evidence),
        )
        object.__setattr__(
''',
)
replace_once(
    "weatherbot/domain/state.py",
    '''        for key, reserved_quantity in expected_sell_reservations.items():
            if reserved_quantity and key not in self.positions:
                raise InvariantViolation("sell order reserves a missing position")

        if not self.opened and (
''',
    '''        for key, reserved_quantity in expected_sell_reservations.items():
            if reserved_quantity and key not in self.positions:
                raise InvariantViolation("sell order reserves a missing position")

        for market_id, evidence in self.resolution_evidence.items():
            if market_id != evidence.market_id:
                raise InvariantViolation("resolution evidence map key does not match market")
            resolution = self.resolutions.get(market_id)
            if resolution is not None and resolution.payouts != evidence.payouts:
                raise InvariantViolation(
                    "recorded resolution differs from its authoritative evidence"
                )

        if not self.opened and (
''',
)
replace_once(
    "weatherbot/domain/state.py",
    '''            or self.positions
            or self.resolutions
        ):
''',
    '''            or self.positions
            or self.resolutions
            or self.resolution_evidence
        ):
''',
)

# Reducers.
replace_once(
    "weatherbot/domain/reducers.py",
    '''    LedgerEvent,
    MarketResolved,
''',
    '''    LedgerEvent,
    MarketResolved,
    MarketResolutionEvidenceRecorded,
''',
)
replace_once(
    "weatherbot/domain/reducers.py",
    '''def _apply_resolution(state: LedgerState, event: MarketResolved) -> LedgerState:
    existing = state.resolutions.get(event.resolution.market_id)
''',
    '''def _apply_resolution_evidence(
    state: LedgerState,
    event: MarketResolutionEvidenceRecorded,
) -> LedgerState:
    market_id = event.evidence.market_id
    existing = state.resolution_evidence.get(market_id)
    if existing is not None:
        if existing == event.evidence:
            return state
        raise DuplicateEventConflict(
            "authoritative market resolution evidence changed after recording"
        )
    resolution = state.resolutions.get(market_id)
    if resolution is not None and resolution.payouts != event.evidence.payouts:
        raise DuplicateEventConflict(
            "authoritative evidence conflicts with the recorded payout vector"
        )
    evidence = dict(state.resolution_evidence)
    evidence[market_id] = event.evidence
    return replace(state, resolution_evidence=evidence)


def _apply_resolution(state: LedgerState, event: MarketResolved) -> LedgerState:
    evidence = state.resolution_evidence.get(event.resolution.market_id)
    if evidence is not None and evidence.payouts != event.resolution.payouts:
        raise DuplicateEventConflict(
            "market resolution payout differs from authoritative evidence"
        )
    existing = state.resolutions.get(event.resolution.market_id)
''',
)
replace_once(
    "weatherbot/domain/reducers.py",
    '''    elif isinstance(event, MarketResolved):
        next_state = _apply_resolution(state, event)
    else:
''',
    '''    elif isinstance(event, MarketResolutionEvidenceRecorded):
        next_state = _apply_resolution_evidence(state, event)
    elif isinstance(event, MarketResolved):
        next_state = _apply_resolution(state, event)
    else:
''',
)

# Public domain exports.
replace_once(
    "weatherbot/domain/__init__.py",
    '''    LedgerEvent,
    MarketResolved,
''',
    '''    LedgerEvent,
    MarketResolved,
    MarketResolutionEvidenceRecorded,
''',
)
replace_once(
    "weatherbot/domain/__init__.py",
    '''    PositionSettled,
)
from weatherbot.domain.model import (
''',
    '''    PositionSettled,
    fingerprint,
)
from weatherbot.domain.model import (
''',
)
replace_once(
    "weatherbot/domain/__init__.py",
    "from weatherbot.domain.reducers import apply_event, replay\n",
    "from weatherbot.domain.reducers import apply_event, replay\n"
    "from weatherbot.domain.resolution import (\n"
    "    MarketResolutionEvidence,\n"
    "    ResolutionEvidenceStatus,\n"
    ")\n",
)
replace_once(
    "weatherbot/domain/__init__.py",
    '''    "MarketResolution",
    "MarketResolved",
''',
    '''    "MarketResolution",
    "MarketResolutionEvidence",
    "MarketResolutionEvidenceRecorded",
    "MarketResolved",
''',
)
replace_once(
    "weatherbot/domain/__init__.py",
    '''    "RiskDecisionStatus",
    "Side",
''',
    '''    "ResolutionEvidenceStatus",
    "RiskDecisionStatus",
    "Side",
''',
)
replace_once(
    "weatherbot/domain/__init__.py",
    '''    "build_order_intent_id",
    "money_from_unit_price",
''',
    '''    "build_order_intent_id",
    "fingerprint",
    "money_from_unit_price",
''',
)

# Persistence codec.
replace_once(
    "weatherbot/persistence/codec.py",
    "from datetime import datetime\n",
    "from datetime import date, datetime\n",
)
replace_once(
    "weatherbot/persistence/codec.py",
    '''    MarketResolution,
    MarketResolved,
''',
    '''    MarketResolution,
    MarketResolved,
    MarketResolutionEvidence,
    MarketResolutionEvidenceRecorded,
''',
)
replace_once(
    "weatherbot/persistence/codec.py",
    '''    PositionSettled,
    Side,
)
''',
    '''    PositionSettled,
    ResolutionEvidenceStatus,
    Side,
)
''',
)
replace_once(
    "weatherbot/persistence/codec.py",
    '''    OrderOutcomeUnknown: "order_outcome_unknown",
    MarketResolved: "market_resolved",
''',
    '''    OrderOutcomeUnknown: "order_outcome_unknown",
    MarketResolutionEvidenceRecorded: "market_resolution_evidence_recorded",
    MarketResolved: "market_resolved",
''',
)
replace_once(
    "weatherbot/persistence/codec.py",
    '''def _resolution_to_data(value: MarketResolution) -> dict[str, object]:
''',
    '''def _evidence_to_data(value: MarketResolutionEvidence) -> dict[str, object]:
    return {
        "market_id": str(value.market_id),
        "condition_id": value.condition_id,
        "source_name": value.source_name,
        "source_url": value.source_url,
        "declared_resolution_source": value.declared_resolution_source,
        "retrieved_at": value.retrieved_at.isoformat(),
        "finalized_at": value.finalized_at.isoformat(),
        "market_date": value.market_date.isoformat(),
        "market_timezone": value.market_timezone,
        "status": value.status.value,
        "resolution_value": value.resolution_value,
        "payouts": [
            {
                "outcome_id": str(payout.outcome_id),
                "payout": format(payout.payout, "f"),
            }
            for payout in value.payouts
        ],
        "payload_hash": value.payload_hash,
    }


def _resolution_to_data(value: MarketResolution) -> dict[str, object]:
''',
)
replace_once(
    "weatherbot/persistence/codec.py",
    '''    if isinstance(event, MarketResolved):
        return {**common, "resolution": _resolution_to_data(event.resolution)}
''',
    '''    if isinstance(event, MarketResolutionEvidenceRecorded):
        return {**common, "evidence": _evidence_to_data(event.evidence)}
    if isinstance(event, MarketResolved):
        return {**common, "resolution": _resolution_to_data(event.resolution)}
''',
)
replace_once(
    "weatherbot/persistence/codec.py",
    '''    if isinstance(event, MarketResolved):
        return None, None, str(event.resolution.market_id), None
''',
    '''    if isinstance(event, MarketResolutionEvidenceRecorded):
        return None, None, str(event.evidence.market_id), None
    if isinstance(event, MarketResolved):
        return None, None, str(event.resolution.market_id), None
''',
)
replace_once(
    "weatherbot/persistence/codec.py",
    '''def _datetime(value: object, *, label: str) -> datetime:
''',
    '''def _date(value: object, *, label: str) -> date:
    text = _text(value, label=label)
    try:
        return date.fromisoformat(text)
    except ValueError as exc:
        raise CorruptLedgerError(f"{label} is not an ISO-8601 date") from exc


def _datetime(value: object, *, label: str) -> datetime:
''',
)
replace_once(
    "weatherbot/persistence/codec.py",
    '''def _resolution(value: object) -> MarketResolution:
''',
    '''def _evidence(value: object) -> MarketResolutionEvidence:
    data = _mapping(value, label="evidence")
    _expect_keys(
        data,
        required={
            "market_id",
            "condition_id",
            "source_name",
            "source_url",
            "declared_resolution_source",
            "retrieved_at",
            "finalized_at",
            "market_date",
            "market_timezone",
            "status",
            "resolution_value",
            "payouts",
            "payload_hash",
        },
        label="evidence",
    )
    payouts: list[OutcomePayout] = []
    for index, raw_payout in enumerate(_sequence(data["payouts"], label="evidence.payouts")):
        payout = _mapping(raw_payout, label=f"evidence.payouts[{index}]")
        _expect_keys(
            payout,
            required={"outcome_id", "payout"},
            label=f"evidence.payouts[{index}]",
        )
        payouts.append(
            OutcomePayout(
                outcome_id=OutcomeId(
                    _text(
                        payout["outcome_id"],
                        label=f"evidence.payouts[{index}].outcome_id",
                    )
                ),
                payout=_decimal(
                    payout["payout"],
                    label=f"evidence.payouts[{index}].payout",
                ),
            )
        )
    status_text = _text(data["status"], label="evidence.status")
    try:
        status = ResolutionEvidenceStatus(status_text)
    except ValueError as exc:
        raise CorruptLedgerError(
            f"evidence.status is unsupported: {status_text!r}"
        ) from exc
    return MarketResolutionEvidence(
        market_id=MarketId(_text(data["market_id"], label="evidence.market_id")),
        condition_id=_text(data["condition_id"], label="evidence.condition_id"),
        source_name=_text(data["source_name"], label="evidence.source_name"),
        source_url=_text(data["source_url"], label="evidence.source_url"),
        declared_resolution_source=_text(
            data["declared_resolution_source"],
            label="evidence.declared_resolution_source",
        ),
        retrieved_at=_datetime(data["retrieved_at"], label="evidence.retrieved_at"),
        finalized_at=_datetime(data["finalized_at"], label="evidence.finalized_at"),
        market_date=_date(data["market_date"], label="evidence.market_date"),
        market_timezone=_text(
            data["market_timezone"],
            label="evidence.market_timezone",
        ),
        status=status,
        resolution_value=_text(
            data["resolution_value"],
            label="evidence.resolution_value",
        ),
        payouts=tuple(payouts),
        payload_hash=_text(data["payload_hash"], label="evidence.payload_hash"),
    )


def _resolution(value: object) -> MarketResolution:
''',
)
replace_once(
    "weatherbot/persistence/codec.py",
    '''    if event_type == "market_resolved":
        event_id, occurred_at = _common(data, required={"resolution"})
''',
    '''    if event_type == "market_resolution_evidence_recorded":
        event_id, occurred_at = _common(data, required={"evidence"})
        return MarketResolutionEvidenceRecorded(
            event_id=event_id,
            occurred_at=occurred_at,
            evidence=_evidence(data["evidence"]),
        )
    if event_type == "market_resolved":
        event_id, occurred_at = _common(data, required={"resolution"})
''',
)
