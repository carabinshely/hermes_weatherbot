"""Learning filters for verified terminal market outcomes."""

from __future__ import annotations

from weatherbot.domain import LedgerState
from weatherbot.domain.resolution import MarketResolutionEvidence


def eligible_resolution_evidence(
    state: LedgerState,
) -> tuple[MarketResolutionEvidence, ...]:
    """Return only verified non-void evidence suitable for model updates."""
    return tuple(
        evidence
        for _, evidence in sorted(
            state.resolution_evidence.items(),
            key=lambda item: str(item[0]),
        )
        if evidence.learning_eligible
    )
