"""Offline fitting and untouched-holdout validation for forecast calibration artifacts."""

from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal

from weatherbot.forecasting.calibration import (
    CalibratedTemperatureModel,
    CalibrationArtifact,
    CalibrationDiagnostics,
    CalibrationError,
    CalibrationGroup,
    CalibrationGroupKey,
    CalibrationSample,
    EmpiricalResidualDistribution,
    GroupLevel,
    NormalResidualDistribution,
    ResidualDistribution,
)
from weatherbot.markets import TemperatureBucket, TemperatureUnit

_FIXED_BASELINE_SIGMA_F = Decimal("2")
_SCORE_EPSILON = 1e-12
_RELIABILITY_BIN_COUNT = 10
_RELIABILITY_THRESHOLD_MIN_F = -100
_RELIABILITY_THRESHOLD_MAX_F = 160


@dataclass(frozen=True, slots=True)
class ReliabilityBin:
    lower_probability: float
    upper_probability: float
    mean_predicted_probability: float
    observed_frequency: float
    count: int

    def to_mapping(self) -> dict[str, float | int]:
        return {
            "lower_probability": self.lower_probability,
            "upper_probability": self.upper_probability,
            "mean_predicted_probability": self.mean_predicted_probability,
            "observed_frequency": self.observed_frequency,
            "count": self.count,
        }


@dataclass(frozen=True, slots=True)
class ValidationMetrics:
    sample_count: int
    forecast_bias_f: float
    mae_f: float
    rmse_f: float
    mean_log_score: float
    mean_ranked_probability_score: float
    baseline_mean_log_score: float
    baseline_mean_ranked_probability_score: float
    reliability_bins: tuple[ReliabilityBin, ...]

    def to_mapping(self) -> dict[str, object]:
        return {
            "sample_count": self.sample_count,
            "forecast_bias_f": self.forecast_bias_f,
            "mae_f": self.mae_f,
            "rmse_f": self.rmse_f,
            "mean_log_score": self.mean_log_score,
            "mean_ranked_probability_score": self.mean_ranked_probability_score,
            "baseline_sigma_f": float(_FIXED_BASELINE_SIGMA_F),
            "baseline_mean_log_score": self.baseline_mean_log_score,
            "baseline_mean_ranked_probability_score": self.baseline_mean_ranked_probability_score,
            "reliability_bins": [item.to_mapping() for item in self.reliability_bins],
        }


@dataclass(frozen=True, slots=True)
class CalibrationFitResult:
    artifact: CalibrationArtifact
    validation: ValidationMetrics


def _sample_standard_deviation(values: Sequence[Decimal]) -> Decimal | None:
    if len(values) < 2:
        return None
    mean = sum(values, Decimal("0")) / Decimal(len(values))
    variance = sum((value - mean) ** 2 for value in values) / Decimal(len(values) - 1)
    if variance <= 0:
        return None
    return variance.sqrt()


def _normal_distribution(values: Sequence[Decimal]) -> NormalResidualDistribution | None:
    sigma = _sample_standard_deviation(values)
    if sigma is None:
        return None
    bias = sum(values, Decimal("0")) / Decimal(len(values))
    return NormalResidualDistribution(bias_f=bias, sigma_f=sigma)


def _empirical_distribution(values: Sequence[Decimal]) -> EmpiricalResidualDistribution:
    return EmpiricalResidualDistribution(tuple(values))


def _normal_crps(distribution: NormalResidualDistribution, observed: Decimal) -> float:
    sigma = float(distribution.sigma_f)
    z = float((observed - distribution.bias_f) / distribution.sigma_f)
    phi = math.exp(-(z**2) / 2.0) / math.sqrt(2.0 * math.pi)
    cdf = distribution.cdf(observed)
    return sigma * (z * (2.0 * cdf - 1.0) + 2.0 * phi - 1.0 / math.sqrt(math.pi))


