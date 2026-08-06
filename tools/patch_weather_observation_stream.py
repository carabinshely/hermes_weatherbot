from __future__ import annotations

from pathlib import Path


def replace_once(path: str, old: str, new: str) -> None:
    file = Path(path)
    content = file.read_text(encoding="utf-8")
    count = content.count(old)
    if count != 1:
        raise SystemExit(f"{path}: expected one marker, found {count}")
    file.write_text(content.replace(old, new, 1), encoding="utf-8")


# Domain events.
replace_once(
    "weatherbot/domain/events.py",
    "from weatherbot.domain.money import Money, as_decimal, require_nonnegative\n",
    "from weatherbot.domain.money import Money, as_decimal, require_nonnegative\n"
    "from weatherbot.domain.observation import WeatherObservationEvidence\n",
)
replace_once(
    "weatherbot/domain/events.py",
    '''@dataclass(frozen=True, slots=True, kw_only=True)
class MarketResolutionEvidenceRecorded(DomainEvent):
    evidence: MarketResolutionEvidence
''',
    '''@dataclass(frozen=True, slots=True, kw_only=True)
class WeatherObservationRecorded(DomainEvent):
    evidence: WeatherObservationEvidence


@dataclass(frozen=True, slots=True, kw_only=True)
class MarketResolutionEvidenceRecorded(DomainEvent):
    evidence: MarketResolutionEvidence
''',
)
replace_once(
    "weatherbot/domain/events.py",
    '''    | OrderOutcomeUnknown
    | MarketResolutionEvidenceRecorded
''',
    '''    | OrderOutcomeUnknown
    | WeatherObservationRecorded
    | MarketResolutionEvidenceRecorded
''',
)

# Derived state.
replace_once(
    "weatherbot/domain/state.py",
    "from weatherbot.domain.money import (\n",
    "from weatherbot.domain.observation import WeatherObservationEvidence\n"
    "from weatherbot.domain.money import (\n",
)
replace_once(
    "weatherbot/domain/state.py",
    '''def _empty_resolution_evidence() -> Mapping[MarketId, MarketResolutionEvidence]:
    return {}


def _empty_event_fingerprints() -> Mapping[EventId, str]:
''',
    '''def _empty_resolution_evidence() -> Mapping[MarketId, MarketResolutionEvidence]:
    return {}


def _empty_weather_observations() -> Mapping[
    MarketId, tuple[WeatherObservationEvidence, ...]
]:
    return {}


def _empty_event_fingerprints() -> Mapping[EventId, str]:
''',
)
replace_once(
    "weatherbot/domain/state.py",
    '''    resolution_evidence: Mapping[MarketId, MarketResolutionEvidence] = field(
        default_factory=_empty_resolution_evidence
    )
    event_fingerprints: Mapping[EventId, str] = field(default_factory=_empty_event_fingerprints)
''',
    '''    resolution_evidence: Mapping[MarketId, MarketResolutionEvidence] = field(
        default_factory=_empty_resolution_evidence
    )
    weather_observations: Mapping[
        MarketId, tuple[WeatherObservationEvidence, ...]
    ] = field(default_factory=_empty_weather_observations)
    event_fingerprints: Mapping[EventId, str] = field(default_factory=_empty_event_fingerprints)
''',
)
replace_once(
    "weatherbot/domain/state.py",
    '''        object.__setattr__(
            self,
            "event_fingerprints",
            _freeze_mapping(self.event_fingerprints),
        )
''',
    '''        object.__setattr__(
            self,
            "weather_observations",
            _freeze_mapping(
                {
                    market_id: tuple(observations)
                    for market_id, observations in self.weather_observations.items()
                }
            ),
        )
        object.__setattr__(
            self,
            "event_fingerprints",
            _freeze_mapping(self.event_fingerprints),
        )
''',
)
replace_once(
    "weatherbot/domain/state.py",
    '''        for market_id, evidence in self.resolution_evidence.items():
            if market_id != evidence.market_id:
                raise InvariantViolation("resolution evidence map key does not match market")
            resolution = self.resolutions.get(market_id)
            if resolution is not None and resolution.payouts != evidence.payouts:
                raise InvariantViolation(
                    "recorded resolution differs from its authoritative evidence"
                )

        if not self.opened and (
''',
    '''        for market_id, evidence in self.resolution_evidence.items():
            if market_id != evidence.market_id:
                raise InvariantViolation("resolution evidence map key does not match market")
            resolution = self.resolutions.get(market_id)
            if resolution is not None and resolution.payouts != evidence.payouts:
                raise InvariantViolation(
                    "recorded resolution differs from its authoritative evidence"
                )

        for market_id, observations in self.weather_observations.items():
            hashes: set[str] = set()
            for observation in observations:
                if observation.market_id != market_id:
                    raise InvariantViolation(
                        "weather observation map key does not match market"
                    )
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

        if not self.opened and (
''',
)
replace_once(
    "weatherbot/domain/state.py",
    '''            or self.resolutions
            or self.resolution_evidence
        ):
''',
    '''            or self.resolutions
            or self.resolution_evidence
            or self.weather_observations
        ):
''',
)

