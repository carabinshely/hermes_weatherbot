from __future__ import annotations

from decimal import Decimal

import pytest

from tests.quoting.helpers import cost_policy
from tests.risk.helpers import capital, policy, size
from weatherbot.domain import (
    LedgerState,
    MarketId,
    Money,
    OutcomeId,
    Position,
    PositionStatus,
    RiskDecisionStatus,
)
from weatherbot.quoting import DepthPolicy, QuoteRejectionReason
from weatherbot.risk import (
    BindingCap,
    RiskCapitalSnapshot,
    SizingPolicy,
    SizingRejectionReason,
)


def test_bankroll_changes_kelly_cash_instead_of_treating_maximum_as_bankroll() -> None:
    large = size(risk_capital=capital(cash="100"), sizing_policy=policy(maximum_cash="100"))
    medium = size(risk_capital=capital(cash="20"), sizing_policy=policy(maximum_cash="100"))
    small = size(risk_capital=capital(cash="5"), sizing_policy=policy(maximum_cash="100"))

    assert large.status is RiskDecisionStatus.APPROVED
    assert medium.status is RiskDecisionStatus.APPROVED
    assert small.status is RiskDecisionStatus.APPROVED
    assert large.target_cash.amount > medium.target_cash.amount > small.target_cash.amount
    assert large.binding_cap is BindingCap.KELLY
    assert medium.binding_cap is BindingCap.KELLY
    assert small.binding_cap is BindingCap.KELLY


def test_maximum_cash_per_trade_is_only_a_ceiling() -> None:
    capped = size(
        risk_capital=capital(cash="100"),
        probability="0.90",
        sizing_policy=policy(maximum_cash="2"),
    )
    below_cap = size(
        risk_capital=capital(cash="5"),
        probability="0.65",
        sizing_policy=policy(maximum_cash="2"),
    )

    assert capped.status is RiskDecisionStatus.APPROVED
    assert capped.target_cash == Money.of("2")
    assert capped.binding_cap is BindingCap.MAX_CASH_PER_TRADE
    assert below_cap.status is RiskDecisionStatus.APPROVED
    assert below_cap.target_cash.amount < Decimal("2")
    assert below_cap.binding_cap is BindingCap.KELLY


def test_reserved_cash_reduces_bankroll_without_double_counting_open_exposure() -> None:
    reserved = size(
        risk_capital=capital(cash="100", reserved="80"),
        sizing_policy=policy(maximum_cash="100"),
    )
    equivalent_cash = size(
        risk_capital=capital(cash="20"),
        sizing_policy=policy(maximum_cash="100"),
    )

    assert reserved.status is RiskDecisionStatus.APPROVED
    assert equivalent_cash.status is RiskDecisionStatus.APPROVED
    assert reserved.capital.available_cash == Money.of("20")
    assert reserved.target_cash == equivalent_cash.target_cash


def test_ledger_snapshot_records_open_cost_basis_without_double_subtracting() -> None:
    market_id = MarketId("weather-market")
    outcome_id = OutcomeId("yes")
    position = Position(
        market_id=market_id,
        outcome_id=outcome_id,
        quantity=Decimal("20"),
        reserved_quantity=Decimal("0"),
        cost_basis=Money.of("10"),
        realized_pnl=Money.zero(),
        status=PositionStatus.OPEN,
    )
    ledger = LedgerState(
        currency="USDC",
        opened=True,
        cash=Money.of("90"),
        reserved_cash=Money.zero(),
        positions={(market_id, outcome_id): position},
    )

    snapshot = RiskCapitalSnapshot.from_ledger(ledger)

    assert snapshot.cash == Money.of("90")
    assert snapshot.available_cash == Money.of("90")
    assert snapshot.open_position_cost_basis == Money.of("10")
    assert snapshot.open_position_count == 1


@pytest.mark.parametrize("probability", ["0", "1", "-0.1", "1.1"])
def test_invalid_probability_rejects_with_zero_size(probability: str) -> None:
    decision = size(probability=probability)

    assert decision.status is RiskDecisionStatus.REJECTED
    assert decision.rejection_reason is SizingRejectionReason.INVALID_PROBABILITY
    assert decision.target_cash.is_zero
    assert decision.quote is None


def test_non_positive_displayed_edge_rejects_before_quoting() -> None:
    decision = size(probability="0.40")

    assert decision.status is RiskDecisionStatus.REJECTED
    assert decision.rejection_reason is SizingRejectionReason.NON_POSITIVE_EDGE
    assert decision.iterations == 0
    assert decision.target_cash.is_zero


def test_zero_available_cash_rejects_before_quoting() -> None:
    decision = size(risk_capital=capital(cash="100", reserved="100"))

    assert decision.status is RiskDecisionStatus.REJECTED
    assert decision.rejection_reason is SizingRejectionReason.NO_AVAILABLE_CASH
    assert decision.binding_cap is BindingCap.AVAILABLE_CASH
    assert decision.target_cash.is_zero


