"""Dependency-light constants shared by calibration build and runtime code."""

from __future__ import annotations

OBSERVATION_CONTRACT_ID = (
    "polymarket:wunderground:airport-daily-high:whole-degree-f:finalized-history:v1"
)
CALIBRATION_LEAD_DAYS: tuple[int, ...] = (0, 1, 2)
