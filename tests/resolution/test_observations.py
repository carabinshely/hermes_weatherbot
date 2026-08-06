from __future__ import annotations

import hashlib
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from tests.resolution.helpers import (
    DECLARED_SOURCE,
    MARKET_DATE,
    MARKET_ID,
    MARKET_TIMEZONE,
    NOW,
    StaticGammaTransport,
    gamma_payload,
    seed_open_position,
)
from weatherbot.domain import (
    DuplicateEventConflict,
    ObservationEvidenceStatus,
    WeatherObservationEvidence,
)
from weatherbot.persistence import SQLiteEventStore
from weatherbot.resolution import (
    GammaResolutionSource,
    ObservationRecorder,
    ResolutionWorker,
    StoredDecisionContextProvider,
    eligible_learning_outcomes,
    eligible_resolution_evidence,
    latest_learning_observation,
    payload_sha256,
)


def observation(
    *,
    temperature: str = "63",
    status: ObservationEvidenceStatus = ObservationEvidenceStatus.FINAL,
    revision: str = "final-v1",
    payload: bytes = b"weather-source-final-v1",
    supersedes: str | None = None,
    source_url: str = DECLARED_SOURCE,
) -> WeatherObservationEvidence:
    return WeatherObservationEvidence(
        market_id=MARKET_ID,
        source_name="Weather Underground daily history",
        source_url=source_url,
        station_id="KMDW",
        measurement_basis="finalized daily high temperature",
        market_date=MARKET_DATE,
        market_timezone=MARKET_TIMEZONE,
        temperature=Decimal(temperature),
        unit="F",
        retrieved_at=NOW + timedelta(minutes=5),
        source_timestamp=NOW - timedelta(hours=1),
        source_revision=revision,
        status=status,
        payload_hash=hashlib.sha256(payload).hexdigest(),
        supersedes_payload_hash=supersedes,
    )


def settle(store: SQLiteEventStore) -> None:
    times = iter((NOW, NOW + timedelta(seconds=1)))
    report = ResolutionWorker(
        store=store,
        source=GammaResolutionSource(StaticGammaTransport(gamma_payload())),
        context_provider=StoredDecisionContextProvider(),
        clock=lambda: next(times),
    ).run_once()
    assert report.settled_positions == 1


def test_verified_settlement_alone_is_not_learning_eligible(tmp_path: Path) -> None:
    database = tmp_path / "ledger.sqlite3"
    with SQLiteEventStore(database) as store:
        seed_open_position(store)
        settle(store)
        state = store.load_state()
        assert eligible_learning_outcomes(state) == ()
        assert eligible_resolution_evidence(state) == ()


def test_final_exact_observation_enables_a_distinct_learning_outcome(
    tmp_path: Path,
) -> None:
    database = tmp_path / "ledger.sqlite3"
    with SQLiteEventStore(database) as store:
        seed_open_position(store)
        settle(store)
        recorder = ObservationRecorder(store)
        evidence = observation()
        assert recorder.record(evidence)
        assert not recorder.record(evidence)

        state = store.load_state()
        assert state.weather_observations[MARKET_ID] == (evidence,)
        outcomes = eligible_learning_outcomes(state)
        assert len(outcomes) == 1
        assert outcomes[0].observed_temperature == Decimal("63")
        assert outcomes[0].observed_unit == "F"
        assert outcomes[0].winning_outcome_ids
        assert eligible_resolution_evidence(state) == (outcomes[0].settlement,)


def test_revised_observation_preserves_history_and_supersedes_exact_payload(
    tmp_path: Path,
) -> None:
    database = tmp_path / "ledger.sqlite3"
    with SQLiteEventStore(database) as store:
        seed_open_position(store)
        settle(store)
        recorder = ObservationRecorder(store)
        original = observation()
        revised = observation(
            temperature="64",
            status=ObservationEvidenceStatus.REVISED,
            revision="final-v2",
            payload=b"weather-source-final-v2",
            supersedes=original.payload_hash,
        )
        assert recorder.record(original)
        assert recorder.record(revised)

        state = store.load_state()
        assert state.weather_observations[MARKET_ID] == (original, revised)
        assert latest_learning_observation(state, MARKET_ID) == revised
        outcome = eligible_learning_outcomes(state)[0]
        assert outcome.observed_temperature == Decimal("64")
        assert outcome.observation.supersedes_payload_hash == original.payload_hash

    with SQLiteEventStore(database, read_only=True) as restarted:
        assert restarted.load_state().weather_observations[MARKET_ID] == (
            original,
            revised,
        )


def test_revision_of_unknown_payload_fails_without_mutating_history(tmp_path: Path) -> None:
    database = tmp_path / "ledger.sqlite3"
    with SQLiteEventStore(database) as store:
        seed_open_position(store)
        before = store.event_count()
        revised = observation(
            status=ObservationEvidenceStatus.REVISED,
            revision="orphan-revision",
            payload=b"orphan",
            supersedes="0" * 64,
        )
        with pytest.raises(DuplicateEventConflict, match="unknown payload"):
            ObservationRecorder(store).record(revised)
        assert store.event_count() == before
        assert not store.load_state().weather_observations


def test_provisional_or_wrong_source_observation_is_excluded_from_learning(
    tmp_path: Path,
) -> None:
    database = tmp_path / "ledger.sqlite3"
    with SQLiteEventStore(database) as store:
        seed_open_position(store)
        settle(store)
        recorder = ObservationRecorder(store)
        assert recorder.record(
            observation(
                status=ObservationEvidenceStatus.PROVISIONAL,
                revision="provisional-v1",
            )
        )
        assert eligible_learning_outcomes(store.load_state()) == ()

        assert recorder.record(
            observation(
                revision="final-other-source",
                payload=b"other-source",
                source_url="https://weather.example.test/other-station",
            )
        )
        assert eligible_learning_outcomes(store.load_state()) == ()


def test_payload_hash_is_computed_from_captured_source_bytes(tmp_path: Path) -> None:
    capture = tmp_path / "source-capture.json"
    capture.write_bytes(b'{"daily_high":63,"station":"KMDW"}')
    assert payload_sha256(capture) == hashlib.sha256(capture.read_bytes()).hexdigest()
