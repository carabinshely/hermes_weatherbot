"""Thin scanner-facing facade for durable PAPER mode."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from datetime import datetime
from decimal import Decimal

from weatherbot.domain import PositionKey, RiskScope, fingerprint
from weatherbot.forecasting import WeatherInputSnapshot
from weatherbot.markets import ConditionId, OrderBookSnapshot, OutcomeTokenId
from weatherbot.paper.ledger import archive_and_reset_paper_ledger
from weatherbot.paper.model import PaperStatus
from weatherbot.paper.runtime import PaperBookFetcher, PaperRuntimeConfig, load_open_position_books
from weatherbot.paper.service import PaperEntryRequest, PaperEntryResult, PaperTradingService
from weatherbot.quoting import CostPolicy, FreshnessPolicy, MarketEventSnapshot


def paper_scan_decision_id(
    *,
    model_version: str,
    scope: RiskScope,
    weather: WeatherInputSnapshot,
    event: MarketEventSnapshot,
    decision_book: OrderBookSnapshot,
) -> str:
    """Stable same-snapshot identity; changed market evidence creates a new decision."""
    if not model_version.strip():
        raise ValueError("paper model version must not be blank")
    material = "\n".join(
        (
            model_version.strip(),
            str(scope.market_id),
            str(scope.outcome_id),
            fingerprint(weather),
            fingerprint(event),
            decision_book.book_hash,
        )
    ).encode()
    return f"paper_scan_{hashlib.sha256(material).hexdigest()}"


def submit_scanner_candidate(
    *,
    runtime: PaperRuntimeConfig,
    strategy_id: str,
    decision_id: str,
    model_version: str,
    probability: Decimal,
    scope: RiskScope,
    weather: WeatherInputSnapshot,
    event: MarketEventSnapshot,
    decision_book: OrderBookSnapshot,
    condition_id: ConditionId,
    token_id: OutcomeTokenId,
    evaluated_at: datetime,
    freshness_policy: FreshnessPolicy,
    cost_policy: CostPolicy,
    fetch_book: PaperBookFetcher,
    audit_metadata: Mapping[str, object],
    owner_id: str,
) -> PaperEntryResult:
    """Execute one scanner PAPER candidate using durable state and a fresh submit-time book."""
    with runtime.open_store() as store:
        service = PaperTradingService(store)
        service.recover()
        valuation_books = load_open_position_books(store, fetch_book)
        execution_book = fetch_book(condition_id, token_id)
        request = PaperEntryRequest(
            strategy_id=strategy_id,
            decision_id=decision_id,
            model_version=model_version,
            model_probability=probability,
            scope=scope,
            weather=weather,
            event=event,
            decision_order_book=decision_book,
            execution_order_book=execution_book,
            valuation_books=valuation_books,
            evaluated_at=evaluated_at,
            freshness_policy=freshness_policy,
            cost_policy=cost_policy,
            sizing_policy=runtime.sizing_policy,
            portfolio_policy=runtime.portfolio_policy,
            audit_metadata=audit_metadata,
        )
        return service.submit_entry(request, owner_id=owner_id)


def paper_runtime_status(
    *,
    runtime: PaperRuntimeConfig,
    observed_at: datetime,
    freshness_policy: FreshnessPolicy,
    cost_policy: CostPolicy,
    fetch_book: PaperBookFetcher,
) -> PaperStatus:
    with runtime.open_store() as store:
        service = PaperTradingService(store)
        service.recover()
        books: Mapping[PositionKey, OrderBookSnapshot] = load_open_position_books(store, fetch_book)
        return service.status(
            books,
            cost_policy=cost_policy,
            observed_at=observed_at,
            maximum_book_age=freshness_policy.maximum_order_book_age,
        )


def reset_paper_runtime(
    *,
    runtime: PaperRuntimeConfig,
    reset_at: datetime,
) -> str:
    archive = archive_and_reset_paper_ledger(
        runtime.ledger_path,
        archive_directory=runtime.archive_directory,
        starting_cash=runtime.starting_cash,
        reset_at=reset_at,
    )
    return str(archive)
