from __future__ import annotations

from decimal import Decimal

from tests.paper.helpers import MARKET, NOW, OUTCOME, paper_book
from tests.quoting.helpers import cost_policy, freshness_policy
from weatherbot.domain import (
    AccountOpened,
    EventId,
    ExecutionAdapter,
    Money,
    OrderIntent,
    OrderIntentCreated,
    OrderState,
    Side,
    replay,
)
from weatherbot.paper import (
    PaperExecutionAdapter,
    PaperExecutionPlan,
    PaperExecutionStatus,
    build_paper_execution_plan,
)


def _intent(*, decision_id: str, quantity: str = "4", limit_price: str = "0.41") -> OrderIntent:
    return OrderIntent.create(
        strategy_id="paper-execution-test",
        decision_id=decision_id,
        market_id=MARKET,
        outcome_id=OUTCOME,
        side=Side.BUY,
        quantity=Decimal(quantity),
        limit_price=Decimal(limit_price),
        fee_reserve=Money.of("1"),
        created_at=NOW,
    )


def test_adapter_satisfies_backend_neutral_execution_contract() -> None:
    adapter = PaperExecutionAdapter(
        policy=cost_policy(),
        maximum_book_age=freshness_policy().maximum_order_book_age,
        book_provider=lambda _intent: paper_book(),
        clock=lambda: NOW,
    )

    assert isinstance(adapter, ExecutionAdapter)
    assert adapter.backend_name == "paper"


def test_full_fill_consumes_displayed_depth_and_charges_only_simulated_fees() -> None:
    intent = _intent(decision_id="full")
    plan = build_paper_execution_plan(
        intent,
        paper_book(first_ask="0.40", first_ask_size="10"),
        policy=cost_policy(),
        submitted_at=NOW,
        maximum_book_age=freshness_policy().maximum_order_book_age,
    )

    assert plan.status is PaperExecutionStatus.FULL_FILL
    assert plan.filled_quantity == intent.quantity
    assert plan.average_price == Decimal("0.400000")
    assert plan.gross_value == Money.of("1.60")
    assert plan.fee == Money.of("0.026")
    assert (plan.gross_value + plan.fee).amount < intent.cash_reservation.amount
    assert PaperExecutionPlan.from_metadata(plan.metadata()) == plan


def test_depth_or_price_deterioration_produces_partial_fill_then_cancel() -> None:
    intent = _intent(decision_id="partial")
    book = paper_book(
        first_ask="0.40",
        first_ask_size="2",
        second_ask="0.60",
        second_ask_size="100",
        book_hash="partial-book",
    )
    plan = build_paper_execution_plan(
        intent,
        book,
        policy=cost_policy(),
        submitted_at=NOW,
        maximum_book_age=freshness_policy().maximum_order_book_age,
    )
    adapter = PaperExecutionAdapter(
        policy=cost_policy(),
        maximum_book_age=freshness_policy().maximum_order_book_age,
        plan_loader=lambda _intent_id: plan.metadata(),
        clock=lambda: NOW,
    )
    events = adapter.submit(intent)
    state = replay(
        (
            AccountOpened(
                event_id=EventId("paper-execution-open"),
                occurred_at=NOW,
                initial_cash=Money.of("100"),
            ),
            OrderIntentCreated(
                event_id=EventId("paper-execution-intent"),
                occurred_at=NOW,
                intent=intent,
            ),
            *events,
        )
    )

    assert plan.status is PaperExecutionStatus.PARTIAL_FILL
    assert plan.filled_quantity == Decimal("2.000000")
    assert len(events) == 4
    assert state.orders[intent.intent_id].state is OrderState.CANCELLED
    assert state.reserved_cash == Money.zero()
    assert state.positions[(MARKET, OUTCOME)].quantity == Decimal("2.000000")


def test_subminimum_executable_depth_rejects_without_fabricating_a_fill() -> None:
    intent = _intent(decision_id="reject")
    plan = build_paper_execution_plan(
        intent,
        paper_book(
            first_ask="0.40",
            first_ask_size="0.5",
            second_ask="0.60",
            second_ask_size="100",
            book_hash="reject-book",
        ),
        policy=cost_policy(),
        submitted_at=NOW,
        maximum_book_age=freshness_policy().maximum_order_book_age,
    )

    assert plan.status is PaperExecutionStatus.REJECTED
    assert plan.filled_quantity == 0
    assert plan.levels == ()
    assert plan.fee == Money.zero()
