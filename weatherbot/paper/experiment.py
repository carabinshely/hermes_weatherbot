"""Deterministic internal PAPER strategy-evaluation experiments.

The experiment engine replays the exact public producer decision function over frozen
read-only evidence, then optionally evaluates hypothetical sizing/risk/fill economics and
frozen settlement evidence in an isolated temporary ledger. Simulated economic state is
never an input to public signal generation.
"""

from __future__ import annotations

import tempfile
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from enum import StrEnum
from pathlib import Path
from typing import cast

from weatherbot.domain import (
    EventId,
    MarketId,
    MarketResolution,
    MarketResolved,
    Money,
    OutcomeId,
    OutcomePayout,
    PositionKey,
    PositionSettled,
    PositionStatus,
    RiskScope,
    fingerprint,
)
from weatherbot.markets import OrderBookSnapshot
from weatherbot.paper.ledger import initialize_paper_store
from weatherbot.paper.service import PaperEntryRequest, PaperEntryResult, PaperTradingService
from weatherbot.persistence import PortfolioRiskEventStore
from weatherbot.producer.config import ProducerPolicy
from weatherbot.producer.model import CalibratedMarketCandidate, HermesSignal
from weatherbot.producer.service import evaluate_candidate
from weatherbot.quoting import CostPolicy, FreshnessPolicy, QuoteEvaluation
from weatherbot.risk import PortfolioRiskPolicy, SizingPolicy

_ENGINE_VERSION = "paper-experiment-v2"


def _empty_metadata() -> Mapping[str, object]:
    return cast(Mapping[str, object], {})


def _empty_valuation_books() -> Mapping[PositionKey, OrderBookSnapshot]:
    return cast(Mapping[PositionKey, OrderBookSnapshot], {})


def _duration_seconds(value: timedelta) -> str:
    seconds = Decimal(value.days * 86400 + value.seconds) + (
        Decimal(value.microseconds) / Decimal(1_000_000)
    )
    return format(seconds, "f")


def _money_identity(value: Money) -> Mapping[str, object]:
    return {
        "amount": format(value.amount, "f"),
        "currency": value.currency,
    }


def _economic_identity(config: PaperEconomicConfig) -> Mapping[str, object]:
    freshness = config.freshness_policy
    costs = config.cost_policy
    sizing = config.sizing_policy
    portfolio = config.portfolio_policy
    return {
        "enabled": config.enabled,
        "starting_cash": _money_identity(config.starting_cash),
        "freshness_policy": {
            "maximum_forecast_age_seconds": _duration_seconds(freshness.maximum_forecast_age),
            "maximum_event_age_seconds": _duration_seconds(freshness.maximum_event_age),
            "maximum_order_book_age_seconds": _duration_seconds(freshness.maximum_order_book_age),
            "maximum_balance_age_seconds": _duration_seconds(freshness.maximum_balance_age),
            "future_tolerance_seconds": _duration_seconds(freshness.future_tolerance),
        },
        "cost_policy": {
            "platform_fee_rate": format(costs.platform_fee_rate, "f"),
            "transaction_cost": format(costs.transaction_cost, "f"),
            "safety_margin_rate": format(costs.safety_margin_rate, "f"),
            "maximum_average_slippage": format(costs.maximum_average_slippage, "f"),
            "maximum_worst_slippage": format(costs.maximum_worst_slippage, "f"),
            "maximum_all_in_price": format(costs.maximum_all_in_price, "f"),
            "minimum_expected_return": format(costs.minimum_expected_return, "f"),
            "depth_policy": costs.depth_policy.value,
        },
        "sizing_policy": {
            "fractional_kelly_multiplier": format(sizing.fractional_kelly_multiplier, "f"),
            "maximum_cash_per_trade": _money_identity(sizing.maximum_cash_per_trade),
            "maximum_iterations": sizing.maximum_iterations,
        },
        "portfolio_policy": {
            "maximum_total_exposure": _money_identity(portfolio.maximum_total_exposure),
            "maximum_event_exposure": _money_identity(portfolio.maximum_event_exposure),
            "maximum_city_date_exposure": _money_identity(portfolio.maximum_city_date_exposure),
            "maximum_correlation_group_exposure": _money_identity(
                portfolio.maximum_correlation_group_exposure
            ),
            "maximum_open_positions": portfolio.maximum_open_positions,
            "maximum_daily_loss": _money_identity(portfolio.maximum_daily_loss),
            "maximum_drawdown": _money_identity(portfolio.maximum_drawdown),
            "maximum_valuation_age_seconds": _duration_seconds(portfolio.maximum_valuation_age),
            "future_tolerance_seconds": _duration_seconds(portfolio.future_tolerance),
            "loss_timezone": portfolio.loss_timezone,
        },
    }


