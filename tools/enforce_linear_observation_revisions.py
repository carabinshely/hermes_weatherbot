from __future__ import annotations

from pathlib import Path


def replace_once(path: str, old: str, new: str) -> None:
    file = Path(path)
    content = file.read_text(encoding="utf-8")
    count = content.count(old)
    if count != 1:
        raise SystemExit(f"{path}: expected one marker, found {count}")
    file.write_text(content.replace(old, new, 1), encoding="utf-8")


replace_once(
    "weatherbot/domain/reducers.py",
    "from weatherbot.domain.money import Money, as_decimal, money_from_unit_price\n",
    "from weatherbot.domain.money import Money, as_decimal, money_from_unit_price\n"
    "from weatherbot.domain.observation import ObservationEvidenceStatus\n",
)
replace_once(
    "weatherbot/domain/reducers.py",
    '''def _apply_weather_observation(
    state: LedgerState,
    event: WeatherObservationRecorded,
) -> LedgerState:
    _require_opened(state)
    evidence = event.evidence
    existing = state.weather_observations.get(evidence.market_id, ())
    for prior in existing:
        if prior.payload_hash == evidence.payload_hash:
            if prior == evidence:
                return state
            raise DuplicateEventConflict(
                "weather observation payload hash was reused with different evidence"
            )
    if evidence.supersedes_payload_hash is not None:
        superseded = next(
            (prior for prior in existing if prior.payload_hash == evidence.supersedes_payload_hash),
            None,
        )
        if superseded is None:
            raise DuplicateEventConflict(
                "weather observation revision supersedes an unknown payload"
            )
        identity_fields = (
            "source_name",
            "source_url",
            "station_id",
            "measurement_basis",
            "market_date",
            "market_timezone",
            "unit",
        )
        if any(getattr(superseded, field) != getattr(evidence, field) for field in identity_fields):
            raise DuplicateEventConflict(
                "weather observation revision changed source or measurement identity"
            )
    observations = dict(state.weather_observations)
    observations[evidence.market_id] = (*existing, evidence)
    return replace(state, weather_observations=observations)
''',
    '''def _apply_weather_observation(
    state: LedgerState,
    event: WeatherObservationRecorded,
) -> LedgerState:
    _require_opened(state)
    evidence = event.evidence
    existing = state.weather_observations.get(evidence.market_id, ())
    for prior in existing:
        if prior.payload_hash == evidence.payload_hash:
            if prior == evidence:
                return state
            raise DuplicateEventConflict(
                "weather observation payload hash was reused with different evidence"
            )

    terminal_history = tuple(prior for prior in existing if prior.learning_eligible)
    if evidence.status is ObservationEvidenceStatus.FINAL:
        if terminal_history:
            raise DuplicateEventConflict(
                "weather observation history already has a final root"
            )
    elif evidence.status is ObservationEvidenceStatus.REVISED:
        if not terminal_history:
            raise DuplicateEventConflict(
                "weather observation revision requires an existing final root"
            )
        latest = terminal_history[-1]
        if evidence.supersedes_payload_hash != latest.payload_hash:
            raise DuplicateEventConflict(
                "weather observation revision must supersede the latest terminal payload"
            )
        identity_fields = (
            "source_name",
            "source_url",
            "station_id",
            "measurement_basis",
            "market_date",
            "market_timezone",
            "unit",
        )
        if any(getattr(latest, field) != getattr(evidence, field) for field in identity_fields):
            raise DuplicateEventConflict(
                "weather observation revision changed source or measurement identity"
            )

    observations = dict(state.weather_observations)
    observations[evidence.market_id] = (*existing, evidence)
    return replace(state, weather_observations=observations)
''',
)

