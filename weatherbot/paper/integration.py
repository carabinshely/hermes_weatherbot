"""Thin scanner-facing facade for durable PAPER mode."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from datetime import UTC, datetime

from weatherbot.domain import Money, PositionKey, RiskScope, fingerprint
from weatherbot.forecasting import CalibratedProbability, WeatherInputSnapshot
from weatherbot.markets import ConditionId, OrderBookSnapshot, OutcomeTokenId
from weatherbot.paper.ledger import archive_and_reset_paper_ledger
from weatherbot.paper.model import PaperStatus
from weatherbot.paper.runtime import PaperBookFetcher, PaperRuntimeConfig, load_open_position_books
from weatherbot.paper.service import PaperEntryRequest, PaperEntryResult, PaperTradingService
from weatherbot.persistence import StartupRecovery
from weatherbot.quoting import CostPolicy, FreshnessPolicy, MarketEventSnapshot


def _utc_now() -> datetime:
    return datetime.now(UTC)


_CALIBRATION_AUDIT_KEYS = frozenset(
    {
        "calibration",
        "model_probability",
        "model_version",
        "artifact_sha256",
        "city_slug",
        "climate_region",
        "lead_days",
        "weather_fingerprint",
        "forecast_source",
        "calibration_group_key",
        "fallback_level",
        "distribution_type",
        "calibration_sample_count",
        "training_cutoff",
    }
)


def _validate_calibrated_context(
    *,
    calibrated: CalibratedProbability,
    scope: RiskScope,
    weather: WeatherInputSnapshot,
) -> None:
    if calibrated.city_slug != scope.city_key:
        raise ValueError("calibrated city_slug must match PAPER risk scope city_key")
    if scope.market_date != weather.forecast.market_date:
        raise ValueError("PAPER risk scope market_date must match calibrated weather")
    if calibrated.forecast_source != weather.forecast.source.value:
        raise ValueError("calibrated forecast_source must match PAPER weather source")
    if calibrated.weather_fingerprint != fingerprint(weather):
        raise ValueError("calibrated weather_fingerprint must match PAPER weather snapshot")


def _scanner_audit_metadata(
    *,
    calibrated: CalibratedProbability,
    audit_metadata: Mapping[str, object],
) -> Mapping[str, object]:
    normalized_keys: dict[str, str] = {}
    for raw_key in audit_metadata:
        normalized = str(raw_key).strip()
        if normalized in normalized_keys:
            raise ValueError(
                f"PAPER caller audit metadata contains duplicate normalized key: {normalized}"
            )
        normalized_keys[normalized] = str(raw_key)
    if normalized_keys.get("bucket_key") != "bucket_key":
        raise ValueError("PAPER caller audit metadata requires one exact bucket_key")
    bucket_key = audit_metadata.get("bucket_key")
    if bucket_key != calibrated.bucket_key:
        raise ValueError("PAPER bucket_key must match calibrated bucket identity")
    collisions = sorted(set(normalized_keys) & _CALIBRATION_AUDIT_KEYS)
    if collisions:
        raise ValueError(
            f"PAPER caller audit metadata cannot override calibration-owned keys: {collisions}"
        )
    return {
        **audit_metadata,
        "calibration": dict(calibrated.audit_metadata()),
    }


def paper_scan_decision_id(
    *,
    calibrated: CalibratedProbability,
    scope: RiskScope,
    weather: WeatherInputSnapshot,
    event: MarketEventSnapshot,
    decision_book: OrderBookSnapshot,
) -> str:
    """Stable exact-evidence identity; changed calibration or market evidence creates a new decision."""
    _validate_calibrated_context(calibrated=calibrated, scope=scope, weather=weather)
    material = "\n".join(
        (
            calibrated.calibration_fingerprint(),
            str(scope.market_id),
            str(scope.outcome_id),
            fingerprint(weather),
            fingerprint(event),
            decision_book.book_hash,
        )
    ).encode()
    return f"paper_scan_{hashlib.sha256(material).hexdigest()}"


def recover_paper_runtime(*, runtime: PaperRuntimeConfig) -> StartupRecovery:
    """Reconcile durable PAPER orders before any new scanner work starts."""
    with runtime.open_store() as store:
        return PaperTradingService(store).recover()


def submit_scanner_candidate(
    *,
    runtime: PaperRuntimeConfig,
    strategy_id: str,
    calibrated: CalibratedProbability,
    scope: RiskScope,
    weather: WeatherInputSnapshot,
    event: MarketEventSnapshot,
    decision_book: OrderBookSnapshot,
    condition_id: ConditionId,
    token_id: OutcomeTokenId,
    freshness_policy: FreshnessPolicy,
    cost_policy: CostPolicy,
    fetch_book: PaperBookFetcher,
    audit_metadata: Mapping[str, object],
    owner_id: str,
) -> PaperEntryResult:
    """Execute one calibrated PAPER candidate using durable state and a fresh submit-time book."""
    _validate_calibrated_context(calibrated=calibrated, scope=scope, weather=weather)
    decision_id = paper_scan_decision_id(
        calibrated=calibrated,
        scope=scope,
        weather=weather,
        event=event,
        decision_book=decision_book,
    )
    caller_audit = _scanner_audit_metadata(
        calibrated=calibrated,
        audit_metadata=audit_metadata,
    )
    with runtime.open_store() as store:
        service = PaperTradingService(store)
        service.recover()
        valuation_books = load_open_position_books(store, fetch_book)
        execution_book = fetch_book(condition_id, token_id)
        evaluated_at = _utc_now()
        request = PaperEntryRequest(
            strategy_id=strategy_id,
            decision_id=decision_id,
            model_version=calibrated.model_version,
            model_probability=calibrated.model_probability,
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
            audit_metadata=caller_audit,
        )
        return service.submit_entry(request, owner_id=owner_id)


def _pristine_status(runtime: PaperRuntimeConfig) -> PaperStatus:
    zero = Money.zero(runtime.starting_cash.currency)
    return PaperStatus(
        starting_cash=runtime.starting_cash,
        cash=runtime.starting_cash,
        reserved_cash=zero,
        available_cash=runtime.starting_cash,
        market_value=zero,
        realized_pnl=zero,
        unrealized_pnl=zero,
        fees=zero,
        exposure=zero,
        equity=runtime.starting_cash,
        high_water_mark=runtime.starting_cash,
        drawdown=zero,
        open_positions=0,
    )


def paper_runtime_status(
    *,
    runtime: PaperRuntimeConfig,
    observed_at: datetime,
    freshness_policy: FreshnessPolicy,
    cost_policy: CostPolicy,
    fetch_book: PaperBookFetcher,
) -> PaperStatus:
    """Report PAPER state without creating, migrating, recovering, or appending ledger data."""
    if not runtime.ledger_path.exists():
        return _pristine_status(runtime)
    with runtime.open_read_only_store() as store:
        books: Mapping[PositionKey, OrderBookSnapshot] = load_open_position_books(store, fetch_book)
        return PaperTradingService(store).status(
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
