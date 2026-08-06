from __future__ import annotations

from pathlib import Path

from tests.domain.helpers import account_opened, event_id
from tests.persistence.helpers import intent_created
from weatherbot.domain import (
    LedgerEvent,
    OrderAggregate,
    OrderIntent,
    OrderRejected,
    OrderState,
)
from weatherbot.persistence import RecoveryAction, SQLiteEventStore


class NoBackendOrderAdapter:
    def __init__(self) -> None:
        self.reconciled: list[OrderState] = []
        self.submit_calls = 0

    @property
    def backend_name(self) -> str:
        return "paper"

    def submit(self, intent: OrderIntent) -> tuple[LedgerEvent, ...]:
        self.submit_calls += 1
        return ()

    def cancel(self, order: OrderAggregate) -> tuple[LedgerEvent, ...]:
        return ()

    def reconcile(self, order: OrderAggregate) -> tuple[LedgerEvent, ...]:
        self.reconciled.append(order.state)
        return (
            OrderRejected(
                event_id=event_id("created-recovery-no-backend-order"),
                occurred_at=order.intent.created_at,
                intent_id=order.intent.intent_id,
                reason="backend confirmed submission did not create an order",
            ),
        )


def test_created_order_with_submission_marker_reconciles_instead_of_resubmitting(
    tmp_path: Path,
) -> None:
    database = tmp_path / "created-submission-window.sqlite3"
    intent = intent_created()

    with SQLiteEventStore(database) as store:
        store.append(account_opened())
        store.commit_order_intent(intent, owner_id="worker-a")
        store.set_adapter_metadata(
            intent.intent.intent_id,
            backend_name="paper",
            payload={"submission_key": "stable-key-1"},
        )

    adapter = NoBackendOrderAdapter()
    with SQLiteEventStore(database) as store:
        initial = store.recover()
        assert len(initial.pending_orders) == 1
        assert initial.pending_orders[0].state is OrderState.CREATED
        assert initial.pending_orders[0].action is RecoveryAction.RECONCILE_BACKEND

        final = store.reconcile_startup(lambda name: adapter)

        assert adapter.submit_calls == 0
        assert adapter.reconciled == [OrderState.CREATED]
        assert final.pending_orders == ()
        assert final.state.orders[intent.intent.intent_id].state is OrderState.REJECTED


def test_created_order_without_submission_marker_is_safe_to_resume(
    tmp_path: Path,
) -> None:
    database = tmp_path / "created-before-submission.sqlite3"
    intent = intent_created()

    with SQLiteEventStore(database) as store:
        store.append(account_opened())
        store.commit_order_intent(intent, owner_id="worker-a")
        recovery = store.recover()

        assert recovery.pending_orders[0].action is RecoveryAction.RESUME_SUBMISSION
        assert recovery.pending_orders[0].adapter is None