replace_once(
    "weatherbot/domain/state.py",
    "from decimal import Decimal\n",
    "from decimal import Decimal\nfrom itertools import pairwise\n",
)
replace_once(
    "weatherbot/domain/state.py",
    "from weatherbot.domain.observation import WeatherObservationEvidence\n",
    "from weatherbot.domain.observation import (\n"
    "    ObservationEvidenceStatus,\n"
    "    WeatherObservationEvidence,\n"
    ")\n",
)
replace_once(
    "weatherbot/domain/state.py",
    '''        for market_id, observations in self.weather_observations.items():
            hashes: set[str] = set()
            for observation in observations:
                if observation.market_id != market_id:
                    raise InvariantViolation("weather observation map key does not match market")
                if observation.payload_hash in hashes:
                    raise InvariantViolation(
                        "weather observation history contains a duplicate payload hash"
                    )
                if (
                    observation.supersedes_payload_hash is not None
                    and observation.supersedes_payload_hash not in hashes
                ):
                    raise InvariantViolation(
                        "weather observation revision supersedes an unknown prior payload"
                    )
                hashes.add(observation.payload_hash)
''',
    '''        for market_id, observations in self.weather_observations.items():
            hashes: set[str] = set()
            terminal_history: list[WeatherObservationEvidence] = []
            for observation in observations:
                if observation.market_id != market_id:
                    raise InvariantViolation("weather observation map key does not match market")
                if observation.payload_hash in hashes:
                    raise InvariantViolation(
                        "weather observation history contains a duplicate payload hash"
                    )
                if (
                    observation.supersedes_payload_hash is not None
                    and observation.supersedes_payload_hash not in hashes
                ):
                    raise InvariantViolation(
                        "weather observation revision supersedes an unknown prior payload"
                    )
                hashes.add(observation.payload_hash)
                if observation.learning_eligible:
                    terminal_history.append(observation)

            if terminal_history:
                root = terminal_history[0]
                if (
                    root.status is not ObservationEvidenceStatus.FINAL
                    or root.supersedes_payload_hash is not None
                ):
                    raise InvariantViolation(
                        "weather observation terminal history must begin with one final root"
                    )
                for previous, current in pairwise(terminal_history):
                    if (
                        current.status is not ObservationEvidenceStatus.REVISED
                        or current.supersedes_payload_hash != previous.payload_hash
                    ):
                        raise InvariantViolation(
                            "weather observation terminal revisions must form one linear chain"
                        )
''',
)

replace_once(
    "tests/resolution/test_observations.py",
    '''        assert recorder.record(
            observation(
                revision="final-other-source",
                payload=b"other-source",
                source_url="https://weather.example.test/other-station",
            )
        )
        assert eligible_learning_outcomes(store.load_state()) == ()
''',
    '''        with pytest.raises(ValueError, match="declared resolution source"):
            recorder.record(
                observation(
                    revision="final-other-source",
                    payload=b"other-source",
                    source_url="https://weather.example.test/other-station",
                )
            )
        assert eligible_learning_outcomes(store.load_state()) == ()
''',
)

with Path("tests/resolution/test_observations.py").open("a", encoding="utf-8") as handle:
    handle.write(
        '''\n\ndef test_second_final_root_is_rejected_without_mutating_history(tmp_path: Path) -> None:\n'''
        '''    database = tmp_path / "ledger.sqlite3"\n'''
        '''    with SQLiteEventStore(database) as store:\n'''
        '''        seed_open_position(store)\n'''
        '''        recorder = ObservationRecorder(store)\n'''
        '''        original = observation()\n'''
        '''        assert recorder.record(original)\n'''
        '''        before = store.event_count()\n'''
        '''        with pytest.raises(DuplicateEventConflict, match="final root"):\n'''
        '''            recorder.record(\n'''
        '''                observation(\n'''
        '''                    temperature="64",\n'''
        '''                    revision="independent-final-v2",\n'''
        '''                    payload=b"independent-final-v2",\n'''
        '''                )\n'''
        '''            )\n'''
        '''        assert store.event_count() == before\n'''
        '''        assert store.load_state().weather_observations[MARKET_ID] == (original,)\n'''
        '''\n\ndef test_branching_revision_is_rejected_without_mutating_history(tmp_path: Path) -> None:\n'''
        '''    database = tmp_path / "ledger.sqlite3"\n'''
        '''    with SQLiteEventStore(database) as store:\n'''
        '''        seed_open_position(store)\n'''
        '''        recorder = ObservationRecorder(store)\n'''
        '''        original = observation()\n'''
        '''        first_revision = observation(\n'''
        '''            temperature="64",\n'''
        '''            status=ObservationEvidenceStatus.REVISED,\n'''
        '''            revision="final-v2",\n'''
        '''            payload=b"linear-final-v2",\n'''
        '''            supersedes=original.payload_hash,\n'''
        '''        )\n'''
        '''        assert recorder.record(original)\n'''
        '''        assert recorder.record(first_revision)\n'''
        '''        before = store.event_count()\n'''
        '''        with pytest.raises(DuplicateEventConflict, match="latest terminal payload"):\n'''
        '''            recorder.record(\n'''
        '''                observation(\n'''
        '''                    temperature="65",\n'''
        '''                    status=ObservationEvidenceStatus.REVISED,\n'''
        '''                    revision="branch-v3",\n'''
        '''                    payload=b"branch-final-v3",\n'''
        '''                    supersedes=original.payload_hash,\n'''
        '''                )\n'''
        '''            )\n'''
        '''        assert store.event_count() == before\n'''
        '''        assert store.load_state().weather_observations[MARKET_ID] == (\n'''
        '''            original,\n'''
        '''            first_revision,\n'''
        '''        )\n'''
    )
