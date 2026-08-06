from __future__ import annotations

from pathlib import Path


def test_scanner_keeps_forecast_and_observation_separate() -> None:
    source = Path("bot_v3.py").read_text(encoding="utf-8")

    assert "WeatherInputSnapshot" in source
    assert "weathersnap.signal_temperature_f" in source
    assert '"forecast_temperature_f"' in source
    assert '"observation_temperature_f"' in source
    assert "best = metar" not in source
    assert 'best_source = "metar"' not in source