def _empirical_crps(distribution: EmpiricalResidualDistribution, observed: Decimal) -> float:
    values = distribution.residuals_f
    observed_float = float(observed)
    first = sum(abs(float(value) - observed_float) for value in values) / len(values)
    n = len(values)
    unordered_pair_sum = sum(
        (2 * index - n + 1) * float(value) for index, value in enumerate(values)
    )
    second = unordered_pair_sum / (n * n)
    return first - second


def _mean_crps(distribution: ResidualDistribution, observed: Sequence[Decimal]) -> float:
    if not observed:
        raise CalibrationError("distribution selection requires validation residuals")
    if isinstance(distribution, NormalResidualDistribution):
        scores = [_normal_crps(distribution, value) for value in observed]
    elif isinstance(distribution, EmpiricalResidualDistribution):
        scores = [_empirical_crps(distribution, value) for value in observed]
    else:
        raise CalibrationError("unsupported distribution during selection")
    return sum(scores) / len(scores)


def _jarque_bera(values: Sequence[Decimal]) -> tuple[float, float]:
    if len(values) < 3:
        return (0.0, 0.0)
    numeric = [float(value) for value in values]
    mean = sum(numeric) / len(numeric)
    second = sum((value - mean) ** 2 for value in numeric) / len(numeric)
    if second <= 0:
        return (float("1e308"), 0.0)
    third = sum((value - mean) ** 3 for value in numeric) / len(numeric)
    fourth = sum((value - mean) ** 4 for value in numeric) / len(numeric)
    skew = third / second**1.5
    kurtosis = fourth / second**2
    statistic = len(numeric) / 6.0 * (skew**2 + ((kurtosis - 3.0) ** 2) / 4.0)
    # Jarque-Bera is asymptotically chi-square with 2 degrees of freedom.
    p_value = math.exp(-statistic / 2.0)
    return (statistic, p_value)


def _inner_selection_split(
    samples: Sequence[CalibrationSample],
) -> tuple[tuple[CalibrationSample, ...], tuple[CalibrationSample, ...]]:
    ordered_dates = sorted({sample.market_date for sample in samples})
    if len(ordered_dates) < 2:
        raise CalibrationError("distribution selection requires at least two distinct dates")
    split_index = max(1, min(len(ordered_dates) - 1, int(len(ordered_dates) * 0.8)))
    validation_start = ordered_dates[split_index]
    inner_train = tuple(sample for sample in samples if sample.market_date < validation_start)
    inner_validation = tuple(sample for sample in samples if sample.market_date >= validation_start)
    if len(inner_train) < 2 or not inner_validation:
        raise CalibrationError("distribution selection split is too small")
    return (inner_train, inner_validation)


def _fit_group(
    key: CalibrationGroupKey,
    samples: Sequence[CalibrationSample],
    *,
    training_end: date,
) -> CalibrationGroup:
    ordered = tuple(
        sorted(samples, key=lambda item: (item.market_date, item.city, item.station_id))
    )
    inner_train, inner_validation = _inner_selection_split(ordered)
    inner_residuals = tuple(sample.residual_f for sample in inner_train)
    validation_residuals = tuple(sample.residual_f for sample in inner_validation)

    normal_candidate = _normal_distribution(inner_residuals)
    empirical_candidate = _empirical_distribution(inner_residuals)
    normal_crps = (
        None if normal_candidate is None else _mean_crps(normal_candidate, validation_residuals)
    )
    empirical_crps = _mean_crps(empirical_candidate, validation_residuals)

    choose_empirical = normal_crps is None or empirical_crps < normal_crps
    all_residuals = tuple(sample.residual_f for sample in ordered)
    if choose_empirical:
        distribution: ResidualDistribution = _empirical_distribution(all_residuals)
    else:
        fitted_normal = _normal_distribution(all_residuals)
        if fitted_normal is None:
            distribution = _empirical_distribution(all_residuals)
        else:
            distribution = fitted_normal

    jarque_bera, p_value = _jarque_bera(all_residuals)
    return CalibrationGroup(
        key=key,
        sample_count=len(ordered),
        distribution=distribution,
        training_end=training_end,
        diagnostics=CalibrationDiagnostics(
            jarque_bera=jarque_bera,
            normality_p_value=p_value,
            normal_selection_crps=normal_crps,
            empirical_selection_crps=empirical_crps,
        ),
    )


