from pathlib import Path


def replace_once(path: str, old: str, new: str) -> None:
    file = Path(path)
    content = file.read_text(encoding="utf-8")
    count = content.count(old)
    if count != 1:
        raise SystemExit(f"{path}: expected one marker, found {count}")
    file.write_text(content.replace(old, new, 1), encoding="utf-8")


replace_once(
    "weatherbot/domain/model.py",
    '''        require_nonnegative(self.fee_reserve, label="fee_reserve")
        require_aware(self.created_at, label="created_at")
''',
    '''        require_nonnegative(self.fee_reserve, label="fee_reserve")
        if self.side is Side.SELL and not self.fee_reserve.is_zero:
            raise InvariantViolation("sell orders must not reserve cash fees")
        require_aware(self.created_at, label="created_at")
''',
)

replace_once(
    "weatherbot/domain/reducers.py",
    '''    if event.price > order.intent.limit_price:
        raise InvariantViolation("buy fill price exceeds order limit")
    reservation_release = (
''',
    '''    if event.price > order.intent.limit_price:
        raise InvariantViolation("buy fill price exceeds order limit")
    remaining_fee_reserve = order.intent.fee_reserve - order.fees
    if event.fee.amount > remaining_fee_reserve.amount:
        raise InvariantViolation("fill fee exceeds the remaining fee reserve")
    reservation_release = (
''',
)

replace_once(
    "weatherbot/domain/state.py",
    "from weatherbot.domain.money import Money, as_decimal, require_nonnegative\n",
    '''from weatherbot.domain.money import (
    Money,
    as_decimal,
    money_from_unit_price,
    require_nonnegative,
)
''',
)

replace_once(
    "weatherbot/domain/state.py",
    '''        for intent_id, order in self.orders.items():
            if intent_id != order.intent.intent_id:
                raise InvariantViolation("order map key does not match intent identifier")
            if order.filled_quantity < 0 or order.filled_quantity > order.intent.quantity:
                raise InvariantViolation("order filled quantity is outside valid bounds")
            require_nonnegative(order.gross_value, label="order gross value")
            require_nonnegative(order.fees, label="order fees")
            require_nonnegative(order.reserved_cash, label="order reserved cash")
            if order.reserved_quantity < 0:
                raise InvariantViolation("order reserved quantity must not be negative")
            if order.state.is_terminal and (
                not order.reserved_cash.is_zero or order.reserved_quantity != 0
            ):
                raise InvariantViolation("terminal orders must retain no reservation")
            if order.intent.side is Side.BUY:
                expected_reserved_cash += order.reserved_cash
            else:
                key = position_key(order.intent.market_id, order.intent.outcome_id)
                expected_sell_reservations[key] += order.reserved_quantity
''',
    '''        for intent_id, order in self.orders.items():
            if intent_id != order.intent.intent_id:
                raise InvariantViolation("order map key does not match intent identifier")
            if order.filled_quantity < 0 or order.filled_quantity > order.intent.quantity:
                raise InvariantViolation("order filled quantity is outside valid bounds")
            for label, amount in (
                ("order gross value", order.gross_value),
                ("order fees", order.fees),
                ("order reserved cash", order.reserved_cash),
                ("order fee reserve", order.intent.fee_reserve),
            ):
                if amount.currency != self.currency:
                    raise InvariantViolation(f"{label} uses a different currency")
                require_nonnegative(amount, label=label)
            if order.reserved_quantity < 0:
                raise InvariantViolation("order reserved quantity must not be negative")
            if order.state.is_terminal and (
                not order.reserved_cash.is_zero or order.reserved_quantity != 0
            ):
                raise InvariantViolation("terminal orders must retain no reservation")
            if order.intent.side is Side.BUY:
                if order.reserved_quantity != 0:
                    raise InvariantViolation("buy orders must not reserve position quantity")
                remaining_fee_reserve = order.intent.fee_reserve - order.fees
                require_nonnegative(
                    remaining_fee_reserve,
                    label="remaining fee reserve",
                )
                if not order.state.is_terminal:
                    expected_order_reservation = money_from_unit_price(
                        order.intent.limit_price,
                        order.remaining_quantity,
                        self.currency,
                    ) + remaining_fee_reserve
                    if order.reserved_cash != expected_order_reservation:
                        raise InvariantViolation(
                            "buy order reservation does not cover remaining quantity and fees"
                        )
                expected_reserved_cash += order.reserved_cash
            else:
                if not order.reserved_cash.is_zero:
                    raise InvariantViolation("sell orders must not reserve cash")
                if (
                    not order.state.is_terminal
                    and order.reserved_quantity != order.remaining_quantity
                ):
                    raise InvariantViolation(
                        "sell order reservation does not equal remaining quantity"
                    )
                key = position_key(order.intent.market_id, order.intent.outcome_id)
                expected_sell_reservations[key] += order.reserved_quantity
''',
)
