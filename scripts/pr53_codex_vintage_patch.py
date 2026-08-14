from __future__ import annotations

from pathlib import Path


def replace_once(path: str, old: str, new: str) -> None:
    file_path = Path(path)
    source = file_path.read_text(encoding="utf-8")
    if old not in source:
        raise SystemExit(f"patch anchor not found in {path}: {old[:120]!r}")
    file_path.write_text(source.replace(old, new, 1), encoding="utf-8")


Path("weatherbot/forecasting/contracts.py").write_text(
    '''"""Dependency-light constants shared by calibration build and runtime code."""

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
        raise ValueError(f"lead_days={lead_days} is outside calibrated lead set {CALIBRATION_LEAD_DAYS}")
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
''',
    encoding="utf-8",
)

replace_once(
    "weatherbot/forecasting/archive.py",
    "from weatherbot.forecasting.calibration import CalibrationError\n",
    "from weatherbot.forecasting.calibration import CalibrationError\n"
    "from weatherbot.forecasting.contracts import (\n"
    "    CALIBRATION_DECISION_LOCAL_TIME,\n"
    "    CALIBRATION_LEAD_DAYS,\n"
    "    CALIBRATION_RUN_CYCLE_HOUR_UTC,\n"
    ")\n",
)
replace_once(
    "weatherbot/forecasting/archive.py",
    "_DEFAULT_HORIZONS = (0, 1, 2)\n",
    "_DEFAULT_HORIZONS = CALIBRATION_LEAD_DAYS\n",
)
replace_once(
    "weatherbot/forecasting/archive.py",
    "    run_cycle_hour_utc: int = 18\n    decision_local_time: time = time(hour=0, minute=15)\n",
    "    run_cycle_hour_utc: int = CALIBRATION_RUN_CYCLE_HOUR_UTC\n"
    "    decision_local_time: time = CALIBRATION_DECISION_LOCAL_TIME\n",
)

replace_once(
    "weatherbot/forecasting/runtime.py",
    "from weatherbot.forecasting.contracts import (\n    CALIBRATION_LEAD_DAYS,\n    OBSERVATION_CONTRACT_ID,\n)\n",
    "from weatherbot.forecasting.contracts import (\n"
    "    CALIBRATION_LEAD_DAYS,\n"
    "    OBSERVATION_CONTRACT_ID,\n"
    "    calibration_runtime_window,\n"
    "    expected_calibration_model_run,\n"
    ")\n",
)
replace_once(
    "weatherbot/forecasting/runtime.py",
    '''        if lead_days not in CALIBRATION_LEAD_DAYS:
            raise CalibrationCompatibilityError(
                f"lead_days={lead_days} is outside calibrated lead set "
                f"{CALIBRATION_LEAD_DAYS}"
            )
        estimate = self.model.probability(
''',
    '''        if lead_days not in CALIBRATION_LEAD_DAYS:
            raise CalibrationCompatibilityError(
                f"lead_days={lead_days} is outside calibrated lead set "
                f"{CALIBRATION_LEAD_DAYS}"
            )
        try:
            decision_start, decision_end = calibration_runtime_window(
                target_date=weather.forecast.market_date,
                market_timezone=weather.forecast.market_timezone,
                lead_days=lead_days,
            )
            expected_run = expected_calibration_model_run(
                target_date=weather.forecast.market_date,
                lead_days=lead_days,
            )
        except (ValueError, KeyError) as exc:
            raise CalibrationCompatibilityError(
                "forecast does not satisfy the calibrated decision-time contract"
            ) from exc
        retrieved = weather.forecast.retrieved_at_utc
        if not decision_start <= retrieved < decision_end:
            raise CalibrationCompatibilityError(
                "forecast retrieval is outside the calibrated market-local decision window"
            )
        model_run = weather.forecast.model_run_initialized_at_utc
        if model_run is not None and model_run != expected_run:
            raise CalibrationCompatibilityError(
                "forecast model-run initialization differs from the calibrated 18Z vintage"
            )
        estimate = self.model.probability(
''',
)

