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


def test_research_status_runs_without_wallet_credentials() -> None:
    environment = os.environ.copy()
    environment.pop("PK", None)
    environment.pop("WALLET", None)
    completed = subprocess.run(
        [sys.executable, "bot_v3.py", "status", "--mode", "research"],
        cwd=Path.cwd(),
        env=environment,
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert completed.returncode == 0, completed.stderr
    assert "Execution mode: RESEARCH" in completed.stdout
    assert "Wallet access: disabled" in completed.stdout


def test_paper_status_uses_isolated_ledger_without_wallet_credentials(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    import bot_v3

    environment_runtime = PaperRuntimeConfig.from_mapping(
        {
            "paper_ledger_path": str(tmp_path / "paper.sqlite3"),
            "paper_archive_directory": str(tmp_path / "archive"),
        },
        base_dir=tmp_path,
    )
    monkeypatch.setattr(bot_v3, "PAPER_RUNTIME", environment_runtime)
    monkeypatch.delenv("PK", raising=False)
    monkeypatch.delenv("WALLET", raising=False)

    bot_v3.show_status(
        ExecutionContext(mode=ExecutionMode.PAPER, configured_mode=ExecutionMode.PAPER)
    )
    output = capsys.readouterr().out

    assert "Wallet access: disabled" in output
    assert "Paper ledger:" in output
    assert str(environment_runtime.ledger_path) in output
    assert "Starting cash:" in output
    assert "Available cash:" in output
    assert "Market value:" in output
    assert "Realized P/L:" in output
    assert "Unrealized P/L:" in output
    assert "Fees:" in output
    assert "Exposure:" in output
    assert "Drawdown:" in output
    assert not environment_runtime.ledger_path.exists()


def test_default_status_is_research_and_wallet_free() -> None:
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
    assert "Execution mode: RESEARCH" in completed.stdout
    assert "Wallet access: disabled" in completed.stdout


def test_non_live_cancel_is_blocked_before_live_client_access() -> None:
    environment = os.environ.copy()
    environment.pop("PK", None)
    environment.pop("WALLET", None)
    completed = subprocess.run(
        [sys.executable, "bot_v3.py", "cancel", "--mode", "research"],
        cwd=Path.cwd(),
        env=environment,
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert completed.returncode == 2
    assert "order cancellation is blocked in research mode" in completed.stderr


def test_paper_reset_requires_explicit_confirmation_before_history_mutation() -> None:
    environment = os.environ.copy()
    environment.pop("PK", None)
    environment.pop("WALLET", None)
    completed = subprocess.run(
        [sys.executable, "bot_v3.py", "paper-reset", "--mode", "paper"],
        cwd=Path.cwd(),
        env=environment,
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert completed.returncode == 2
    assert "--confirm-paper-reset" in completed.stderr


def test_paper_strategy_scanner_is_explicitly_disabled_in_phase_48a() -> None:
    source = Path("bot_v3.py").read_text(encoding="utf-8")
    scanner = source[source.index("def scan_and_trade") : source.index("\n\ndef show_status")]

    assert "if context.mode is not ExecutionMode.RESEARCH:" in scanner
    assert "return _blocked_strategy_scan(context)" in scanner
    assert "submit_scanner_candidate(" not in scanner
    assert "recover_paper_runtime(" not in scanner
    assert "place_buy_order(" not in scanner
    assert "PAPER_RUNTIME" not in scanner
    assert "PK" not in scanner
    assert "WALLET" not in scanner


def test_paper_resolution_and_status_keep_dedicated_ledger_admin_path() -> None:
    public_source = Path("bot_v3.py").read_text(encoding="utf-8")
    legacy_source = Path("bot_v3_legacy.py").read_text(encoding="utf-8")

    assert "PAPER_RUNTIME = _legacy.PAPER_RUNTIME" in public_source
    assert "_legacy.PAPER_RUNTIME = PAPER_RUNTIME" in public_source
    assert "_legacy.show_status(context)" in public_source
    assert "PAPER_RUNTIME.ledger_path" in legacy_source
    assert "paper_runtime_status(" in legacy_source
    assert 'choices=("scan", "run", "status", "resolve", "cancel", "paper-reset")' in legacy_source
