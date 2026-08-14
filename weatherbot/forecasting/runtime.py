"""Fail-closed runtime boundary for accepted calibrated temperature probabilities.

A mechanically valid calibration artifact is not sufficient for runtime use.  Runtime
loading requires a separate, reviewed approval manifest at one fixed repository location.
This module is intentionally execution-agnostic: it produces model probabilities and
provenance only.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import cast

from weatherbot.forecasting.archive import PRODUCTION_FORECAST_CONTRACT_ID
from weatherbot.forecasting.calibration import (
    CalibratedTemperatureModel,
    CalibrationError,
    load_calibration_artifact,
)
from weatherbot.forecasting.contracts import (
    CALIBRATION_LEAD_DAYS,
    OBSERVATION_CONTRACT_ID,
)
from weatherbot.forecasting.model import WeatherInputSnapshot
from weatherbot.markets import TemperatureBucket

_APPROVAL_SCHEMA_VERSION = 1
_APPROVAL_RELATIVE_PATH = Path("config/calibration-approval.json")
_ACCEPTED_ARTIFACT_DIRECTORY = Path("artifacts/calibration/accepted")
_REJECTED_ARTIFACT_SHA256 = frozenset(
    {
        "aff6f9c1e8f6971104e6640abcd4306bc68e84116b9a52d9d9ee993ea468cc07",
        "d0f09ff723fab5bc250e824bb2edc66f96730575c5b569778432b8f5b5eefbdc",
    }
)
_APPROVAL_FIELDS = frozenset(
    {
        "schema_version",
        "decision",
        "model_version",
        "artifact_path",
        "artifact_sha256",
        "forecast_contract_id",
        "observation_contract_id",
        "acceptance_reference",
        "accepted_at_utc",
    }
)


class CalibrationRuntimeError(RuntimeError):
    """Base failure for runtime calibration approval or compatibility."""


class CalibrationUnavailable(CalibrationRuntimeError):
    """Raised when no approved probability model is available for runtime use."""


class CalibrationApprovalError(CalibrationRuntimeError):
    """Raised when runtime approval evidence is malformed or invalid."""


class CalibrationCompatibilityError(CalibrationRuntimeError):
    """Raised when approved evidence does not match the runtime model contracts."""


def _nonblank(value: object, *, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise CalibrationApprovalError(f"{label} must be a non-blank string")
    return value.strip()


def _sha256(value: object, *, label: str) -> str:
    digest = _nonblank(value, label=label).lower()
    if len(digest) != 64 or any(character not in "0123456789abcdef" for character in digest):
        raise CalibrationApprovalError(f"{label} must be a SHA-256 hex digest")
    return digest


def _accepted_timestamp(value: object) -> datetime:
    text = _nonblank(value, label="accepted_at_utc")
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise CalibrationApprovalError("accepted_at_utc must be ISO-8601") from exc
    offset = parsed.utcoffset()
    if parsed.tzinfo is None or offset is None:
        raise CalibrationApprovalError("accepted_at_utc must be timezone-aware")
    if offset.total_seconds() != 0:
        raise CalibrationApprovalError("accepted_at_utc must use UTC")
    return parsed


@dataclass(frozen=True, slots=True)
class CalibrationApproval:
    model_version: str
    artifact_path: Path
    artifact_sha256: str
    forecast_contract_id: str
    observation_contract_id: str
    acceptance_reference: str
    accepted_at_utc: datetime


@dataclass(frozen=True, slots=True)
class CalibratedProbability:
    """One model probability bundled with the provenance required to reproduce it."""

    model_probability: Decimal
    model_version: str
    artifact_sha256: str
    forecast_source: str
    calibration_group_key: str
    fallback_level: str
    distribution_type: str
    calibration_sample_count: int
    training_cutoff: date

    def audit_metadata(self) -> Mapping[str, object]:
        return {
            "model_probability": format(self.model_probability, "f"),
            "model_version": self.model_version,
            "artifact_sha256": self.artifact_sha256,
            "forecast_source": self.forecast_source,
            "calibration_group_key": self.calibration_group_key,
            "fallback_level": self.fallback_level,
            "distribution_type": self.distribution_type,
            "calibration_sample_count": self.calibration_sample_count,
            "training_cutoff": self.training_cutoff.isoformat(),
        }


@dataclass(frozen=True, slots=True)
class CalibratedProbabilityRuntime:
    """Scanner-facing wrapper around one separately approved immutable artifact."""

    model: CalibratedTemperatureModel

    def probability(
        self,
        *,
        city: str,
        climate_region: str,
        lead_days: int,
        weather: WeatherInputSnapshot,
        bucket: TemperatureBucket,
    ) -> CalibratedProbability:
        if lead_days not in CALIBRATION_LEAD_DAYS:
            raise CalibrationCompatibilityError(
                f"lead_days={lead_days} is outside calibrated lead set {CALIBRATION_LEAD_DAYS}"
            )
        estimate = self.model.probability(
            city=city,
            climate_region=climate_region,
            forecast_source=weather.forecast.source,
            market_date=weather.forecast.market_date,
            lead_days=lead_days,
            forecast_temperature_f=weather.signal_temperature_f,
            bucket=bucket,
        )
        return CalibratedProbability(
            model_probability=Decimal(str(estimate.probability)),
            model_version=estimate.model_version,
            artifact_sha256=estimate.artifact_sha256,
            forecast_source=weather.forecast.source.value,
            calibration_group_key=estimate.calibration_group_key,
            fallback_level=estimate.fallback_level.value,
            distribution_type=estimate.distribution_type.value,
            calibration_sample_count=estimate.calibration_sample_count,
            training_cutoff=estimate.training_cutoff,
        )


def _load_approval(repository_root: Path) -> CalibrationApproval:
    approval_path = repository_root / _APPROVAL_RELATIVE_PATH
    if not approval_path.is_file():
        raise CalibrationUnavailable(
            f"no accepted calibration approval is configured at {_APPROVAL_RELATIVE_PATH}"
        )
    try:
        decoded: object = json.loads(approval_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CalibrationApprovalError("calibration approval manifest is not valid JSON") from exc
    if not isinstance(decoded, dict):
        raise CalibrationApprovalError("calibration approval manifest must be an object")
    raw = cast(dict[str, object], decoded)
    unknown = set(raw) - _APPROVAL_FIELDS
    missing = _APPROVAL_FIELDS - set(raw)
    if unknown:
        raise CalibrationApprovalError(
            f"calibration approval manifest has unsupported fields: {sorted(unknown)}"
        )
    if missing:
        raise CalibrationApprovalError(
            f"calibration approval manifest is missing fields: {sorted(missing)}"
        )
    if raw["schema_version"] != _APPROVAL_SCHEMA_VERSION:
        raise CalibrationApprovalError("unsupported calibration approval schema")
    if raw["decision"] != "accepted":
        raise CalibrationUnavailable("calibration approval decision is not accepted")

    artifact_path_text = _nonblank(raw["artifact_path"], label="artifact_path")
    relative_artifact = Path(artifact_path_text)
    if relative_artifact.is_absolute() or ".." in relative_artifact.parts:
        raise CalibrationApprovalError("artifact_path must be a repository-relative safe path")
    accepted_directory = (repository_root / _ACCEPTED_ARTIFACT_DIRECTORY).resolve()
    artifact_path = (repository_root / relative_artifact).resolve()
    if not artifact_path.is_relative_to(accepted_directory):
        raise CalibrationApprovalError(
            f"artifact_path must be inside {_ACCEPTED_ARTIFACT_DIRECTORY}"
        )

    artifact_sha = _sha256(raw["artifact_sha256"], label="artifact_sha256")
    if artifact_sha in _REJECTED_ARTIFACT_SHA256:
        raise CalibrationApprovalError("known rejected calibration artifact cannot be approved")
    if artifact_path.name != f"{artifact_sha}.json":
        raise CalibrationApprovalError("accepted artifact filename must equal its SHA-256")

    return CalibrationApproval(
        model_version=_nonblank(raw["model_version"], label="model_version"),
        artifact_path=artifact_path,
        artifact_sha256=artifact_sha,
        forecast_contract_id=_nonblank(raw["forecast_contract_id"], label="forecast_contract_id"),
        observation_contract_id=_nonblank(
            raw["observation_contract_id"], label="observation_contract_id"
        ),
        acceptance_reference=_nonblank(raw["acceptance_reference"], label="acceptance_reference"),
        accepted_at_utc=_accepted_timestamp(raw["accepted_at_utc"]),
    )


def load_calibrated_probability_runtime(
    *, repository_root: str | Path
) -> CalibratedProbabilityRuntime:
    """Load exactly one reviewed artifact or fail closed before probability generation."""

    root = Path(repository_root).resolve()
    approval = _load_approval(root)
    if approval.forecast_contract_id != PRODUCTION_FORECAST_CONTRACT_ID:
        raise CalibrationCompatibilityError("approved forecast contract is incompatible")
    if approval.observation_contract_id != OBSERVATION_CONTRACT_ID:
        raise CalibrationCompatibilityError("approved observation contract is incompatible")
    if not approval.artifact_path.is_file():
        raise CalibrationUnavailable("approved calibration artifact is missing")
    try:
        artifact = load_calibration_artifact(approval.artifact_path)
    except (OSError, CalibrationError) as exc:
        raise CalibrationApprovalError(
            "approved calibration artifact failed strict loading"
        ) from exc
    if artifact.artifact_sha256 != approval.artifact_sha256:
        raise CalibrationApprovalError("approval artifact SHA does not match loaded artifact")
    if artifact.model_version != approval.model_version:
        raise CalibrationApprovalError("approval model version does not match loaded artifact")
    if artifact.forecast_contract_id != approval.forecast_contract_id:
        raise CalibrationCompatibilityError("artifact forecast contract differs from approval")
    if artifact.observation_contract_id != approval.observation_contract_id:
        raise CalibrationCompatibilityError("artifact observation contract differs from approval")
    return CalibratedProbabilityRuntime(CalibratedTemperatureModel(artifact))
