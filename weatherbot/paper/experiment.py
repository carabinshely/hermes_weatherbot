"""Deterministic internal PAPER strategy-evaluation experiments.

This module deliberately separates the strategy question (would this versioned strategy
emit a signal for frozen evidence?) from optional hypothetical economics. Economic state
may change sizing/risk/fill results, but it is never an input to ``StrategyDecision``.
"""

from __future__ import annotations

import tempfile
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from enum import StrEnum
from pathlib import Path
from typing import cast

from weatherbot.domain import Money, PositionKey, RiskScope, fingerprint
from weatherbot.forecasting import CalibratedProbability, WeatherInputSnapshot
from weatherbot.markets import OrderBookSnapshot
from weatherbot.paper.ledger import initialize_paper_store
from weatherbot.paper.service import PaperEntryRequest, PaperEntryResult, PaperTradingService
from weatherbot.persistence import PortfolioRiskEventStore
from weatherbot.quoting import CostPolicy, FreshnessPolicy, MarketEventSnapshot
from weatherbot.risk import PortfolioRiskPolicy, SizingPolicy

_ENGINE_VERSION = "paper-experiment-v1"


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
            "fractional_kelly_multiplier": format(
                sizing.fractional_kelly_multiplier,
                "f",
            ),
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
class StrategyDecision:
    """Strategy-only decision shared conceptually with the future public producer path."""

    would_emit: bool
    classification: str
    model_probability: Decimal
    market_reference_price: Decimal
    expected_edge: Decimal
    reason: str
    metadata: Mapping[str, object] = field(default_factory=_empty_metadata)

    def __post_init__(self) -> None:
        if not self.classification.strip():
            raise ValueError("strategy classification must not be blank")
        if not self.reason.strip():
            raise ValueError("strategy decision reason must not be blank")
        probability = Decimal(self.model_probability)
        reference = Decimal(self.market_reference_price)
        edge = Decimal(self.expected_edge)
        if probability < 0 or probability > 1:
            raise ValueError("strategy model probability must be between zero and one")
        if reference <= 0 or reference > 1:
            raise ValueError("strategy market reference price must be within (0, 1]")
        object.__setattr__(self, "model_probability", probability)
        object.__setattr__(self, "market_reference_price", reference)
        object.__setattr__(self, "expected_edge", edge)


