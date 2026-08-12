from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier

import pytest

from tests.risk.portfolio_helpers import (
    MARKET_A,
    MARKET_B,
    NOW,
    YES_A,
    YES_B,
    buy_intent_created,
    opened,
    policy,
    risk_scope,
    state_for,
    valuation_for,
)
from weatherbot.domain import Money, RiskDecisionStatus
from weatherbot.persistence import (
    PortfolioRiskEventStore,
    RiskCheckedCommitResult,
)
from weatherbot.risk import PortfolioRiskRejectionReason


def test_portfolio_risk_store_blocks_unchecked_buy_intent(tmp_path) -> None:
    database = tmp_path / "risk.sqlite3"
    intent = buy_intent_created()

    with PortfolioRiskEventStore(database) as store:
        store.append(opened())
        with pytest.raises(ValueError, match="risk_checked"):
            store.commit_order_intent(intent, owner_id="worker-a")

    with PortfolioRiskEventStore(database) as reopened:
        assert reopened.event_count() == 1
        assert reopened.load_state().reserved_cash == Money.zero()


def test_retry_of_same_approved_risk_decision_is_idempotent(tmp_path) -> None:
    database = tmp_path / "risk.sqlite3"
    scope = risk_scope()
    intent = buy_intent_created()
    initial_state = state_for((opened(),))
    valuation = valuation_for(initial_state)

    with PortfolioRiskEventStore(database) as store:
        store.append(opened())
        first = store.commit_risk_checked_order_intent(
            intent,
            scope=scope,
            valuation=valuation,
            policy=policy(total="6"),
            evaluated_at=NOW,
            owner_id="worker-a",
            metadata={"scan": "scan-1"},
        )
        count_after_first = store.event_count()
        second = store.commit_risk_checked_order_intent(
            intent,
            scope=scope,
            valuation=valuation,
            policy=policy(total="6"),
            evaluated_at=NOW,
            owner_id="worker-a",
            metadata={"scan": "scan-1"},
        )

        assert first.committed
        assert first.decision is not None
        assert first.decision.status is RiskDecisionStatus.APPROVED
        assert second.committed
        assert second.decision is None
        assert not second.append_result.appended
        assert store.event_count() == count_after_first
        assert store.load_state().reserved_cash == Money.of("4")


def test_valid_valuation_is_recorded_even_when_entry_is_rejected(tmp_path) -> None:
    database = tmp_path / "risk.sqlite3"
    initial_state = state_for((opened(),))
    valuation = valuation_for(initial_state)
    intent = buy_intent_created(decision_id="rejected-valid-valuation")

    with PortfolioRiskEventStore(database) as store:
        store.append(opened())
        result = store.commit_risk_checked_order_intent(
            intent,
            scope=risk_scope(),
            valuation=valuation,
            policy=policy(total="3"),
            evaluated_at=NOW,
            owner_id="worker-a",
        )

        assert not result.committed
        assert result.decision is not None
        assert result.decision.rejection_reason is PortfolioRiskRejectionReason.TOTAL_EXPOSURE
        assert result.append_result.appended
        assert store.event_count() == 2
        assert store.load_state().reserved_cash == Money.zero()
        assert len(store.load_state().orders) == 0


def test_stale_rejected_valuation_is_not_persisted(tmp_path) -> None:
    database = tmp_path / "risk.sqlite3"
    initial_state = state_for((opened(),))
    stale = valuation_for(initial_state, assembled_at=NOW - timedelta(seconds=31))
    intent = buy_intent_created(decision_id="rejected-stale-valuation")

    with PortfolioRiskEventStore(database) as store:
        store.append(opened())
        result = store.commit_risk_checked_order_intent(
            intent,
            scope=risk_scope(),
            valuation=stale,
            policy=policy(),
            evaluated_at=NOW,
            owner_id="worker-a",
        )

        assert not result.committed
        assert result.decision is not None
        assert result.decision.rejection_reason is PortfolioRiskRejectionReason.STALE_VALUATION
        assert not result.append_result.appended
        assert store.event_count() == 1


def test_different_concurrent_decisions_cannot_race_past_total_exposure_cap(tmp_path) -> None:
    database = tmp_path / "risk.sqlite3"
    initial_state = state_for((opened(),))
    valuation = valuation_for(initial_state)
    selected_policy = policy(total="6")
    intents = (
        buy_intent_created(MARKET_A, YES_A, decision_id="race-a"),
        buy_intent_created(MARKET_B, YES_B, decision_id="race-b"),
    )
    scopes = (
        risk_scope(MARKET_A, YES_A, event_id="event-a", city_key="new-york"),
        risk_scope(MARKET_B, YES_B, event_id="event-b", city_key="boston"),
    )
    barrier = Barrier(2)

    with PortfolioRiskEventStore(database) as store:
        store.append(opened())

    def attempt(index: int) -> RiskCheckedCommitResult:
        with PortfolioRiskEventStore(database) as store:
            barrier.wait()
            return store.commit_risk_checked_order_intent(
                intents[index],
                scope=scopes[index],
                valuation=valuation,
                policy=selected_policy,
                evaluated_at=NOW,
                owner_id=f"worker-{index}",
                metadata={"race": "total-exposure"},
            )

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = tuple(executor.map(attempt, (0, 1)))

    committed = [result for result in results if result.committed]
    rejected = [result for result in results if not result.committed]
    assert len(committed) == 1
    assert len(rejected) == 1
    assert committed[0].decision is not None
    assert committed[0].decision.status is RiskDecisionStatus.APPROVED
    assert rejected[0].decision is not None
    assert rejected[0].decision.rejection_reason is PortfolioRiskRejectionReason.TOTAL_EXPOSURE

    with PortfolioRiskEventStore(database) as reopened:
        reopened.verify_integrity()
        state = reopened.load_state()
        assert state.reserved_cash == Money.of("4")
        assert state.available_cash == Money.of("96")
        assert len(state.orders) == 1
        claims = reopened.list_decision_claims()
        assert sorted(claim.status for claim in claims) == ["committed", "completed"]
        rejected_claim = next(claim for claim in claims if claim.status == "completed")
        assert rejected_claim.metadata["portfolio_risk_rejection_reason"] == "total_exposure"
