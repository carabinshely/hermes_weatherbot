from __future__ import annotations

from pathlib import Path


def test_scanner_keeps_forecast_and_observation_separate() -> None:
    bot_source = Path("bot_v3.py").read_text(encoding="utf-8")
    runtime_source = Path("weatherbot/forecasting/runtime.py").read_text(encoding="utf-8")
    model_source = Path("weatherbot/forecasting/model.py").read_text(encoding="utf-8")

    assert "WeatherInputSnapshot" in runtime_source
    assert "weather.signal_temperature_f" in runtime_source
    assert "weather.signal_temperature_f" in bot_source
    assert "**weather_metadata" in bot_source
    assert '"forecast_temperature_f"' in model_source
    assert '"observation_temperature_f"' in model_source
    assert "best = metar" not in bot_source
    assert 'best_source = "metar"' not in bot_source


def test_public_scanner_has_no_fixed_sigma_probability_path() -> None:
    bot_source = Path("bot_v3.py").read_text(encoding="utf-8")

    assert "SIGMA_F" not in bot_source
    assert "get_sigma(" not in bot_source
    assert "sigma=2.0" not in bot_source
    assert "bot-v3-normal-cdf-sigma-v1" not in bot_source
    assert '"true_prob"' not in bot_source
    assert "load_calibrated_probability_runtime" in bot_source
    assert "model_probability" in bot_source
    assert "climate_region" in bot_source


def test_precalibration_scanner_is_quarantined_from_public_entrypoint() -> None:
    bot_source = Path("bot_v3.py").read_text(encoding="utf-8")

    assert "import bot_v3_legacy as _legacy" in bot_source
    assert "_legacy.scan_and_trade(" not in bot_source
    assert "_legacy.run_loop(" not in bot_source


def test_public_scanner_does_not_reexport_quarantined_helpers() -> None:
    bot_source = Path("bot_v3.py").read_text(encoding="utf-8")

    assert "from bot_v3_legacy import *" not in bot_source
    assert "count=len(CALIBRATION_LEAD_DAYS)" in bot_source
    assert "zip(" in bot_source and "CALIBRATION_LEAD_DAYS, dates, strict=True" in bot_source
    assert "persist_research_signal(signal)" in bot_source
    assert "_ = signal" not in bot_source


def test_scanner_gates_network_work_to_calibrated_decision_window() -> None:
    source = Path("bot_v3.py").read_text(encoding="utf-8")

    gate = source.index("calibration_runtime_window(")
    network = source.index("_legacy.get_forecast_snapshot(")
    assert gate < network
    assert "decision_start <= now < decision_end" in source
    assert "CALIBRATION_DECISION_WINDOW.total_seconds()" in source


def test_candidate_runtime_calibration_rejections_are_local() -> None:
    source = Path("bot_v3.py").read_text(encoding="utf-8")

    assert "except (CalibrationError, CalibrationRuntimeError) as exc:" in source
    assert "calibration rejected candidate" in source


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
