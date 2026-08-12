from __future__ import annotations

from decimal import Decimal

from tests.quoting.helpers import (
    NOW,
    cost_policy,
    event_snapshot,
    freshness_policy,
    order_book,
    weather_snapshot,
)
from weatherbot.domain import Money
from weatherbot.quoting import BalanceSnapshot, CostPolicy, DepthPolicy
from weatherbot.risk import RiskCapitalSnapshot, SizingDecision, SizingPolicy, size_executable_buy


def capital(
    *,
    cash: str = "100",
    reserved: str = "0",
    open_cost_basis: str = "0",
    open_count: int = 0,
) -> RiskCapitalSnapshot:
    cash_money = Money.of(cash)
    reserved_money = Money.of(reserved)
    return RiskCapitalSnapshot(
        cash=cash_money,
        reserved_cash=reserved_money,
        available_cash=cash_money - reserved_money,
        open_position_cost_basis=Money.of(open_cost_basis),
        open_position_count=open_count,
    )


def policy(
    *,
    multiplier: str = "0.25",
    maximum_cash: str = "100",
    maximum_iterations: int = 8,
) -> SizingPolicy:
    return SizingPolicy(
        fractional_kelly_multiplier=Decimal(multiplier),
        maximum_cash_per_trade=Money.of(maximum_cash),
        maximum_iterations=maximum_iterations,
    )


def live_balance(snapshot: RiskCapitalSnapshot) -> BalanceSnapshot:
    return BalanceSnapshot(
        available_cash=snapshot.available_cash.amount,
        reserved_cash=snapshot.reserved_cash.amount,
        observed_at_utc=NOW,
        source="test-ledger",
    )


def size(
    *,
    risk_capital: RiskCapitalSnapshot | None = None,
    probability: str = "0.65",
    sizing_policy: SizingPolicy | None = None,
    costs: CostPolicy | None = None,
    depth_policy: DepthPolicy = DepthPolicy.REJECT,
    first_ask: str = "0.40",
    second_ask: str = "0.42",
    first_size: str = "1000",
    second_size: str = "1000",
    with_balance: bool = False,
) -> SizingDecision:
    snapshot = risk_capital or capital()
    selected_costs = costs or cost_policy(depth_policy=depth_policy)
    return size_executable_buy(
        capital=snapshot,
        probability=Decimal(probability),
        weather=weather_snapshot(),
        event=event_snapshot(),
        order_book=order_book(
            first_ask=first_ask,
            second_ask=second_ask,
            first_size=first_size,
            second_size=second_size,
        ),
        balance=live_balance(snapshot) if with_balance else None,
        evaluated_at=NOW,
        freshness_policy=freshness_policy(),
        cost_policy=selected_costs,
        sizing_policy=sizing_policy or policy(),
    )