class EconomicEvaluationStatus(StrEnum):
    DISABLED = "disabled"
    UNAVAILABLE = "unavailable"
    EVALUATED = "evaluated"


@dataclass(frozen=True, slots=True)
class PaperSettlementEvidence:
    """Frozen hypothetical resolution for the selected outcome in one evidence case."""

    resolved_at: datetime
    outcome_payout: Decimal

    def __post_init__(self) -> None:
        if self.resolved_at.tzinfo is None or self.resolved_at.utcoffset() is None:
            raise ValueError("paper settlement resolved_at must be timezone-aware")
        payout = Decimal(self.outcome_payout)
        if payout < 0 or payout > 1:
            raise ValueError("paper settlement outcome_payout must be between zero and one")
        object.__setattr__(self, "outcome_payout", payout)


@dataclass(frozen=True, slots=True)
class PaperEvidenceCase:
    """One frozen public-producer candidate plus optional hypothetical economic evidence."""

    case_id: str
    decision_at: datetime
    candidate: CalibratedMarketCandidate
    execution_book: OrderBookSnapshot | None = None
    valuation_books: Mapping[PositionKey, OrderBookSnapshot] = field(
        default_factory=_empty_valuation_books
    )
    settlement: PaperSettlementEvidence | None = None
    correlation_groups: tuple[str, ...] = ()
    metadata: Mapping[str, object] = field(default_factory=_empty_metadata)

    def __post_init__(self) -> None:
        if not self.case_id.strip():
            raise ValueError("paper evidence case_id must not be blank")
        if self.decision_at.tzinfo is None or self.decision_at.utcoffset() is None:
            raise ValueError("paper evidence decision_at must be timezone-aware")
        if any(not group.strip() for group in self.correlation_groups):
            raise ValueError("paper correlation groups must not be blank")
        normalized_groups = tuple(
            sorted({group.strip().casefold() for group in self.correlation_groups})
        )
        object.__setattr__(self, "correlation_groups", normalized_groups)
        if self.execution_book is not None:
            if self.execution_book.condition_id != self.candidate.decision_book.condition_id:
                raise ValueError("execution evidence must use the decision-book condition")
            if self.execution_book.token_id != self.candidate.decision_book.token_id:
                raise ValueError("execution evidence must use the decision-book token")
        if self.settlement is not None and self.settlement.resolved_at < self.decision_at:
            raise ValueError("paper settlement cannot precede the strategy decision")

    @property
    def scope(self) -> RiskScope:
        return RiskScope(
            market_id=MarketId(self.candidate.market_id),
            outcome_id=OutcomeId(self.candidate.token_id),
            event_id=self.candidate.event_id,
            city_key=self.candidate.city_slug,
            market_date=self.candidate.market_date,
            correlation_groups=self.correlation_groups,
        )

    @property
    def evidence_fingerprint(self) -> str:
        return fingerprint(
            {
                "case_id": self.case_id,
                "decision_at": self.decision_at.astimezone(UTC),
                "candidate": self.candidate,
                "execution_book": self.execution_book,
                "valuation_books": self.valuation_books,
                "settlement": self.settlement,
                "correlation_groups": self.correlation_groups,
                "metadata": self.metadata,
            }
        )


@dataclass(frozen=True, slots=True)
class PaperEconomicConfig:
    """Explicit experiment-only economics; never part of public signal generation."""

    starting_cash: Money
    freshness_policy: FreshnessPolicy
    cost_policy: CostPolicy
    sizing_policy: SizingPolicy
    portfolio_policy: PortfolioRiskPolicy
    enabled: bool = True

    def __post_init__(self) -> None:
        if self.starting_cash.amount <= 0:
            raise ValueError("paper experiment starting cash must be positive")


