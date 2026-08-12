from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from weatherbot.domain import (
    AccountOpened,
    EventId,
    FillId,
    FillReceived,
    LedgerEvent,
    LedgerState,
    MarketId,
    Money,
    OrderIntent,
    OrderIntentCreated,
    OrderSubmitted,
    OutcomeId,
    PortfolioValuation,
    PortfolioValuationRecorded,
    PositionStatus,
    PositionValuation,
    RiskScope,
    RiskScopeRegistered,
    Side,
    replay,
    risk_scope_event_id,
)
from weatherbot.risk import PortfolioRiskPolicy

NOW = datetime(2026, 1, 2, 12, 0, tzinfo=UTC)
MARKET_A = MarketId("weather-nyc-2026-01-03")
MARKET_B = MarketId("weather-boston-2026-01-03")
MARKET_C = MarketId("weather-miami-2026-01-04")
YES_A = OutcomeId("yes-a")
YES_B = OutcomeId("yes-b")
YES_C = OutcomeId("yes-c")


def opened(cash: str = "100") -> AccountOpened:
    return AccountOpened(
        event_id=EventId("account-opened-portfolio"),
        occurred_at=NOW - timedelta(hours=2),
        initial_cash=Money.of(cash),
    )


def risk_scope(
    market_id: MarketId = MARKET_A,
    outcome_id: OutcomeId = YES_A,
    *,
    event_id: str = "event-east-2026-01-03",
    city_key: str = "new-york",
    market_date: date = date(2026, 1, 3),
    groups: tuple[str, ...] = (),
) -> RiskScope:
    return RiskScope(
        market_id=market_id,
        outcome_id=outcome_id,
        event_id=event_id,
        city_key=city_key,
        market_date=market_date,
        correlation_groups=groups,
    )


def scope_registered(scope: RiskScope, *, occurred_at: datetime | None = None) -> RiskScopeRegistered:
    return RiskScopeRegistered(
        event_id=risk_scope_event_id(scope),
        occurred_at=occurred_at or NOW - timedelta(minutes=50),
        scope=scope,
    )


def buy_intent_created(
    market_id: MarketId = MARKET_A,
    outcome_id: OutcomeId = YES_A,
    *,
    decision_id: str = "decision-a",
    quantity: str = "8",
    limit_price: str = "0.50",
    fee_reserve: str = "0",
    created_at: datetime | None = None,
) -> OrderIntentCreated:
    timestamp = created_at or NOW - timedelta(minutes=40)
    intent = OrderIntent.create(
        strategy_id="weather-v1",
        decision_id=decision_id,
        market_id=market_id,
        outcome_id=outcome_id,
        side=Side.BUY,
        quantity=Decimal(quantity),
        limit_price=Decimal(limit_price),
        fee_reserve=Money.of(fee_reserve),
        created_at=timestamp,
    )
    return OrderIntentCreated(
        event_id=EventId(f"intent-created-{decision_id}"),
        occurred_at=timestamp,
        intent=intent,
    )


def submitted(
    intent_event: OrderIntentCreated,
    *,
    suffix: str = "buy",
    occurred_at: datetime | None = None,
) -> OrderSubmitted:
    return OrderSubmitted(
        event_id=EventId(f"submitted-{suffix}-{intent_event.intent.decision_id}"),
        occurred_at=occurred_at or intent_event.occurred_at + timedelta(minutes=1),
        intent_id=intent_event.intent.intent_id,
        backend_order_id=f"backend-{suffix}-{intent_event.intent.decision_id}",
    )


def fill(
    intent_event: OrderIntentCreated,
    *,
    suffix: str = "buy",
    quantity: str | None = None,
    price: str = "0.40",
    fee: str = "0",
    occurred_at: datetime | None = None,
) -> FillReceived:
    return FillReceived(
        event_id=EventId(f"fill-event-{suffix}-{intent_event.intent.decision_id}"),
        occurred_at=occurred_at or intent_event.occurred_at + timedelta(minutes=2),
        intent_id=intent_event.intent.intent_id,
        fill_id=FillId(f"fill-{suffix}-{intent_event.intent.decision_id}"),
        quantity=Decimal(quantity or str(intent_event.intent.quantity)),
        price=Decimal(price),
        fee=Money.of(fee),
    )


