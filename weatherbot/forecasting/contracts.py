"""Dependency-light constants shared by calibration build and runtime code."""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

OBSERVATION_CONTRACT_ID = (
    "polymarket:wunderground:airport-daily-high:whole-degree-f:finalized-history:v1"
)
CALIBRATION_LEAD_DAYS: tuple[int, ...] = (0, 1, 2)
CALIBRATION_RUN_CYCLE_HOUR_UTC = 18
CALIBRATION_DECISION_LOCAL_TIME = time(hour=0, minute=15)
CALIBRATION_DECISION_WINDOW = timedelta(minutes=10)


def calibration_decision_day(*, target_date: date, lead_days: int) -> date:
    """Return the market-local decision date for one calibrated target/lead pair."""
    if lead_days not in CALIBRATION_LEAD_DAYS:
        raise ValueError(
            f"lead_days={lead_days} is outside calibrated lead set {CALIBRATION_LEAD_DAYS}"
        )
    return target_date - timedelta(days=lead_days)


def expected_calibration_model_run(*, target_date: date, lead_days: int) -> datetime:
    """Return the ECMWF run identity frozen by the calibration sampling policy."""
    decision_day = calibration_decision_day(target_date=target_date, lead_days=lead_days)
    return datetime.combine(
        decision_day - timedelta(days=1),
        time(hour=CALIBRATION_RUN_CYCLE_HOUR_UTC),
        UTC,
    )


def calibration_runtime_window(
    *, target_date: date, market_timezone: str, lead_days: int
) -> tuple[datetime, datetime]:
    """Return the narrow UTC interval in which runtime may evaluate this forecast vintage."""
    decision_day = calibration_decision_day(target_date=target_date, lead_days=lead_days)
    timezone = ZoneInfo(market_timezone)
    start = datetime.combine(
        decision_day,
        CALIBRATION_DECISION_LOCAL_TIME,
        timezone,
    ).astimezone(UTC)
    return start, start + CALIBRATION_DECISION_WINDOW
