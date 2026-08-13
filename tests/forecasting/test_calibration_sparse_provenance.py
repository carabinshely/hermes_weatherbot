from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from weatherbot.forecasting.calibration_sparse_sweep import load_unavailable_run_registry

_EVIDENCE = Path("tests/fixtures/forecasting/open_meteo_unavailable_runs_2026-08-13.json")


def test_unavailable_run_timestamp_matches_registry_key() -> None:
    expected = datetime(2026, 6, 10, 18, tzinfo=UTC)
    registry = load_unavailable_run_registry(_EVIDENCE)

    assert registry[expected].run_initialized_at_utc == expected
