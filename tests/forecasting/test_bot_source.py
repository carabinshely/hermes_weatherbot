from __future__ import annotations

from pathlib import Path


def test_scanner_keeps_forecast_and_observation_separate() -> None:
    bot_source = Path("bot_v3.py").read_text(encoding="utf-8")
    model_source = Path("weatherbot/forecasting/model.py").read_text(encoding="utf-8")

    assert "WeatherInputSnapshot" in bot_source
    assert "weathersnap.signal_temperature_f" in bot_source
    assert "**weather_metadata" in bot_source
    assert '"forecast_temperature_f"' in model_source
    assert '"observation_temperature_f"' in model_source
    assert "best = metar" not in bot_source
    assert 'best_source = "metar"' not in bot_source
