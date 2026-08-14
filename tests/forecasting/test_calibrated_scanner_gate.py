from __future__ import annotations

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


@pytest.mark.parametrize("mode", (ExecutionMode.PAPER, ExecutionMode.LIVE))
def test_execution_strategy_scans_are_disabled_before_network(
    monkeypatch: pytest.MonkeyPatch,
    mode: ExecutionMode,
) -> None:
    _forbid_network(monkeypatch)

    def forbidden_loader(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("disabled execution modes must not load calibration")

    monkeypatch.setattr(bot_v3, "load_calibrated_probability_runtime", forbidden_loader)

    new_trades, errors = bot_v3.scan_and_trade(_context(mode))

    assert new_trades == 0
    assert len(errors) == 1
    assert f"{mode.value.upper()} strategy scanning is disabled" in errors[0]


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


def test_scanner_climate_regions_match_calibration_market_contract() -> None:
    calibration = {market.city: market.climate_region for market in DEFAULT_MARKETS}
    scanner = {city: str(details["climate_region"]) for city, details in bot_v3.LOCATIONS.items()}

    assert scanner == calibration
