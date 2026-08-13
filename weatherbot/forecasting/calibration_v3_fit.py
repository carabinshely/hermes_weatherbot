"""Frozen V3 offline calibration fitter.

Empirical residual candidates remain part of chronological diagnostics. Artifact groups
are emitted only when the complete allowed training sample has a finite positive-
variance bias-adjusted normal fit; otherwise that group is omitted so consumers can
use the existing broader-group hierarchy.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date, datetime

from weatherbot.forecasting.calibration import (
    CalibratedTemperatureModel,
    CalibrationArtifact,
    CalibrationDiagnostics,
    CalibrationError,
    CalibrationGroup,
    CalibrationGroupKey,
    CalibrationSample,
    DistributionKind,
)
from weatherbot.forecasting.calibration_fit import (
    ValidationMetrics,
    evaluate_holdout,
    validate_calibration_samples,
)
from weatherbot.forecasting.calibration_policy import CalibrationGroupFitDecision
from weatherbot.forecasting.calibration_v3_math import (
    empirical_distribution,
    inner_selection_split,
    jarque_bera,
    keys_for_sample,
    mean_crps,
    normal_distribution,
)


@dataclass(frozen=True, slots=True)
class V3CalibrationFitResult:
    artifact: CalibrationArtifact
    validation: ValidationMetrics
    group_fit_decisions: tuple[CalibrationGroupFitDecision, ...]


def _fit_group_v3(
    key: CalibrationGroupKey,
    samples: Sequence[CalibrationSample],
    *,
    training_end: date,
) -> tuple[CalibrationGroup | None, CalibrationGroupFitDecision]:
    ordered = tuple(
        sorted(samples, key=lambda item: (item.market_date, item.city, item.station_id))
    )
    inner_train, inner_validation = inner_selection_split(ordered)
    inner_residuals = tuple(sample.residual_f for sample in inner_train)
    validation_residuals = tuple(sample.residual_f for sample in inner_validation)

    normal_candidate = normal_distribution(inner_residuals)
    empirical_candidate = empirical_distribution(inner_residuals)
    normal_crps = (
        None if normal_candidate is None else mean_crps(normal_candidate, validation_residuals)
    )
    empirical_crps = mean_crps(empirical_candidate, validation_residuals)

    all_residuals = tuple(sample.residual_f for sample in ordered)
    fitted_normal = normal_distribution(all_residuals)
    jarque_bera_statistic, p_value = jarque_bera(all_residuals)
    diagnostics = CalibrationDiagnostics(
        jarque_bera=jarque_bera_statistic,
        normality_p_value=p_value,
        normal_selection_crps=normal_crps,
        empirical_selection_crps=empirical_crps,
    )

    if fitted_normal is None:
        return (
            None,
            CalibrationGroupFitDecision(
                key=key,
                sample_count=len(ordered),
                diagnostics=diagnostics,
                runtime_eligible=False,
                runtime_distribution_type=None,
                omission_reason="normal_fit_unavailable",
            ),
        )

    group = CalibrationGroup(
        key=key,
        sample_count=len(ordered),
        distribution=fitted_normal,
        training_end=training_end,
        diagnostics=diagnostics,
    )
    return (
        group,
        CalibrationGroupFitDecision(
            key=key,
            sample_count=len(ordered),
            diagnostics=diagnostics,
            runtime_eligible=True,
            runtime_distribution_type=DistributionKind.NORMAL,
        ),
    )


def fit_v3_calibration_artifact(
    samples: Iterable[CalibrationSample],
    *,
    model_version: str,
    created_at_utc: datetime,
    forecast_contract_id: str,
    observation_contract_id: str,
    training_start: date,
    training_end: date,
    validation_start: date,
    validation_end: date,
    dataset_sha256: str,
    min_sample_count: int,
) -> V3CalibrationFitResult:
    all_samples = validate_calibration_samples(samples)
    if training_end >= validation_start:
        raise CalibrationError("validation must start after the training cutoff")
    training = tuple(
        sample for sample in all_samples if training_start <= sample.market_date <= training_end
    )
    validation_samples = tuple(
        sample for sample in all_samples if validation_start <= sample.market_date <= validation_end
    )
    if not training:
        raise CalibrationError("training interval contains no samples")
    if not validation_samples:
        raise CalibrationError("validation interval contains no samples")
    if any(sample.market_date > training_end for sample in training):
        raise CalibrationError("training data leaks beyond the training cutoff")
    if any(sample.market_date <= training_end for sample in validation_samples):
        raise CalibrationError("validation data overlaps training data")

    grouped: dict[CalibrationGroupKey, list[CalibrationSample]] = defaultdict(list)
    for sample in training:
        for key in keys_for_sample(sample):
            grouped[key].append(sample)

    groups: list[CalibrationGroup] = []
    decisions: list[CalibrationGroupFitDecision] = []
    for key, members in sorted(grouped.items(), key=lambda item: item[0].stable_key):
        if len(members) < min_sample_count:
            continue
        group, decision = _fit_group_v3(key, members, training_end=training_end)
        decisions.append(decision)
        if group is not None:
            groups.append(group)

    if not groups:
        raise CalibrationError(
            "no calibration group satisfies the minimum sample and V3 normal-fit policy"
        )

    artifact = CalibrationArtifact(
        model_version=model_version,
        created_at_utc=created_at_utc,
        forecast_contract_id=forecast_contract_id,
        observation_contract_id=observation_contract_id,
        training_start=training_start,
        training_end=training_end,
        validation_start=validation_start,
        validation_end=validation_end,
        dataset_sha256=dataset_sha256,
        min_sample_count=min_sample_count,
        groups=tuple(groups),
    )
    validation = evaluate_holdout(CalibratedTemperatureModel(artifact), validation_samples)
    return V3CalibrationFitResult(
        artifact=artifact,
        validation=validation,
        group_fit_decisions=tuple(decisions),
    )
