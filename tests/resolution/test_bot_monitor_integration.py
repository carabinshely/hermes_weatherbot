from __future__ import annotations

from pathlib import Path


def test_public_bot_does_not_mix_resolution_ledger_commands_into_signal_cli() -> None:
    source = Path("bot_v3.py").read_text(encoding="utf-8")
    cli_source = Path("weatherbot/producer/cli.py").read_text(encoding="utf-8")

    assert "run_resolution_monitor_cycle" not in source
    assert "resolve_ledger_positions" not in source
    assert 'choices=("scan", "run", "status")' in cli_source


def test_internal_paper_cli_retains_safe_resolution_delegate() -> None:
    source = Path("weatherbot/paper/cli.py").read_text(encoding="utf-8")
    assert '"resolve"' in source
    assert "run_resolution_cycle" in source
    assert "run_resolution_monitor_cycle" not in source
    assert "bot_v3_legacy" not in source


def test_quarantined_resolution_cycle_remains_read_only() -> None:
    source = Path("bot_v3_legacy_impl.py").read_text(encoding="utf-8")
    resolution_start = source.index("def run_resolution_monitor_cycle")
    resolution_end = source.index("\n\ndef run_loop", resolution_start)
    block = source[resolution_start:resolution_end]
    assert "get_clob" not in block
    assert "ensure_approvals" not in block
    assert "place_buy_order" not in block
    assert "PK" not in block
    assert "WALLET" not in block
