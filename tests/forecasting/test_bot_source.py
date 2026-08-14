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
