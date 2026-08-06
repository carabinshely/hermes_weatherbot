from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from tests.domain.helpers import account_opened, event_id
from tests.persistence.helpers import acknowledged, fill, intent_created, submitted
from weatherbot.domain import Money, OrderIntentId, OrderSubmitted
from weatherbot.persistence import (
    ConcurrentDecisionError,
    DuplicateIntentError,
    PersistenceError,
    SQLiteEventStore,
)


def test_state_rebuilds_entirely_from_events_after_restart(tmp_path: Path) -> None:
    database = tmp_path / "ledger.sqlite3"
    intent = intent_created()

    with SQLiteEventStore(database) as store:
        store.append(account_opened())
        store.commit_order_intent(intent, owner_id="scan-a", metadata={"scan": 1})
        store.set_adapter_metadata(
            intent.intent.intent_id,
            backend_name="paper",
            payload={"book_timestamp": "2026-01-02T03:04:00+00:00"},
        )
        store.append_many((submitted(intent), acknowledged(intent), fill(intent)))
        checkpoint = store.create_checkpoint()
        expected_state = store.load_state()

        assert checkpoint.sequence == 5
        assert expected_state.cash == Money.of("95.98")
        assert store.event_count() == 5

    with SQLiteEventStore(database) as reopened:
        rebuilt = reopened.load_state()
        recovery = reopened.recover()

        assert rebuilt == expected_state
        assert recovery.state == expected_state
        assert recovery.last_sequence == 5
        assert recovery.is_clean


def test_interrupted_batch_rolls_back_every_event(tmp_path: Path) -> None:
    database = tmp_path / "atomic.sqlite3"
    missing = OrderSubmitted(
        event_id=event_id("missing-order-submit"),
        occurred_at=account_opened().occurred_at,
        intent_id=OrderIntentId("intent-does-not-exist"),
        backend_order_id="paper-missing",
    )

    with SQLiteEventStore(database) as store:
        with pytest.raises(Exception, match="not found"):
            store.append_many((account_opened(), missing))

        assert store.event_count() == 0
        assert store.load_state().opened is False


def test_duplicate_event_delivery_is_idempotent_but_conflicts_fail(tmp_path: Path) -> None:
    database = tmp_path / "duplicates.sqlite3"
    opened = account_opened()

    with SQLiteEventStore(database) as store:
        first = store.append(opened)
        second = store.append(opened)

        assert first.appended_sequences == (1,)
        assert second.appended_sequences == ()
        assert second.duplicate_event_ids == (str(opened.event_id),)
        assert store.event_count() == 1

        conflicting = opened.__class__(
            event_id=opened.event_id,
            occurred_at=opened.occurred_at,
            initial_cash=Money.of("200"),
        )
        with pytest.raises(Exception, match="reused with different data"):
            store.append(conflicting)

        assert store.event_count() == 1
        assert store.load_state().cash == Money.of("100")


def _commit_same_intent(database: Path, owner: str) -> tuple[bool, int]:
    intent = intent_created()
    with SQLiteEventStore(database) as store:
        result = store.commit_order_intent(intent, owner_id=owner, metadata={"source": "scan"})
        return result.appended, result.last_sequence


def test_concurrent_scans_cannot_create_duplicate_intents(tmp_path: Path) -> None:
    database = tmp_path / "concurrent.sqlite3"
    with SQLiteEventStore(database) as store:
        store.append(account_opened())

    def commit(owner: str) -> tuple[bool, int]:
        return _commit_same_intent(database, owner)

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(commit, ("worker-a", "worker-b")))

    assert sorted(appended for appended, _ in results) == [False, True]
    assert {sequence for _, sequence in results} == {2}

    with SQLiteEventStore(database) as store:
        assert store.event_count() == 2
        claims = store.list_decision_claims()
        assert len(claims) == 1
        assert claims[0].status == "committed"


def test_one_decision_cannot_commit_two_different_intents(tmp_path: Path) -> None:
    database = tmp_path / "decision-conflict.sqlite3"
    first = intent_created(event_name="intent-a", decision_id="same-decision", quantity="10")
    second = intent_created(event_name="intent-b", decision_id="same-decision", quantity="11")

    with SQLiteEventStore(database) as store:
        store.append(account_opened())
        store.commit_order_intent(first, owner_id="worker-a")

        with pytest.raises(DuplicateIntentError, match="already committed intent"):
            store.commit_order_intent(second, owner_id="worker-b")

        assert store.event_count() == 2
        assert len(store.load_state().orders) == 1


def test_claimed_but_uncommitted_decision_is_reported_on_restart(tmp_path: Path) -> None:
    database = tmp_path / "claim-recovery.sqlite3"

    with SQLiteEventStore(database) as store:
        created = store.claim_decision(
            "decision-stale",
            owner_id="worker-a",
            metadata={"market": "weather"},
        )
        duplicate = store.claim_decision(
            "decision-stale",
            owner_id="worker-a",
            metadata={"market": "weather"},
        )

        assert created.created is True
        assert duplicate.created is False

        with pytest.raises(ConcurrentDecisionError, match="already claimed"):
            store.claim_decision(
                "decision-stale",
                owner_id="worker-b",
                metadata={"market": "weather"},
            )

    with SQLiteEventStore(database) as store:
        recovery = store.recover()
        assert len(recovery.pending_decisions) == 1
        assert recovery.pending_decisions[0].claim.decision_key == "decision-stale"

        store.complete_decision_without_intent(
            "decision-stale",
            owner_id="worker-a",
        )
        assert store.recover().pending_decisions == ()


def test_order_intents_cannot_bypass_atomic_decision_commit(tmp_path: Path) -> None:
    database = tmp_path / "intent-boundary.sqlite3"

    with SQLiteEventStore(database) as store:
        store.append(account_opened())
        with pytest.raises(PersistenceError, match="commit_order_intent"):
            store.append(intent_created())

        assert store.event_count() == 1
