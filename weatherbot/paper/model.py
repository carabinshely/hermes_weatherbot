"""Typed contracts for deterministic paper execution, reporting, and audit evidence."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from typing import cast

from weatherbot.domain import Money, OrderIntentId, as_decimal


class PaperExecutionStatus(StrEnum):
    FULL_FILL = "full_fill"
    PARTIAL_FILL = "partial_fill"
    REJECTED = "rejected"


def _text(value: object, *, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a non-blank string")
    return value.strip()


def _decimal(value: object, *, label: str) -> Decimal:
    if isinstance(value, bool):
        raise TypeError(f"{label} must be decimal")
    try:
        result = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"{label} must be decimal") from exc
    if not result.is_finite():
        raise ValueError(f"{label} must be finite")
    return result


def _aware(value: datetime, *, label: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{label} must be timezone-aware")
    return value


def _mapping(value: object, *, label: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{label} must be an object")
    return cast(Mapping[str, object], value)


def _sequence(value: object, *, label: str) -> Sequence[object]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        raise ValueError(f"{label} must be an array")
    return cast(Sequence[object], value)


@dataclass(frozen=True, slots=True)
class PaperFillLevel:
    price: Decimal
    quantity: Decimal

    def __post_init__(self) -> None:
        price = as_decimal(self.price)
        quantity = as_decimal(self.quantity)
        if price <= 0 or price > 1:
            raise ValueError("paper fill level price must be greater than zero and at most one")
        if quantity <= 0:
            raise ValueError("paper fill level quantity must be positive")
        object.__setattr__(self, "price", price)
        object.__setattr__(self, "quantity", quantity)

    def metadata(self) -> dict[str, str]:
        return {
            "price": format(self.price, "f"),
            "quantity": format(self.quantity, "f"),
        }


@dataclass(frozen=True, slots=True)
class PaperExecutionPlan:
    """Immutable execution result derived from one contemporaneous order-book snapshot."""

    intent_id: OrderIntentId
    status: PaperExecutionStatus
    backend_order_id: str
    submitted_at: datetime
    order_book_hash: str
    order_book_observed_at: datetime
    condition_id: str
    token_id: str
    requested_quantity: Decimal
    filled_quantity: Decimal
    average_price: Decimal | None
    worst_price: Decimal | None
    gross_value: Money
    fee: Money
    levels: tuple[PaperFillLevel, ...]
    reason: str

    def __post_init__(self) -> None:
        _text(str(self.intent_id), label="intent_id")
        _text(self.backend_order_id, label="backend_order_id")
        _text(self.order_book_hash, label="order_book_hash")
        _text(self.condition_id, label="condition_id")
        _text(self.token_id, label="token_id")
        _text(self.reason, label="reason")
        _aware(self.submitted_at, label="submitted_at")
        _aware(self.order_book_observed_at, label="order_book_observed_at")
        requested = as_decimal(self.requested_quantity)
        filled = as_decimal(self.filled_quantity)
        if requested <= 0:
            raise ValueError("paper requested quantity must be positive")
        if filled < 0 or filled > requested:
            raise ValueError("paper filled quantity must be within the request")
        if self.gross_value.currency != self.fee.currency:
            raise ValueError("paper execution gross value and fee use different currencies")
        if self.gross_value.is_negative or self.fee.is_negative:
            raise ValueError("paper execution monetary values must not be negative")
        level_quantity = as_decimal(sum((level.quantity for level in self.levels), Decimal("0")))
        if level_quantity != filled:
            raise ValueError("paper execution levels do not reconcile to filled quantity")

        if self.status is PaperExecutionStatus.REJECTED:
            if filled != 0 or self.levels:
                raise ValueError("rejected paper execution cannot contain fills")
            if self.average_price is not None or self.worst_price is not None:
                raise ValueError("rejected paper execution cannot contain fill prices")
            if not self.gross_value.is_zero or not self.fee.is_zero:
                raise ValueError("rejected paper execution cannot contain financial amounts")
        else:
            if filled <= 0 or not self.levels:
                raise ValueError("filled paper execution requires positive fill levels")
            if self.average_price is None or self.worst_price is None:
                raise ValueError("filled paper execution requires average and worst prices")
            average = as_decimal(self.average_price)
            worst = as_decimal(self.worst_price)
            if average <= 0 or average > 1 or worst <= 0 or worst > 1:
                raise ValueError("paper execution prices must be within (0, 1]")
            if self.gross_value.is_zero:
                raise ValueError("filled paper execution requires positive gross value")
            if self.status is PaperExecutionStatus.FULL_FILL and filled != requested:
                raise ValueError("full paper fill must satisfy the entire requested quantity")
            if self.status is PaperExecutionStatus.PARTIAL_FILL and not filled < requested:
                raise ValueError("partial paper fill must leave requested quantity unfilled")
            object.__setattr__(self, "average_price", average)
            object.__setattr__(self, "worst_price", worst)

        object.__setattr__(self, "requested_quantity", requested)
        object.__setattr__(self, "filled_quantity", filled)

    def metadata(self) -> dict[str, object]:
        return {
            "paper_execution_plan": {
                "intent_id": str(self.intent_id),
                "status": self.status.value,
                "backend_order_id": self.backend_order_id,
                "submitted_at": self.submitted_at.isoformat(),
                "order_book_hash": self.order_book_hash,
                "order_book_observed_at": self.order_book_observed_at.isoformat(),
                "condition_id": self.condition_id,
                "token_id": self.token_id,
                "requested_quantity": format(self.requested_quantity, "f"),
                "filled_quantity": format(self.filled_quantity, "f"),
                "average_price": (
                    None if self.average_price is None else format(self.average_price, "f")
                ),
                "worst_price": None if self.worst_price is None else format(self.worst_price, "f"),
                "gross_value": format(self.gross_value.amount, "f"),
                "fee": format(self.fee.amount, "f"),
                "currency": self.gross_value.currency,
                "levels": [level.metadata() for level in self.levels],
                "reason": self.reason,
            }
        }

    @classmethod
    def from_metadata(cls, payload: Mapping[str, object]) -> PaperExecutionPlan:
        data = _mapping(payload.get("paper_execution_plan"), label="paper_execution_plan")
        try:
            status = PaperExecutionStatus(_text(data.get("status"), label="paper status"))
        except ValueError as exc:
            raise ValueError("paper execution metadata has unsupported status") from exc
        submitted_at = datetime.fromisoformat(
            _text(data.get("submitted_at"), label="paper submitted_at")
        )
        book_observed_at = datetime.fromisoformat(
            _text(data.get("order_book_observed_at"), label="paper order_book_observed_at")
        )
        levels = tuple(
            PaperFillLevel(
                price=_decimal(
                    _mapping(raw, label="paper level").get("price"),
                    label="paper level price",
                ),
                quantity=_decimal(
                    _mapping(raw, label="paper level").get("quantity"),
                    label="paper level quantity",
                ),
            )
            for raw in _sequence(data.get("levels"), label="paper levels")
        )
        average_raw = data.get("average_price")
        worst_raw = data.get("worst_price")
        currency = _text(data.get("currency"), label="paper currency")
        return cls(
            intent_id=OrderIntentId(_text(data.get("intent_id"), label="paper intent_id")),
            status=status,
            backend_order_id=_text(
                data.get("backend_order_id"),
                label="paper backend_order_id",
            ),
            submitted_at=_aware(submitted_at, label="paper submitted_at"),
            order_book_hash=_text(data.get("order_book_hash"), label="paper order_book_hash"),
            order_book_observed_at=_aware(
                book_observed_at,
                label="paper order_book_observed_at",
            ),
            condition_id=_text(data.get("condition_id"), label="paper condition_id"),
            token_id=_text(data.get("token_id"), label="paper token_id"),
            requested_quantity=_decimal(
                data.get("requested_quantity"),
                label="paper requested_quantity",
            ),
            filled_quantity=_decimal(
                data.get("filled_quantity"),
                label="paper filled_quantity",
            ),
            average_price=(
                None if average_raw is None else _decimal(average_raw, label="paper average_price")
            ),
            worst_price=(
                None if worst_raw is None else _decimal(worst_raw, label="paper worst_price")
            ),
            gross_value=Money.of(
                _decimal(data.get("gross_value"), label="paper gross_value"),
                currency,
            ),
            fee=Money.of(_decimal(data.get("fee"), label="paper fee"), currency),
            levels=levels,
            reason=_text(data.get("reason"), label="paper reason"),
        )


@dataclass(frozen=True, slots=True)
class PaperStatus:
    starting_cash: Money
    cash: Money
    reserved_cash: Money
    available_cash: Money
    market_value: Money
    realized_pnl: Money
    unrealized_pnl: Money
    fees: Money
    exposure: Money
    equity: Money
    high_water_mark: Money
    drawdown: Money
    open_positions: int

    def __post_init__(self) -> None:
        currency = self.starting_cash.currency
        for value in (
            self.cash,
            self.reserved_cash,
            self.available_cash,
            self.market_value,
            self.realized_pnl,
            self.unrealized_pnl,
            self.fees,
            self.exposure,
            self.equity,
            self.high_water_mark,
            self.drawdown,
        ):
            if value.currency != currency:
                raise ValueError("paper status mixes currencies")
        if self.open_positions < 0:
            raise ValueError("paper status open_positions must not be negative")
