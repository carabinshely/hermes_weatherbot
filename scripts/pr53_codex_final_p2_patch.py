from __future__ import annotations

from pathlib import Path


def replace_once(path: str, old: str, new: str) -> None:
    file_path = Path(path)
    source = file_path.read_text(encoding="utf-8")
    if old not in source:
        raise SystemExit(f"patch anchor not found in {path}: {old[:120]!r}")
    file_path.write_text(source.replace(old, new, 1), encoding="utf-8")


replace_once(
    "bot_v3.py",
    '''                "forecast_temp": forecast_temp,
                "market_date": market_date,
''',
    '''                "forecast_temp": forecast_temp,
                "city_slug": city_slug,
                "climate_region": str(loc["climate_region"]),
                "lead_days": horizon_index,
                "market_date": market_date,
''',
)

replace_once(
    "bot_v3.py",
    '''    last_full_scan = 0.0
    scan_probe_interval = CALIBRATION_DECISION_WINDOW.total_seconds()
    while True:
        now_ts = time.time()
        if now_ts - last_full_scan >= scan_probe_interval:
            scan_and_trade(context)
            last_full_scan = time.time()
            continue

        try:
            run_resolution_monitor_cycle()
        except Exception as exc:
            _legacy.warn(f"Resolution monitor error: {exc}")
        time.sleep(_legacy.MONITOR_INTERVAL)
''',
    '''    last_scan_probe = 0.0
    last_resolution = 0.0
    scan_probe_interval = min(
        60.0,
        CALIBRATION_DECISION_WINDOW.total_seconds() / 4.0,
    )
    resolution_interval = max(1.0, float(_legacy.MONITOR_INTERVAL))
    sleep_interval = min(scan_probe_interval, resolution_interval)

    while True:
        now_ts = time.time()
        if now_ts - last_scan_probe >= scan_probe_interval:
            scan_and_trade(context)
            last_scan_probe = now_ts

        if now_ts - last_resolution >= resolution_interval:
            try:
                run_resolution_monitor_cycle()
            except Exception as exc:
                _legacy.warn(f"Resolution monitor error: {exc}")
            last_resolution = now_ts

        time.sleep(sleep_interval)
''',
)

source_test = Path("tests/forecasting/test_bot_source.py")
source = source_test.read_text(encoding="utf-8")
source += '''


def test_persisted_signal_includes_probability_input_dimensions() -> None:
    source = Path("bot_v3.py").read_text(encoding="utf-8")

    assert '"city_slug": city_slug' in source
    assert '"climate_region": str(loc["climate_region"])' in source
    assert '"lead_days": horizon_index' in source


def test_continuous_probe_interval_is_shorter_than_decision_window() -> None:
    source = Path("bot_v3.py").read_text(encoding="utf-8")

    assert "CALIBRATION_DECISION_WINDOW.total_seconds() / 4.0" in source
    assert "scan_probe_interval = min(" in source
    assert "sleep_interval = min(scan_probe_interval, resolution_interval)" in source
'''
source_test.write_text(source, encoding="utf-8")

gate_test = Path("tests/forecasting/test_calibrated_scanner_gate.py")
gate_source = gate_test.read_text(encoding="utf-8")
gate_source = gate_source.replace(
    '''        "training_cutoff": "2026-08-10",
    }
''',
    '''        "training_cutoff": "2026-08-10",
        "city_slug": "chicago",
        "climate_region": "ohio_valley",
        "lead_days": 0,
    }
''',
    1,
)
gate_test.write_text(gate_source, encoding="utf-8")

docs = Path("docs/forecast-calibration.md")
docs_source = docs.read_text(encoding="utf-8")
docs_source += '''

Continuous RESEARCH does not sleep for an entire decision window between probes. It probes at most once per minute (and therefore at least four times faster than the 10-minute eligibility window) while scheduling resolution monitoring independently. This prevents monitor sleep/overhead from skipping a city's daily decision window.

Durable RESEARCH records also preserve the probability-call dimensions `city_slug`, `climate_region`, and `lead_days` in addition to the selected calibration group/fallback metadata. This keeps historical probabilities reproducible even if location-to-region mappings change later.
'''
docs.write_text(docs_source, encoding="utf-8")
