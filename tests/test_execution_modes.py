from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from execution_modes import (
    ExecutionContext,
    ExecutionMode,
    LiveExecutionBlocked,
    ModeConfigurationError,
    parse_mode,
    require_live,
    resolve_execution_context,
    run_live_operation,
)
from weatherbot.paper import PaperRuntimeConfig


@pytest.mark.parametrize("value", [None, "", "disabled", "automatic", 1])
def test_invalid_modes_fail_closed(value: object) -> None:
    with pytest.raises(ModeConfigurationError):
        parse_mode(value, source="test mode")


def test_research_is_safe_default_from_config() -> None:
    context = resolve_execution_context(
        configured_mode="research", cli_mode=None, confirm_live=False
    )
    assert context.mode is ExecutionMode.RESEARCH
    assert not context.requires_wallet
    assert context.label == "RESEARCH"


def test_cli_can_safely_downgrade_live_configuration() -> None:
    context = resolve_execution_context(
        configured_mode="live", cli_mode="research", confirm_live=False
    )
    assert context.mode is ExecutionMode.RESEARCH


@pytest.mark.parametrize(
    ("configured_mode", "cli_mode", "confirm_live"),
    [
        ("research", "live", True),
        ("live", None, True),
        ("live", "live", False),
        ("paper", "paper", True),
    ],
)
def test_live_mode_requires_all_independent_gates(
    configured_mode: str, cli_mode: str | None, confirm_live: bool
) -> None:
    with pytest.raises(ModeConfigurationError):
        resolve_execution_context(
            configured_mode=configured_mode,
            cli_mode=cli_mode,
            confirm_live=confirm_live,
        )


def test_live_mode_requires_config_cli_and_confirmation() -> None:
    context = resolve_execution_context(configured_mode="live", cli_mode="live", confirm_live=True)
    assert context.mode is ExecutionMode.LIVE
    assert context.live_confirmed
    assert context.requires_wallet


@pytest.mark.parametrize("mode", [ExecutionMode.RESEARCH, ExecutionMode.PAPER])
def test_non_live_gate_never_calls_live_callback(mode: ExecutionMode) -> None:
    context = ExecutionContext(mode=mode, configured_mode=mode)
    calls: list[str] = []

    with pytest.raises(LiveExecutionBlocked):
        run_live_operation(
            context,
            operation="test live call",
            callback=lambda: calls.append("live"),
        )

    assert calls == []
    with pytest.raises(LiveExecutionBlocked):
        require_live(context, operation="test live call")


def test_public_status_runs_without_wallet_credentials() -> None:
    environment = os.environ.copy()
    environment.pop("PK", None)
    environment.pop("WALLET", None)
    completed = subprocess.run(
        [sys.executable, "bot_v3.py", "status"],
        cwd=Path.cwd(),
        env=environment,
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert completed.returncode == 0, completed.stderr
    assert "Hermes public producer" in completed.stdout
    assert "strategy:" in completed.stdout
    assert "policy fingerprint:" in completed.stdout
    assert "signal log:" in completed.stdout
    assert "Wallet" not in completed.stdout


def test_paper_status_uses_isolated_ledger_without_wallet_credentials(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    import bot_v3_legacy as legacy
    from weatherbot.paper import cli as paper_cli

    environment_runtime = PaperRuntimeConfig.from_mapping(
        {
            "paper_ledger_path": str(tmp_path / "paper.sqlite3"),
            "paper_archive_directory": str(tmp_path / "archive"),
        },
        base_dir=tmp_path,
    )
    monkeypatch.setattr(legacy, "PAPER_RUNTIME", environment_runtime)
    monkeypatch.delenv("PK", raising=False)
    monkeypatch.delenv("WALLET", raising=False)

    assert paper_cli.show_status() == 0
    output = capsys.readouterr().out

    assert "Hermes internal PAPER R&D" in output
    assert f"ledger: {environment_runtime.ledger_path}" in output
    assert "starting cash:" in output
    assert "available cash:" in output
    assert "exposure:" in output
    assert "realized P/L:" in output
    assert "unrealized P/L:" in output
    assert "open positions:" in output
    assert not environment_runtime.ledger_path.exists()


@pytest.mark.parametrize("command", ["cancel", "resolve", "paper-reset"])
def test_public_cli_rejects_execution_and_paper_admin_commands(command: str) -> None:
    completed = subprocess.run(
        [sys.executable, "bot_v3.py", command],
        cwd=Path.cwd(),
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert completed.returncode == 2
    assert "invalid choice" in completed.stderr


def test_public_cli_has_no_execution_mode_flag() -> None:
    completed = subprocess.run(
        [sys.executable, "bot_v3.py", "status", "--mode", "research"],
        cwd=Path.cwd(),
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert completed.returncode == 2
    assert "unrecognized arguments" in completed.stderr


def test_paper_reset_requires_explicit_confirmation_before_history_mutation() -> None:
    environment = os.environ.copy()
    environment.pop("PK", None)
    environment.pop("WALLET", None)
    completed = subprocess.run(
        [sys.executable, "-m", "weatherbot.paper", "reset"],
        cwd=Path.cwd(),
        env=environment,
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert completed.returncode == 2
    assert "reset requires --confirm-reset" in completed.stderr


def test_public_entrypoint_is_only_the_signal_producer() -> None:
    source = Path("bot_v3.py").read_text(encoding="utf-8")

    assert "from weatherbot.producer.cli import main" in source
    for forbidden in (
        "bot_v3_legacy",
        "ExecutionMode",
        "PAPER_RUNTIME",
        "place_buy_order",
        "cancel_all_orders",
        "PK",
        "WALLET",
    ):
        assert forbidden not in source


def test_internal_paper_cli_owns_paper_admin_path() -> None:
    public_source = Path("bot_v3.py").read_text(encoding="utf-8")
    paper_source = Path("weatherbot/paper/cli.py").read_text(encoding="utf-8")

    assert "PAPER_RUNTIME" not in public_source
    assert 'choices=("scan", "run", "status", "resolve", "reset")' in paper_source
    assert "_legacy.PAPER_RUNTIME" in paper_source
    assert "run_resolution_monitor_cycle" in paper_source
    assert "reset_paper_runtime" in paper_source
