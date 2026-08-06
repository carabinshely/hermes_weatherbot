from __future__ import annotations

from decimal import Decimal

import pytest

from weatherbot.markets import (
    TemperatureBucket,
    TemperatureMarketError,
    TemperatureMarketPartition,
    TemperatureUnit,
    parse_temperature_bucket,
)


def chicago_partition() -> TemperatureMarketPartition:
    questions = (
        "Will the highest temperature in Chicago be 53°F or below on April 18?",
        "Will the highest temperature in Chicago be between 54-55°F on April 18?",
        "Will the highest temperature in Chicago be between 56-57°F on April 18?",
        "Will the highest temperature in Chicago be between 58-59°F on April 18?",
        "Will the highest temperature in Chicago be between 60-61°F on April 18?",
        "Will the highest temperature in Chicago be between 62-63°F on April 18?",
        "Will the highest temperature in Chicago be between 64-65°F on April 18?",
        "Will the highest temperature in Chicago be between 66-67°F on April 18?",
        "Will the highest temperature in Chicago be between 68-69°F on April 18?",
        "Will the highest temperature in Chicago be between 70-71°F on April 18?",
        "Will the highest temperature in Chicago be 72°F or higher on April 18?",
    )
    return TemperatureMarketPartition(tuple(parse_temperature_bucket(q) for q in questions))


@pytest.mark.parametrize(
    ("question", "lower", "upper", "unit"),
    [
        ("Will it be -5°F or below on April 18?", None, Decimal("-5"), "F"),
        ("Will it be 72°F or higher on April 18?", Decimal("72"), None, "F"),
        (
            "Will it be between 64-65°F on April 18?",
            Decimal("64"),
            Decimal("65"),
            "F",
        ),
        ("Will it be 19°C on April 18?", Decimal("19"), Decimal("19"), "C"),
    ],
)
def test_parses_explicit_bucket_types(
    question: str,
    lower: Decimal | None,
    upper: Decimal | None,
    unit: str,
) -> None:
    bucket = parse_temperature_bucket(question)
    assert bucket.lower_inclusive == lower
    assert bucket.upper_inclusive == upper
    assert bucket.unit.value == unit


def test_exact_degree_bucket_has_finite_nonzero_probability() -> None:
    bucket = parse_temperature_bucket("Will it be 19°C on April 18?")
    probability = bucket.probability(Decimal("19"), Decimal("2"))
    assert 0 < probability < 1
    assert probability == pytest.approx(0.1974126514, abs=1e-10)
    assert bucket.continuous_lower == Decimal("18.5")
    assert bucket.continuous_upper == Decimal("19.5")


def test_bounded_integer_labels_use_half_degree_continuous_edges() -> None:
    bucket = TemperatureBucket.bounded(64, 65, TemperatureUnit.FAHRENHEIT)
    assert bucket.continuous_lower == Decimal("63.5")
    assert bucket.continuous_upper == Decimal("65.5")
    assert bucket.probability(64, 2) == pytest.approx(0.3720789733, abs=1e-10)


def test_lower_and_upper_tail_probabilities_are_included() -> None:
    lower = TemperatureBucket.lower_tail(53, TemperatureUnit.FAHRENHEIT)
    upper = TemperatureBucket.upper_tail(72, TemperatureUnit.FAHRENHEIT)
    assert 0 < lower.probability(63, 2) < 1
    assert 0 < upper.probability(63, 2) < 1


def test_complete_partition_probability_sums_to_one() -> None:
    partition = chicago_partition()
    probabilities = partition.probabilities(Decimal("63"), Decimal("2"))
    assert sum(probability for _, probability in probabilities) == pytest.approx(
        1.0,
        abs=1e-12,
    )
    assert probabilities[0][0].is_lower_tail
    assert probabilities[-1][0].is_upper_tail


@pytest.mark.parametrize(
    ("reported", "expected_label"),
    [
        (53, "53°F or below"),
        (54, "54-55°F"),
        (55, "54-55°F"),
        (63, "62-63°F"),
        (71, "70-71°F"),
        (72, "72°F or higher"),
        (-50, "53°F or below"),
        (120, "72°F or higher"),
    ],
)
def test_partition_matches_each_boundary_exactly_once(
    reported: int,
    expected_label: str,
) -> None:
    assert chicago_partition().bucket_for_reported(reported).label == expected_label


def test_forecast_classification_uses_half_up_rounding() -> None:
    partition = chicago_partition()
    assert partition.bucket_for_forecast("63.49").label == "62-63°F"
    assert partition.bucket_for_forecast("63.50").label == "64-65°F"
    assert partition.bucket_for_forecast("-5.5").label == "53°F or below"


@pytest.mark.parametrize(
    "buckets",
    [
        (
            TemperatureBucket.lower_tail(53, TemperatureUnit.FAHRENHEIT),
            TemperatureBucket.bounded(55, 56, TemperatureUnit.FAHRENHEIT),
            TemperatureBucket.upper_tail(57, TemperatureUnit.FAHRENHEIT),
        ),
        (
            TemperatureBucket.lower_tail(53, TemperatureUnit.FAHRENHEIT),
            TemperatureBucket.bounded(53, 55, TemperatureUnit.FAHRENHEIT),
            TemperatureBucket.upper_tail(56, TemperatureUnit.FAHRENHEIT),
        ),
        (
            TemperatureBucket.bounded(54, 55, TemperatureUnit.FAHRENHEIT),
            TemperatureBucket.upper_tail(56, TemperatureUnit.FAHRENHEIT),
        ),
        (
            TemperatureBucket.lower_tail(53, TemperatureUnit.FAHRENHEIT),
            TemperatureBucket.bounded(54, 55, TemperatureUnit.CELSIUS),
            TemperatureBucket.upper_tail(56, TemperatureUnit.FAHRENHEIT),
        ),
    ],
)
def test_malformed_or_incomplete_partitions_fail_closed(
    buckets: tuple[TemperatureBucket, ...],
) -> None:
    with pytest.raises(TemperatureMarketError):
        TemperatureMarketPartition(buckets)


def test_fractional_reported_resolution_value_fails_closed() -> None:
    with pytest.raises(TemperatureMarketError, match="whole-degree"):
        chicago_partition().bucket_for_reported(Decimal("63.5"))
