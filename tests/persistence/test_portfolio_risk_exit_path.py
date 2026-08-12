from __future__ import annotations

from datetime import timedelta
from pathlib import Path

from tests.risk.portfolio_helpers import (
    NOW,
    buy_intent_created,
    fill,
    opened,
    policy,
    risk_scope,
    sell_intent_created,
    state_for,
    submitted,
    valuation_for,
)
from weatherbot.domain import Money
from weatherbot.persistence import PortfolioRiskEventStore


def test_portfolio_risk_store_keeps_sell_exit_path_available(tmp_path: Path) -> None:
    database = tmp_path / "risk.sqlite3"
    buy = buy_intent_created(decision_id="entry-for-exit-test")
    initial_state = state_for((opened(),))

    with PortfolioRiskEventStore(database) as store:
        store.append(opened())
        entry = store.commit_risk_checked_order_intent(
            buy,
            scope=risk_scope(),
            valuation=valuation_for(initial_state),
            policy=policy(),
            evaluated_at=NOW,
            owner_id="entry-worker",
        )
        assert entry.committed

        store.append(submitted(buy, suffix="entry"))
        store.append(fill(buy, suffix="entry", price="0.40"))

        sell = sell_intent_created(
            decision_id="exit-after-risk-gate",
            quantity="4",
            limit_price="0.30",
        )
        exit_result = store.commit_order_intent(sell, owner_id="exit-worker")

        assert exit_result.appended
        state = store.load_state()
        position = state.positions[(sell.intent.market_id, sell.intent.outcome_id)]
        assert position.quantity == buy.intent.quantity
        assert position.reserved_quantity == sell.intent.quantity
        assert state.reserved_cash == Money.zero()


def test_same_position_can_reenter_after_full_exit_and_restart(tmp_path: Path) -> None:
    database = tmp_path / "risk.sqlite3"
    scope = risk_scope()
    first_buy = buy_intent_created(decision_id="reentry-first", quantity="8")
    initial_state = state_for((opened(),))

    with PortfolioRiskEventStore(database) as store:
        store.append(opened())
        first_entry = store.commit_risk_checked_order_intent(
            first_buy,
            scope=scope,
            valuation=valuation_for(initial_state),
            policy=policy(),
            evaluated_at=NOW,
            owner_id="first-entry-worker",
        )
        assert first_entry.committed
        store.append(submitted(first_buy, suffix="reentry-first"))
        store.append(fill(first_buy, suffix="reentry-first", price="0.40"))

        full_exit = sell_intent_created(
            decision_id="reentry-full-exit",
            quantity="8",
            limit_price="0.30",
        )
        store.commit_order_intent(full_exit, owner_id="full-exit-worker")
        store.append(submitted(full_exit, suffix="reentry-full-exit"))
        store.append(
            fill(
                full_exit,
                suffix="reentry-full-exit",
                quantity="8",
                price="0.30",
            )
        )
        exited_state = store.load_state()
        position = exited_state.positions[scope.position_key]
        assert position.quantity == 0
        assert exited_state.reserved_cash == Money.zero()

    reentry_at = NOW + timedelta(seconds=1)
    second_buy = buy_intent_created(
        decision_id="reentry-second",
        quantity="2",
        created_at=reentry_at,
    )
    with PortfolioRiskEventStore(database) as reopened:
        result = reopened.commit_risk_checked_order_intent(
            second_buy,
            scope=scope,
            valuation=valuation_for(exited_state, assembled_at=reentry_at),
            policy=policy(),
            evaluated_at=reentry_at,
            owner_id="second-entry-worker",
        )

        assert result.committed
        state = reopened.load_state()
        assert state.reserved_cash == second_buy.intent.cash_reservation