replace_once(
    "bot_v3.py",
    "from weatherbot.forecasting.contracts import CALIBRATION_LEAD_DAYS\n",
    "from weatherbot.forecasting.contracts import (\n"
    "    CALIBRATION_DECISION_WINDOW,\n"
    "    CALIBRATION_LEAD_DAYS,\n"
    "    calibration_runtime_window,\n"
    ")\n",
)
replace_once(
    "bot_v3.py",
    '''        dates = tuple(
            candidate.isoformat()
            for candidate in calendar.candidate_dates(now, count=len(CALIBRATION_LEAD_DAYS))
        )

        try:
            started = time.time()
''',
    '''        dates = tuple(
            candidate.isoformat()
            for candidate in calendar.candidate_dates(now, count=len(CALIBRATION_LEAD_DAYS))
        )
        try:
            decision_start, decision_end = calibration_runtime_window(
                target_date=datetime.strptime(dates[0], "%Y-%m-%d").date(),
                market_timezone=market_timezone,
                lead_days=CALIBRATION_LEAD_DAYS[0],
            )
        except (ValueError, IndexError) as exc:
            errors.append(f"{loc['name']}: invalid calibration decision window: {exc}")
            print("invalid decision window")
            continue
        if not decision_start <= now < decision_end:
            _legacy.skip(
                f"{loc['name']}: outside calibrated 00:15 market-local decision window"
            )
            print("outside calibrated decision window")
            continue

        try:
            started = time.time()
''',
)
replace_once(
    "bot_v3.py",
    "    last_full_scan = 0.0\n    while True:\n        now_ts = time.time()\n        if now_ts - last_full_scan >= SCAN_INTERVAL:\n",
    "    last_full_scan = 0.0\n"
    "    scan_probe_interval = CALIBRATION_DECISION_WINDOW.total_seconds()\n"
    "    while True:\n"
    "        now_ts = time.time()\n"
    "        if now_ts - last_full_scan >= scan_probe_interval:\n",
)

runtime_test = Path("tests/forecasting/test_calibration_runtime.py")
runtime_source = runtime_test.read_text(encoding="utf-8")
runtime_source = runtime_source.replace(
    "from datetime import UTC, date, datetime\n",
    "from datetime import UTC, date, datetime, time, timedelta\n",
    1,
)
runtime_source = runtime_source.replace(
    "from weatherbot.forecasting.model import ForecastSource\n",
    "from weatherbot.forecasting.model import (\n"
    "    DailyHighForecast,\n"
    "    ForecastSource,\n"
    "    WeatherInputSnapshot,\n"
    ")\n",
    1,
)
runtime_source = runtime_source.replace(
    "    CALIBRATION_LEAD_DAYS,\n    OBSERVATION_CONTRACT_ID,\n",
    "    CALIBRATION_LEAD_DAYS,\n"
    "    OBSERVATION_CONTRACT_ID,\n"
    "    expected_calibration_model_run,\n",
    1,
)
runtime_source = runtime_source.replace(
    "    weather = weather_snapshot()\n",
    "    weather = weather_snapshot(issued_at=datetime(2026, 8, 6, 4, 16, tzinfo=UTC))\n",
)
runtime_source += '''


def test_runtime_rejects_forecast_outside_calibrated_decision_window(tmp_path: Path) -> None:
    _approved_repository(tmp_path)
    runtime = load_calibrated_probability_runtime(repository_root=tmp_path)
    weather = weather_snapshot(issued_at=datetime(2026, 8, 6, 14, 0, tzinfo=UTC))
    bucket = TemperatureBucket.bounded(86, 86, TemperatureUnit.FAHRENHEIT)

    with pytest.raises(CalibrationCompatibilityError, match="decision window"):
        runtime.probability(
            city="chicago",
            climate_region="ohio_valley",
            lead_days=0,
            weather=weather,
            bucket=bucket,
        )


def test_runtime_rejects_mismatched_model_run_vintage(tmp_path: Path) -> None:
    _approved_repository(tmp_path)
    runtime = load_calibrated_probability_runtime(repository_root=tmp_path)
    base = weather_snapshot(issued_at=datetime(2026, 8, 6, 4, 16, tzinfo=UTC))
    forecast = base.forecast
    mismatched = DailyHighForecast(
        temperature_f=forecast.temperature_f,
        market_date=forecast.market_date,
        market_timezone=forecast.market_timezone,
        source=forecast.source,
        snapshot_issued_at_utc=forecast.snapshot_issued_at_utc,
        valid_from_utc=forecast.valid_from_utc,
        valid_until_utc=forecast.valid_until_utc,
        retrieved_at_utc=forecast.retrieved_at_utc,
        model_run_initialized_at_utc=expected_calibration_model_run(
            target_date=forecast.market_date,
            lead_days=0,
        ) + timedelta(hours=6),
    )
    weather = WeatherInputSnapshot(
        forecast=mismatched,
        observation=None,
        assembled_at_utc=base.assembled_at_utc,
    )
    bucket = TemperatureBucket.bounded(86, 86, TemperatureUnit.FAHRENHEIT)

    with pytest.raises(CalibrationCompatibilityError, match="18Z vintage"):
        runtime.probability(
            city="chicago",
            climate_region="ohio_valley",
            lead_days=0,
            weather=weather,
            bucket=bucket,
        )
'''
runtime_test.write_text(runtime_source, encoding="utf-8")

