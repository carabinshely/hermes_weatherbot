"""Shared parsing, probability, matching, and settlement buckets for temperature markets."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from enum import StrEnum

_NUMBER = r"(-?\d+(?:\.\d+)?)"
_LOWER_TAIL = re.compile(
    rf"{_NUMBER}\s*°?\s*([FC])\s+or\s+below",
    re.IGNORECASE,
)
_UPPER_TAIL = re.compile(
    rf"{_NUMBER}\s*°?\s*([FC])\s+or\s+higher",
    re.IGNORECASE,
)
_BOUNDED = re.compile(
    rf"between\s+{_NUMBER}\s*(?:-|–|—|to)\s*{_NUMBER}\s*°?\s*([FC])",
    re.IGNORECASE,
)
_EXACT = re.compile(
    rf"\bbe\s+{_NUMBER}\s*°?\s*([FC])\s+on\b",
    re.IGNORECASE,
)


class TemperatureMarketError(ValueError):
    """Raised when temperature outcome semantics are missing or inconsistent."""


class TemperatureUnit(StrEnum):
    FAHRENHEIT = "F"
    CELSIUS = "C"

    @classmethod
    def parse(cls, value: str) -> TemperatureUnit:
        normalized = value.strip().upper()
        try:
            return cls(normalized)
        except ValueError as exc:
            raise TemperatureMarketError(f"unsupported temperature unit: {value!r}") from exc


def _decimal(value: Decimal | int | str | float, *, label: str) -> Decimal:
    if isinstance(value, float):
        if not math.isfinite(value):
            raise TemperatureMarketError(f"{label} must be finite")
        value = str(value)
    try:
        result = Decimal(value)
    except (InvalidOperation, ValueError) as exc:
        raise TemperatureMarketError(f"{label} must be decimal") from exc
    if not result.is_finite():
        raise TemperatureMarketError(f"{label} must be finite")
    return result


def _require_integral(value: Decimal, *, label: str) -> Decimal:
    if value != value.to_integral_value():
        raise TemperatureMarketError(f"{label} must be a whole-degree settlement value")
    return value


def _normal_cdf(value: float) -> float:
    return 0.5 * (1.0 + math.erf(value / math.sqrt(2.0)))


@dataclass(frozen=True, slots=True)
class TemperatureBucket:
    """A complete discrete settlement bucket represented without magic sentinels.

    Polymarket's daily temperature outcomes are labelled in whole degrees. The
    public resolution source reports the finalized daily high as a whole-degree
    value, so probability mass for an inclusive integer range ``a..b`` is
    represented by the continuous interval ``[a - 0.5, b + 0.5)``. Exact-degree
    markets are therefore finite one-degree bins rather than zero-width points.
    """

    unit: TemperatureUnit
    lower_inclusive: Decimal | None
    upper_inclusive: Decimal | None

    def __post_init__(self) -> None:
        if self.lower_inclusive is None and self.upper_inclusive is None:
            raise TemperatureMarketError("bucket requires at least one bound")
        lower = (
            None
            if self.lower_inclusive is None
            else _require_integral(
                _decimal(self.lower_inclusive, label="lower bound"),
                label="lower bound",
            )
        )
        upper = (
            None
            if self.upper_inclusive is None
            else _require_integral(
                _decimal(self.upper_inclusive, label="upper bound"),
                label="upper bound",
            )
        )
        if lower is not None and upper is not None and lower > upper:
            raise TemperatureMarketError("bucket lower bound exceeds upper bound")
        object.__setattr__(self, "lower_inclusive", lower)
        object.__setattr__(self, "upper_inclusive", upper)

    @classmethod
    def lower_tail(
        cls,
        maximum: Decimal | int | str,
        unit: TemperatureUnit,
    ) -> TemperatureBucket:
        return cls(
            unit=unit,
            lower_inclusive=None,
            upper_inclusive=_decimal(maximum, label="maximum"),
        )

    @classmethod
    def upper_tail(
        cls,
        minimum: Decimal | int | str,
        unit: TemperatureUnit,
    ) -> TemperatureBucket:
        return cls(
            unit=unit,
            lower_inclusive=_decimal(minimum, label="minimum"),
            upper_inclusive=None,
        )

    @classmethod
    def bounded(
        cls,
        lower: Decimal | int | str,
        upper: Decimal | int | str,
        unit: TemperatureUnit,
    ) -> TemperatureBucket:
        return cls(
            unit=unit,
            lower_inclusive=_decimal(lower, label="lower"),
            upper_inclusive=_decimal(upper, label="upper"),
        )

    @property
    def is_lower_tail(self) -> bool:
        return self.lower_inclusive is None

    @property
    def is_upper_tail(self) -> bool:
        return self.upper_inclusive is None

    @property
    def is_exact(self) -> bool:
        return (
            self.lower_inclusive is not None
            and self.lower_inclusive == self.upper_inclusive
        )

    @property
    def continuous_lower(self) -> Decimal | None:
        if self.lower_inclusive is None:
            return None
        return self.lower_inclusive - Decimal("0.5")

    @property
    def continuous_upper(self) -> Decimal | None:
        if self.upper_inclusive is None:
            return None
        return self.upper_inclusive + Decimal("0.5")

    @property
    def label(self) -> str:
        suffix = f"°{self.unit.value}"
        if self.is_lower_tail:
            assert self.upper_inclusive is not None
            return f"{self.upper_inclusive:g}{suffix} or below"
        if self.is_upper_tail:
            assert self.lower_inclusive is not None
            return f"{self.lower_inclusive:g}{suffix} or higher"
        assert self.lower_inclusive is not None
        assert self.upper_inclusive is not None
        if self.is_exact:
            return f"{self.lower_inclusive:g}{suffix}"
        return f"{self.lower_inclusive:g}-{self.upper_inclusive:g}{suffix}"

    @property
    def key(self) -> str:
        lower = "-inf" if self.lower_inclusive is None else format(self.lower_inclusive, "f")
        upper = "inf" if self.upper_inclusive is None else format(self.upper_inclusive, "f")
        return f"{self.unit.value}:{lower}:{upper}"

    def contains_reported(self, value: Decimal | int | str | float) -> bool:
        reported = _require_integral(
            _decimal(value, label="reported temperature"),
            label="reported temperature",
        )
        if self.lower_inclusive is not None and reported < self.lower_inclusive:
            return False
        if self.upper_inclusive is not None and reported > self.upper_inclusive:
            return False
        return True

    def contains_forecast(self, value: Decimal | int | str | float) -> bool:
        """Classify a point forecast using half-up rounding to source precision."""
        forecast = _decimal(value, label="forecast temperature")
        reported = forecast.quantize(Decimal("1"), rounding=ROUND_HALF_UP)
        return self.contains_reported(reported)

    def probability(
        self,
        mean: Decimal | int | str | float,
        sigma: Decimal | int | str | float,
    ) -> float:
        mean_decimal = _decimal(mean, label="forecast mean")
        sigma_decimal = _decimal(sigma, label="sigma")
        if sigma_decimal <= 0:
            raise TemperatureMarketError("sigma must be positive")
        mean_float = float(mean_decimal)
        sigma_float = float(sigma_decimal)
        lower = self.continuous_lower
        upper = self.continuous_upper
        if lower is None:
            assert upper is not None
            return _normal_cdf((float(upper) - mean_float) / sigma_float)
        if upper is None:
            return 1.0 - _normal_cdf((float(lower) - mean_float) / sigma_float)
        return _normal_cdf((float(upper) - mean_float) / sigma_float) - _normal_cdf(
            (float(lower) - mean_float) / sigma_float
        )


def parse_temperature_bucket(question: str) -> TemperatureBucket:
    if not isinstance(question, str) or not question.strip():
        raise TemperatureMarketError("market question must not be blank")

    lower_tail = _LOWER_TAIL.search(question)
    if lower_tail:
        return TemperatureBucket.lower_tail(
            lower_tail.group(1),
            TemperatureUnit.parse(lower_tail.group(2)),
        )

    upper_tail = _UPPER_TAIL.search(question)
    if upper_tail:
        return TemperatureBucket.upper_tail(
            upper_tail.group(1),
            TemperatureUnit.parse(upper_tail.group(2)),
        )

    bounded = _BOUNDED.search(question)
    if bounded:
        return TemperatureBucket.bounded(
            bounded.group(1),
            bounded.group(2),
            TemperatureUnit.parse(bounded.group(3)),
        )

    exact = _EXACT.search(question)
    if exact:
        value = exact.group(1)
        return TemperatureBucket.bounded(
            value,
            value,
            TemperatureUnit.parse(exact.group(2)),
        )

    raise TemperatureMarketError(
        f"question does not contain a supported temperature bucket: {question!r}"
    )


@dataclass(frozen=True, slots=True)
class TemperatureMarketPartition:
    buckets: tuple[TemperatureBucket, ...]

    def __post_init__(self) -> None:
        if len(self.buckets) < 2:
            raise TemperatureMarketError("temperature market requires at least two buckets")
        units = {bucket.unit for bucket in self.buckets}
        if len(units) != 1:
            raise TemperatureMarketError("temperature market mixes units")
        if sum(bucket.is_lower_tail for bucket in self.buckets) != 1:
            raise TemperatureMarketError("temperature market requires exactly one lower tail")
        if sum(bucket.is_upper_tail for bucket in self.buckets) != 1:
            raise TemperatureMarketError("temperature market requires exactly one upper tail")
        if len({bucket.key for bucket in self.buckets}) != len(self.buckets):
            raise TemperatureMarketError("temperature market contains duplicate buckets")

        def sort_key(bucket: TemperatureBucket) -> tuple[int, Decimal]:
            if bucket.lower_inclusive is None:
                return (0, Decimal("0"))
            return (1, bucket.lower_inclusive)

        ordered = tuple(sorted(self.buckets, key=sort_key))
        if not ordered[0].is_lower_tail or not ordered[-1].is_upper_tail:
            raise TemperatureMarketError("temperature tails do not bound the full outcome set")

        for previous, current in zip(ordered, ordered[1:], strict=False):
            previous_upper = previous.upper_inclusive
            current_lower = current.lower_inclusive
            if previous_upper is None or current_lower is None:
                raise TemperatureMarketError("tail bucket appears inside the outcome set")
            expected = previous_upper + Decimal("1")
            if current_lower < expected:
                raise TemperatureMarketError(
                    f"temperature buckets overlap at {current_lower:g}°{current.unit.value}"
                )
            if current_lower > expected:
                raise TemperatureMarketError(
                    f"temperature buckets leave a gap before {current_lower:g}°{current.unit.value}"
                )

        object.__setattr__(self, "buckets", ordered)

    @property
    def unit(self) -> TemperatureUnit:
        return self.buckets[0].unit

    def bucket_for_reported(
        self,
        value: Decimal | int | str | float,
    ) -> TemperatureBucket:
        matches = [bucket for bucket in self.buckets if bucket.contains_reported(value)]
        if len(matches) != 1:
            raise TemperatureMarketError(
                f"reported temperature maps to {len(matches)} buckets instead of one"
            )
        return matches[0]

    def bucket_for_forecast(
        self,
        value: Decimal | int | str | float,
    ) -> TemperatureBucket:
        forecast = _decimal(value, label="forecast temperature")
        reported = forecast.quantize(Decimal("1"), rounding=ROUND_HALF_UP)
        return self.bucket_for_reported(reported)

    def probabilities(
        self,
        mean: Decimal | int | str | float,
        sigma: Decimal | int | str | float,
    ) -> tuple[tuple[TemperatureBucket, float], ...]:
        probabilities = tuple(
            (bucket, bucket.probability(mean, sigma)) for bucket in self.buckets
        )
        total = sum(probability for _, probability in probabilities)
        if not math.isclose(total, 1.0, rel_tol=0.0, abs_tol=1e-12):
            raise TemperatureMarketError(
                f"bucket probabilities sum to {total:.15f}, expected one"
            )
        return probabilities