@dataclass(frozen=True, slots=True)
class PaperExperimentSpec:
    """Versioned strategy policy and frozen evidence defining one reproducible experiment."""

    policy: ProducerPolicy
    evidence_cases: tuple[PaperEvidenceCase, ...]
    economics: PaperEconomicConfig | None = None
    engine_version: str = _ENGINE_VERSION

    def __post_init__(self) -> None:
        if self.engine_version != _ENGINE_VERSION:
            raise ValueError(f"paper engine_version must match running engine {_ENGINE_VERSION!r}")
        if not self.evidence_cases:
            raise ValueError("paper experiment requires at least one evidence case")
        case_ids = [case.case_id for case in self.evidence_cases]
        if len(case_ids) != len(set(case_ids)):
            raise ValueError("paper experiment case_id values must be unique")
        ordered = tuple(
            sorted(
                self.evidence_cases,
                key=lambda case: (case.decision_at, case.case_id),
            )
        )
        if ordered != self.evidence_cases:
            raise ValueError("paper evidence cases must be ordered by decision_at then case_id")

    @property
    def strategy_id(self) -> str:
        return self.policy.strategy_id

    @property
    def strategy_version(self) -> str:
        return self.policy.strategy_version

    @property
    def experiment_id(self) -> str:
        economics_identity: object = None
        if self.economics is not None:
            economics_identity = _economic_identity(self.economics)
        digest = fingerprint(
            {
                "engine_version": self.engine_version,
                "producer_policy_fingerprint": self.policy.fingerprint,
                "producer_policy": self.policy.identity_mapping(),
                "evidence": [case.evidence_fingerprint for case in self.evidence_cases],
                "economics": economics_identity,
            }
        )
        return f"paper_exp_{digest}"


@dataclass(frozen=True, slots=True)
class PaperSettlementResult:
    resolved_at: datetime
    outcome_payout: Decimal
    realized_pnl: Money
    cash_after: Money
    appended_events: int


@dataclass(frozen=True, slots=True)
class PaperCaseResult:
    case_id: str
    evidence_fingerprint: str
    signal: HermesSignal | None
    producer_evaluation: QuoteEvaluation
    economic_status: EconomicEvaluationStatus
    economic_result: PaperEntryResult | None
    economic_reason: str | None
    settlement_result: PaperSettlementResult | None
    settlement_reason: str | None

    @property
    def would_emit(self) -> bool:
        return self.signal is not None


@dataclass(frozen=True, slots=True)
class PaperExperimentResult:
    experiment_id: str
    engine_version: str
    strategy_id: str
    strategy_version: str
    policy_fingerprint: str
    cases: tuple[PaperCaseResult, ...]

    @property
    def would_emit_count(self) -> int:
        return sum(1 for case in self.cases if case.would_emit)

    @property
    def economically_evaluated_count(self) -> int:
        return sum(
            1 for case in self.cases if case.economic_status is EconomicEvaluationStatus.EVALUATED
        )

    @property
    def hypothetically_settled_count(self) -> int:
        return sum(1 for case in self.cases if case.settlement_result is not None)


def _economic_request(
    *,
    spec: PaperExperimentSpec,
    case: PaperEvidenceCase,
    signal: HermesSignal,
) -> PaperEntryRequest:
    economics = spec.economics
    if economics is None:
        raise ValueError("economic request requires experiment economics")
    if case.execution_book is None:
        raise ValueError("economic request requires frozen execution evidence")
    candidate = case.candidate
    if signal.model_probability != candidate.calibrated.model_probability:
        raise ValueError("public signal probability must equal calibrated evidence probability")
    return PaperEntryRequest(
        strategy_id=f"{spec.strategy_id}@{spec.strategy_version}",
        decision_id=f"{spec.experiment_id}:{case.case_id}",
        model_version=candidate.calibrated.model_version,
        model_probability=candidate.calibrated.model_probability,
        scope=case.scope,
        weather=candidate.weather,
        event=candidate.event,
        decision_order_book=candidate.decision_book,
        execution_order_book=case.execution_book,
        valuation_books=case.valuation_books,
        evaluated_at=case.decision_at,
        freshness_policy=economics.freshness_policy,
        cost_policy=economics.cost_policy,
        sizing_policy=economics.sizing_policy,
        portfolio_policy=economics.portfolio_policy,
        audit_metadata={
            **dict(case.metadata),
            "bucket_key": candidate.calibrated.bucket_key,
            "paper_experiment_id": spec.experiment_id,
            "paper_evidence_fingerprint": case.evidence_fingerprint,
            "producer_policy_fingerprint": spec.policy.fingerprint,
            "hermes_signal_id": signal.signal_id,
            "calibration": dict(candidate.calibrated.audit_metadata()),
            "hypothetical": True,
        },
    )


