from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from weatherbot.paper import PaperRuntimeConfig
from weatherbot.paper import cli as paper_cli
from weatherbot.paper.config import PaperResearchConfig
from weatherbot.quoting import CostPolicy, DepthPolicy, FreshnessPolicy


def _research_config(tmp_path: Path) -> PaperResearchConfig:
    runtime = PaperRuntimeConfig.from_mapping(
        {
            "paper_ledger_path": str(tmp_path / "paper.sqlite3"),
            "paper_archive_directory": str(tmp_path / "archive"),
        },
        base_dir=tmp_path,
    )
    return PaperResearchConfig(
        runtime=runtime,
        freshness_policy=FreshnessPolicy(
            maximum_forecast_age=timedelta(hours=6),
            maximum_event_age=timedelta(minutes=2),
            maximum_order_book_age=timedelta(seconds=30),
            maximum_balance_age=timedelta(seconds=30),
        ),
        cost_policy=CostPolicy(
            platform_fee_rate=Decimal("0.01"),
            transaction_cost=Decimal("0.01"),
            safety_margin_rate=Decimal("0.02"),
            maximum_average_slippage=Decimal("0.03"),
            maximum_worst_slippage=Decimal("0.05"),
            maximum_all_in_price=Decimal("0.45"),
            minimum_expected_return=Decimal("0.10"),
            depth_policy=DepthPolicy.REJECT,
        ),
        scan_interval_seconds=3600,
    )


def test_internal_parser_exposes_paper_commands_only() -> None:
    parser = paper_cli.build_parser()

    assert parser.parse_args(["scan"]).command == "scan"
    assert parser.parse_args(["run"]).command == "run"
    assert parser.parse_args(["status"]).command == "status"
    assert parser.parse_args(["resolve"]).command == "resolve"
    reset = parser.parse_args(["reset", "--confirm-reset"])
    assert reset.command == "reset"
    assert reset.confirm_reset is True


def test_internal_status_reads_pristine_runtime_without_wallet_or_network(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    research = _research_config(tmp_path)
    monkeypatch.setattr(paper_cli, "_load_research_config", lambda: research)
    monkeypatch.delenv("PK", raising=False)
    monkeypatch.delenv("WALLET", raising=False)

    assert paper_cli.main(["status"]) == 0
    output = capsys.readouterr().out

    assert "Hermes internal PAPER R&D" in output
    assert f"ledger: {research.runtime.ledger_path}" in output
    assert "available cash:" in output
    assert "open positions: 0" in output
    assert not research.runtime.ledger_path.exists()


def test_internal_reset_requires_explicit_confirmation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    research = _research_config(tmp_path)
    monkeypatch.setattr(paper_cli, "_load_research_config", lambda: research)

    assert paper_cli.main(["reset"]) == 2
    assert "reset requires --confirm-reset" in capsys.readouterr().err
    assert not research.runtime.ledger_path.exists()
