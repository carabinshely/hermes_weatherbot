from __future__ import annotations

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
