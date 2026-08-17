from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
from decimal import Decimal
from pathlib import Path

from tests.paper.helpers import calibrated_probability, paper_book, scope
from tests.quoting.helpers import NOW, cost_policy, event_snapshot, freshness_policy, weather_snapshot
from tests.risk.helpers import policy as sizing_policy
from tests.risk.portfolio_helpers import policy as portfolio_policy
from weatherbot.domain import Money
from weatherbot.paper.experiment import (
    EconomicEvaluationStatus,
    PaperEconomicConfig,
    PaperEvidenceCase,
    PaperExperimentEngine,
    PaperExperimentSpec,
    StrategyDecision,
)
from weatherbot.paper.io import write_experiment_artifacts


def _case(*, execution: bool = True) -> PaperEvidenceCase:
    calibrated = calibrated_probability()
    decision_book = paper_book(book_hash="experiment-decision")
    return PaperEvidenceCase(
        case_id="chicago-2026-08-06-F85-86",
        decision_at=NOW,
        calibrated=calibrated,
        scope=scope(),
        weather=weather_snapshot(),
        event=event_snapshot(),
        decision_book=decision_book,
        execution_book=(paper_book(book_hash="experiment-execution") if execution else None),
        metadata={"bucket_key": calibrated.bucket_key, "fixture": "issue-59"},
    )


def _economics(starting_cash: str) -> PaperEconomicConfig:
    return PaperEconomicConfig(
        starting_cash=Money.of(starting_cash),
        freshness_policy=freshness_policy(),
        cost_policy=cost_policy(),
        sizing_policy=sizing_policy(maximum_cash="2"),
        portfolio_policy=portfolio_policy(
            total="50",
            event="25",
            city_date="25",
            correlation="25",
            positions=10,
            daily_loss="20",
            drawdown="50",
        ),
    )


def _strategy(
    case: PaperEvidenceCase,
    parameters: Mapping[str, object],
) -> StrategyDecision:
    threshold = Decimal(str(parameters["minimum_edge"]))
    probability = case.calibrated.model_probability
    market = case.decision_book.best_ask
    edge = probability - market
    return StrategyDecision(
        would_emit=edge >= threshold,
        classification="candidate-buy" if edge >= threshold else "below-threshold",
        model_probability=probability,
        market_reference_price=market,
        expected_edge=edge,
        reason=(
            "candidate threshold satisfied" if edge >= threshold else "candidate threshold not met"
        ),
        metadata={"threshold": format(threshold, "f")},
    )


def _spec(*, starting_cash: str = "100", execution: bool = True) -> PaperExperimentSpec:
    return PaperExperimentSpec(
        strategy_id="weather-threshold",
        strategy_version="candidate-v1",
        strategy_parameters={"minimum_edge": "0.10"},
        evidence_cases=(_case(execution=execution),),
        economics=_economics(starting_cash),
    )


def test_strategy_decision_is_independent_of_experiment_bankroll() -> None:
    engine = PaperExperimentEngine()
    rich = engine.evaluate(_spec(starting_cash="100"), strategy_evaluator=_strategy)
    constrained = engine.evaluate(_spec(starting_cash="1"), strategy_evaluator=_strategy)

    assert rich.cases[0].strategy == constrained.cases[0].strategy
    assert rich.cases[0].strategy.would_emit is True
    assert rich.cases[0].economic_status is EconomicEvaluationStatus.EVALUATED
    assert constrained.cases[0].economic_status is EconomicEvaluationStatus.EVALUATED
    assert rich.cases[0].economic_result is not None
    assert constrained.cases[0].economic_result is not None
    assert rich.cases[0].economic_result.status != constrained.cases[0].economic_result.status


def test_missing_execution_evidence_does_not_erase_strategy_signal() -> None:
    result = PaperExperimentEngine().evaluate(
        _spec(execution=False),
        strategy_evaluator=_strategy,
    )

    case = result.cases[0]
    assert case.strategy.would_emit is True
    assert result.would_emit_count == 1
    assert case.economic_status is EconomicEvaluationStatus.UNAVAILABLE
    assert case.economic_result is None
    assert case.economic_reason == "frozen execution evidence is unavailable"


def test_experiment_identity_covers_strategy_and_economic_policy() -> None:
    baseline = _spec()
    changed_strategy = replace(
        baseline,
        strategy_parameters={"minimum_edge": "0.11"},
    )
    changed_economics = replace(baseline, economics=_economics("99"))

    assert baseline.experiment_id != changed_strategy.experiment_id
    assert baseline.experiment_id != changed_economics.experiment_id


def test_canonical_outputs_are_byte_identical_on_rerun(tmp_path: Path) -> None:
    spec = _spec()
    engine = PaperExperimentEngine()
    first = engine.evaluate(spec, strategy_evaluator=_strategy)
    second = engine.evaluate(spec, strategy_evaluator=_strategy)

    assert first == second
    first_artifacts = write_experiment_artifacts(first, output_directory=tmp_path)
    before = {
        path.name: path.read_bytes()
        for path in (
            first_artifacts.summary_path,
            first_artifacts.evaluations_path,
            first_artifacts.checksums_path,
        )
    }
    second_artifacts = write_experiment_artifacts(second, output_directory=tmp_path)
    after = {
        path.name: path.read_bytes()
        for path in (
            second_artifacts.summary_path,
            second_artifacts.evaluations_path,
            second_artifacts.checksums_path,
        )
    }

    assert before == after