def _keys_for_sample(sample: CalibrationSample) -> tuple[CalibrationGroupKey, ...]:
    return (
        CalibrationGroupKey(
            GroupLevel.CITY_SOURCE_LEAD_SEASON,
            sample.forecast_source,
            city=sample.city,
            lead_days=sample.lead_days,
            season=sample.season,
        ),
        CalibrationGroupKey(
            GroupLevel.REGION_SOURCE_LEAD_SEASON,
            sample.forecast_source,
            climate_region=sample.climate_region,
            lead_days=sample.lead_days,
            season=sample.season,
        ),
        CalibrationGroupKey(
            GroupLevel.SOURCE_LEAD_SEASON,
            sample.forecast_source,
            lead_days=sample.lead_days,
            season=sample.season,
        ),
        CalibrationGroupKey(
            GroupLevel.SOURCE_LEAD,
            sample.forecast_source,
            lead_days=sample.lead_days,
        ),
        CalibrationGroupKey(GroupLevel.SOURCE, sample.forecast_source),
    )


def validate_calibration_samples(
    samples: Iterable[CalibrationSample],
) -> tuple[CalibrationSample, ...]:
    ordered = tuple(
        sorted(
            samples,
            key=lambda item: (
                item.market_date,
                item.city,
                item.lead_days,
                item.station_id,
                item.measurement_basis,
            ),
        )
    )
    if not ordered:
        raise CalibrationError("calibration dataset is empty")
    identities: set[tuple[str, object, date, int, str, str]] = set()
    for sample in ordered:
        if sample.identity in identities:
            raise CalibrationError(f"duplicate calibration sample identity: {sample.identity}")
        identities.add(sample.identity)
    return ordered


def fit_calibration_artifact(
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
) -> CalibrationFitResult:
    all_samples = validate_calibration_samples(samples)
    if training_end >= validation_start:
        raise CalibrationError("validation must start after the training cutoff")
    training = tuple(
        sample for sample in all_samples if training_start <= sample.market_date <= training_end
    )
    holdout = tuple(
        sample for sample in all_samples if validation_start <= sample.market_date <= validation_end
    )
    if not training:
        raise CalibrationError("training interval contains no samples")
    if not holdout:
        raise CalibrationError("validation interval contains no untouched holdout samples")
    if any(sample.market_date > training_end for sample in training):
        raise CalibrationError("training data leaks beyond the training cutoff")
    if any(sample.market_date <= training_end for sample in holdout):
        raise CalibrationError("holdout overlaps training data")

    grouped: dict[CalibrationGroupKey, list[CalibrationSample]] = defaultdict(list)
    for sample in training:
        for key in _keys_for_sample(sample):
            grouped[key].append(sample)

    groups: list[CalibrationGroup] = []
    for key, members in sorted(grouped.items(), key=lambda item: item[0].stable_key):
        if len(members) < min_sample_count:
            continue
        groups.append(_fit_group(key, members, training_end=training_end))
    if not groups:
        raise CalibrationError("no calibration group satisfies the minimum sample policy")

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
    validation = evaluate_holdout(CalibratedTemperatureModel(artifact), holdout)
    return CalibrationFitResult(artifact=artifact, validation=validation)


