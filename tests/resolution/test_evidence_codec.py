from __future__ import annotations

from dataclasses import replace

import pytest

from tests.resolution.helpers import (
    CONDITION_ID,
    DECLARED_SOURCE,
    MARKET_DATE,
    MARKET_ID,
    MARKET_TIMEZONE,
    NOW,
    StaticGammaTransport,
    gamma_payload,
)
from weatherbot.domain import (
    AccountOpened,
    DuplicateEventConflict,
    EventId,
    LedgerState,
    MarketResolutionEvidenceRecorded,
    Money,
    apply_event,
)
from weatherbot.markets import ConditionId
from weatherbot.persistence.codec import decode_event, encode_event
from weatherbot.resolution import (
    GammaResolutionSource,
    ResolutionContext,
    bucket_from_key,
)


def context() -> ResolutionContext:
    return ResolutionContext(
        market_id=MARKET_ID,
        condition_id=ConditionId(CONDITION_ID),
        market_date=MARKET_DATE,
        market_timezone=MARKET_TIMEZONE,
        bucket=bucket_from_key("F:62:63"),
        declared_resolution_source=DECLARED_SOURCE,
    )


def evidence_event(*, yes: str = "1", no: str = "0") -> MarketResolutionEvidenceRecorded:
    result = GammaResolutionSource(
        StaticGammaTransport(gamma_payload(yes=yes, no=no))
    ).poll(context(), checked_at=NOW)
    assert result.evidence is not None
    return MarketResolutionEvidenceRecorded(
        event_id=EventId(f"evidence-{yes}-{no}"),
        occurred_at=NOW,
        evidence=result.evidence,
    )


def opened_state() -> LedgerState:
    return apply_event(
        LedgerState.empty(),
        AccountOpened(
            event_id=EventId("account-opened-for-evidence"),
            occurred_at=NOW,
            initial_cash=Money.zero(),
        ),
    )


def test_resolution_evidence_codec_round_trip_is_canonical() -> None:
    event = evidence_event()
    encoded = encode_event(event)
    assert encoded.event_type == "market_resolution_evidence_recorded"
    assert encoded.market_id == str(MARKET_ID)
    assert decode_event(encoded.payload_json) == event
    assert encode_event(decode_event(encoded.payload_json)).payload_json == encoded.payload_json


def test_conflicting_evidence_for_one_market_fails_closed() -> None:
    first = evidence_event(yes="1", no="0")
    conflicting = evidence_event(yes="0", no="1")
    state = apply_event(opened_state(), first)
    with pytest.raises(DuplicateEventConflict, match="evidence changed"):
        apply_event(state, conflicting)


def test_same_evidence_with_new_event_id_is_idempotent_at_market_level() -> None:
    first = evidence_event()
    duplicate = replace(first, event_id=EventId("same-evidence-new-delivery"))
    state = apply_event(opened_state(), first)
    next_state = apply_event(state, duplicate)
    assert next_state.resolution_evidence[MARKET_ID] == first.evidence
    assert len(next_state.event_fingerprints) == 3
