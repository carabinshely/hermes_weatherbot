from __future__ import annotations

from pathlib import Path

from tests.paper.helpers import entry_request, paper_book, scope
from tests.quoting.helpers import NOW
from weatherbot.domain import Money
from weatherbot.markets import ConditionId, OutcomeTokenId
from weatherbot.paper import (
    PaperEntryStatus,
    PaperRuntimeConfig,
    PaperTradingService,
    load_open_position_books,
    open_position_book_references,
    paper_scan_decision_id,
)


def test_runtime_config_uses_separate_durable_paper_ledger_and_fixed_limits(tmp_path: Path) -> None:
    config = PaperRuntimeConfig.from_mapping({}, base_dir=tmp_path)

    assert config.starting_cash == Money.of("100")
    assert config.ledger_path == tmp_path / "state/paper-ledger.sqlite3"
    assert config.archive_directory == tmp_path / "state/paper-archive"
    assert config.sizing_policy.maximum_cash_per_trade == Money.of("2")
    assert config.portfolio_policy.maximum_total_exposure == Money.of("20")
    assert config.portfolio_policy.maximum_open_positions == 10


def test_scanner_decision_id_is_stable_for_same_snapshot_and_changes_with_book() -> None:
    request = entry_request()
    first = paper_scan_decision_id(
        model_version=request.model_version,
        scope=request.scope,
        weather=request.weather,
        event=request.event,
        decision_book=request.decision_order_book,
    )
    second = paper_scan_decision_id(
        model_version=request.model_version,
        scope=request.scope,
        weather=request.weather,
        event=request.event,
        decision_book=request.decision_order_book,
    )
    changed_book = paper_book(book_hash="changed-decision-book")
    changed = paper_scan_decision_id(
        model_version=request.model_version,
        scope=request.scope,
        weather=request.weather,
        event=request.event,
        decision_book=changed_book,
    )

    assert first == second
    assert first.startswith("paper_scan_")
    assert changed != first


def test_restart_reconstructs_open_position_public_book_identity(tmp_path: Path) -> None:
    runtime = PaperRuntimeConfig.from_mapping(
        {"paper_ledger_path": "paper.sqlite3"},
        base_dir=tmp_path,
    )
    with runtime.open_store() as store:
        result = PaperTradingService(store, clock=lambda: NOW).submit_entry(
            entry_request(decision_id="runtime-restart"),
            owner_id="paper",
        )
        assert result.status is PaperEntryStatus.FILLED

    calls: list[tuple[str, str]] = []
    with runtime.open_store() as reopened:
        references = open_position_book_references(reopened)
        assert len(references) == 1
        reference = references[0]
        assert reference.position_key == scope().position_key
        assert str(reference.token_id) == str(scope().outcome_id)

        def fetch(condition_id: ConditionId, token_id: OutcomeTokenId):
            calls.append((str(condition_id), str(token_id)))
            return paper_book(book_hash="runtime-restarted-book")

        books = load_open_position_books(reopened, fetch)

    assert set(books) == {scope().position_key}
    assert calls == [(str(references[0].condition_id), str(references[0].token_id))]
