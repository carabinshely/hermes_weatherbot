from __future__ import annotations

from pathlib import Path

import pytest

import bot_v3_legacy as legacy
from weatherbot.paper import PaperRuntimeConfig
from weatherbot.paper import cli as paper_cli


def _runtime(tmp_path: Path) -> PaperRuntimeConfig:
    return PaperRuntimeConfig.from_mapping(
        {
            "paper_ledger_path": str(tmp_path / "paper.sqlite3"),
            "paper_archive_directory": str(tmp_path / "archive"),
        },
        base_dir=tmp_path,
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
    runtime = _runtime(tmp_path)
    monkeypatch.setattr(legacy, "PAPER_RUNTIME", runtime)
    monkeypatch.delenv("PK", raising=False)
    monkeypatch.delenv("WALLET", raising=False)

    assert paper_cli.main(["status"]) == 0
    output = capsys.readouterr().out

    assert "Hermes internal PAPER R&D" in output
    assert f"ledger: {runtime.ledger_path}" in output
    assert "available cash:" in output
    assert "open positions: 0" in output
    assert not runtime.ledger_path.exists()


def test_internal_reset_requires_explicit_confirmation(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert paper_cli.main(["reset"]) == 2
    assert "reset requires --confirm-reset" in capsys.readouterr().err