def sell_intent_created(
    market_id: MarketId = MARKET_A,
    outcome_id: OutcomeId = YES_A,
    *,
    decision_id: str = "decision-sell-a",
    quantity: str = "5",
    limit_price: str = "0.30",
    created_at: datetime | None = None,
) -> OrderIntentCreated:
    timestamp = created_at or NOW - timedelta(minutes=10)
    intent = OrderIntent.create(
        strategy_id="weather-v1",
        decision_id=decision_id,
        market_id=market_id,
        outcome_id=outcome_id,
        side=Side.SELL,
        quantity=Decimal(quantity),
        limit_price=Decimal(limit_price),
        fee_reserve=Money.zero(),
        created_at=timestamp,
    )
    return OrderIntentCreated(
        event_id=EventId(f"intent-created-{decision_id}"),
        occurred_at=timestamp,
        intent=intent,
    )


def filled_position_events(
    scope: RiskScope | None = None,
    *,
    buy_fee: str = "0.10",
) -> tuple[LedgerEvent, ...]:
    selected_scope = scope or risk_scope()
    intent = buy_intent_created(
        selected_scope.market_id,
        selected_scope.outcome_id,
        decision_id="filled-a",
        quantity="10",
        limit_price="0.50",
        fee_reserve=buy_fee,
    )
    return (
        opened(),
        scope_registered(selected_scope),
        intent,
        submitted(intent),
        fill(intent, price="0.40", fee=buy_fee),
    )


def state_for(events: tuple[LedgerEvent, ...]) -> LedgerState:
    return replay(events)


def valuation_for(
    state: LedgerState,
    *,
    liquidation_values: dict[tuple[MarketId, OutcomeId], str] | None = None,
    assembled_at: datetime = NOW,
    source: str = "test-liquidation-books",
) -> PortfolioValuation:
    values = liquidation_values or {}
    marks: list[PositionValuation] = []
    total = Money.zero(state.currency)
    for key, position in sorted(state.positions.items(), key=lambda item: (str(item[0][0]), str(item[0][1]))):
        if position.status is not PositionStatus.OPEN or position.quantity <= 0:
            continue
        value = Money.of(values.get(key, str(position.cost_basis.amount)), state.currency)
        total += value
        marks.append(
            PositionValuation(
                market_id=key[0],
                outcome_id=key[1],
                quantity=position.quantity,
                liquidation_value=value,
                observed_at=assembled_at - timedelta(seconds=1),
            )
        )
    return PortfolioValuation(
        positions=tuple(marks),
        equity=state.cash + total,
        assembled_at=assembled_at,
        source=source,
    )


def valuation_recorded(
    valuation: PortfolioValuation,
    *,
    suffix: str = "history",
) -> PortfolioValuationRecorded:
    return PortfolioValuationRecorded(
        event_id=EventId(f"portfolio-valuation-{suffix}"),
        occurred_at=valuation.assembled_at,
        valuation=valuation,
    )


def policy(
    *,
    total: str = "100",
    event: str = "100",
    city_date: str = "100",
    correlation: str = "100",
    positions: int = 10,
    daily_loss: str = "100",
    drawdown: str = "100",
) -> PortfolioRiskPolicy:
    return PortfolioRiskPolicy(
        maximum_total_exposure=Money.of(total),
        maximum_event_exposure=Money.of(event),
        maximum_city_date_exposure=Money.of(city_date),
        maximum_correlation_group_exposure=Money.of(correlation),
        maximum_open_positions=positions,
        maximum_daily_loss=Money.of(daily_loss),
        maximum_drawdown=Money.of(drawdown),
    )
