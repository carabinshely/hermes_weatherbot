from __future__ import annotations

from pathlib import Path


def test_scanner_keeps_forecast_and_observation_separate() -> None:
    source = Path("weatherbot/producer/sources.py").read_text(encoding="utf-8")
    runtime_source = Path("weatherbot/forecasting/runtime.py").read_text(encoding="utf-8")
    model_source = Path("weatherbot/forecasting/model.py").read_text(encoding="utf-8")

    assert "WeatherInputSnapshot" in runtime_source
    assert "weather.signal_temperature_f" in runtime_source
    assert "matching_observation" in source
    assert '"forecast_temperature_f"' in model_source
    assert '"observation_temperature_f"' in model_source
    assert "best = metar" not in source


def test_public_scanner_has_no_fixed_sigma_or_learning_probability_path() -> None:
    scanner = Path("weatherbot/producer/scanner.py").read_text(encoding="utf-8")
    service = Path("weatherbot/producer/service.py").read_text(encoding="utf-8")
    combined = scanner + service

    assert "SIGMA_F" not in combined
    assert "get_sigma(" not in combined
    assert "sigma=2.0" not in combined
    assert '"true_prob"' not in combined
    assert "calc_kelly" not in combined
    assert "get_adjusted_kelly" not in combined
    assert "get_adjusted_ev_floor" not in combined
    assert "model_probability" in service
    assert "calibration_runtime.probability(" in scanner


def test_precalibration_scanner_is_not_imported_by_public_entrypoint() -> None:
    bot_source = Path("bot_v3.py").read_text(encoding="utf-8")
    producer_sources = "\n".join(
        path.read_text(encoding="utf-8") for path in Path("weatherbot/producer").glob("*.py")
    )

    assert "bot_v3_legacy" not in bot_source
    assert "bot_v3_legacy" not in producer_sources
    assert "weatherbot.paper" not in producer_sources


def test_scanner_gates_network_work_to_calibrated_decision_window() -> None:
    source = Path("weatherbot/producer/scanner.py").read_text(encoding="utf-8")

    gate = source.index("calibration_runtime_window(")
    network = source.index("weather_fetcher(")
    assert gate < network
    assert "decision_start <= current < decision_end" in source


def test_candidate_runtime_calibration_rejections_are_local() -> None:
    source = Path("weatherbot/producer/scanner.py").read_text(encoding="utf-8")

    assert "except (CalibrationError, CalibrationRuntimeError) as exc:" in source
    assert "calibration rejected candidate" in source


def test_probability_provenance_flows_into_typed_signal() -> None:
    model = Path("weatherbot/producer/model.py").read_text(encoding="utf-8")
    service = Path("weatherbot/producer/service.py").read_text(encoding="utf-8")

    for field in (
        "model_version",
        "artifact_sha256",
        "calibration_fingerprint",
        "weather_fingerprint",
        "forecast_source",
        "calibration_group_key",
        "fallback_level",
        "distribution_type",
        "calibration_sample_count",
        "training_cutoff",
    ):
        assert field in model
        assert field in service
