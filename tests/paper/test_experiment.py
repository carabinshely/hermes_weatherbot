from __future__ import annotations

import json
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from typing import cast

import pytest

from tests.paper.helpers import paper_book
from tests.producer.test_boundary import candidate, policy
from tests.quoting.helpers import NOW, cost_policy, freshness_policy
from tests.risk.helpers import policy as sizing_policy
from tests.risk.portfolio_helpers import policy as portfolio_policy
from weatherbot.domain import Money
from weatherbot.paper.experiment import (
    EconomicEvaluationStatus,
    PaperEconomicConfig,
    PaperEvidenceCase,
    PaperExperimentEngine,
    PaperExperimentSpec,
    PaperSettlementEvidence,
)
from weatherbot.paper.io import case_payload, summary_payload, write_experiment_artifacts
from weatherbot.producer.service import evaluate_candidate


def _case(
    *,
    execution: bool = True,
    settlement: bool = True,
) -> PaperEvidenceCase:
    item = candidate()
    return PaperEvidenceCase(
        case_id="chicago-2026-08-06-F85-86",
        decision_at=NOW,
        candidate=item,
        execution_book=(
            paper_book(book_hash="experiment-execution") if execution else None
        ),
        settlement=(
            PaperSettlementEvidence(
                resolved_at=NOW + timedelta(days=1),
                outcome_payout=Decimal("1"),
            )
            if settlement
            else None
        ),
        metadata={
            "fixture": "issue-59",
            "declared_resolution_source": "frozen-fixture",
        },
    )


def _economics(starting_cash: str, *, enabled: bool = True) -> PaperEconomicConfig:
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
        enabled=enabled,
    )


def _spec(
    *,
    starting_cash: str = "100",
    execution: bool = True,
    settlement: bool = True,
    economics_enabled: bool = True,
) -> PaperExperimentSpec:
    return PaperExperimentSpec(
        policy=replace(policy(), strategy_version="candidate-v1"),
        evidence_cases=(
            _case(execution=execution, settlement=settlement),
        ),
        economics=_economics(starting_cash, enabled=economics_enabled),
    )


def test_paper_replays_exact_public_signal_decision() -> None:
    spec = _spec(settlement=False)
    evidence = spec.evidence_cases[0]
    expected_signal, expected_evaluation = evaluate_candidate(
        evidence.candidate,
        spec.policy,
        evaluated_at=evidence.decision_at,
    )

    result = PaperExperimentEngine().evaluate(spec)
    case = result.cases[0]

    assert expected_signal is not None
    assert case.signal == expected_signal
    assert case.producer_evaluation == expected_evaluation
    assert case.would_emit is True
    assert result.would_emit_count == 1
    assert result.policy_fingerprint == spec.policy.fingerprint


def test_public_signal_is_independent_of_experiment_bankroll() -> None:
    engine = PaperExperimentEngine()
    rich = engine.evaluate(_spec(starting_cash="100", settlement=False))
    constrained = engine.evaluate(_spec(starting_cash="1", settlement=False))

    assert rich.cases[0].signal == constrained.cases[0].signal
    assert rich.cases[0].signal is not None
    assert rich.cases[0].economic_status is EconomicEvaluationStatus.EVALUATED
    assert constrained.cases[0].economic_status is EconomicEvaluationStatus.EVALUATED
    assert rich.cases[0].economic_result is not None
    assert constrained.cases[0].economic_result is not None
    assert rich.cases[0].economic_result != constrained.cases[0].economic_result


def test_missing_execution_evidence_does_not_erase_public_signal() -> None:
    result = PaperExperimentEngine().evaluate(_spec(execution=False))

    case = result.cases[0]
    assert case.signal is not None
    assert case.economic_status is EconomicEvaluationStatus.UNAVAILABLE
    assert case.economic_result is None
    assert case.economic_reason == "frozen execution evidence is unavailable"
    assert case.settlement_result is None
    assert case.settlement_reason == (
        "hypothetical settlement requires an evaluated economic entry"
    )


def test_rejected_public_candidate_is_not_revived_by_paper_economics() -> None:
    spec = _spec(settlement=False)
    spec = replace(
        spec,
        policy=replace(spec.policy, minimum_expected_return=Decimal("10")),
    )

    result = PaperExperimentEngine().evaluate(spec)
    case = result.cases[0]

    assert case.signal is None
    assert case.producer_evaluation.accepted is False
    assert case.economic_status is EconomicEvaluationStatus.UNAVAILABLE
    assert case.economic_result is None
    assert case.economic_reason is not None
    assert case.economic_reason.startswith("public producer rejected candidate")


def test_frozen_settlement_records_hypothetical_realized_pnl() -> None:
    result = PaperExperimentEngine().evaluate(_spec())
    case = result.cases[0]

    assert case.signal is not None
    assert case.economic_result is not None
    assert case.settlement_result is not None
    assert case.settlement_result.outcome_payout == Decimal("1")
    assert case.settlement_result.realized_pnl.amount > 0
    assert case.settlement_result.appended_events == 2
    assert result.hypothetically_settled_count == 1


