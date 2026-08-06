from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from tests.resolution.helpers import (
    MARKET_ID,
    NOW,
    YES_TOKEN,
    StaticGammaTransport,
    gamma_payload,
    seed_open_position,
)
from weatherbot.domain import PositionStatus
from weatherbot.persistence import SQLiteEventStore
from weatherbot.resolution import (
    GammaResolutionSource,
    ResolutionPollStatus,
    ResolutionWorker,
    StoredDecisionContextProvider,
    eligible_resolution_evidence,
)


def worker(store: SQLiteEventStore, payload: dict[str, object]) -> ResolutionWorker:
    times = iter((NOW, NOW + timedelta(seconds=1)))
    return ResolutionWorker(
        store=store,
        source=GammaResolutionSource(StaticGammaTransport(payload)),
        context_provider=StoredDecisionContextProvider(),
        clock=lambda: next(times),
    )


@pytest.mark.parametrize(
    ("yes", "no", "expected_cash", "expected_pnl", "status"),
    [
        ("1", "0", "106", "6", ResolutionPollStatus.FINAL),
        ("0", "1", "96", "-4", ResolutionPollStatus.FINAL),
        ("0.5", "0.5", "101", "1", ResolutionPollStatus.VOID),
    ],
)
def test_terminal_resolution_is_atomic_and_replayable(
    tmp_path: Path,
    yes: str,
    no: str,
    expected_cash: str,
    expected_pnl: str,
    status: ResolutionPollStatus,
) -> None:
    database = tmp_path / "ledger.sqlite3"
    with SQLiteEventStore(database) as store:
        intent = seed_open_position(store)
        report = worker(store, gamma_payload(yes=yes, no=no)).run_once()
        assert report.checked == 1
        assert report.items[0].status is status
        assert report.items[0].positions_settled == 1
        assert report.items[0].events_appended == 3
        assert store.event_count() == 7

        state = store.load_state()
        position = state.positions[(MARKET_ID, intent.outcome_id)]
        assert position.status is PositionStatus.SETTLED
        assert position.quantity == 0
        assert position.realized_pnl.amount == Decimal(expected_pnl)
        assert state.cash.amount == Decimal(expected_cash)
        assert MARKET_ID in state.resolutions
        assert MARKET_ID in state.resolution_evidence
        assert len(eligible_resolution_evidence(state)) == (0 if status is ResolutionPollStatus.VOID else 1)

    with SQLiteEventStore(database, read_only=True) as restarted:
        state = restarted.load_state()
        assert state.cash.amount == Decimal(expected_cash)
        assert state.positions[(MARKET_ID, intent.outcome_id)].status is PositionStatus.SETTLED
        assert restarted.event_count() == 7


def test_repeated_resolution_cycle_cannot_duplicate_settlement(tmp_path: Path) -> None:
    database = tmp_path / "ledger.sqlite3"
    with SQLiteEventStore(database) as store:
        seed_open_position(store)
        first = worker(store, gamma_payload()).run_once()
        assert first.settled_positions == 1
        count = store.event_count()

        second_times = iter((NOW + timedelta(minutes=1), NOW + timedelta(minutes=1, seconds=1)))
        second = ResolutionWorker(
            store=store,
            source=GammaResolutionSource(StaticGammaTransport(gamma_payload())),
            context_provider=StoredDecisionContextProvider(),
            clock=lambda: next(second_times),
        ).run_once()
        assert second.checked == 0
        assert second.settled_positions == 0
        assert store.event_count() == count


def test_nonterminal_status_does_not_mutate_financial_state(tmp_path: Path) -> None:
    database = tmp_path / "ledger.sqlite3"
    with SQLiteEventStore(database) as store:
        intent = seed_open_position(store)
        before = store.load_state()
        report = worker(
            store,
            gamma_payload(closed=False, status="proposed"),
        ).run_once()
        assert report.items[0].status is ResolutionPollStatus.DELAYED
        assert report.items[0].events_appended == 0
        assert store.event_count() == 4
        after = store.load_state()
        assert after.cash == before.cash
        assert after.positions[(MARKET_ID, intent.outcome_id)].status is PositionStatus.OPEN
        assert not after.resolutions
        assert not after.resolution_evidence


def test_unknown_position_token_rolls_back_evidence_and_resolution(tmp_path: Path) -> None:
    database = tmp_path / "ledger.sqlite3"
    with SQLiteEventStore(database) as store:
        intent = seed_open_position(store, outcome_token="111111111111111111")
        report = worker(store, gamma_payload()).run_once()
        assert report.items[0].status is ResolutionPollStatus.MALFORMED
        assert report.items[0].events_appended == 0
        assert store.event_count() == 4
        state = store.load_state()
        assert not state.resolutions
        assert not state.resolution_evidence
        assert state.positions[(MARKET_ID, intent.outcome_id)].status is PositionStatus.OPEN


def test_conflicting_resolution_context_fails_closed(tmp_path: Path) -> None:
    database = tmp_path / "ledger.sqlite3"
    with SQLiteEventStore(database) as store:
        seed_open_position(store, bucket_key="F:64:65")
        report = worker(store, gamma_payload()).run_once()
        assert report.items[0].status is ResolutionPollStatus.MALFORMED
        assert "bucket" in report.items[0].reason
        assert store.event_count() == 4


def test_worker_uses_numeric_outcome_token_from_position(tmp_path: Path) -> None:
    database = tmp_path / "ledger.sqlite3"
    with SQLiteEventStore(database) as store:
        intent = seed_open_position(store)
        assert str(intent.outcome_id) == YES_TOKEN
        report = worker(store, gamma_payload()).run_once()
        assert report.settled_positions == 1