# Reducers.
replace_once(
    "weatherbot/domain/reducers.py",
    '''    PositionSettled,
    fingerprint,
''',
    '''    PositionSettled,
    WeatherObservationRecorded,
    fingerprint,
''',
)
replace_once(
    "weatherbot/domain/reducers.py",
    '''def _apply_resolution_evidence(
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
    if evidence.supersedes_payload_hash is not None:
        superseded = next(
            (
                prior
                for prior in existing
                if prior.payload_hash == evidence.supersedes_payload_hash
            ),
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
        if any(
            getattr(superseded, field) != getattr(evidence, field)
            for field in identity_fields
        ):
            raise DuplicateEventConflict(
                "weather observation revision changed source or measurement identity"
            )
    observations = dict(state.weather_observations)
    observations[evidence.market_id] = (*existing, evidence)
    return replace(state, weather_observations=observations)


def _apply_resolution_evidence(
''',
)
replace_once(
    "weatherbot/domain/reducers.py",
    '''    elif isinstance(event, MarketResolutionEvidenceRecorded):
        next_state = _apply_resolution_evidence(state, event)
''',
    '''    elif isinstance(event, WeatherObservationRecorded):
        next_state = _apply_weather_observation(state, event)
    elif isinstance(event, MarketResolutionEvidenceRecorded):
        next_state = _apply_resolution_evidence(state, event)
''',
)

# Domain exports.
replace_once(
    "weatherbot/domain/__init__.py",
    '''    PositionSettled,
    fingerprint,
''',
    '''    PositionSettled,
    WeatherObservationRecorded,
    fingerprint,
''',
)
replace_once(
    "weatherbot/domain/__init__.py",
    "from weatherbot.domain.money import Money, as_decimal, money_from_unit_price\n",
    "from weatherbot.domain.money import Money, as_decimal, money_from_unit_price\n"
    "from weatherbot.domain.observation import (\n"
    "    ObservationEvidenceStatus,\n"
    "    WeatherObservationEvidence,\n"
    ")\n",
)
replace_once(
    "weatherbot/domain/__init__.py",
    '''    "Money",
    "OrderAcknowledged",
''',
    '''    "Money",
    "ObservationEvidenceStatus",
    "OrderAcknowledged",
''',
)
replace_once(
    "weatherbot/domain/__init__.py",
    '''    "Signal",
    "allowed_transitions",
''',
    '''    "Signal",
    "WeatherObservationEvidence",
    "WeatherObservationRecorded",
    "allowed_transitions",
''',
)

