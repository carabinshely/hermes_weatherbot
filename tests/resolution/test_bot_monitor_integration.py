from __future__ import annotations

from pathlib import Path


def test_bot_monitor_invokes_real_resolution_cycle() -> None:
    source = Path("bot_v3.py").read_text(encoding="utf-8")
    assert "resolve_ledger_positions" in source
    assert "run_resolution_monitor_cycle" in source
    assert 'choices=("scan", "run", "status", "resolve", "cancel", "paper-reset")' in source
    assert 'elif args.command == "resolve":' in source


def test_resolution_command_does_not_call_live_order_functions() -> None:
    source = Path("bot_v3.py").read_text(encoding="utf-8")
    resolution_start = source.index("def run_resolution_monitor_cycle")
    resolution_end = source.index("\n\ndef run_loop", resolution_start)
    block = source[resolution_start:resolution_end]
    assert "get_clob" not in block
    assert "ensure_approvals" not in block
    assert "place_buy_order" not in block
    assert "PK" not in block
    assert "WALLET" not in block
