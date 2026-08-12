from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

from tests.paper.helpers import entry_request, paper_book, scope
from tests.quoting.helpers import NOW, cost_policy, freshness_policy
from weatherbot.domain import Money
from weatherbot.paper import (
    PaperEntryStatus,
    PaperTradingService,
    archive_and_reset_paper_ledger,
    build_paper_valuation,
    initialize_paper_store,
)


def test_status_reconciles_cash_positions_fees_exposure_and_drawdown(tmp_path: Path) -> None:
    database = tmp_path / "status.sqlite3"
    with initialize_paper_store(database, starting_cash=Money.of("100"), opened_at=NOW) as store:
        service = PaperTradingService(store, clock=lambda: NOW)
        entry = service.submit_entry(entry_request(decision_id="status-entry"), owner_id="paper")
        assert entry.status is PaperEntryStatus.FILLED
        mark_book = paper_book(
            first_bid="0.34",
            second_bid="0.33",
            book_hash="status-mark",
        )
        status = service.status(
            {scope().position_key: mark_book},
            cost_policy=cost_policy(),
            observed_at=NOW,
            maximum_book_age=freshness_policy().maximum_order_book_age,
        )

        position = entry.state.positions[scope().position_key]
        assert status.starting_cash == Money.of("100")
        assert status.cash == entry.state.cash
        assert status.reserved_cash == Money.zero()
        assert status.available_cash == status.cash
        assert status.market_value.amount > 0
        assert status.exposure == position.cost_basis
        assert status.fees.amount > 0
        assert status.unrealized_pnl == status.market_value - position.cost_basis
        assert status.equity == status.cash + status.market_value
        assert status.high_water_mark.amount >= status.equity.amount
        assert status.drawdown.amount >= 0
        assert status.open_positions == 1


def test_unavailable_bid_depth_marks_unexecutable_remainder_at_zero(tmp_path: Path) -> None:
    database = tmp_path / "depth-mark.sqlite3"
    with initialize_paper_store(database, starting_cash=Money.of("100"), opened_at=NOW) as store:
        entry = PaperTradingService(store, clock=lambda: NOW).submit_entry(
            entry_request(decision_id="depth-mark-entry"),
            owner_id="paper",
        )
        assert entry.status is PaperEntryStatus.FILLED
        shallow = paper_book(
            first_bid="0.34",
            first_bid_size="1",
            second_bid="0.33",
            second_bid_size="1",
            book_hash="shallow-liquidation",
        )
        valuation = build_paper_valuation(
            entry.state,
            {scope().position_key: shallow},
            policy=cost_policy(),
            observed_at=NOW,
            maximum_book_age=freshness_policy().maximum_order_book_age,
        )

        mark = valuation.positions[0]
        assert mark.quantity == entry.state.positions[scope().position_key].quantity
        assert mark.liquidation_value.amount < Decimal("0.67")
        assert valuation.equity == entry.state.cash + mark.liquidation_value


def test_existing_ledger_never_silently_changes_starting_cash(tmp_path: Path) -> None:
    database = tmp_path / "no-silent-reset.sqlite3"
    store = initialize_paper_store(database, starting_cash=Money.of("100"), opened_at=NOW)
    store.close()

    with pytest.raises(ValueError, match="archive/reset"):
        initialize_paper_store(database, starting_cash=Money.of("50"), opened_at=NOW)


def test_explicit_reset_archives_verified_history_before_new_account(tmp_path: Path) -> None:
    database = tmp_path / "reset.sqlite3"
    archive_dir = tmp_path / "archive"
    with initialize_paper_store(database, starting_cash=Money.of("100"), opened_at=NOW) as store:
        result = PaperTradingService(store, clock=lambda: NOW).submit_entry(
            entry_request(decision_id="before-reset"),
            owner_id="paper",
        )
        assert result.status is PaperEntryStatus.FILLED
        old_count = store.event_count()

    archive = archive_and_reset_paper_ledger(
        database,
        archive_directory=archive_dir,
        starting_cash=Money.of("50"),
        reset_at=NOW,
    )

    assert archive.exists()
    with initialize_paper_store(archive, starting_cash=Money.of("100")) as archived:
        assert archived.event_count() == old_count
        archived.verify_integrity()
    with initialize_paper_store(database, starting_cash=Money.of("50")) as fresh:
        assert fresh.event_count() == 1
        assert fresh.load_state().cash == Money.of("50")
