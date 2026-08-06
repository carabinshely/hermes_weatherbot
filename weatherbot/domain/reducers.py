"""Pure reducers that derive balances, orders, and positions from events."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import replace

from weatherbot.domain.errors import (
    AggregateNotFound,
    DuplicateEventConflict,
    InvalidTransition,
    InvariantViolation,
)
from weatherbot.domain.events import (
    AccountOpened,
    FillReceived,
    LedgerEvent,
    MarketResolved,
    OrderAcknowledged,
    OrderCancelled,
    OrderIntentCreated,
    OrderOutcomeUnknown,
    OrderRejected,
    OrderSubmitted,
    PositionSettled,
    fingerprint,
)
from weatherbot.domain.model import (
    OrderAggregate,
    OrderIntentId,
    OrderState,
    Position,
    PositionStatus,
    Side,
    require_transition,
)
from weatherbot.domain.money import Money, as_decimal, money_from_unit_price
from weatherbot.domain.state import LedgerState, PositionKey, position_key


def _require_opened(state: LedgerState) -> None:
    if not state.opened:
        raise InvalidTransition("ledger must be opened before financial events are applied")


def _order(state: LedgerState, intent_id: OrderIntentId) -> OrderAggregate:
    try:
        return state.orders[intent_id]
    except KeyError as exc:
        raise AggregateNotFound(f"order intent not found: {intent_id}") from exc


def _replace_order(state: LedgerState, order: OrderAggregate) -> LedgerState:
    orders = dict(state.orders)
    orders[order.intent.intent_id] = order
    return replace(state, orders=orders)


def _replace_position(state: LedgerState, position: Position) -> LedgerState:
    positions = dict(state.positions)
    positions[position_key(position.market_id, position.outcome_id)] = position
    return replace(state, positions=positions)


def _position(state: LedgerState, key: PositionKey) -> Position:
    try:
        return state.positions[key]
    except KeyError as exc:
        raise AggregateNotFound(f"position not found: {key[0]}/{key[1]}") from exc


def _record_event(state: LedgerState, event: LedgerEvent, event_fp: str) -> LedgerState:
    event_fingerprints = dict(state.event_fingerprints)
    event_fingerprints[event.event_id] = event_fp
    return replace(state, event_fingerprints=event_fingerprints)


def _release_reservations(
    state: LedgerState,
    order: OrderAggregate,
) -> tuple[LedgerState, OrderAggregate]:
    if order.intent.side is Side.BUY:
        state = replace(state, reserved_cash=state.reserved_cash - order.reserved_cash)
        return state, replace(order, reserved_cash=Money.zero(state.currency))

    key = position_key(order.intent.market_id, order.intent.outcome_id)
    position = _position(state, key)
    if position.reserved_quantity < order.reserved_quantity:
        raise InvariantViolation("order reservation exceeds position reservation")
    position = replace(
        position,
        reserved_quantity=as_decimal(position.reserved_quantity - order.reserved_quantity),
    )
    state = _replace_position(state, position)
    return state, replace(order, reserved_quantity=as_decimal(0))


def _apply_account_opened(state: LedgerState, event: AccountOpened) -> LedgerState:
    if state.opened:
        raise InvalidTransition("ledger is already opened")
    if event.initial_cash.currency != state.currency:
        raise InvariantViolation("initial cash currency differs from ledger currency")
    return replace(state, opened=True, cash=event.initial_cash)


def _apply_intent_created(
    state: LedgerState,
    event: OrderIntentCreated,
) -> LedgerState:
    _require_opened(state)
    intent = event.intent
    existing = state.orders.get(intent.intent_id)
    if existing is not None:
        if existing.intent == intent:
            return state
        raise DuplicateEventConflict(
            f"intent identifier {intent.intent_id} was reused with different data"
        )
    if intent.fee_reserve.currency != state.currency:
        raise InvariantViolation("order intent currency differs from ledger currency")

    order = OrderAggregate.new(intent)
    if intent.side is Side.BUY:
        if state.available_cash.amount < order.reserved_cash.amount:
            raise InvariantViolation("insufficient available cash for order reservation")
        state = replace(state, reserved_cash=state.reserved_cash + order.reserved_cash)
    else:
        key = position_key(intent.market_id, intent.outcome_id)
        position = _position(state, key)
        if position.status is not PositionStatus.OPEN:
            raise InvalidTransition("cannot reserve a settled position")
        if position.available_quantity < intent.quantity:
            raise InvariantViolation("insufficient available position quantity")
        position = replace(
            position,
            reserved_quantity=as_decimal(position.reserved_quantity + intent.quantity),
        )
        state = _replace_position(state, position)

    return _replace_order(state, order)


def _apply_submitted(state: LedgerState, event: OrderSubmitted) -> LedgerState:
    order = _order(state, event.intent_id)
    require_transition(order.state, OrderState.SUBMITTED)
    if order.backend_order_id not in {None, event.backend_order_id}:
        raise DuplicateEventConflict("order has a different backend identifier")
    return _replace_order(
        state,
        replace(
            order,
            state=OrderState.SUBMITTED,
            backend_order_id=event.backend_order_id,
        ),
    )


def _apply_acknowledged(
    state: LedgerState,
    event: OrderAcknowledged,
) -> LedgerState:
    order = _order(state, event.intent_id)
    require_transition(order.state, OrderState.ACKNOWLEDGED)
    return _replace_order(
        state,
        replace(order, state=OrderState.ACKNOWLEDGED, unknown_reason=None),
    )


def _apply_unknown(state: LedgerState, event: OrderOutcomeUnknown) -> LedgerState:
    order = _order(state, event.intent_id)
    require_transition(order.state, OrderState.UNKNOWN)
    return _replace_order(
        state,
        replace(
            order,
            state=OrderState.UNKNOWN,
            unknown_reason=event.reason,
        ),
    )


def _apply_terminal(
    state: LedgerState,
    intent_id: OrderIntentId,
    target: OrderState,
    reason: str,
) -> LedgerState:
    order = _order(state, intent_id)
    require_transition(order.state, target)
    state, order = _release_reservations(state, order)
    order = replace(
        order,
        state=target,
        terminal_reason=reason,
        unknown_reason=None,
    )
    return _replace_order(state, order)


def _apply_buy_fill(
    state: LedgerState,
    order: OrderAggregate,
    event: FillReceived,
    gross: Money,
) -> tuple[LedgerState, OrderAggregate]:
    if event.price > order.intent.limit_price:
        raise InvariantViolation("buy fill price exceeds order limit")
    remaining_fee_reserve = order.intent.fee_reserve - order.fees
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
    if debit.amount > state.cash.amount:
        raise InvariantViolation("fill would make cash negative")

    state = replace(
        state,
        cash=state.cash - debit,
        reserved_cash=state.reserved_cash - reservation_release,
    )
    order = replace(order, reserved_cash=order.reserved_cash - reservation_release)

    key = position_key(order.intent.market_id, order.intent.outcome_id)
    position = state.positions.get(key) or Position.empty(
        order.intent.market_id,
        order.intent.outcome_id,
        state.currency,
    )
    if position.status is not PositionStatus.OPEN:
        raise InvalidTransition("cannot add a fill to a settled position")
    position = replace(
        position,
        quantity=as_decimal(position.quantity + event.quantity),
        cost_basis=position.cost_basis + debit,
    )
    return _replace_position(state, position), order


def _apply_sell_fill(
    state: LedgerState,
    order: OrderAggregate,
    event: FillReceived,
    gross: Money,
) -> tuple[LedgerState, OrderAggregate]:
    if event.price < order.intent.limit_price:
        raise InvariantViolation("sell fill price is below order limit")
    key = position_key(order.intent.market_id, order.intent.outcome_id)
    position = _position(state, key)
    if position.status is not PositionStatus.OPEN:
        raise InvalidTransition("cannot sell a settled position")
    if position.quantity < event.quantity:
        raise InvariantViolation("sell fill exceeds position quantity")
    if position.reserved_quantity < event.quantity:
        raise InvariantViolation("sell fill exceeds reserved position quantity")
    if order.reserved_quantity < event.quantity:
        raise InvariantViolation("sell fill exceeds order reservation")
    if event.fee.amount > gross.amount:
        raise InvariantViolation("sell fee exceeds gross proceeds")

    net_proceeds = gross - event.fee
    if event.quantity == position.quantity:
        allocated_cost = position.cost_basis
    else:
        allocated_cost = position.cost_basis.scale(event.quantity / position.quantity)

    remaining_quantity = as_decimal(position.quantity - event.quantity)
    position = replace(
        position,
        quantity=remaining_quantity,
        reserved_quantity=as_decimal(position.reserved_quantity - event.quantity),
        cost_basis=(
            Money.zero(state.currency)
            if remaining_quantity == 0
            else position.cost_basis - allocated_cost
        ),
        realized_pnl=position.realized_pnl + net_proceeds - allocated_cost,
    )
    order = replace(
        order,
        reserved_quantity=as_decimal(order.reserved_quantity - event.quantity),
    )
    state = replace(state, cash=state.cash + net_proceeds)
    return _replace_position(state, position), order


def _apply_fill(state: LedgerState, event: FillReceived) -> LedgerState:
    order = _order(state, event.intent_id)
    existing_fill_fp = order.fill_fingerprints.get(event.fill_id)
    if existing_fill_fp is not None:
        if existing_fill_fp == event.delivery_fingerprint:
            return state
        raise DuplicateEventConflict(
            f"fill identifier {event.fill_id} was reused with different data"
        )

    filled_quantity = as_decimal(order.filled_quantity + event.quantity)
    if filled_quantity > order.intent.quantity:
        raise InvariantViolation("fill would exceed order quantity")
    target = (
        OrderState.FILLED
        if filled_quantity == order.intent.quantity
        else OrderState.PARTIALLY_FILLED
    )
    require_transition(order.state, target)
    if event.fee.currency != state.currency:
        raise InvariantViolation("fill fee currency differs from ledger currency")

    gross = money_from_unit_price(event.price, event.quantity, state.currency)
    if order.intent.side is Side.BUY:
        state, order = _apply_buy_fill(state, order, event, gross)
    else:
        state, order = _apply_sell_fill(state, order, event, gross)

    fill_fingerprints = dict(order.fill_fingerprints)
    fill_fingerprints[event.fill_id] = event.delivery_fingerprint
    order = replace(
        order,
        state=target,
        filled_quantity=filled_quantity,
        gross_value=order.gross_value + gross,
        fees=order.fees + event.fee,
        fill_fingerprints=fill_fingerprints,
        unknown_reason=None,
    )

    if target is OrderState.FILLED:
        state, order = _release_reservations(state, order)
    return _replace_order(state, order)


def _apply_resolution(state: LedgerState, event: MarketResolved) -> LedgerState:
    existing = state.resolutions.get(event.resolution.market_id)
    if existing is not None:
        if existing == event.resolution:
            return state
        raise DuplicateEventConflict("market resolution changed after being recorded")
    resolutions = dict(state.resolutions)
    resolutions[event.resolution.market_id] = event.resolution
    return replace(state, resolutions=resolutions)


def _apply_settlement(state: LedgerState, event: PositionSettled) -> LedgerState:
    key = position_key(event.market_id, event.outcome_id)
    position = _position(state, key)
    if position.status is not PositionStatus.OPEN or position.quantity <= 0:
        raise InvalidTransition("position is not open for settlement")
    if position.reserved_quantity != 0:
        raise InvariantViolation("position cannot settle while quantity is reserved")
    try:
        resolution = state.resolutions[event.market_id]
    except KeyError as exc:
        raise AggregateNotFound(f"market resolution not found: {event.market_id}") from exc
    if event.fee.currency != state.currency:
        raise InvariantViolation("settlement fee currency differs from ledger currency")

    payout = resolution.payout_for(event.outcome_id)
    gross = money_from_unit_price(payout, position.quantity, state.currency)
    if event.fee.amount > gross.amount:
        raise InvariantViolation("settlement fee exceeds gross proceeds")
    net = gross - event.fee
    position = replace(
        position,
        quantity=as_decimal(0),
        reserved_quantity=as_decimal(0),
        cost_basis=Money.zero(state.currency),
        realized_pnl=position.realized_pnl + net - position.cost_basis,
        status=PositionStatus.SETTLED,
        settlement_payout=payout,
    )
    state = replace(state, cash=state.cash + net)
    return _replace_position(state, position)


def apply_event(state: LedgerState, event: LedgerEvent) -> LedgerState:
    """Apply one event without mutating the prior state."""
    event_fp = fingerprint(event)
    existing_event_fp = state.event_fingerprints.get(event.event_id)
    if existing_event_fp is not None:
        if existing_event_fp == event_fp:
            return state
        raise DuplicateEventConflict(
            f"event identifier {event.event_id} was reused with different data"
        )

    if isinstance(event, AccountOpened):
        next_state = _apply_account_opened(state, event)
    elif isinstance(event, OrderIntentCreated):
        next_state = _apply_intent_created(state, event)
    elif isinstance(event, OrderSubmitted):
        next_state = _apply_submitted(state, event)
    elif isinstance(event, OrderAcknowledged):
        next_state = _apply_acknowledged(state, event)
    elif isinstance(event, FillReceived):
        next_state = _apply_fill(state, event)
    elif isinstance(event, OrderRejected):
        next_state = _apply_terminal(
            state,
            event.intent_id,
            OrderState.REJECTED,
            event.reason,
        )
    elif isinstance(event, OrderCancelled):
        next_state = _apply_terminal(
            state,
            event.intent_id,
            OrderState.CANCELLED,
            event.reason,
        )
    elif isinstance(event, OrderOutcomeUnknown):
        next_state = _apply_unknown(state, event)
    elif isinstance(event, MarketResolved):
        next_state = _apply_resolution(state, event)
    else:
        next_state = _apply_settlement(state, event)

    next_state = _record_event(next_state, event, event_fp)
    next_state.assert_invariants()
    return next_state


def replay(events: Iterable[LedgerEvent], *, currency: str = "USDC") -> LedgerState:
    """Rebuild the complete state from an event stream after a restart."""
    state = LedgerState.empty(currency)
    for event in events:
        state = apply_event(state, event)
    return state