bot_source_test = Path("tests/forecasting/test_bot_source.py")
bot_source = bot_source_test.read_text(encoding="utf-8")
bot_source += '''


def test_scanner_gates_network_work_to_calibrated_decision_window() -> None:
    source = Path("bot_v3.py").read_text(encoding="utf-8")

    gate = source.index("calibration_runtime_window(")
    network = source.index("_legacy.get_forecast_snapshot(")
    assert gate < network
    assert "decision_start <= now < decision_end" in source
    assert "CALIBRATION_DECISION_WINDOW.total_seconds()" in source
'''
bot_source_test.write_text(bot_source, encoding="utf-8")

archive_test = Path("tests/forecasting/test_archive.py")
archive_source = archive_test.read_text(encoding="utf-8")
archive_source += '''


def test_default_archive_policy_matches_dependency_light_runtime_contract() -> None:
    from weatherbot.forecasting.contracts import (
        CALIBRATION_DECISION_LOCAL_TIME,
        CALIBRATION_LEAD_DAYS,
        CALIBRATION_RUN_CYCLE_HOUR_UTC,
    )

    policy = CalibrationForecastSamplingPolicy()
    assert policy.run_cycle_hour_utc == CALIBRATION_RUN_CYCLE_HOUR_UTC
    assert policy.decision_local_time == CALIBRATION_DECISION_LOCAL_TIME
    assert policy.horizons == CALIBRATION_LEAD_DAYS
'''
archive_test.write_text(archive_source, encoding="utf-8")

calibration_docs = Path("docs/forecast-calibration.md")
docs_source = calibration_docs.read_text(encoding="utf-8")
docs_source += '''

### Runtime forecast-vintage gate

The calibrated residuals are tied to the frozen market-local decision policy: D+0/D+1/D+2 are sampled from the previous UTC calendar day's 18Z ECMWF IFS 0.25° run at the 00:15 market-local decision point. Runtime therefore accepts probability generation only when the production forecast is retrieved in the narrow 00:15-00:25 market-local decision window for the corresponding decision day. The public scanner checks this window before weather or market network work, and the calibrated probability boundary checks the forecast retrieval timestamp independently. If model-run initialization metadata is available, it must also equal the expected previous-day 18Z run. A later continuously updated forecast cannot silently reuse the frozen residual distribution.

Continuous RESEARCH mode probes on the decision-window cadence so each U.S. timezone can be evaluated near its own market-local decision point; mechanical resolution monitoring continues between probes.
'''
calibration_docs.write_text(docs_source, encoding="utf-8")
