"""Versioned offline calibration fitting-policy definitions."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from weatherbot.forecasting.calibration import (
    CalibrationDiagnostics,
    CalibrationError,
    CalibrationGroupKey,
    DistributionKind,
)


class CalibrationFittingPolicy(StrEnum):
    """Stable identifiers for reproducible calibration fitting behavior."""

    LEGACY_FAMILY_SELECTION_V1 = "legacy-family-selection-v1"
    V3_NORMAL_RUNTIME_V1 = "v3-normal-runtime-v1"


@dataclass(frozen=True, slots=True)
class CalibrationGroupFitDecision:
    """Diagnostic evidence and artifact-inclusion decision for one candidate group."""

    key: CalibrationGroupKey
    sample_count: int
    diagnostics: CalibrationDiagnostics
    runtime_eligible: bool
    runtime_distribution_type: DistributionKind | None
    omission_reason: str | None = None

    def __post_init__(self) -> None:
        if self.sample_count <= 0:
            raise CalibrationError("group fit decision sample_count must be positive")
        if self.runtime_eligible != (self.runtime_distribution_type is not None):
            raise CalibrationError("runtime eligibility and distribution type disagree")
        if self.runtime_eligible and self.omission_reason is not None:
            raise CalibrationError("runtime-eligible group cannot have an omission reason")
        if not self.runtime_eligible and not self.omission_reason:
            raise CalibrationError("omitted group requires an omission reason")

    def to_mapping(self) -> dict[str, object]:
        return {
            "calibration_group_key": self.key.stable_key,
            "level": self.key.level.value,
            "forecast_source": self.key.forecast_source.value,
            "city": self.key.city,
            "climate_region": self.key.climate_region,
            "lead_days": self.key.lead_days,
            "season": None if self.key.season is None else self.key.season.value,
            "sample_count": self.sample_count,
            "diagnostics": dict(self.diagnostics.to_mapping()),
            "runtime_eligible": self.runtime_eligible,
            "runtime_distribution_type": (
                None
                if self.runtime_distribution_type is None
                else self.runtime_distribution_type.value
            ),
            "omission_reason": self.omission_reason,
        }
