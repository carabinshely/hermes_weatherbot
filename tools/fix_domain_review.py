from pathlib import Path


def replace_once(path: str, old: str, new: str) -> None:
    file = Path(path)
    content = file.read_text(encoding="utf-8")
    count = content.count(old)
    if count != 1:
        raise SystemExit(f"{path}: expected one marker, found {count}")
    file.write_text(content.replace(old, new, 1), encoding="utf-8")


replace_once(
    "weatherbot/domain/events.py",
    '''    if is_dataclass(value) and not isinstance(value, type):
        return {
            field.name: _canonicalize(cast(object, getattr(value, field.name)))
            for field in fields(value)
        }
''',
    '''    if is_dataclass(value) and not isinstance(value, type):
        value_type = type(value)
        canonical: dict[str, object] = {
            "__type__": f"{value_type.__module__}.{value_type.__qualname__}"
        }
        canonical.update(
            {
                field.name: _canonicalize(cast(object, getattr(value, field.name)))
                for field in fields(value)
            }
        )
        return canonical
''',
)

replace_once(
    "weatherbot/domain/money.py",
    '''def as_decimal(value: object) -> Decimal:
    """Return a finite six-decimal value without accepting binary floats."""
    if isinstance(value, bool):
        raise TypeError("boolean values are not valid decimal amounts")
    if isinstance(value, float):
        raise TypeError("binary floating-point values are not valid decimal amounts")
    if not isinstance(value, (Decimal, int, str)):
        raise TypeError(f"unsupported decimal value type: {type(value).__name__}")
    try:
        result = value if isinstance(value, Decimal) else Decimal(value)
        if not result.is_finite():
            raise ValueError("decimal values must be finite")
        return result.quantize(_QUANTUM, rounding=ROUND_HALF_EVEN)
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"invalid decimal value: {value!r}") from exc
''',
    '''def _coerce_decimal(value: object) -> Decimal:
    if isinstance(value, bool):
        raise TypeError("boolean values are not valid decimal amounts")
    if isinstance(value, float):
        raise TypeError("binary floating-point values are not valid decimal amounts")
    if not isinstance(value, (Decimal, int, str)):
        raise TypeError(f"unsupported decimal value type: {type(value).__name__}")
    try:
        result = value if isinstance(value, Decimal) else Decimal(value)
        if not result.is_finite():
            raise ValueError("decimal values must be finite")
        return result
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"invalid decimal value: {value!r}") from exc


def as_decimal(value: object) -> Decimal:
    """Return a finite six-decimal value without accepting binary floats."""
    return _coerce_decimal(value).quantize(_QUANTUM, rounding=ROUND_HALF_EVEN)
''',
)

replace_once(
    "weatherbot/domain/money.py",
    "        return Money(self.amount * as_decimal(factor), self.currency)\n",
    "        return Money(self.amount * _coerce_decimal(factor), self.currency)\n",
)

replace_once(
    "weatherbot/domain/model.py",
    "from enum import StrEnum\nfrom typing import NewType, Self\n",
    "from enum import StrEnum\nfrom types import MappingProxyType\nfrom typing import NewType, Self\n",
)

replace_once(
    "weatherbot/domain/model.py",
    '''def _empty_fill_fingerprints() -> Mapping[FillId, str]:
    return {}
''',
    '''def _empty_fill_fingerprints() -> Mapping[FillId, str]:
    return {}


def _freeze_fill_fingerprints(
    value: Mapping[FillId, str],
) -> Mapping[FillId, str]:
    return MappingProxyType(dict(value))
''',
)

replace_once(
    "weatherbot/domain/model.py",
    '''    fill_fingerprints: Mapping[FillId, str] = field(
        default_factory=_empty_fill_fingerprints
    )

    @classmethod
''',
    '''    fill_fingerprints: Mapping[FillId, str] = field(
        default_factory=_empty_fill_fingerprints
    )

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "fill_fingerprints",
            _freeze_fill_fingerprints(self.fill_fingerprints),
        )

    @classmethod
''',
)

replace_once(
    "weatherbot/domain/reducers.py",
    '''    remaining_fee_reserve = order.intent.fee_reserve - order.fees
    if event.fee.amount > remaining_fee_reserve.amount:
        raise InvariantViolation("fill fee exceeds the remaining fee reserve")
    reservation_release = (
        money_from_unit_price(
            order.intent.limit_price,
            event.quantity,
            state.currency,
        )
        + event.fee
    )
    if reservation_release.amount > order.reserved_cash.amount:
        raise InvariantViolation("fill exceeds the remaining cash reservation")
    debit = gross + event.fee
''',
    '''    remaining_fee_reserve = order.intent.fee_reserve - order.fees
    if event.fee.amount > remaining_fee_reserve.amount:
        raise InvariantViolation("fill fee exceeds the remaining fee reserve")
    remaining_quantity = as_decimal(order.remaining_quantity - event.quantity)
    remaining_reservation = money_from_unit_price(
        order.intent.limit_price,
        remaining_quantity,
        state.currency,
    ) + (remaining_fee_reserve - event.fee)
    if remaining_reservation.amount > order.reserved_cash.amount:
        raise InvariantViolation("remaining reservation exceeds current reservation")
    reservation_release = order.reserved_cash - remaining_reservation
    debit = gross + event.fee
''',
)

replace_once(
    "weatherbot/domain/reducers.py",
    '''    order = replace(order, reserved_cash=order.reserved_cash - reservation_release)
''',
    '''    order = replace(order, reserved_cash=remaining_reservation)
''',
)