def _settlement_event_id(
    kind: str,
    *,
    spec: PaperExperimentSpec,
    case: PaperEvidenceCase,
) -> EventId:
    digest = fingerprint(
        {
            "kind": kind,
            "experiment_id": spec.experiment_id,
            "case_id": case.case_id,
            "settlement": case.settlement,
        }
    )
    return EventId(f"paper_{kind}_{digest}")


def _settle_hypothetical_position(
    *,
    store: PortfolioRiskEventStore,
    spec: PaperExperimentSpec,
    case: PaperEvidenceCase,
) -> tuple[PaperSettlementResult | None, str | None]:
    settlement = case.settlement
    if settlement is None:
        return None, None

    before = store.load_state()
    position = before.positions.get(case.scope.position_key)
    if position is None or position.status is not PositionStatus.OPEN or position.quantity <= 0:
        return None, "no hypothetical open position is available for settlement"
    if position.reserved_quantity != 0:
        return None, "hypothetical position still has reserved quantity"

    resolution = MarketResolution(
        market_id=case.scope.market_id,
        payouts=(
            OutcomePayout(
                outcome_id=case.scope.outcome_id,
                payout=settlement.outcome_payout,
            ),
        ),
        resolved_at=settlement.resolved_at,
    )
    appended = store.append_many(
        (
            MarketResolved(
                event_id=_settlement_event_id("resolved", spec=spec, case=case),
                occurred_at=settlement.resolved_at,
                resolution=resolution,
            ),
            PositionSettled(
                event_id=_settlement_event_id("settled", spec=spec, case=case),
                occurred_at=settlement.resolved_at,
                market_id=case.scope.market_id,
                outcome_id=case.scope.outcome_id,
                fee=Money.zero(before.currency),
            ),
        )
    )
    settled = appended.state.positions[case.scope.position_key]
    return (
        PaperSettlementResult(
            resolved_at=settlement.resolved_at,
            outcome_payout=settlement.outcome_payout,
            realized_pnl=settled.realized_pnl,
            cash_after=appended.state.cash,
            appended_events=len(appended.appended_sequences),
        ),
        None,
    )


def _pending_settlement_sort_key(
    item: tuple[int, PaperEvidenceCase],
) -> tuple[datetime, str]:
    settlement = item[1].settlement
    assert settlement is not None
    return settlement.resolved_at, item[1].case_id


def _settle_due_positions(
    *,
    store: PortfolioRiskEventStore,
    spec: PaperExperimentSpec,
    pending: list[tuple[int, PaperEvidenceCase]],
    results: list[PaperCaseResult],
    before: datetime | None,
) -> None:
    """Apply only settlements known strictly before the next decision time.

    A settlement at or after a decision timestamp must never change the bankroll, loss,
    drawdown, or exposure state used by that decision. Remaining settlements are applied
    after all decisions have been evaluated.
    """
    due: list[tuple[int, PaperEvidenceCase]] = []
    remaining: list[tuple[int, PaperEvidenceCase]] = []
    for index, case in pending:
        settlement = case.settlement
        assert settlement is not None
        if before is None or settlement.resolved_at < before:
            due.append((index, case))
        else:
            remaining.append((index, case))

    for index, case in sorted(due, key=_pending_settlement_sort_key):
        settlement_result, settlement_reason = _settle_hypothetical_position(
            store=store,
            spec=spec,
            case=case,
        )
        results[index] = replace(
            results[index],
            settlement_result=settlement_result,
            settlement_reason=settlement_reason,
        )
    pending[:] = remaining


