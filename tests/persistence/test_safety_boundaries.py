from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from tests.domain.helpers import account_opened, event_id
from tests.persistence.helpers import intent_created, submitted
from weatherbot.domain import (
    AccountOpened,
    LedgerEvent,
    OrderAggregate,
    OrderIntent,
    OrderIntentId,
    OrderRejected,
)
from weatherbot.persistence import (
    CorruptLedgerError,
    RecoveryRequiredError,
    SQLiteEventStore,
)
from weatherbot.persistence.codec import decode_event, encode_event


def test_malformed_and_nonfinite_decimal_payloads_fail_as_corruption() -> None:
    encoded = encode_event(account_opened())

    malformed = encoded.payload_json.replace('"100.000000"', '"not-a-number"')
    with pytest.raises(CorruptLedgerError, match="not a decimal string"):
        decode_event(malformed)

    nonfinite = encoded.payload_json.replace('"100.000000"', '"NaN"')
    with pytest.raises(CorruptLedgerError, match="finite decimal"):
        decode_event(nonfinite)


def test_decision_metadata_corruption_is_detected_on_open(tmp_path: Path) -> None:
    database = tmp_path / "decision-metadata-corrupt.sqlite3"
    with SQLiteEventStore(database) as store:
        store.claim_decision(
            "decision-corrupt",
            owner_id="worker-a",
            metadata={"scan": "weather"},
        )

    connection = sqlite3.connect(database)
    try:
        connection.execute(
            "UPDATE decision_claims SET metadata_json = ? WHERE decision_key = ?",
            ('{"scan":"tampered"}', "decision-corrupt"),
        )
        connection.commit()
    finally:
        connection.close()

    with pytest.raises(CorruptLedgerError, match="metadata hash mismatch"):
        SQLiteEventStore(database)


def test_adapter_metadata_corruption_is_detected_on_open(tmp_path: Path) -> None:
    database = tmp_path / "adapter-metadata-corrupt.sqlite3"
    intent = intent_created()
    with SQLiteEventStore(database) as store:
        store.append(account_opened())
        store.commit_order_intent(intent, owner_id="worker-a")
        store.set_adapter_metadata(
            intent.intent.intent_id,
            backend_name="paper",
            payload={"backend_order_id": "paper-1"},
        )

    connection = sqlite3.connect(database)
    try:
        connection.execute(
            "UPDATE adapter_metadata SET payload_hash = ? WHERE intent_id = ?",
            ("0" * 64, str(intent.intent.intent_id)),
        )
        connection.commit()
    finally:
        connection.close()

    with pytest.raises(CorruptLedgerError, match="metadata hash mismatch"):
        SQLiteEventStore(database)


def test_committed_decision_detects_truncated_intent_tail(tmp_path: Path) -> None:
    database = tmp_path / "truncated-intent.sqlite3"
    intent = intent_created()
    with SQLiteEventStore(database) as store:
        store.append(account_opened())
        store.commit_order_intent(intent, owner_id="worker-a")

    connection = sqlite3.connect(database)
    try:
        connection.execute("DELETE FROM ledger_events WHERE sequence = 2")
        connection.commit()
    finally:
        connection.close()

    with pytest.raises(CorruptLedgerError, match="references missing intent"):
        SQLiteEventStore(database)


class WrongIntentAdapter:
    @property
    def backend_name(self) -> str:
        return "paper"

    def submit(self, intent: OrderIntent) -> tuple[LedgerEvent, ...]:
        return ()

    def cancel(self, order: OrderAggregate) -> tuple[LedgerEvent, ...]:
        return ()

    def reconcile(self, order: OrderAggregate) -> tuple[LedgerEvent, ...]:
        return (
            OrderRejected(
                event_id=event_id("wrong-intent-rejection"),
                occurred_at=order.intent.created_at,
                intent_id=OrderIntentId("another-intent"),
                reason="malformed adapter result",
            ),
        )


class UnsupportedEventAdapter(WrongIntentAdapter):
    def reconcile(self, order: OrderAggregate) -> tuple[LedgerEvent, ...]:
        return (
            AccountOpened(
                event_id=event_id("unsupported-recovery-event"),
                occurred_at=order.intent.created_at,
                initial_cash=order.intent.fee_reserve,
            ),
        )


def pending_backend_database(tmp_path: Path) -> Path:
    database = tmp_path / "pending-backend.sqlite3"
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
    return database


def test_reconciliation_rejects_events_for_another_intent(tmp_path: Path) -> None:
    database = pending_backend_database(tmp_path)

    with SQLiteEventStore(database) as store:
        before = store.event_count()
        with pytest.raises(RecoveryRequiredError, match="another-intent"):
            store.reconcile_startup(lambda name: WrongIntentAdapter())
        assert store.event_count() == before


def test_reconciliation_rejects_non_order_lifecycle_events(tmp_path: Path) -> None:
    database = pending_backend_database(tmp_path)

    with SQLiteEventStore(database) as store:
        before = store.event_count()
        with pytest.raises(RecoveryRequiredError, match="unsupported recovery event"):
            store.reconcile_startup(lambda name: UnsupportedEventAdapter())
        assert store.event_count() == before
