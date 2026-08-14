from __future__ import annotations

import inspect
from pathlib import Path
from typing import cast

import pytest

from tests.paper.helpers import calibrated_probability, paper_book, scope
from tests.quoting.helpers import NOW, cost_policy, event_snapshot, freshness_policy, weather_snapshot
from weatherbot.markets import ConditionId, OutcomeTokenId
from weatherbot.paper import (
    PaperEntryStatus,
    PaperRuntimeConfig,
    paper_scan_decision_id,
    submit_scanner_candidate,
)


def _submit(
    tmp_path: Path,
    *,
    audit_metadata: dict[str, object] | None = None,
):
    runtime = PaperRuntimeConfig.from_mapping(
        {"paper_ledger_path": "paper.sqlite3"},
        base_dir=tmp_path,
    )
    calibrated = calibrated_probability()
    weather = weather_snapshot()
    event = event_snapshot()
    decision_book = paper_book(book_hash="calibrated-decision-book")
    paper_scope = scope()
    decision_id = paper_scan_decision_id(
        calibrated=calibrated,
        scope=paper_scope,
        weather=weather,
        event=event,
        decision_book=decision_book,
    )

    def fetch(_condition_id: ConditionId, _token_id: OutcomeTokenId):
        return paper_book(book_hash="calibrated-execution-book")

    result = submit_scanner_candidate(
        runtime=runtime,
        strategy_id="bot-v3-weather",
        decision_id=decision_id,
        calibrated=calibrated,
        scope=paper_scope,
        weather=weather,
        event=event,
        decision_book=decision_book,
        condition_id=decision_book.condition_id,
        token_id=decision_book.token_id,
        evaluated_at=NOW,
        freshness_policy=freshness_policy(),
        cost_policy=cost_policy(),
        fetch_book=fetch,
        audit_metadata=audit_metadata
        or {
            "fixture": "calibrated-paper",
            "bucket_key": "F:85:86",
            "bucket_label": "85-86°F",
            "declared_resolution_source": "https://example.com/resolution-rules",
        },
        owner_id="calibrated-paper-test",
    )
    return runtime, calibrated, decision_id, result


def test_scanner_facade_accepts_only_typed_calibration_identity() -> None:
    parameters = inspect.signature(submit_scanner_candidate).parameters
    decision_parameters = inspect.signature(paper_scan_decision_id).parameters

    assert "calibrated" in parameters
    assert "model_version" not in parameters
    assert "probability" not in parameters
    assert "calibrated" in decision_parameters
    assert "model_version" not in decision_parameters


def test_calibrated_paper_entry_persists_complete_probability_provenance(tmp_path: Path) -> None:
    runtime, calibrated, decision_id, result = _submit(tmp_path)

    assert result.status is PaperEntryStatus.FILLED
    with runtime.open_read_only_store() as store:
        claim = next(item for item in store.list_decision_claims() if item.decision_key == decision_id)

    assert claim.metadata["model_version"] == calibrated.model_version
    assert claim.metadata["model_probability"] == format(calibrated.model_probability, "f")
    caller_audit = claim.metadata["caller_audit"]
    assert isinstance(caller_audit, dict)
    calibration = cast(dict[str, object], caller_audit["calibration"])
    assert calibration == dict(calibrated.audit_metadata())
    assert calibration["artifact_sha256"] == calibrated.artifact_sha256
    assert calibration["city_slug"] == "chicago"
    assert calibration["climate_region"] == "ohio_valley"
    assert calibration["lead_days"] == 0
    assert calibration["forecast_source"] == "open_meteo_ecmwf_ifs025"
    assert calibration["calibration_group_key"] == calibrated.calibration_group_key
    assert calibration["fallback_level"] == "source"
    assert calibration["distribution_type"] == "normal"
    assert calibration["calibration_sample_count"] == 60
    assert calibration["training_cutoff"] == "2026-08-10"


def test_scanner_facade_rejects_calibration_metadata_spoof_before_ledger_mutation(
    tmp_path: Path,
) -> None:
    runtime = PaperRuntimeConfig.from_mapping(
        {"paper_ledger_path": "paper.sqlite3"},
        base_dir=tmp_path,
    )
    calibrated = calibrated_probability()
    weather = weather_snapshot()
    event = event_snapshot()
    decision_book = paper_book(book_hash="spoof-decision-book")
    decision_id = paper_scan_decision_id(
        calibrated=calibrated,
        scope=scope(),
        weather=weather,
        event=event,
        decision_book=decision_book,
    )

    def forbidden_fetch(_condition_id: ConditionId, _token_id: OutcomeTokenId):
        raise AssertionError("spoof rejection must occur before PAPER network/book work")

    with pytest.raises(ValueError, match="cannot override calibration-owned keys"):
        submit_scanner_candidate(
            runtime=runtime,
            strategy_id="bot-v3-weather",
            decision_id=decision_id,
            calibrated=calibrated,
            scope=scope(),
            weather=weather,
            event=event,
            decision_book=decision_book,
            condition_id=decision_book.condition_id,
            token_id=decision_book.token_id,
            evaluated_at=NOW,
            freshness_policy=freshness_policy(),
            cost_policy=cost_policy(),
            fetch_book=forbidden_fetch,
            audit_metadata={
                "bucket_key": "F:85:86",
                "artifact_sha256": "spoof",
            },
            owner_id="calibrated-paper-test",
        )

    assert not runtime.ledger_path.exists()


def test_scanner_facade_rejects_probability_context_mismatch() -> None:
    calibrated = calibrated_probability(city_slug="nyc")
    weather = weather_snapshot()
    event = event_snapshot()
    decision_book = paper_book(book_hash="context-decision-book")

    with pytest.raises(ValueError, match="city_slug"):
        paper_scan_decision_id(
            calibrated=calibrated,
            scope=scope(),
            weather=weather,
            event=event,
            decision_book=decision_book,
        )
