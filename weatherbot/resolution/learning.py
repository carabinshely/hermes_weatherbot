"""Learning filters joining verified settlement and exact observations."""

from __future__ import annotations

from weatherbot.domain import LedgerState
from weatherbot.domain.resolution import MarketResolutionEvidence
from weatherbot.resolution.observations import (
    VerifiedLearningOutcome,
    eligible_learning_outcomes,
)


def eligible_resolution_evidence(
    state: LedgerState,
) -> tuple[MarketResolutionEvidence, ...]:
    """Return settlement evidence only when exact observation evidence also exists."""
    return tuple(outcome.settlement for outcome in eligible_learning_outcomes(state))


__all__ = [
    "VerifiedLearningOutcome",
    "eligible_learning_outcomes",
    "eligible_resolution_evidence",
]
