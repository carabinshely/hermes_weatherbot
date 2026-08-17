"""Canonical deterministic output for internal PAPER experiments."""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from weatherbot.paper.experiment import PaperCaseResult, PaperExperimentResult


def _economic_payload(case: PaperCaseResult) -> object:
    result = case.economic_result
    if result is None:
        return None
    sizing = None if result.sizing is None else result.sizing.metadata()
    risk = None if result.risk_decision is None else result.risk_decision.metadata()
    plan = None if result.execution_plan is None else result.execution_plan.metadata()
    state = result.state
    return {
        "status": result.status.value,
        "appended_events": result.appended_events,
        "sizing": sizing,
        "portfolio_risk": risk,
        "hypothetical_fill": plan,
        "portfolio_after": {
            "cash": format(state.cash.amount, "f"),
            "reserved_cash": format(state.reserved_cash.amount, "f"),
            "available_cash": format(state.available_cash.amount, "f"),
            "currency": state.currency,
            "open_positions": sum(
                1
                for position in state.positions.values()
                if position.quantity > 0 and position.status.value == "open"
            ),
        },
    }


def case_payload(case: PaperCaseResult) -> dict[str, object]:
    decision = case.strategy
    return {
        "case_id": case.case_id,
        "evidence_fingerprint": case.evidence_fingerprint,
        "strategy": {
            "would_emit": decision.would_emit,
            "classification": decision.classification,
            "model_probability": format(decision.model_probability, "f"),
            "market_reference_price": format(decision.market_reference_price, "f"),
            "expected_edge": format(decision.expected_edge, "f"),
            "reason": decision.reason,
            "metadata": dict(decision.metadata),
        },
        "economics": {
            "status": case.economic_status.value,
            "reason": case.economic_reason,
            "hypothetical": True,
            "result": _economic_payload(case),
        },
    }


def summary_payload(result: PaperExperimentResult) -> dict[str, object]:
    return {
        "schema_version": 1,
        "experiment_id": result.experiment_id,
        "engine_version": result.engine_version,
        "strategy_id": result.strategy_id,
        "strategy_version": result.strategy_version,
        "case_count": len(result.cases),
        "would_emit_count": result.would_emit_count,
        "economically_evaluated_count": result.economically_evaluated_count,
        "development_evidence_only": True,
        "verified_profitability": False,
        "public_or_paid_eligibility": False,
    }


def _canonical_json(payload: object) -> str:
    return json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        default=str,
    )


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class PaperExperimentArtifacts:
    directory: Path
    summary_path: Path
    evaluations_path: Path
    checksums_path: Path


def write_experiment_artifacts(
    result: PaperExperimentResult,
    *,
    output_directory: str | Path,
) -> PaperExperimentArtifacts:
    """Write immutable canonical results; conflicting reuse of an experiment ID fails closed."""
    root = Path(output_directory)
    directory = root / result.experiment_id
    directory.mkdir(parents=True, exist_ok=True)

    summary_text = _canonical_json(summary_payload(result)) + "\n"
    evaluation_lines = [_canonical_json(case_payload(case)) for case in result.cases]
    evaluations_text = "\n".join(evaluation_lines) + "\n"
    checksums = {
        "summary.json": _sha256_text(summary_text),
        "evaluations.jsonl": _sha256_text(evaluations_text),
    }
    checksums_text = _canonical_json(checksums) + "\n"

    expected: Mapping[str, str] = {
        "summary.json": summary_text,
        "evaluations.jsonl": evaluations_text,
        "checksums.json": checksums_text,
    }
    for name, text in expected.items():
        path = directory / name
        if path.exists():
            existing = path.read_text(encoding="utf-8")
            if existing != text:
                raise ValueError(
                    f"experiment {result.experiment_id} already has conflicting {name}"
                )
            continue
        temporary = path.with_name(f".{path.name}.tmp")
        temporary.write_text(text, encoding="utf-8")
        with temporary.open("r+", encoding="utf-8") as handle:
            handle.flush()
            os.fsync(handle.fileno())
        temporary.replace(path)

    return PaperExperimentArtifacts(
        directory=directory,
        summary_path=directory / "summary.json",
        evaluations_path=directory / "evaluations.jsonl",
        checksums_path=directory / "checksums.json",
    )
