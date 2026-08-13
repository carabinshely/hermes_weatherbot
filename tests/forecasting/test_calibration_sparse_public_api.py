from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path

from weatherbot.forecasting.calibration_sparse_sweep import (
    load_unavailable_run_registry,
    missing_horizons,
)

_EVIDENCE = Path("tests/fixtures/forecasting/open_meteo_unavailable_runs_2026-08-13.json")


def test_missing_horizons_is_public_sparse_gap_contract() -> None:
    registry = load_unavailable_run_registry(_EVIDENCE)
    run = datetime(2026, 6, 10, 18, tzinfo=UTC)

    assert registry[run].run_initialized_at_utc == run
    assert set(missing_horizons(date(2026, 6, 10), registry)) == {0}
