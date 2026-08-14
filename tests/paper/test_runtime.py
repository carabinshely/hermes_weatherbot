from __future__ import annotations

from dataclasses import replace
from decimal import Decimal
from pathlib import Path

import pytest

from tests.paper.helpers import calibrated_probability, entry_request, paper_book, scope
from tests.quoting.helpers import NOW, cost_policy, freshness_policy
from weatherbot.domain import Money
from weatherbot.markets import ConditionId, OutcomeTokenId
from weatherbot.paper import (
    PaperEntryStatus,
    PaperRuntimeConfig,
    PaperTradingService,
    load_open_position_books,
    open_position_book_references,
    paper_runtime_status,
    paper_scan_decision_id,
    recover_paper_runtime,
)


def test_runtime_config_uses_separate_durable_paper_ledger_and_fixed_limits(tmp_path: Path) -> None:
    config = PaperRuntimeConfig.from_mapping({}, base_dir=tmp_path)

    assert config.starting_cash == Money.of("100")
    assert config.ledger_path == tmp_path / "state/paper-ledger.sqlite3"
    assert config.archive_directory == tmp_path / "state/paper-archive"
    assert config.sizing_policy.maximum_cash_per_trade == Money.of("2")
    assert config.portfolio_policy.maximum_total_exposure == Money.of("20")
    assert config.portfolio_policy.maximum_open_positions == 10


def test_scanner_decision_id_is_stable_and_changes_with_calibration_or_book() -> None:
    request = entry_request()
    calibrated = calibrated_probability()
    first = paper_scan_decision_id(
        calibrated=calibrated,
        scope=request.scope,
        weather=request.weather,
        event=request.event,
        decision_book=request.decision_order_book,
    )
    second = paper_scan_decision_id(
        calibrated=calibrated,
        scope=request.scope,
        weather=request.weather,
        event=request.event,
        decision_book=request.decision_order_book,
    )
    changed_book = paper_scan_decision_id(
        calibrated=calibrated,
        scope=request.scope,
        weather=request.weather,
        event=request.event,
        decision_book=paper_book(book_hash="changed-decision-book"),
    )
    changed_artifact = paper_scan_decision_id(
        calibrated=replace(calibrated, artifact_sha256="b" * 64),
        scope=request.scope,
        weather=request.weather,
        event=request.event,
        decision_book=request.decision_order_book,
    )
    changed_probability = paper_scan_decision_id(
        calibrated=replace(calibrated, model_probability=Decimal("0.66")),
        scope=request.scope,
        weather=request.weather,
        event=request.event,
        decision_book=request.decision_order_book,
    )
    changed_group = paper_scan_decision_id(
        calibrated=replace(calibrated, calibration_group_key="source+lead|D+0"),
        scope=request.scope,
        weather=request.weather,
        event=request.event,
        decision_book=request.decision_order_book,
    )
    changed_lead = paper_scan_decision_id(
        calibrated=replace(calibrated, lead_days=1),
        scope=request.scope,
        weather=request.weather,
        event=request.event,
        decision_book=request.decision_order_book,
    )

    assert first == second
    assert first.startswith("paper_scan_")
    assert changed_book != first
    assert changed_artifact != first
    assert changed_probability != first
    assert changed_group != first
    assert changed_lead != first


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


def test_pristine_paper_status_does_not_create_a_ledger(tmp_path: Path) -> None:
    runtime = PaperRuntimeConfig.from_mapping(
        {"paper_ledger_path": "paper.sqlite3"},
        base_dir=tmp_path,
    )
    assert not runtime.ledger_path.exists()

    def forbidden_fetch(_condition_id: ConditionId, _token_id: OutcomeTokenId):
        raise AssertionError("pristine PAPER status should not fetch a book")

    status = paper_runtime_status(
        runtime=runtime,
        observed_at=NOW,
        freshness_policy=freshness_policy(),
        cost_policy=cost_policy(),
        fetch_book=forbidden_fetch,
    )

    assert status.starting_cash == Money.of("100")
    assert status.cash == Money.of("100")
    assert status.available_cash == Money.of("100")
    assert status.equity == Money.of("100")
    assert status.open_positions == 0
    assert not runtime.ledger_path.exists()


def test_existing_paper_status_uses_query_only_store_and_never_recovers(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = PaperRuntimeConfig.from_mapping(
        {"paper_ledger_path": "paper.sqlite3"},
        base_dir=tmp_path,
    )
    with runtime.open_store() as store:
        before_events = store.event_count()

    def forbidden_recover(_service: PaperTradingService):
        raise AssertionError("PAPER status must not invoke recovery")

    monkeypatch.setattr(PaperTradingService, "recover", forbidden_recover)

    def forbidden_fetch(_condition_id: ConditionId, _token_id: OutcomeTokenId):
        raise AssertionError("empty PAPER status should not fetch a book")

    status = paper_runtime_status(
        runtime=runtime,
        observed_at=NOW,
        freshness_policy=freshness_policy(),
        cost_policy=cost_policy(),
        fetch_book=forbidden_fetch,
    )

    assert status.cash == Money.of("100")
    with runtime.open_read_only_store() as store:
        assert store.read_only
        assert store.event_count() == before_events


def test_explicit_paper_recovery_initializes_and_reconciles_runtime(tmp_path: Path) -> None:
    runtime = PaperRuntimeConfig.from_mapping(
        {"paper_ledger_path": "paper.sqlite3"},
        base_dir=tmp_path,
    )

    recovery = recover_paper_runtime(runtime=runtime)

    assert recovery.is_clean
    assert runtime.ledger_path.exists()
    with runtime.open_read_only_store() as store:
        assert store.load_state().cash == Money.of("100")
