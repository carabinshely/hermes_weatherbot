"""Runtime assembly for the public resolution worker."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from weatherbot.persistence import SQLiteEventStore
from weatherbot.resolution.context import StoredDecisionContextProvider
from weatherbot.resolution.gamma import (
    GammaResolutionSource,
    RequestsGammaResolutionTransport,
)
from weatherbot.resolution.model import ResolutionCycleReport
from weatherbot.resolution.worker import ResolutionWorker


def run_resolution_cycle(
    database_path: str | Path,
    *,
    timeout_seconds: float = 15.0,
) -> ResolutionCycleReport:
    path = Path(database_path)
    now = datetime.now(UTC)
    if not path.exists():
        return ResolutionCycleReport.empty(now)
    with SQLiteEventStore(path) as store:
        worker = ResolutionWorker(
            store=store,
            source=GammaResolutionSource(
                RequestsGammaResolutionTransport(timeout_seconds=timeout_seconds)
            ),
            context_provider=StoredDecisionContextProvider(),
        )
        return worker.run_once()