def test_depth_reduce_converges_to_depth_bound_executable_quote() -> None:
    decision = size(
        risk_capital=capital(cash="100"),
        probability="0.90",
        sizing_policy=policy(maximum_cash="100"),
        depth_policy=DepthPolicy.REDUCE,
        first_size="3",
        second_size="2",
    )

    assert decision.status is RiskDecisionStatus.APPROVED
    assert decision.depth_reduced
    assert decision.binding_cap is BindingCap.EXECUTABLE_DEPTH
    assert decision.iterations >= 2
    assert decision.quote is not None
    assert decision.target_cash.amount <= decision.quote.requested_budget


def test_depth_reject_fails_closed() -> None:
    decision = size(
        risk_capital=capital(cash="100"),
        probability="0.90",
        sizing_policy=policy(maximum_cash="100"),
        depth_policy=DepthPolicy.REJECT,
        first_size="3",
        second_size="2",
    )

    assert decision.status is RiskDecisionStatus.REJECTED
    assert decision.rejection_reason is SizingRejectionReason.QUOTE_REJECTED
    assert decision.quote_rejection_reason is QuoteRejectionReason.INSUFFICIENT_DEPTH
    assert decision.binding_cap is BindingCap.EXECUTABLE_DEPTH
    assert decision.target_cash.is_zero


def test_fees_can_erase_seed_edge_and_force_zero_size() -> None:
    decision = size(
        probability="0.405",
        sizing_policy=policy(multiplier="1", maximum_cash="100"),
    )

    assert decision.status is RiskDecisionStatus.REJECTED
    assert decision.rejection_reason is SizingRejectionReason.QUOTE_REJECTED
    assert decision.quote_rejection_reason is QuoteRejectionReason.FEE_ERASED_EDGE
    assert decision.target_cash.is_zero


def test_market_minimum_rejects_small_bankroll_size() -> None:
    decision = size(
        risk_capital=capital(cash="1"),
        sizing_policy=policy(maximum_cash="100"),
    )

    assert decision.status is RiskDecisionStatus.REJECTED
    assert decision.rejection_reason is SizingRejectionReason.BELOW_MINIMUM_ORDER
    assert decision.quote_rejection_reason is QuoteRejectionReason.BELOW_MINIMUM_ORDER
    assert decision.binding_cap is BindingCap.MINIMUM_ORDER
    assert decision.target_cash.is_zero


def test_fixed_transaction_cost_can_require_multiple_downward_requotes() -> None:
    decision = size(
        risk_capital=capital(cash="20"),
        sizing_policy=policy(maximum_cash="100"),
        costs=cost_policy(transaction_cost="0.10"),
    )
    seed_raw_kelly = (Decimal("0.65") - Decimal("0.40")) / (Decimal("1") - Decimal("0.40"))
    seed_cash = Decimal("20") * Decimal("0.25") * seed_raw_kelly

    assert decision.status is RiskDecisionStatus.APPROVED
    assert decision.iterations > 1
    assert decision.target_cash.amount < seed_cash
    assert decision.final_all_in_price is not None
    assert decision.final_all_in_price > Decimal("0.40")


def test_iteration_limit_fails_closed_instead_of_using_unconverged_size() -> None:
    decision = size(
        risk_capital=capital(cash="20"),
        sizing_policy=policy(maximum_cash="100", maximum_iterations=1),
        costs=cost_policy(transaction_cost="0.10"),
    )

    assert decision.status is RiskDecisionStatus.REJECTED
    assert decision.rejection_reason is SizingRejectionReason.NON_CONVERGENT
    assert decision.iterations == 1
    assert decision.target_cash.is_zero
    assert decision.quote is None


def test_approved_size_obeys_cash_cap_and_quote_invariants() -> None:
    snapshot = capital(cash="100", reserved="25")
    selected_policy = policy(maximum_cash="2")
    decision = size(
        risk_capital=snapshot,
        probability="0.90",
        sizing_policy=selected_policy,
        with_balance=True,
    )

    assert decision.status is RiskDecisionStatus.APPROVED
    assert decision.quote is not None
    assert Decimal("0") < decision.target_cash.amount
    assert decision.target_cash.amount <= snapshot.available_cash.amount
    assert decision.target_cash.amount <= selected_policy.maximum_cash_per_trade.amount
    assert decision.target_cash.amount <= decision.quote.requested_budget
    assert decision.target_cash.amount.as_tuple().exponent == -6
    assert decision.quote_fingerprint == decision.quote.fingerprint
    metadata = decision.metadata()
    assert metadata["sizing_target_cash"] == format(decision.target_cash.amount, "f")


def test_policy_rejects_non_conservative_or_non_positive_limits() -> None:
    with pytest.raises(ValueError, match="multiplier"):
        SizingPolicy(fractional_kelly_multiplier=Decimal("1.01"))
    with pytest.raises(ValueError, match="positive"):
        SizingPolicy(maximum_cash_per_trade=Money.zero())