def _rounded_reported_temperature(value: Decimal) -> int:
    return int(value.quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def _baseline_probability(
    sample: CalibrationSample,
    bucket: TemperatureBucket,
) -> float:
    return bucket.probability(sample.forecast_temperature_f, _FIXED_BASELINE_SIGMA_F)


def evaluate_holdout(
    model: CalibratedTemperatureModel,
    samples: Sequence[CalibrationSample],
) -> ValidationMetrics:
    if not samples:
        raise CalibrationError("holdout evaluation requires samples")
    errors = [float(sample.residual_f) for sample in samples]
    log_scores: list[float] = []
    baseline_log_scores: list[float] = []
    ranked_scores: list[float] = []
    baseline_ranked_scores: list[float] = []
    reliability_pairs: list[tuple[float, int]] = []

    for sample in samples:
        observed_degree = _rounded_reported_temperature(sample.observed_temperature_f)
        realized_bucket = TemperatureBucket.bounded(
            observed_degree,
            observed_degree,
            TemperatureUnit.FAHRENHEIT,
        )
        estimate = model.probability(
            city=sample.city,
            climate_region=sample.climate_region,
            forecast_source=sample.forecast_source,
            market_date=sample.market_date,
            lead_days=sample.lead_days,
            forecast_temperature_f=sample.forecast_temperature_f,
            bucket=realized_bucket,
        )
        baseline_probability = _baseline_probability(sample, realized_bucket)
        log_scores.append(-math.log(max(estimate.probability, _SCORE_EPSILON)))
        baseline_log_scores.append(-math.log(max(baseline_probability, _SCORE_EPSILON)))

        sample_ranked_score = 0.0
        sample_baseline_ranked_score = 0.0
        for threshold in range(_RELIABILITY_THRESHOLD_MIN_F, _RELIABILITY_THRESHOLD_MAX_F + 1):
            threshold_bucket = TemperatureBucket.lower_tail(
                threshold,
                TemperatureUnit.FAHRENHEIT,
            )
            predicted = model.probability(
                city=sample.city,
                climate_region=sample.climate_region,
                forecast_source=sample.forecast_source,
                market_date=sample.market_date,
                lead_days=sample.lead_days,
                forecast_temperature_f=sample.forecast_temperature_f,
                bucket=threshold_bucket,
            ).probability
            baseline = _baseline_probability(sample, threshold_bucket)
            observed = int(observed_degree <= threshold)
            sample_ranked_score += (predicted - observed) ** 2
            sample_baseline_ranked_score += (baseline - observed) ** 2
            reliability_pairs.append((predicted, observed))
        ranked_scores.append(sample_ranked_score)
        baseline_ranked_scores.append(sample_baseline_ranked_score)

    return ValidationMetrics(
        sample_count=len(samples),
        forecast_bias_f=sum(errors) / len(errors),
        mae_f=sum(abs(value) for value in errors) / len(errors),
        rmse_f=math.sqrt(sum(value**2 for value in errors) / len(errors)),
        mean_log_score=sum(log_scores) / len(log_scores),
        mean_ranked_probability_score=sum(ranked_scores) / len(ranked_scores),
        baseline_mean_log_score=sum(baseline_log_scores) / len(baseline_log_scores),
        baseline_mean_ranked_probability_score=(
            sum(baseline_ranked_scores) / len(baseline_ranked_scores)
        ),
        reliability_bins=_reliability_bins(reliability_pairs),
    )


def _reliability_bins(pairs: Sequence[tuple[float, int]]) -> tuple[ReliabilityBin, ...]:
    bins: list[list[tuple[float, int]]] = [[] for _ in range(_RELIABILITY_BIN_COUNT)]
    for probability, observed in pairs:
        index = min(_RELIABILITY_BIN_COUNT - 1, int(probability * _RELIABILITY_BIN_COUNT))
        bins[index].append((probability, observed))
    result: list[ReliabilityBin] = []
    for index, members in enumerate(bins):
        if not members:
            continue
        lower = index / _RELIABILITY_BIN_COUNT
        upper = (index + 1) / _RELIABILITY_BIN_COUNT
        result.append(
            ReliabilityBin(
                lower_probability=lower,
                upper_probability=upper,
                mean_predicted_probability=sum(item[0] for item in members) / len(members),
                observed_frequency=sum(item[1] for item in members) / len(members),
                count=len(members),
            )
        )
    return tuple(result)
