from __future__ import annotations

from pathlib import Path

import pytest

from tests.domain.helpers import account_opened, event_id
from tests.persistence.helpers import intent_created, submitted, unknown
from weatherbot.domain import (
    LedgerEvent,
    OrderAggregate,
    OrderIntent,
    OrderRejected,
    OrderState,
)
from weatherbot.persistence import (
    RecoveryAction,
    RecoveryRequiredError,
    SQLiteEventStore,
)


class RejectingAdapter:
    def __init__(self, backend_name: str = "paper") -> None:
        self._backend_name = backend_name
        self.reconciled: list[OrderState] = []

    @property
    def backend_name(self) -> str:
        return self._backend_name

    def submit(self, intent: OrderIntent) -> tuple[LedgerEvent, ...]:
        return ()

    def cancel(self, order: OrderAggregate) -> tuple[LedgerEvent, ...]:
        return ()

    def reconcile(self, order: OrderAggregate) -> tuple[LedgerEvent, ...]:
        self.reconciled.append(order.state)
        return (
            OrderRejected(
                event_id=event_id(f"recovery-rejected-{order.intent.decision_id}"),
                occurred_at=order.intent.created_at,
                intent_id=order.intent.intent_id,
                reason="backend confirmed no live order",
            ),
        )


def test_created_order_is_reported_for_safe_submission_resume(tmp_path: Path) -> None:
    database = tmp_path / "created-recovery.sqlite3"
    intent = intent_created()

    with SQLiteEventStore(database) as store:
        store.append(account_opened())
        store.commit_order_intent(intent, owner_id="worker-a")
        report = store.recover()

        assert len(report.pending_orders) == 1
        pending = report.pending_orders[0]
        assert pending.action is RecoveryAction.RESUME_SUBMISSION
        assert pending.state is OrderState.CREATED
        assert pending.adapter is None


def test_unknown_backend_state_is_reconciled_and_persisted_on_startup(
    tmp_path: Path,
) -> None:
    database = tmp_path / "unknown-recovery.sqlite3"
    intent = intent_created()

    with SQLiteEventStore(database) as store:
        store.append(account_opened())
        store.commit_order_intent(intent, owner_id="worker-a")
        store.set_adapter_metadata(
            intent.intent.intent_id,
            backend_name="paper",
            payload={"order_source": "recorded-book"},
        )
        store.append_many((submitted(intent), unknown(intent)))

    adapter = RejectingAdapter()
    with SQLiteEventStore(database) as store:
        initial = store.recover()
        assert initial.backend_reconciliation_required
        assert initial.pending_orders[0].state is OrderState.UNKNOWN
        assert initial.pending_orders[0].adapter is not None

        final = store.reconcile_startup(lambda name: adapter)

        assert adapter.reconciled == [OrderState.UNKNOWN]
        assert final.pending_orders == ()
        assert final.state.orders[intent.intent.intent_id].state is OrderState.REJECTED
        assert store.event_count() == 5

    with SQLiteEventStore(database) as reopened:
        assert reopened.recover().is_clean
        assert reopened.load_state().orders[intent.intent.intent_id].state is OrderState.REJECTED


def test_backend_reconciliation_fails_closed_without_metadata(tmp_path: Path) -> None:
    database = tmp_path / "missing-metadata.sqlite3"
    intent = intent_created()

    with SQLiteEventStore(database) as store:
        store.append(account_opened())
        store.commit_order_intent(intent, owner_id="worker-a")
        store.append(submitted(intent))

        with pytest.raises(RecoveryRequiredError, match="no adapter metadata"):
            store.reconcile_startup(lambda name: RejectingAdapter(name))

        assert store.load_state().orders[intent.intent.intent_id].state is OrderState.SUBMITTED


def test_adapter_resolver_must_return_the_recorded_backend(tmp_path: Path) -> None:
    database = tmp_path / "wrong-adapter.sqlite3"
    intent = intent_created()

    with SQLiteEventStore(database) as store:
        store.append(account_opened())
        store.commit_order_intent(intent, owner_id="worker-a")
        store.set_adapter_metadata(
            intent.intent.intent_id,
            backend_name="paper",
            payload={},
        )
        store.append(submitted(intent))

        with pytest.raises(RecoveryRequiredError, match="resolver returned"):
            store.reconcile_startup(lambda name: RejectingAdapter("live"))


def test_adapter_metadata_is_json_only_and_backend_assignment_is_stable(
    tmp_path: Path,
) -> None:
    database = tmp_path / "adapter-metadata.sqlite3"
    intent = intent_created()

    with SQLiteEventStore(database) as store:
        store.append(account_opened())
        store.commit_order_intent(intent, owner_id="worker-a")

        with pytest.raises(TypeError, match="floating-point"):
            store.set_adapter_metadata(
                intent.intent.intent_id,
                backend_name="paper",
                payload={"price": 0.5},
            )

        metadata = store.set_adapter_metadata(
            intent.intent.intent_id,
            backend_name="paper",
            payload={"backend_order_id": "paper-1"},
        )
        assert metadata.payload == {"backend_order_id": "paper-1"}

        with pytest.raises(RecoveryRequiredError, match="already assigned"):
            store.set_adapter_metadata(
                intent.intent.intent_id,
                backend_name="live",
                payload={},
            )
