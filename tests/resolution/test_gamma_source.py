from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

import pytest

from tests.resolution.helpers import (
    CONDITION_ID,
    DECLARED_SOURCE,
    MARKET_DATE,
    MARKET_ID,
    MARKET_TIMEZONE,
    NO_TOKEN,
    NOW,
    YES_TOKEN,
    StaticGammaTransport,
    gamma_payload,
)
from weatherbot.domain import OutcomeId
from weatherbot.markets import ConditionId
from weatherbot.resolution import (
    GammaResolutionSource,
    ResolutionContext,
    ResolutionPollStatus,
    ResolutionSourceUnavailable,
    bucket_from_key,
)


def context(bucket_key: str = "F:62:63") -> ResolutionContext:
    return ResolutionContext(
        market_id=MARKET_ID,
        condition_id=ConditionId(CONDITION_ID),
        market_date=MARKET_DATE,
        market_timezone=MARKET_TIMEZONE,
        bucket=bucket_from_key(bucket_key),
        declared_resolution_source=DECLARED_SOURCE,
    )


@pytest.mark.parametrize(
    ("yes", "no", "winning_token"),
    [
        ("1", "0", YES_TOKEN),
        ("0", "1", NO_TOKEN),
    ],
)
def test_final_binary_payout_maps_to_explicit_outcome_tokens(
    yes: str,
    no: str,
    winning_token: str,
) -> None:
    transport = StaticGammaTransport(gamma_payload(yes=yes, no=no))
    result = GammaResolutionSource(transport).poll(context(), checked_at=NOW)
    assert result.status is ResolutionPollStatus.FINAL
    assert result.evidence is not None
    assert result.resolution is not None
    assert result.evidence.learning_eligible
    assert result.evidence.condition_id == CONDITION_ID
    assert result.evidence.market_date == MARKET_DATE
    assert result.evidence.market_timezone == MARKET_TIMEZONE
    assert result.evidence.declared_resolution_source == DECLARED_SOURCE
    assert result.resolution.payout_for(OutcomeId(winning_token)) == Decimal("1")
    assert transport.calls == [str(MARKET_ID)]


def test_half_payout_is_typed_as_void_and_not_learnable() -> None:
    result = GammaResolutionSource(StaticGammaTransport(gamma_payload(yes="0.5", no="0.5"))).poll(
        context(), checked_at=NOW
    )
    assert result.status is ResolutionPollStatus.VOID
    assert result.evidence is not None
    assert not result.evidence.learning_eligible


def test_pending_delayed_and_disputed_markets_do_not_emit_resolution() -> None:
    pending = GammaResolutionSource(
        StaticGammaTransport(gamma_payload(closed=False, status="proposed"))
    ).poll(context(), checked_at=NOW - timedelta(hours=3))
    assert pending.status is ResolutionPollStatus.PENDING
    assert pending.evidence is None

    delayed = GammaResolutionSource(
        StaticGammaTransport(gamma_payload(closed=False, status="proposed"))
    ).poll(context(), checked_at=NOW + timedelta(hours=3))
    assert delayed.status is ResolutionPollStatus.DELAYED
    assert delayed.evidence is None

    disputed = GammaResolutionSource(
        StaticGammaTransport(gamma_payload(closed=False, status="disputed"))
    ).poll(context(), checked_at=NOW)
    assert disputed.status is ResolutionPollStatus.DISPUTED
    assert disputed.evidence is None


@pytest.mark.parametrize(
    "payload",
    [
        gamma_payload(yes="0.7", no="0.3"),
        {**gamma_payload(), "conditionId": "0x" + "cd" * 32},
        {
            **gamma_payload(),
            "question": ("Will the highest temperature in Chicago be between 64-65°F on April 18?"),
        },
        {**gamma_payload(), "resolutionSource": ""},
        {**gamma_payload(), "closedTime": "2027-01-01T00:00:00Z"},
    ],
)
def test_malformed_or_conflicting_final_payloads_fail_closed(
    payload: dict[str, object],
) -> None:
    result = GammaResolutionSource(StaticGammaTransport(payload)).poll(
        context(),
        checked_at=NOW,
    )
    assert result.status is ResolutionPollStatus.MALFORMED
    assert result.evidence is None
    assert result.resolution is None


class UnavailableTransport:
    def get_market(self, market_id: str) -> dict[str, object]:
        raise ResolutionSourceUnavailable(f"offline for {market_id}")


def test_unavailable_source_is_nonterminal() -> None:
    result = GammaResolutionSource(UnavailableTransport()).poll(
        context(),
        checked_at=NOW,
    )
    assert result.status is ResolutionPollStatus.UNAVAILABLE
    assert result.evidence is None


@pytest.mark.parametrize(
    ("bucket_key", "question"),
    [
        (
            "F:-inf:53",
            "Will the highest temperature in Chicago be 53°F or below on April 18?",
        ),
        (
            "F:72:inf",
            "Will the highest temperature in Chicago be 72°F or higher on April 18?",
        ),
        (
            "F:72:72",
            "Will the highest temperature in Chicago be 72°F on April 18?",
        ),
    ],
)
def test_tail_and_exact_degree_contracts_are_reused_at_resolution(
    bucket_key: str,
    question: str,
) -> None:
    result = GammaResolutionSource(StaticGammaTransport(gamma_payload(question=question))).poll(
        context(bucket_key), checked_at=NOW
    )
    assert result.status is ResolutionPollStatus.FINAL
