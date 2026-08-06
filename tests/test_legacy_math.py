from __future__ import annotations

from pathlib import Path

import pytest

from tests.legacy_loader import load_legacy_math

LEGACY = load_legacy_math(Path("bot_v3.py"))


def test_expected_value_matches_binary_contract_return() -> None:
    assert LEGACY.calc_ev(0.75, 0.30) == pytest.approx(1.5)


def test_quarter_kelly_is_capped_and_non_negative() -> None:
    assert LEGACY.calc_kelly(0.75, 0.30) == pytest.approx(0.1607, abs=1e-4)
    assert LEGACY.calc_kelly(0.10, 0.90) == 0.0


def test_bet_size_uses_configured_cap() -> None:
    assert LEGACY.bet_size(0.25) == 0.5
    assert LEGACY.bet_size(2.0) == 2.0
