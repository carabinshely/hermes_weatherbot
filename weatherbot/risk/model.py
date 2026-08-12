"""Backend-neutral bankroll sizing contracts."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from typing import Self

from weatherbot.domain import LedgerState, Money, PositionStatus, RiskDecisionStatus
from weatherbot.quoting import QuoteRejectionReason, ValidatedExecutableQuote


type RatioInput = Decimal | int | str


def _ratio(value: RatioInput, *, label: str) -> Decimal:
    if isinstance(value, bool):
        raise TypeError(f"{label} must not be boolean")
    try:
        parsed = value if isinstance(value, Decimal) else Decimal(value)
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"{label} must be decimal") from exc
    if not parsed.is_finite():
        raise ValueError(f"{label} must be finite")
    return parsed


def _require_nonnegative(value: Money, *, label: str) -> None:
    if value.is_negative:
        raise ValueError(f"{label} must not be negative")


class BindingCap(StrEnum):
    """The constraint that determines the final approved cash budget."""

    KELLY = "kelly"
    MAX_CASH_PER_TRADE = "max_cash_per_trade"
    AVAILABLE_CASH = "available_cash"
    EXECUTABLE_DEPTH = "executable_depth"
    MINIMUM_ORDER = "minimum_order"


class SizingRejectionReason(StrEnum):
    """Deterministic reasons for refusing to create an executable size."""

    INVALID_PROBABILITY = "invalid_probability"
    INVALID_PRICE = "invalid_price"
    NO_AVAILABLE_CASH = "no_available_cash"
    NON_POSITIVE_EDGE = "non_positive_edge"
    BELOW_MINIMUM_ORDER = "below_minimum_order"
    QUOTE_REJECTED = "quote_rejected"
    NON_CONVERGENT = "non_convergent"


@dataclass(frozen=True, slots=True)
class RiskCapitalSnapshot:
    """Exact bankroll inputs used by one sizing decision.

    ``available_cash`` is deliberately explicit and must equal ``cash - reserved_cash``.
    Filled BUY exposure is recorded separately for audit and future portfolio controls; it is
    not subtracted from available cash a second time.
    """

    cash: Money
    reserved_cash: Money
    available_cash: Money
    open_position_cost_basis: Money
    open_position_count: int

    def __post_init__(self) -> None:
        currency = self.cash.currency
        for label, amount in (
            ("cash", self.cash),
            ("reserved_cash", self.reserved_cash),
            ("available_cash", self.available_cash),
            ("open_position_cost_basis", self.open_position_cost_basis),
        ):
            if amount.currency != currency:
                raise ValueError(f"{label} uses a different currency")
            _require_nonnegative(amount, label=label)
        if self.available_cash != self.cash - self.reserved_cash:
            raise ValueError("available_cash must equal cash - reserved_cash")
        if isinstance(self.open_position_count, bool) or self.open_position_count < 0:
            raise ValueError("open_position_count must be a non-negative integer")
        if self.open_position_count == 0 and not self.open_position_cost_basis.is_zero:
            raise ValueError("zero open positions cannot have positive open cost basis")

    @classmethod
    def from_ledger(cls, state: LedgerState) -> Self:
        """Build the canonical risk-capital view from verified ledger state."""
        state.assert_invariants()
        open_positions = tuple(
            position
            for position in state.positions.values()
            if position.status is PositionStatus.OPEN and position.quantity > 0
        )
        open_cost_basis = Money.zero(state.currency)
        for position in open_positions:
            open_cost_basis += position.cost_basis
        return cls(
            cash=state.cash,
            reserved_cash=state.reserved_cash,
            available_cash=state.available_cash,
            open_position_cost_basis=open_cost_basis,
            open_position_count=len(open_positions),
        )


@dataclass(frozen=True, slots=True)
class SizingPolicy:
    """Fixed policy for the initial bankroll-sizing evaluation."""

    fractional_kelly_multiplier: Decimal = Decimal("0.25")
    maximum_cash_per_trade: Money = Money.of("2")
    maximum_iterations: int = 8

    def __post_init__(self) -> None:
        multiplier = _ratio(
            self.fractional_kelly_multiplier,
            label="fractional Kelly multiplier",
        )
        if multiplier <= 0 or multiplier > 1:
            raise ValueError("fractional Kelly multiplier must be greater than zero and at most one")
        if self.maximum_cash_per_trade.amount <= 0:
            raise ValueError("maximum_cash_per_trade must be positive")
        if isinstance(self.maximum_iterations, bool) or self.maximum_iterations <= 0:
            raise ValueError("maximum_iterations must be a positive integer")
        object.__setattr__(self, "fractional_kelly_multiplier", multiplier)


@dataclass(frozen=True, slots=True)
class SizingDecision:
    """Auditable result of bankroll sizing plus executable quote convergence."""

    status: RiskDecisionStatus
    capital: RiskCapitalSnapshot
    policy: SizingPolicy
    model_probability: Decimal | None
    seed_price: Decimal
    final_all_in_price: Decimal | None
    raw_kelly: Decimal
    fractional_kelly: Decimal
    uncapped_kelly_cash: Money
    target_cash: Money
    binding_cap: BindingCap | None
    iterations: int
    quote: ValidatedExecutableQuote | None = None
    rejection_reason: SizingRejectionReason | None = None
    quote_rejection_reason: QuoteRejectionReason | None = None
    detail: str | None = None
    depth_reduced: bool = False

    def __post_init__(self) -> None:
        currency = self.capital.cash.currency
        for label, amount in (
            ("uncapped_kelly_cash", self.uncapped_kelly_cash),
            ("target_cash", self.target_cash),
            ("maximum_cash_per_trade", self.policy.maximum_cash_per_trade),
        ):
            if amount.currency != currency:
                raise ValueError(f"{label} uses a different currency")
            _require_nonnegative(amount, label=label)
        if self.iterations < 0:
            raise ValueError("iterations must not be negative")
        if self.status is RiskDecisionStatus.APPROVED:
            if self.quote is None or self.target_cash.is_zero:
                raise ValueError("approved sizing requires a positive target and validated quote")
            if self.rejection_reason is not None or self.quote_rejection_reason is not None:
                raise ValueError("approved sizing cannot contain a rejection reason")
            if self.final_all_in_price is None:
                raise ValueError("approved sizing requires a final all-in price")
        else:
            if self.quote is not None or not self.target_cash.is_zero:
                raise ValueError("rejected sizing must contain no quote or target cash")
            if self.rejection_reason is None:
                raise ValueError("rejected sizing requires a rejection reason")
        if self.target_cash.amount > self.capital.available_cash.amount:
            raise ValueError("target cash exceeds available cash")
        if self.target_cash.amount > self.policy.maximum_cash_per_trade.amount:
            raise ValueError("target cash exceeds maximum cash per trade")
        if self.quote is not None and self.target_cash.amount > self.quote.requested_budget:
            raise ValueError("target cash exceeds final validated quote budget")

    @property
    def quote_fingerprint(self) -> str | None:
        return None if self.quote is None else self.quote.fingerprint

    def metadata(self) -> dict[str, str | int | bool | None]:
        """Flatten the complete sizing provenance for logs and later #16 composition."""
        return {
            "sizing_status": self.status.value,
            "sizing_rejection_reason": (
                None if self.rejection_reason is None else self.rejection_reason.value
            ),
            "sizing_quote_rejection_reason": (
                None if self.quote_rejection_reason is None else self.quote_rejection_reason.value
            ),
            "sizing_detail": self.detail,
            "sizing_cash": format(self.capital.cash.amount, "f"),
            "sizing_reserved_cash": format(self.capital.reserved_cash.amount, "f"),
            "sizing_available_cash": format(self.capital.available_cash.amount, "f"),
            "sizing_open_position_cost_basis": format(
                self.capital.open_position_cost_basis.amount, "f"
            ),
            "sizing_open_position_count": self.capital.open_position_count,
            "sizing_model_probability": (
                None if self.model_probability is None else format(self.model_probability, "f")
            ),
            "sizing_seed_price": format(self.seed_price, "f"),
            "sizing_final_all_in_price": (
                None if self.final_all_in_price is None else format(self.final_all_in_price, "f")
            ),
            "sizing_raw_kelly": format(self.raw_kelly, "f"),
            "sizing_fractional_kelly_multiplier": format(
                self.policy.fractional_kelly_multiplier, "f"
            ),
            "sizing_fractional_kelly": format(self.fractional_kelly, "f"),
            "sizing_uncapped_kelly_cash": format(self.uncapped_kelly_cash.amount, "f"),
            "sizing_maximum_cash_per_trade": format(
                self.policy.maximum_cash_per_trade.amount, "f"
            ),
            "sizing_target_cash": format(self.target_cash.amount, "f"),
            "sizing_binding_cap": None if self.binding_cap is None else self.binding_cap.value,
            "sizing_quote_fingerprint": self.quote_fingerprint,
            "sizing_depth_reduced": self.depth_reduced,
            "sizing_iterations": self.iterations,
        }
