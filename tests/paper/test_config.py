from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import pytest

from weatherbot.paper.config import load_paper_research_config


def _write_config(root: Path, payload: dict[str, object]) -> None:
    (root / "config.json").write_text(json.dumps(payload), encoding="utf-8")


def test_paper_research_config_uses_safe_defaults(tmp_path: Path) -> None:
    _write_config(tmp_path, {})

    config = load_paper_research_config(tmp_path)

    assert config.runtime.starting_cash.amount == Decimal("100")
    assert config.runtime.ledger_path == tmp_path / "state/paper-ledger.sqlite3"
    assert config.cost_policy.minimum_expected_return == Decimal("0.10")
    assert config.scan_interval_seconds == 3600


def test_paper_research_config_preserves_adaptive_ev_floor(tmp_path: Path) -> None:
    _write_config(tmp_path, {"min_ev": "0.10"})
    learning = tmp_path / "data/learning"
    learning.mkdir(parents=True)
    (learning / "model.json").write_text(
        json.dumps({"ev_floor": 0.17}),
        encoding="utf-8",
    )

    config = load_paper_research_config(tmp_path)

    assert config.cost_policy.minimum_expected_return == Decimal("0.17")


def test_paper_research_config_fails_closed_on_invalid_learning_state(tmp_path: Path) -> None:
    _write_config(tmp_path, {})
    learning = tmp_path / "data/learning"
    learning.mkdir(parents=True)
    (learning / "model.json").write_text(
        json.dumps({"ev_floor": "not-a-number"}),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="ev_floor"):
        load_paper_research_config(tmp_path)