class PaperExperimentEngine:
    """Replay public strategy decisions, then optionally simulate isolated economics."""

    def evaluate(self, spec: PaperExperimentSpec) -> PaperExperimentResult:
        experiment_id = spec.experiment_id

        # Public decisions are evaluated first for the complete frozen evidence set.
        # No simulated bankroll, position, ledger, fill, or prior PAPER outcome exists yet.
        public_results = tuple(
            evaluate_candidate(
                case.candidate,
                spec.policy,
                evaluated_at=case.decision_at,
            )
            for case in spec.evidence_cases
        )

        economics = spec.economics
        service: PaperTradingService | None = None
        tempdir: tempfile.TemporaryDirectory[str] | None = None
        store: PortfolioRiskEventStore | None = None
        pending_settlements: list[tuple[int, PaperEvidenceCase]] = []

        if economics is not None and economics.enabled:
            opened_at = spec.evidence_cases[0].decision_at.astimezone(UTC)
            tempdir = tempfile.TemporaryDirectory(prefix=f"{experiment_id[:24]}-")
            store = initialize_paper_store(
                Path(tempdir.name) / "experiment.sqlite3",
                starting_cash=economics.starting_cash,
                opened_at=opened_at,
            )
            service = PaperTradingService(store, clock=lambda: opened_at)

        results: list[PaperCaseResult] = []
        try:
            for case, (signal, producer_evaluation) in zip(
                spec.evidence_cases,
                public_results,
                strict=True,
            ):
                if store is not None:
                    _settle_due_positions(
                        store=store,
                        spec=spec,
                        pending=pending_settlements,
                        results=results,
                        before=case.decision_at,
                    )

                economic_status = EconomicEvaluationStatus.DISABLED
                economic_result: PaperEntryResult | None = None
                economic_reason: str | None = None
                settlement_result: PaperSettlementResult | None = None
                settlement_reason: str | None = None

                if economics is not None and economics.enabled:
                    if signal is None:
                        economic_status = EconomicEvaluationStatus.UNAVAILABLE
                        rejection = producer_evaluation.rejection_reason
                        reason = "public producer rejected candidate"
                        if rejection is not None:
                            reason = f"{reason}: {rejection.value}"
                        economic_reason = reason
                    elif case.execution_book is None:
                        economic_status = EconomicEvaluationStatus.UNAVAILABLE
                        economic_reason = "frozen execution evidence is unavailable"
                    else:
                        assert service is not None
                        request = _economic_request(
                            spec=spec,
                            case=case,
                            signal=signal,
                        )
                        economic_result = service.submit_entry(
                            request,
                            owner_id=f"experiment:{experiment_id}",
                        )
                        economic_status = EconomicEvaluationStatus.EVALUATED

                if (case.settlement is not None and economics is None) or (
                    case.settlement is not None and economics is not None and not economics.enabled
                ):
                    settlement_reason = "hypothetical settlement requires enabled economics"
                elif (
                    case.settlement is not None
                    and economic_status is not EconomicEvaluationStatus.EVALUATED
                ):
                    settlement_reason = (
                        "hypothetical settlement requires an evaluated economic entry"
                    )

                results.append(
                    PaperCaseResult(
                        case_id=case.case_id,
                        evidence_fingerprint=case.evidence_fingerprint,
                        signal=signal,
                        producer_evaluation=producer_evaluation,
                        economic_status=economic_status,
                        economic_result=economic_result,
                        economic_reason=economic_reason,
                        settlement_result=settlement_result,
                        settlement_reason=settlement_reason,
                    )
                )
                if (
                    case.settlement is not None
                    and economic_status is EconomicEvaluationStatus.EVALUATED
                ):
                    pending_settlements.append((len(results) - 1, case))

            if store is not None:
                _settle_due_positions(
                    store=store,
                    spec=spec,
                    pending=pending_settlements,
                    results=results,
                    before=None,
                )
        finally:
            if store is not None:
                store.close()
            if tempdir is not None:
                tempdir.cleanup()

        return PaperExperimentResult(
            experiment_id=experiment_id,
            engine_version=spec.engine_version,
            strategy_id=spec.strategy_id,
            strategy_version=spec.strategy_version,
            policy_fingerprint=spec.policy.fingerprint,
            cases=tuple(results),
        )