@dataclass(frozen=True, slots=True)
class PaperEvidenceCase:
    """One frozen, pre-strategy evidence case suitable for deterministic replay."""

    case_id: str
    decision_at: datetime
    calibrated: CalibratedProbability
    scope: RiskScope
    weather: WeatherInputSnapshot
    event: MarketEventSnapshot
    decision_book: OrderBookSnapshot
    execution_book: OrderBookSnapshot | None = None
    valuation_books: Mapping[PositionKey, OrderBookSnapshot] = field(
        default_factory=_empty_valuation_books
    )
    metadata: Mapping[str, object] = field(default_factory=_empty_metadata)

    def __post_init__(self) -> None:
        if not self.case_id.strip():
            raise ValueError("paper evidence case_id must not be blank")
        if self.decision_at.tzinfo is None or self.decision_at.utcoffset() is None:
            raise ValueError("paper evidence decision_at must be timezone-aware")
        if self.calibrated.city_slug != self.scope.city_key:
            raise ValueError("calibrated city_slug must match evidence risk scope")
        if self.calibrated.model_probability <= 0 or self.calibrated.model_probability >= 1:
            raise ValueError("paper evidence requires scanner-eligible calibrated probability")
        if str(self.scope.outcome_id) != str(self.decision_book.token_id):
            raise ValueError("paper evidence scope outcome must match decision-book token")
        if self.execution_book is not None:
            if self.execution_book.condition_id != self.decision_book.condition_id:
                raise ValueError("execution evidence must use the decision-book condition")
            if self.execution_book.token_id != self.decision_book.token_id:
                raise ValueError("execution evidence must use the decision-book token")

    @property
    def evidence_fingerprint(self) -> str:
        return fingerprint(
            {
                "case_id": self.case_id,
                "decision_at": self.decision_at.astimezone(UTC),
                "calibration": dict(self.calibrated.audit_metadata()),
                "scope": self.scope,
                "weather": self.weather,
                "event": self.event,
                "decision_book": self.decision_book,
                "execution_book": self.execution_book,
                "valuation_books": self.valuation_books,
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
    strategy_id: str
    strategy_version: str
    strategy_parameters: Mapping[str, object]
    evidence_cases: tuple[PaperEvidenceCase, ...]
    economics: PaperEconomicConfig | None = None
    engine_version: str = _ENGINE_VERSION

    def __post_init__(self) -> None:
        if not self.strategy_id.strip():
            raise ValueError("paper strategy_id must not be blank")
        if not self.strategy_version.strip():
            raise ValueError("paper strategy_version must not be blank")
        if not self.engine_version.strip():
            raise ValueError("paper engine_version must not be blank")
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
    def experiment_id(self) -> str:
        economics_identity: object = None
        if self.economics is not None:
            economics_identity = _economic_identity(self.economics)
        digest = fingerprint(
            {
                "engine_version": self.engine_version,
                "strategy_id": self.strategy_id,
                "strategy_version": self.strategy_version,
                "strategy_parameters": self.strategy_parameters,
                "evidence": [case.evidence_fingerprint for case in self.evidence_cases],
                "economics": economics_identity,
            }
        )
        return f"paper_exp_{digest}"


@dataclass(frozen=True, slots=True)
class PaperCaseResult:
    case_id: str
    evidence_fingerprint: str
    strategy: StrategyDecision
    economic_status: EconomicEvaluationStatus
    economic_result: PaperEntryResult | None
    economic_reason: str | None


@dataclass(frozen=True, slots=True)
class PaperExperimentResult:
    experiment_id: str
    engine_version: str
    strategy_id: str
    strategy_version: str
    cases: tuple[PaperCaseResult, ...]

    @property
    def would_emit_count(self) -> int:
        return sum(1 for case in self.cases if case.strategy.would_emit)

    @property
    def economically_evaluated_count(self) -> int:
        return sum(
            1 for case in self.cases if case.economic_status is EconomicEvaluationStatus.EVALUATED
        )


StrategyEvaluator = Callable[[PaperEvidenceCase, Mapping[str, object]], StrategyDecision]


def _economic_request(
    *,
    spec: PaperExperimentSpec,
    case: PaperEvidenceCase,
    decision: StrategyDecision,
) -> PaperEntryRequest:
    economics = spec.economics
    if economics is None:
        raise ValueError("economic request requires experiment economics")
    if case.execution_book is None:
        raise ValueError("economic request requires frozen execution evidence")
    if decision.model_probability != case.calibrated.model_probability:
        raise ValueError("strategy decision probability must equal calibrated evidence probability")
    return PaperEntryRequest(
        strategy_id=f"{spec.strategy_id}@{spec.strategy_version}",
        decision_id=f"{spec.experiment_id}:{case.case_id}",
        model_version=case.calibrated.model_version,
        model_probability=case.calibrated.model_probability,
        scope=case.scope,
        weather=case.weather,
        event=case.event,
        decision_order_book=case.decision_book,
        execution_order_book=case.execution_book,
        valuation_books=case.valuation_books,
        evaluated_at=case.decision_at,
        freshness_policy=economics.freshness_policy,
        cost_policy=economics.cost_policy,
        sizing_policy=economics.sizing_policy,
        portfolio_policy=economics.portfolio_policy,
        audit_metadata={
            **dict(case.metadata),
            "bucket_key": case.calibrated.bucket_key,
            "paper_experiment_id": spec.experiment_id,
            "paper_evidence_fingerprint": case.evidence_fingerprint,
            "calibration": dict(case.calibrated.audit_metadata()),
            "hypothetical": True,
        },
    )


class PaperExperimentEngine:
    """Evaluate frozen evidence without any global PAPER account or network dependency."""

    def evaluate(
        self,
        spec: PaperExperimentSpec,
        *,
        strategy_evaluator: StrategyEvaluator,
    ) -> PaperExperimentResult:
        experiment_id = spec.experiment_id
        economics = spec.economics
        service: PaperTradingService | None = None
        tempdir: tempfile.TemporaryDirectory[str] | None = None
        store: PortfolioRiskEventStore | None = None

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
            for case in spec.evidence_cases:
                decision = strategy_evaluator(case, spec.strategy_parameters)
                if decision.model_probability != case.calibrated.model_probability:
                    raise ValueError(
                        f"strategy evaluator changed calibrated probability for case {case.case_id}"
                    )

                economic_status = EconomicEvaluationStatus.DISABLED
                economic_result: PaperEntryResult | None = None
                economic_reason: str | None = None

                if economics is not None and economics.enabled:
                    if not decision.would_emit:
                        economic_status = EconomicEvaluationStatus.UNAVAILABLE
                        economic_reason = "strategy would not emit a signal"
                    elif case.execution_book is None:
                        economic_status = EconomicEvaluationStatus.UNAVAILABLE
                        economic_reason = "frozen execution evidence is unavailable"
                    else:
                        assert service is not None
                        request = _economic_request(
                            spec=spec,
                            case=case,
                            decision=decision,
                        )
                        economic_result = service.submit_entry(
                            request,
                            owner_id=f"experiment:{experiment_id}",
                        )
                        economic_status = EconomicEvaluationStatus.EVALUATED

                results.append(
                    PaperCaseResult(
                        case_id=case.case_id,
                        evidence_fingerprint=case.evidence_fingerprint,
                        strategy=decision,
                        economic_status=economic_status,
                        economic_result=economic_result,
                        economic_reason=economic_reason,
                    )
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
            cases=tuple(results),
        )
