"""Exact decimal money and quantity helpers for domain accounting."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_EVEN, Decimal, InvalidOperation
from typing import Self

from weatherbot.domain.errors import InvariantViolation

type DecimalInput = Decimal | int | str

_QUANTUM = Decimal("0.000001")


def as_decimal(value: DecimalInput) -> Decimal:
    """Return a finite six-decimal value without accepting binary floats."""
    if isinstance(value, bool):
        raise TypeError("boolean values are not valid decimal amounts")
    try:
        result = value if isinstance(value, Decimal) else Decimal(value)
        if not result.is_finite():
            raise ValueError("decimal values must be finite")
        return result.quantize(_QUANTUM, rounding=ROUND_HALF_EVEN)
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"invalid decimal value: {value!r}") from exc


@dataclass(frozen=True, slots=True)
class Money:
    """A currency-qualified exact amount.

    Negative values are allowed so the same type can represent realized profit/loss.
    State invariants decide where negative amounts are forbidden.
    """

    amount: Decimal
    currency: str = "USDC"

    def __post_init__(self) -> None:
        currency = self.currency.strip().upper()
        if not currency:
            raise ValueError("currency must not be blank")
        object.__setattr__(self, "amount", as_decimal(self.amount))
        object.__setattr__(self, "currency", currency)

    @classmethod
    def of(cls, value: DecimalInput, currency: str = "USDC") -> Self:
        return cls(as_decimal(value), currency)

    @classmethod
    def zero(cls, currency: str = "USDC") -> Self:
        return cls(Decimal("0"), currency)

    def _require_same_currency(self, other: Money) -> None:
        if self.currency != other.currency:
            raise InvariantViolation(f"currency mismatch: {self.currency} != {other.currency}")

    def __add__(self, other: Money) -> Money:
        self._require_same_currency(other)
        return Money(self.amount + other.amount, self.currency)

    def __sub__(self, other: Money) -> Money:
        self._require_same_currency(other)
        return Money(self.amount - other.amount, self.currency)

    def __neg__(self) -> Money:
        return Money(-self.amount, self.currency)

    def scale(self, factor: DecimalInput) -> Money:
        return Money(self.amount * as_decimal(factor), self.currency)

    @property
    def is_negative(self) -> bool:
        return self.amount < 0

    @property
    def is_zero(self) -> bool:
        return self.amount == 0


def money_from_unit_price(
    unit_price: DecimalInput,
    quantity: DecimalInput,
    currency: str = "USDC",
) -> Money:
    return Money.of(as_decimal(unit_price) * as_decimal(quantity), currency)


def require_nonnegative(value: Money, *, label: str) -> None:
    if value.is_negative:
        raise InvariantViolation(f"{label} must not be negative: {value.amount}")