# Persistence codec imports and event type.
replace_once(
    "weatherbot/persistence/codec.py",
    '''    OutcomePayout,
    PositionSettled,
    ResolutionEvidenceStatus,
''',
    '''    OutcomePayout,
    PositionSettled,
    ObservationEvidenceStatus,
    ResolutionEvidenceStatus,
''',
)
replace_once(
    "weatherbot/persistence/codec.py",
    '''    ResolutionEvidenceStatus,
    Side,
)
''',
    '''    ResolutionEvidenceStatus,
    Side,
    WeatherObservationEvidence,
    WeatherObservationRecorded,
)
''',
)
replace_once(
    "weatherbot/persistence/codec.py",
    '''    OrderOutcomeUnknown: "order_outcome_unknown",
    MarketResolutionEvidenceRecorded: "market_resolution_evidence_recorded",
''',
    '''    OrderOutcomeUnknown: "order_outcome_unknown",
    WeatherObservationRecorded: "weather_observation_recorded",
    MarketResolutionEvidenceRecorded: "market_resolution_evidence_recorded",
''',
)
replace_once(
    "weatherbot/persistence/codec.py",
    '''def _evidence_to_data(value: MarketResolutionEvidence) -> dict[str, object]:
''',
    '''def _observation_to_data(value: WeatherObservationEvidence) -> dict[str, object]:
    return {
        "market_id": str(value.market_id),
        "source_name": value.source_name,
        "source_url": value.source_url,
        "station_id": value.station_id,
        "measurement_basis": value.measurement_basis,
        "market_date": value.market_date.isoformat(),
        "market_timezone": value.market_timezone,
        "temperature": format(value.temperature, "f"),
        "unit": value.unit,
        "retrieved_at": value.retrieved_at.isoformat(),
        "source_timestamp": (
            value.source_timestamp.isoformat()
            if value.source_timestamp is not None
            else None
        ),
        "source_revision": value.source_revision,
        "status": value.status.value,
        "payload_hash": value.payload_hash,
        "supersedes_payload_hash": value.supersedes_payload_hash,
    }


def _evidence_to_data(value: MarketResolutionEvidence) -> dict[str, object]:
''',
)
replace_once(
    "weatherbot/persistence/codec.py",
    '''    if isinstance(event, MarketResolutionEvidenceRecorded):
        return {**common, "evidence": _evidence_to_data(event.evidence)}
''',
    '''    if isinstance(event, WeatherObservationRecorded):
        return {**common, "evidence": _observation_to_data(event.evidence)}
    if isinstance(event, MarketResolutionEvidenceRecorded):
        return {**common, "evidence": _evidence_to_data(event.evidence)}
''',
)
replace_once(
    "weatherbot/persistence/codec.py",
    '''    if isinstance(event, MarketResolutionEvidenceRecorded):
        return None, None, str(event.evidence.market_id), None
''',
    '''    if isinstance(event, WeatherObservationRecorded):
        return None, None, str(event.evidence.market_id), None
    if isinstance(event, MarketResolutionEvidenceRecorded):
        return None, None, str(event.evidence.market_id), None
''',
)
replace_once(
    "weatherbot/persistence/codec.py",
    '''def _integer(value: object, *, label: str) -> int:
''',
    '''def _optional_text(value: object, *, label: str) -> str | None:
    if value is None:
        return None
    return _text(value, label=label)


def _integer(value: object, *, label: str) -> int:
''',
)
replace_once(
    "weatherbot/persistence/codec.py",
    '''def _evidence(value: object) -> MarketResolutionEvidence:
''',
    '''def _observation(value: object) -> WeatherObservationEvidence:
    data = _mapping(value, label="weather_observation")
    _expect_keys(
        data,
        required={
            "market_id",
            "source_name",
            "source_url",
            "station_id",
            "measurement_basis",
            "market_date",
            "market_timezone",
            "temperature",
            "unit",
            "retrieved_at",
            "source_timestamp",
            "source_revision",
            "status",
            "payload_hash",
            "supersedes_payload_hash",
        },
        label="weather_observation",
    )
    status_text = _text(data["status"], label="weather_observation.status")
    try:
        status = ObservationEvidenceStatus(status_text)
    except ValueError as exc:
        raise CorruptLedgerError(
            f"weather_observation.status is unsupported: {status_text!r}"
        ) from exc
    source_timestamp_text = _optional_text(
        data["source_timestamp"],
        label="weather_observation.source_timestamp",
    )
    return WeatherObservationEvidence(
        market_id=MarketId(
            _text(data["market_id"], label="weather_observation.market_id")
        ),
        source_name=_text(
            data["source_name"],
            label="weather_observation.source_name",
        ),
        source_url=_text(
            data["source_url"],
            label="weather_observation.source_url",
        ),
        station_id=_text(
            data["station_id"],
            label="weather_observation.station_id",
        ),
        measurement_basis=_text(
            data["measurement_basis"],
            label="weather_observation.measurement_basis",
        ),
        market_date=_date(
            data["market_date"],
            label="weather_observation.market_date",
        ),
        market_timezone=_text(
            data["market_timezone"],
            label="weather_observation.market_timezone",
        ),
        temperature=_decimal(
            data["temperature"],
            label="weather_observation.temperature",
        ),
        unit=_text(data["unit"], label="weather_observation.unit"),
        retrieved_at=_datetime(
            data["retrieved_at"],
            label="weather_observation.retrieved_at",
        ),
        source_timestamp=(
            _datetime(
                source_timestamp_text,
                label="weather_observation.source_timestamp",
            )
            if source_timestamp_text is not None
            else None
        ),
        source_revision=_text(
            data["source_revision"],
            label="weather_observation.source_revision",
        ),
        status=status,
        payload_hash=_text(
            data["payload_hash"],
            label="weather_observation.payload_hash",
        ),
        supersedes_payload_hash=_optional_text(
            data["supersedes_payload_hash"],
            label="weather_observation.supersedes_payload_hash",
        ),
    )


def _evidence(value: object) -> MarketResolutionEvidence:
''',
)
replace_once(
    "weatherbot/persistence/codec.py",
    '''    if event_type == "market_resolution_evidence_recorded":
        event_id, occurred_at = _common(data, required={"evidence"})
''',
    '''    if event_type == "weather_observation_recorded":
        event_id, occurred_at = _common(data, required={"evidence"})
        return WeatherObservationRecorded(
            event_id=event_id,
            occurred_at=occurred_at,
            evidence=_observation(data["evidence"]),
        )
    if event_type == "market_resolution_evidence_recorded":
        event_id, occurred_at = _common(data, required={"evidence"})
''',
)