def test_disabled_economics_leaves_public_decision_intact() -> None:
    result = PaperExperimentEngine().evaluate(
        _spec(economics_enabled=False),
    )
    case = result.cases[0]

    assert case.signal is not None
    assert case.economic_status is EconomicEvaluationStatus.DISABLED
    assert case.economic_result is None
    assert case.settlement_result is None
    assert case.settlement_reason == "hypothetical settlement requires enabled economics"


def test_no_economics_is_a_valid_signal_only_experiment() -> None:
    spec = replace(_spec(settlement=False), economics=None)
    result = PaperExperimentEngine().evaluate(spec)

    assert result.cases[0].signal is not None
    assert result.cases[0].economic_status is EconomicEvaluationStatus.DISABLED
    assert result.cases[0].economic_result is None


def test_experiment_identity_covers_public_policy_evidence_and_economics() -> None:
    baseline = _spec(settlement=False)
    changed_strategy = replace(
        baseline,
        policy=replace(baseline.policy, strategy_version="candidate-v2"),
    )
    changed_threshold = replace(
        baseline,
        policy=replace(
            baseline.policy,
            minimum_expected_return=baseline.policy.minimum_expected_return
            + Decimal("0.01"),
        ),
    )
    changed_economics = replace(baseline, economics=_economics("99"))
    changed_evidence = replace(
        baseline,
        evidence_cases=(
            replace(
                baseline.evidence_cases[0],
                metadata={"fixture": "changed"},
            ),
        ),
    )

    assert baseline.experiment_id != changed_strategy.experiment_id
    assert baseline.experiment_id != changed_threshold.experiment_id
    assert baseline.experiment_id != changed_economics.experiment_id
    assert baseline.experiment_id != changed_evidence.experiment_id


def test_canonical_outputs_are_byte_identical_on_rerun(tmp_path: Path) -> None:
    spec = _spec()
    engine = PaperExperimentEngine()
    first = engine.evaluate(spec)
    second = engine.evaluate(spec)

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


def test_artifacts_label_public_decision_and_economics_boundaries(tmp_path: Path) -> None:
    result = PaperExperimentEngine().evaluate(_spec())
    artifacts = write_experiment_artifacts(result, output_directory=tmp_path)

    summary_raw: object = json.loads(artifacts.summary_path.read_text(encoding="utf-8"))
    evaluation_raw: object = json.loads(
        artifacts.evaluations_path.read_text(encoding="utf-8").splitlines()[0]
    )
    assert isinstance(summary_raw, dict)
    assert isinstance(evaluation_raw, dict)
    summary = cast(dict[str, object], summary_raw)
    evaluation = cast(dict[str, object], evaluation_raw)
    public = cast(dict[str, object], evaluation["public_producer"])
    economics = cast(dict[str, object], evaluation["economics"])
    settlement = cast(dict[str, object], evaluation["settlement"])
    settlement_value = cast(dict[str, object], settlement["result"])
    case = result.cases[0]
    assert case.signal is not None
    assert case.settlement_result is not None

    assert summary["development_evidence_only"] is True
    assert summary["verified_profitability"] is False
    assert summary["public_or_paid_eligibility"] is False
    assert summary["automatic_promotion"] is False
    assert summary["hypothetically_settled_count"] == 1
    assert public["signal"] == case.signal.to_mapping()
    assert economics["hypothetical"] is True
    assert settlement["hypothetical"] is True
    assert settlement_value["realized_pnl"] == format(
        case.settlement_result.realized_pnl.amount,
        "f",
    )
    case_mapping = case_payload(case)
    public_mapping = cast(dict[str, object], case_mapping["public_producer"])
    assert public_mapping["would_emit"] is True
    assert summary_payload(result)["automatic_promotion"] is False


def test_invalid_settlement_and_experiment_order_fail_closed() -> None:
    with pytest.raises(ValueError, match="between zero and one"):
        PaperSettlementEvidence(
            resolved_at=NOW + timedelta(days=1),
            outcome_payout=Decimal("1.1"),
        )
    with pytest.raises(ValueError, match="cannot precede"):
        replace(
            _case(settlement=False),
            settlement=PaperSettlementEvidence(
                resolved_at=NOW - timedelta(seconds=1),
                outcome_payout=Decimal("1"),
            ),
        )

    first = _case(settlement=False)
    second = replace(
        first,
        case_id="earlier-sort-key",
        decision_at=first.decision_at - timedelta(seconds=1),
    )
    with pytest.raises(ValueError, match="ordered"):
        PaperExperimentSpec(
            policy=policy(),
            evidence_cases=(first, second),
        )
    with pytest.raises(ValueError, match="unique"):
        PaperExperimentSpec(
            policy=policy(),
            evidence_cases=(first, first),
        )
