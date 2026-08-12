from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

import pytest
import requests

from tests.paper.helpers import entry_request, paper_book, scope
from tests.quoting.helpers import NOW
from weatherbot.domain import LedgerEvent, Money, OrderState
from weatherbot.paper import (
    PaperEntryStatus,
    PaperExecutionStatus,
    PaperTradingService,
    initialize_paper_store,
)
from weatherbot.persistence import AppendResult, PortfolioRiskEventStore


def test_service_executes_audited_entry_and_same_decision_is_idempotent(tmp_path: Path) -> None:
    database = tmp_path / "paper.sqlite3"
    with initialize_paper_store(database, starting_cash=Money.of("100"), opened_at=NOW) as store:
        service = PaperTradingService(store, clock=lambda: NOW)
        request = entry_request()
        first = service.submit_entry(request, owner_id="paper-worker")
        count_after_first = store.event_count()
        second = service.submit_entry(request, owner_id="paper-worker")

        assert first.status is PaperEntryStatus.FILLED
        assert first.sizing is not None
        assert first.execution_plan is not None
        assert first.execution_plan.status is PaperExecutionStatus.FULL_FILL
        assert first.state.reserved_cash == Money.zero()
        position = first.state.positions[scope().position_key]
        assert position.quantity > 0
        assert position.cost_basis.amount > 0

        claim = next(
            item
            for item in store.list_decision_claims()
            if item.decision_key == request.decision_id
        )
        assert claim.metadata["paper_mode"] is True
        assert claim.metadata["model_version"] == "fixture-model-1"
        assert claim.metadata["quote_age_seconds"] == "5.0"
        caller_audit = claim.metadata["caller_audit"]
        assert isinstance(caller_audit, dict)
        assert caller_audit["legacy_float"] == "1.25"
        assert isinstance(claim.metadata["decision_order_book"], dict)
        assert isinstance(claim.metadata["weather_snapshot"], dict)

        adapter = store.get_adapter_metadata(
            first.state.orders[next(iter(first.state.orders))].intent.intent_id
        )
        assert adapter is not None
        assert adapter.backend_name == "paper"
        assert "paper_execution_plan" in adapter.payload

        assert second.status is PaperEntryStatus.IDEMPOTENT
        assert second.sizing is None
        assert second.execution_plan == first.execution_plan
        assert store.event_count() == count_after_first
        assert second.state == first.state


def test_contemporaneous_execution_book_can_partial_fill_or_reject() -> None:
    partial_book = paper_book(
        first_ask="0.40",
        first_ask_size="2",
        second_ask="0.60",
        second_ask_size="100",
        book_hash="service-partial",
    )
    reject_book = paper_book(
        first_ask="0.40",
        first_ask_size="0.5",
        second_ask="0.60",
        second_ask_size="100",
        book_hash="service-reject",
    )

    assert partial_book.book_hash != reject_book.book_hash


@pytest.mark.parametrize(
    ("book", "expected"),
    (
        (
            paper_book(
                first_ask="0.40",
                first_ask_size="2",
                second_ask="0.60",
                second_ask_size="100",
                book_hash="partial-runtime",
            ),
            PaperEntryStatus.PARTIAL_FILL,
        ),
        (
            paper_book(
                first_ask="0.40",
                first_ask_size="0.5",
                second_ask="0.60",
                second_ask_size="100",
                book_hash="reject-runtime",
            ),
            PaperEntryStatus.EXECUTION_REJECTED,
        ),
    ),
)
def test_service_reprices_at_submission_depth(
    tmp_path: Path,
    book: object,
    expected: PaperEntryStatus,
) -> None:
    from weatherbot.markets import OrderBookSnapshot

    assert isinstance(book, OrderBookSnapshot)
    database = tmp_path / f"{expected.value}.sqlite3"
    with initialize_paper_store(database, starting_cash=Money.of("100"), opened_at=NOW) as store:
        result = PaperTradingService(store, clock=lambda: NOW).submit_entry(
            entry_request(decision_id=f"decision-{expected.value}", execution_book=book),
            owner_id="paper-worker",
        )

        assert result.status is expected
        assert result.execution_plan is not None
        assert result.state.reserved_cash == Money.zero()
        if expected is PaperEntryStatus.PARTIAL_FILL:
            assert result.execution_plan.filled_quantity == 2
            assert result.state.positions[scope().position_key].quantity == 2
        else:
            assert result.execution_plan.filled_quantity == 0
            assert scope().position_key not in result.state.positions


def test_new_decision_cannot_duplicate_existing_paper_exposure(tmp_path: Path) -> None:
    database = tmp_path / "duplicate.sqlite3"
    with initialize_paper_store(database, starting_cash=Money.of("100"), opened_at=NOW) as store:
        service = PaperTradingService(store, clock=lambda: NOW)
        first = service.submit_entry(entry_request(decision_id="first"), owner_id="paper-worker")
        assert first.status is PaperEntryStatus.FILLED
        count_before = len(first.state.orders)

        second = service.submit_entry(
            entry_request(
                decision_id="duplicate-position",
                valuation_books={scope().position_key: paper_book(book_hash="duplicate-mark")},
            ),
            owner_id="paper-worker",
        )

        assert second.status is PaperEntryStatus.RISK_REJECTED
        assert second.risk_decision is not None
        assert len(second.state.orders) == count_before


def test_execution_plan_survives_crash_between_intent_commit_and_lifecycle_append(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = tmp_path / "crash.sqlite3"
    store = initialize_paper_store(database, starting_cash=Money.of("100"), opened_at=NOW)
    original_append_many = PortfolioRiskEventStore.append_many

    def fail_append(
        _store: PortfolioRiskEventStore,
        _events: Iterable[LedgerEvent],
    ) -> AppendResult:
        raise RuntimeError("simulated process crash")

    monkeypatch.setattr(PortfolioRiskEventStore, "append_many", fail_append)
    with pytest.raises(RuntimeError, match="simulated process crash"):
        PaperTradingService(store, clock=lambda: NOW).submit_entry(
            entry_request(decision_id="crash-recovery"),
            owner_id="paper-worker",
        )

    crashed_state = store.load_state()
    order = next(iter(crashed_state.orders.values()))
    assert order.state is OrderState.CREATED
    assert crashed_state.reserved_cash.amount > 0
    assert store.get_adapter_metadata(order.intent.intent_id) is not None
    store.close()

    monkeypatch.setattr(PortfolioRiskEventStore, "append_many", original_append_many)
    with initialize_paper_store(database, starting_cash=Money.of("100")) as reopened:
        recovery = PaperTradingService(reopened, clock=lambda: NOW).recover()
        state = reopened.load_state()

        assert recovery.is_clean
        recovered_order = state.orders[order.intent.intent_id]
        assert recovered_order.state is OrderState.FILLED
        assert state.reserved_cash == Money.zero()
        assert state.positions[scope().position_key].quantity > 0


def test_paper_service_cannot_reach_any_http_endpoint(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def forbidden_network(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("paper mode attempted network I/O")

    monkeypatch.setattr(requests.sessions.Session, "request", forbidden_network)
    database = tmp_path / "network-guard.sqlite3"
    with initialize_paper_store(database, starting_cash=Money.of("100"), opened_at=NOW) as store:
        result = PaperTradingService(store, clock=lambda: NOW).submit_entry(
            entry_request(decision_id="network-guard"),
            owner_id="paper-worker",
        )

        assert result.status is PaperEntryStatus.FILLED
