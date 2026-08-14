from __future__ import annotations

import json
from pathlib import Path

import pytest

import bot_v3
import bot_v3_legacy
from execution_modes import ExecutionContext, ExecutionMode
from weatherbot.forecasting import CalibrationUnavailable
from weatherbot.forecasting.calibration_build import DEFAULT_MARKETS


def _context(mode: ExecutionMode) -> ExecutionContext:
    return ExecutionContext(
        mode=mode,
        configured_mode=mode,
        live_confirmed=mode is ExecutionMode.LIVE,
    )


def _forbid_network(monkeypatch: pytest.MonkeyPatch) -> None:
    def forbidden(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("strategy gate should run before weather/market network work")

    monkeypatch.setattr(bot_v3.requests, "get", forbidden)


def test_missing_calibration_fails_research_scan_before_network(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _forbid_network(monkeypatch)

    def unavailable(*_args: object, **_kwargs: object) -> object:
        raise CalibrationUnavailable("fixture has no accepted artifact")

    monkeypatch.setattr(bot_v3, "load_calibrated_probability_runtime", unavailable)

    new_trades, errors = bot_v3.scan_and_trade(_context(ExecutionMode.RESEARCH))

    assert new_trades == 0
    assert len(errors) == 1
    assert "failed closed" in errors[0]
    assert "fixture has no accepted artifact" in errors[0]


def test_paper_recovers_before_missing_calibration_and_then_stops_before_network(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _forbid_network(monkeypatch)
    events: list[str] = []

    def recover(*_args: object, **_kwargs: object) -> object:
        events.append("recover")
        return object()

    def unavailable(*_args: object, **_kwargs: object) -> object:
        events.append("calibration")
        raise CalibrationUnavailable("fixture has no accepted artifact")

    monkeypatch.setattr(bot_v3, "recover_paper_runtime", recover)
    monkeypatch.setattr(bot_v3, "load_calibrated_probability_runtime", unavailable)

    new_trades, errors = bot_v3.scan_and_trade(_context(ExecutionMode.PAPER))

    assert new_trades == 0
    assert len(errors) == 1
    assert events == ["recover", "calibration"]
    assert "PAPER scan failed closed" in errors[0]


def test_live_strategy_scan_is_disabled_before_recovery_calibration_or_network(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _forbid_network(monkeypatch)

    def forbidden(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("LIVE must not recover PAPER or load calibration")

    monkeypatch.setattr(bot_v3, "recover_paper_runtime", forbidden)
    monkeypatch.setattr(bot_v3, "load_calibrated_probability_runtime", forbidden)

    new_trades, errors = bot_v3.scan_and_trade(_context(ExecutionMode.LIVE))

    assert new_trades == 0
    assert len(errors) == 1
    assert "LIVE strategy scanning remains disabled" in errors[0]


def test_quarantined_legacy_scanner_is_hard_disabled_before_network(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def forbidden(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("legacy scanner guard must run before network work")

    monkeypatch.setattr(bot_v3_legacy.requests, "get", forbidden)

    with pytest.raises(RuntimeError, match="legacy strategy scanning is disabled"):
        bot_v3_legacy.scan_and_trade(_context(ExecutionMode.RESEARCH))


def test_quarantined_legacy_cli_cannot_dispatch_scan(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    calls: list[ExecutionContext] = []

    def forbidden_scan(context: ExecutionContext) -> tuple[int, list[str]]:
        calls.append(context)
        return 0, []

    monkeypatch.setattr(bot_v3_legacy, "scan_and_trade", forbidden_scan)

    assert bot_v3_legacy.main(["scan", "--mode", "research"]) == 2
    assert calls == []
    assert "legacy strategy scanning is disabled" in capsys.readouterr().err


def test_quarantined_legacy_run_loop_is_disabled_before_live_approvals(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    def forbidden_approvals() -> None:
        calls.append("approvals")

    monkeypatch.setattr(bot_v3_legacy, "ensure_approvals", forbidden_approvals)

    with pytest.raises(RuntimeError, match="legacy strategy loop is disabled"):
        bot_v3_legacy.run_loop(_context(ExecutionMode.LIVE))

    assert calls == []


def test_scanner_climate_regions_match_calibration_market_contract() -> None:
    calibration = {market.city: market.climate_region for market in DEFAULT_MARKETS}
    scanner = {city: str(details["climate_region"]) for city, details in bot_v3.LOCATIONS.items()}

    assert scanner == calibration


def test_public_entrypoint_does_not_expose_quarantined_strategy_primitives() -> None:
    for name in (
        "SIGMA_F",
        "get_sigma",
        "bucket_prob",
        "ensure_approvals",
        "place_buy_order",
    ):
        assert not hasattr(bot_v3, name)


def test_research_signal_log_preserves_complete_calibration_provenance(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    log_path = tmp_path / "research-signals.jsonl"
    monkeypatch.setattr(bot_v3, "RESEARCH_SIGNAL_LOG", log_path)
    signal: dict[str, object] = {
        "model_probability": "0.427",
        "model_version": "issue12-v3-fixture",
        "artifact_sha256": "a" * 64,
        "forecast_source": "open_meteo_ecmwf_ifs025",
        "calibration_group_key": "source|open_meteo_ecmwf_ifs025",
        "fallback_level": "source",
        "distribution_type": "normal",
        "calibration_sample_count": 60,
        "training_cutoff": "2026-08-10",
        "city_slug": "chicago",
        "climate_region": "ohio_valley",
        "lead_days": 0,
    }

    bot_v3.persist_research_signal(signal)

    assert json.loads(log_path.read_text(encoding="utf-8")) == signal


@pytest.mark.parametrize("mode", (ExecutionMode.RESEARCH, ExecutionMode.PAPER))
def test_non_live_run_loop_retains_resolution_monitoring(
    monkeypatch: pytest.MonkeyPatch,
    mode: ExecutionMode,
) -> None:
    events: list[str] = []
    clock = iter((10000.0,))

    monkeypatch.setattr(bot_v3.time, "time", lambda: next(clock))

    def record_scan(context: ExecutionContext) -> tuple[int, list[str]]:
        events.append(f"scan:{context.mode.value}")
        return 0, []

    def record_resolution(*_args: object, **_kwargs: object) -> None:
        events.append("resolve")

    class StopLoop(Exception):
        pass

    def stop_after_monitor(_seconds: float) -> None:
        raise StopLoop

    monkeypatch.setattr(bot_v3, "scan_and_trade", record_scan)
    monkeypatch.setattr(bot_v3, "run_resolution_monitor_cycle", record_resolution)
    monkeypatch.setattr(bot_v3.time, "sleep", stop_after_monitor)

    with pytest.raises(StopLoop):
        bot_v3.run_loop(_context(mode))

    assert events == [f"scan:{mode.value}", "resolve"]


def test_scanner_has_one_shared_calibration_call_and_paper_bypasses_legacy_sizing() -> None:
    source = Path("bot_v3.py").read_text(encoding="utf-8")
    scanner = source[source.index("def scan_and_trade") : source.index("\n\ndef show_status")]

    assert scanner.count("calibration_runtime.probability(") == 1
    paper_start = scanner.index(
        "if context.mode is ExecutionMode.PAPER:", scanner.index("calibrated =")
    )
    submit = scanner.index("submit_scanner_candidate(", paper_start)
    research_sizing = scanner.index("preliminary_kelly =", paper_start)
    paper_segment = scanner[paper_start:research_sizing]

    assert paper_start < submit < research_sizing
    assert "continue" in paper_segment
    assert "_legacy.calc_kelly(" not in paper_segment
    assert "_legacy.get_adjusted_kelly(" not in paper_segment
    assert "_legacy.bet_size(" not in paper_segment
