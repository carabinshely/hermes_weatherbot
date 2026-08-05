from __future__ import annotations

from pathlib import Path

import pytest

from tests.legacy_loader import load_legacy_math

LEGACY = load_legacy_math(Path("bot_v3.py"))


def test_bounded_bucket_probability_is_valid() -> None:
    probability = LEGACY.bucket_prob(72.0, 70.0, 75.0, sigma=2.0)
    assert 0.0 < probability < 1.0
    assert probability == pytest.approx(0.7745375, abs=1e-7)


def test_expected_value_matches_binary_contract_return() -> None:
    assert LEGACY.calc_ev(0.75, 0.30) == pytest.approx(1.5)


def test_quarter_kelly_is_capped_and_non_negative() -> None:
    assert LEGACY.calc_kelly(0.75, 0.30) == pytest.approx(0.1607, abs=1e-4)
    assert LEGACY.calc_kelly(0.10, 0.90) == 0.0


def test_bet_size_uses_configured_cap() -> None:
    assert LEGACY.bet_size(0.25) == 0.5
    assert LEGACY.bet_size(2.0) == 2.0


@pytest.mark.parametrize(
    ("question", "expected"),
    [
        ("Will it be 53°F or below on April 18?", (-999.0, 53.0)),
        ("Will it be 72°F or higher on April 18?", (72.0, 999.0)),
        ("Will it be between 64-65°F on April 18?", (64.0, 65.0)),
        ("Will it be 72°F on April 18?", (72.0, 72.0)),
    ],
)
def test_temperature_range_parser(question: str, expected: tuple[float, float]) -> None:
    assert LEGACY.parse_temp_range(question) == expected


@pytest.mark.xfail(reason="Known defect tracked by #10: exact-degree buckets have zero width")
def test_exact_degree_bucket_has_nonzero_probability() -> None:
    assert LEGACY.bucket_prob(72.0, 72.0, 72.0, sigma=2.0) > 0.0
