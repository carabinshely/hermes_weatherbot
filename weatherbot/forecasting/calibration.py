"""Versioned residual-distribution calibration for temperature probabilities.

The production probability interface deliberately models forecast residuals rather than
exposing a magic ``sigma``.  Historical fitting lives offline; runtime code loads one
strict, checksummed artifact and selects the most specific group with enough evidence.
"""

from __future__ import annotations

import hashlib
import json
import math
from bisect import bisect_left
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from pathlib import Path
from typing import Protocol, cast

from weatherbot.forecasting.model import ForecastSource
from weatherbot.markets import TemperatureBucket

_ARTIFACT_SCHEMA_VERSION = 1


class CalibrationError(ValueError):
    """Raised when calibration evidence or a model artifact is invalid."""


def _text(value: object, *, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise CalibrationError(f"{label} must be a non-blank string")
    return value.strip()


def _decimal(value: object, *, label: str) -> Decimal:
    if isinstance(value, bool):
        raise CalibrationError(f"{label} must be decimal")
    try:
        result = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise CalibrationError(f"{label} must be decimal") from exc
    if not result.is_finite():
        raise CalibrationError(f"{label} must be finite")
    return result


def _utc(value: datetime, *, label: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise CalibrationError(f"{label} must be timezone-aware")
    return value.astimezone(UTC)


def _timestamp(value: object, *, label: str) -> datetime:
    text = _text(value, label=label)
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise CalibrationError(f"{label} must be ISO-8601") from exc
    return _utc(parsed, label=label)


def _date(value: object, *, label: str) -> date:
    text = _text(value, label=label)
    try:
        return date.fromisoformat(text)
    except ValueError as exc:
        raise CalibrationError(f"{label} must be an ISO date") from exc


def _integer(value: object, *, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise CalibrationError(f"{label} must be an integer")
    return value


def _number(value: object, *, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise CalibrationError(f"{label} must be numeric")
    result = float(value)
    if not math.isfinite(result):
        raise CalibrationError(f"{label} must be finite")
    return result


def _optional_number(value: object, *, label: str) -> float | None:
    if value is None:
        return None
    return _number(value, label=label)


def _mapping(value: object, *, label: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise CalibrationError(f"{label} must be an object")
    return cast(Mapping[str, object], value)


def _sequence(value: object, *, label: str) -> Sequence[object]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        raise CalibrationError(f"{label} must be an array")
    return cast(Sequence[object], value)


def _sha256(value: object, *, label: str) -> str:
    text = _text(value, label=label).lower()
    if len(text) != 64 or any(character not in "0123456789abcdef" for character in text):
        raise CalibrationError(f"{label} must be a SHA-256 hex digest")
    return text


def _canonical_bytes(value: Mapping[str, object]) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


class Season(StrEnum):
    DJF = "DJF"
    MAM = "MAM"
    JJA = "JJA"
    SON = "SON"

    @classmethod
    def for_date(cls, value: date) -> Season:
        if value.month in {12, 1, 2}:
            return cls.DJF
        if value.month in {3, 4, 5}:
            return cls.MAM
        if value.month in {6, 7, 8}:
            return cls.JJA
        return cls.SON


@dataclass(frozen=True, slots=True)
class CalibrationSample:
    """One point-in-time forecast joined to one finalized observation target."""

    city: str
    climate_region: str
    forecast_source: ForecastSource
    market_date: date
    lead_days: int
    forecast_temperature_f: Decimal
    observed_temperature_f: Decimal
    forecast_as_of_utc: datetime
    observation_finalized_at_utc: datetime
    observation_source: str
    station_id: str
    measurement_basis: str
    forecast_payload_sha256: str
    observation_payload_sha256: str

    def __post_init__(self) -> None:
        city = self.city.strip().lower()
        region = self.climate_region.strip().lower()
        if not city:
            raise CalibrationError("sample city must not be blank")
        if not region:
            raise CalibrationError("sample climate region must not be blank")
        _integer(self.lead_days, label="sample lead_days")
        if not 0 <= self.lead_days <= 7:
            raise CalibrationError("sample lead_days must be between 0 and 7")
        forecast = _decimal(self.forecast_temperature_f, label="forecast temperature")
        observed = _decimal(self.observed_temperature_f, label="observed temperature")
        forecast_as_of = _utc(self.forecast_as_of_utc, label="forecast as-of time")
        observation_finalized = _utc(
            self.observation_finalized_at_utc,
            label="observation finalized time",
        )
        if forecast_as_of >= observation_finalized:
            raise CalibrationError("forecast must predate finalized observation evidence")
        observation_source = self.observation_source.strip()
        station_id = self.station_id.strip().upper()
        measurement_basis = self.measurement_basis.strip()
        if not observation_source:
            raise CalibrationError("sample observation source must not be blank")
        if not station_id:
            raise CalibrationError("sample station_id must not be blank")
        if not measurement_basis:
            raise CalibrationError("sample measurement_basis must not be blank")
        forecast_hash = _sha256(self.forecast_payload_sha256, label="forecast payload hash")
        observation_hash = _sha256(
            self.observation_payload_sha256,
            label="observation payload hash",
        )
        object.__setattr__(self, "city", city)
        object.__setattr__(self, "climate_region", region)
        object.__setattr__(self, "forecast_temperature_f", forecast)
        object.__setattr__(self, "observed_temperature_f", observed)
        object.__setattr__(self, "forecast_as_of_utc", forecast_as_of)
        object.__setattr__(self, "observation_finalized_at_utc", observation_finalized)
        object.__setattr__(self, "observation_source", observation_source)
        object.__setattr__(self, "station_id", station_id)
        object.__setattr__(self, "measurement_basis", measurement_basis)
        object.__setattr__(self, "forecast_payload_sha256", forecast_hash)
        object.__setattr__(self, "observation_payload_sha256", observation_hash)

    @property
    def season(self) -> Season:
        return Season.for_date(self.market_date)

    @property
    def residual_f(self) -> Decimal:
        return self.observed_temperature_f - self.forecast_temperature_f

    @property
    def identity(self) -> tuple[str, ForecastSource, date, int, str, str]:
        return (
            self.city,
            self.forecast_source,
            self.market_date,
            self.lead_days,
            self.station_id,
            self.measurement_basis,
        )


class DistributionKind(StrEnum):
    NORMAL = "normal"
    EMPIRICAL = "empirical"


class ResidualDistribution(Protocol):
    @property
    def kind(self) -> DistributionKind: ...

    def cdf(self, residual_f: Decimal) -> float: ...

    def to_mapping(self) -> Mapping[str, object]: ...


@dataclass(frozen=True, slots=True)
class NormalResidualDistribution:
    bias_f: Decimal
    sigma_f: Decimal

    def __post_init__(self) -> None:
        bias = _decimal(self.bias_f, label="normal bias")
        sigma = _decimal(self.sigma_f, label="normal sigma")
        if sigma <= 0:
            raise CalibrationError("normal sigma must be positive")
        object.__setattr__(self, "bias_f", bias)
        object.__setattr__(self, "sigma_f", sigma)

    @property
    def kind(self) -> DistributionKind:
        return DistributionKind.NORMAL

    def cdf(self, residual_f: Decimal) -> float:
        residual = _decimal(residual_f, label="residual")
        z = float((residual - self.bias_f) / self.sigma_f)
        return 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))

    def to_mapping(self) -> Mapping[str, object]:
        return {
            "kind": self.kind.value,
            "bias_f": format(self.bias_f, "f"),
            "sigma_f": format(self.sigma_f, "f"),
        }


@dataclass(frozen=True, slots=True)
class EmpiricalResidualDistribution:
    residuals_f: tuple[Decimal, ...]

    def __post_init__(self) -> None:
        residuals = tuple(
            sorted(_decimal(value, label="empirical residual") for value in self.residuals_f)
        )
        if len(residuals) < 2:
            raise CalibrationError("empirical distribution requires at least two residuals")
        object.__setattr__(self, "residuals_f", residuals)

    @property
    def kind(self) -> DistributionKind:
        return DistributionKind.EMPIRICAL

    def cdf(self, residual_f: Decimal) -> float:
        residual = _decimal(residual_f, label="residual")
        return bisect_left(self.residuals_f, residual) / len(self.residuals_f)

    def to_mapping(self) -> Mapping[str, object]:
        return {
            "kind": self.kind.value,
            "residuals_f": [format(value, "f") for value in self.residuals_f],
        }


class GroupLevel(StrEnum):
    CITY_SOURCE_LEAD_SEASON = "city_source_lead_season"
    REGION_SOURCE_LEAD_SEASON = "region_source_lead_season"
    SOURCE_LEAD_SEASON = "source_lead_season"
    SOURCE_LEAD = "source_lead"
    SOURCE = "source"


@dataclass(frozen=True, slots=True)
class CalibrationGroupKey:
    level: GroupLevel
    forecast_source: ForecastSource
    city: str | None = None
    climate_region: str | None = None
    lead_days: int | None = None
    season: Season | None = None

    def __post_init__(self) -> None:
        city = None if self.city is None else self.city.strip().lower()
        region = None if self.climate_region is None else self.climate_region.strip().lower()
        if city == "" or region == "":
            raise CalibrationError("calibration group location must not be blank")
        if self.lead_days is not None:
            _integer(self.lead_days, label="calibration group lead_days")
            if not 0 <= self.lead_days <= 7:
                raise CalibrationError("calibration group lead_days must be between 0 and 7")

        expected = {
            GroupLevel.CITY_SOURCE_LEAD_SEASON: (True, False, True, True),
            GroupLevel.REGION_SOURCE_LEAD_SEASON: (False, True, True, True),
            GroupLevel.SOURCE_LEAD_SEASON: (False, False, True, True),
            GroupLevel.SOURCE_LEAD: (False, False, True, False),
            GroupLevel.SOURCE: (False, False, False, False),
        }[self.level]
        actual = (
            city is not None,
            region is not None,
            self.lead_days is not None,
            self.season is not None,
        )
        if actual != expected:
            raise CalibrationError(f"invalid dimensions for calibration level {self.level.value}")
        object.__setattr__(self, "city", city)
        object.__setattr__(self, "climate_region", region)

    @property
    def stable_key(self) -> str:
        parts = [self.level.value, self.forecast_source.value]
        if self.city is not None:
            parts.append(f"city={self.city}")
        if self.climate_region is not None:
            parts.append(f"region={self.climate_region}")
        if self.lead_days is not None:
            parts.append(f"lead=D+{self.lead_days}")
        if self.season is not None:
            parts.append(f"season={self.season.value}")
        return "|".join(parts)


@dataclass(frozen=True, slots=True)
class CalibrationDiagnostics:
    jarque_bera: float
    normality_p_value: float
    normal_selection_crps: float | None = None
    empirical_selection_crps: float | None = None

    def __post_init__(self) -> None:
        for label, value in (
            ("jarque_bera", self.jarque_bera),
            ("normality_p_value", self.normality_p_value),
        ):
            if not math.isfinite(value) or value < 0:
                raise CalibrationError(f"{label} must be finite and non-negative")
        if self.normality_p_value > 1:
            raise CalibrationError("normality_p_value must not exceed one")
        for label, value in (
            ("normal_selection_crps", self.normal_selection_crps),
            ("empirical_selection_crps", self.empirical_selection_crps),
        ):
            if value is not None and (not math.isfinite(value) or value < 0):
                raise CalibrationError(f"{label} must be finite and non-negative")

    def to_mapping(self) -> Mapping[str, object]:
        return {
            "jarque_bera": self.jarque_bera,
            "normality_p_value": self.normality_p_value,
            "normal_selection_crps": self.normal_selection_crps,
            "empirical_selection_crps": self.empirical_selection_crps,
        }


@dataclass(frozen=True, slots=True)
class CalibrationGroup:
    key: CalibrationGroupKey
    sample_count: int
    distribution: ResidualDistribution
    training_end: date
    diagnostics: CalibrationDiagnostics

    def __post_init__(self) -> None:
        _integer(self.sample_count, label="group sample_count")
        if self.sample_count <= 0:
            raise CalibrationError("group sample_count must be positive")

    def to_mapping(self) -> Mapping[str, object]:
        return {
            "level": self.key.level.value,
            "forecast_source": self.key.forecast_source.value,
            "city": self.key.city,
            "climate_region": self.key.climate_region,
            "lead_days": self.key.lead_days,
            "season": None if self.key.season is None else self.key.season.value,
            "sample_count": self.sample_count,
            "training_end": self.training_end.isoformat(),
            "distribution": dict(self.distribution.to_mapping()),
            "diagnostics": dict(self.diagnostics.to_mapping()),
        }


@dataclass(frozen=True, slots=True)
class CalibrationArtifact:
    model_version: str
    created_at_utc: datetime
    forecast_contract_id: str
    observation_contract_id: str
    training_start: date
    training_end: date
    validation_start: date
    validation_end: date
    dataset_sha256: str
    min_sample_count: int
    groups: tuple[CalibrationGroup, ...]
    schema_version: int = _ARTIFACT_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != _ARTIFACT_SCHEMA_VERSION:
            raise CalibrationError(f"unsupported calibration schema version: {self.schema_version}")
        model_version = self.model_version.strip()
        forecast_contract = self.forecast_contract_id.strip()
        observation_contract = self.observation_contract_id.strip()
        if not model_version:
            raise CalibrationError("model_version must not be blank")
        if not forecast_contract:
            raise CalibrationError("forecast_contract_id must not be blank")
        if not observation_contract:
            raise CalibrationError("observation_contract_id must not be blank")
        created = _utc(self.created_at_utc, label="artifact creation time")
        if self.training_start > self.training_end:
            raise CalibrationError("training interval is reversed")
        if self.training_end >= self.validation_start:
            raise CalibrationError("validation must start after the training cutoff")
        if self.validation_start > self.validation_end:
            raise CalibrationError("validation interval is reversed")
        dataset_hash = _sha256(self.dataset_sha256, label="dataset hash")
        _integer(self.min_sample_count, label="min_sample_count")
        if self.min_sample_count < 2:
            raise CalibrationError("min_sample_count must be at least two")
        if not self.groups:
            raise CalibrationError("calibration artifact requires at least one group")
        keys = [group.key.stable_key for group in self.groups]
        if len(keys) != len(set(keys)):
            raise CalibrationError("calibration artifact contains duplicate groups")
        for group in self.groups:
            if group.training_end != self.training_end:
                raise CalibrationError("group training cutoff differs from artifact cutoff")
        object.__setattr__(self, "model_version", model_version)
        object.__setattr__(self, "forecast_contract_id", forecast_contract)
        object.__setattr__(self, "observation_contract_id", observation_contract)
        object.__setattr__(self, "created_at_utc", created)
        object.__setattr__(self, "dataset_sha256", dataset_hash)
        object.__setattr__(
            self, "groups", tuple(sorted(self.groups, key=lambda item: item.key.stable_key))
        )

    def payload_mapping(self) -> Mapping[str, object]:
        return {
            "schema_version": self.schema_version,
            "model_version": self.model_version,
            "created_at_utc": self.created_at_utc.isoformat(),
            "forecast_contract_id": self.forecast_contract_id,
            "observation_contract_id": self.observation_contract_id,
            "training_start": self.training_start.isoformat(),
            "training_end": self.training_end.isoformat(),
            "validation_start": self.validation_start.isoformat(),
            "validation_end": self.validation_end.isoformat(),
            "dataset_sha256": self.dataset_sha256,
            "min_sample_count": self.min_sample_count,
            "groups": [dict(group.to_mapping()) for group in self.groups],
        }

    @property
    def artifact_sha256(self) -> str:
        return hashlib.sha256(_canonical_bytes(self.payload_mapping())).hexdigest()

    def to_json(self) -> str:
        envelope: dict[str, object] = {
            "artifact_sha256": self.artifact_sha256,
            "artifact": dict(self.payload_mapping()),
        }
        return json.dumps(envelope, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


@dataclass(frozen=True, slots=True)
class ProbabilityEstimate:
    probability: float
    model_version: str
    artifact_sha256: str
    calibration_group_key: str
    fallback_level: GroupLevel
    distribution_type: DistributionKind
    calibration_sample_count: int
    training_cutoff: date

    def __post_init__(self) -> None:
        if not math.isfinite(self.probability) or not 0 <= self.probability <= 1:
            raise CalibrationError("model probability must be finite and between zero and one")


@dataclass(frozen=True, slots=True)
class CalibratedTemperatureModel:
    artifact: CalibrationArtifact

    def probability(
        self,
        *,
        city: str,
        climate_region: str,
        forecast_source: ForecastSource,
        market_date: date,
        lead_days: int,
        forecast_temperature_f: Decimal | int | str | float,
        bucket: TemperatureBucket,
    ) -> ProbabilityEstimate:
        city_key = city.strip().lower()
        region_key = climate_region.strip().lower()
        if not city_key or not region_key:
            raise CalibrationError("city and climate_region are required for calibration")
        _integer(lead_days, label="lead_days")
        if not 0 <= lead_days <= 7:
            raise CalibrationError("lead_days must be an integer between 0 and 7")
        forecast = _decimal(forecast_temperature_f, label="forecast temperature")
        season = Season.for_date(market_date)
        candidates = (
            CalibrationGroupKey(
                GroupLevel.CITY_SOURCE_LEAD_SEASON,
                forecast_source,
                city=city_key,
                lead_days=lead_days,
                season=season,
            ),
            CalibrationGroupKey(
                GroupLevel.REGION_SOURCE_LEAD_SEASON,
                forecast_source,
                climate_region=region_key,
                lead_days=lead_days,
                season=season,
            ),
            CalibrationGroupKey(
                GroupLevel.SOURCE_LEAD_SEASON,
                forecast_source,
                lead_days=lead_days,
                season=season,
            ),
            CalibrationGroupKey(
                GroupLevel.SOURCE_LEAD,
                forecast_source,
                lead_days=lead_days,
            ),
            CalibrationGroupKey(GroupLevel.SOURCE, forecast_source),
        )
        by_key = {group.key.stable_key: group for group in self.artifact.groups}
        selected: CalibrationGroup | None = None
        for candidate in candidates:
            group = by_key.get(candidate.stable_key)
            if group is not None and group.sample_count >= self.artifact.min_sample_count:
                selected = group
                break
        if selected is None:
            raise CalibrationError(
                "no compatible calibration group satisfies the minimum evidence policy"
            )
        probability = _bucket_probability(
            distribution=selected.distribution,
            forecast_temperature_f=forecast,
            bucket=bucket,
        )
        return ProbabilityEstimate(
            probability=probability,
            model_version=self.artifact.model_version,
            artifact_sha256=self.artifact.artifact_sha256,
            calibration_group_key=selected.key.stable_key,
            fallback_level=selected.key.level,
            distribution_type=selected.distribution.kind,
            calibration_sample_count=selected.sample_count,
            training_cutoff=self.artifact.training_end,
        )


def _bucket_probability(
    *,
    distribution: ResidualDistribution,
    forecast_temperature_f: Decimal,
    bucket: TemperatureBucket,
) -> float:
    lower = bucket.continuous_lower
    upper = bucket.continuous_upper
    if lower is None:
        if upper is None:
            raise CalibrationError("temperature bucket has no bounds")
        probability = distribution.cdf(upper - forecast_temperature_f)
    elif upper is None:
        probability = 1.0 - distribution.cdf(lower - forecast_temperature_f)
    else:
        probability = distribution.cdf(upper - forecast_temperature_f) - distribution.cdf(
            lower - forecast_temperature_f
        )
    if probability < 0 and math.isclose(probability, 0.0, abs_tol=1e-15):
        probability = 0.0
    if probability > 1 and math.isclose(probability, 1.0, abs_tol=1e-15):
        probability = 1.0
    if not math.isfinite(probability) or not 0 <= probability <= 1:
        raise CalibrationError(f"invalid calibrated bucket probability: {probability}")
    return probability


def _parse_group(value: object) -> CalibrationGroup:
    raw = _mapping(value, label="calibration group")
    try:
        level = GroupLevel(_text(raw.get("level"), label="group level"))
        source = ForecastSource(_text(raw.get("forecast_source"), label="group forecast source"))
    except ValueError as exc:
        raise CalibrationError("unsupported calibration group enum value") from exc
    city_raw = raw.get("city")
    region_raw = raw.get("climate_region")
    lead_raw = raw.get("lead_days")
    season_raw = raw.get("season")
    city = None if city_raw is None else _text(city_raw, label="group city")
    region = None if region_raw is None else _text(region_raw, label="group climate region")
    lead = None if lead_raw is None else _integer(lead_raw, label="group lead_days")
    try:
        season = None if season_raw is None else Season(_text(season_raw, label="group season"))
    except ValueError as exc:
        raise CalibrationError("unsupported calibration season") from exc

    distribution_raw = _mapping(raw.get("distribution"), label="group distribution")
    try:
        distribution_kind = DistributionKind(
            _text(distribution_raw.get("kind"), label="distribution kind")
        )
    except ValueError as exc:
        raise CalibrationError("unsupported residual distribution") from exc
    distribution: ResidualDistribution
    if distribution_kind is DistributionKind.NORMAL:
        distribution = NormalResidualDistribution(
            bias_f=_decimal(distribution_raw.get("bias_f"), label="normal bias"),
            sigma_f=_decimal(distribution_raw.get("sigma_f"), label="normal sigma"),
        )
    else:
        residual_values = _sequence(
            distribution_raw.get("residuals_f"),
            label="empirical residuals",
        )
        distribution = EmpiricalResidualDistribution(
            tuple(_decimal(item, label="empirical residual") for item in residual_values)
        )

    diagnostics_raw = _mapping(raw.get("diagnostics"), label="group diagnostics")
    diagnostics = CalibrationDiagnostics(
        jarque_bera=_number(diagnostics_raw.get("jarque_bera"), label="jarque_bera"),
        normality_p_value=_number(
            diagnostics_raw.get("normality_p_value"),
            label="normality_p_value",
        ),
        normal_selection_crps=_optional_number(
            diagnostics_raw.get("normal_selection_crps"),
            label="normal_selection_crps",
        ),
        empirical_selection_crps=_optional_number(
            diagnostics_raw.get("empirical_selection_crps"),
            label="empirical_selection_crps",
        ),
    )
    key = CalibrationGroupKey(
        level=level,
        forecast_source=source,
        city=city,
        climate_region=region,
        lead_days=lead,
        season=season,
    )
    return CalibrationGroup(
        key=key,
        sample_count=_integer(raw.get("sample_count"), label="group sample_count"),
        distribution=distribution,
        training_end=_date(raw.get("training_end"), label="group training_end"),
        diagnostics=diagnostics,
    )


def calibration_artifact_from_json(text: str) -> CalibrationArtifact:
    try:
        decoded: object = json.loads(text)
    except json.JSONDecodeError as exc:
        raise CalibrationError("calibration artifact is not valid JSON") from exc
    envelope = _mapping(decoded, label="artifact envelope")
    expected_sha = _sha256(envelope.get("artifact_sha256"), label="artifact checksum")
    raw = _mapping(envelope.get("artifact"), label="artifact payload")
    try:
        artifact = CalibrationArtifact(
            schema_version=_integer(raw.get("schema_version"), label="schema_version"),
            model_version=_text(raw.get("model_version"), label="model_version"),
            created_at_utc=_timestamp(raw.get("created_at_utc"), label="created_at_utc"),
            forecast_contract_id=_text(
                raw.get("forecast_contract_id"),
                label="forecast_contract_id",
            ),
            observation_contract_id=_text(
                raw.get("observation_contract_id"),
                label="observation_contract_id",
            ),
            training_start=_date(raw.get("training_start"), label="training_start"),
            training_end=_date(raw.get("training_end"), label="training_end"),
            validation_start=_date(raw.get("validation_start"), label="validation_start"),
            validation_end=_date(raw.get("validation_end"), label="validation_end"),
            dataset_sha256=_sha256(raw.get("dataset_sha256"), label="dataset hash"),
            min_sample_count=_integer(
                raw.get("min_sample_count"),
                label="min_sample_count",
            ),
            groups=tuple(
                _parse_group(item)
                for item in _sequence(raw.get("groups"), label="calibration groups")
            ),
        )
    except KeyError as exc:
        raise CalibrationError("calibration artifact is missing a required field") from exc
    if artifact.artifact_sha256 != expected_sha:
        raise CalibrationError("calibration artifact checksum mismatch")
    return artifact


def load_calibration_artifact(path: str | Path) -> CalibrationArtifact:
    return calibration_artifact_from_json(Path(path).read_text(encoding="utf-8"))
