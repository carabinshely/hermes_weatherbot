"""Pure fitting and diagnostic helpers for the frozen V3 calibration policy."""

from __future__ import annotations

import math
from collections.abc import Sequence
from decimal import Decimal, DecimalException

from weatherbot.forecasting.calibration import (
    CalibrationError,
    CalibrationGroupKey,
    CalibrationSample,
    EmpiricalResidualDistribution,
    GroupLevel,
    NormalResidualDistribution,
    ResidualDistribution,
)


def sample_standard_deviation(values: Sequence[Decimal]) -> Decimal | None:
    if len(values) < 2:
        return None
    try:
        mean = sum(values, Decimal("0")) / Decimal(len(values))
        variance = sum((value - mean) ** 2 for value in values) / Decimal(len(values) - 1)
        if not variance.is_finite() or variance <= 0:
            return None
        sigma = variance.sqrt()
    except DecimalException:
        return None
    if not sigma.is_finite() or sigma <= 0:
        return None
    return sigma


def normal_distribution(values: Sequence[Decimal]) -> NormalResidualDistribution | None:
    sigma = sample_standard_deviation(values)
    if sigma is None:
        return None
    try:
        bias = sum(values, Decimal("0")) / Decimal(len(values))
    except DecimalException:
        return None
    if not bias.is_finite():
        return None
    try:
        return NormalResidualDistribution(bias_f=bias, sigma_f=sigma)
    except CalibrationError:
        return None


def empirical_distribution(values: Sequence[Decimal]) -> EmpiricalResidualDistribution:
    return EmpiricalResidualDistribution(tuple(values))


def normal_crps(distribution: NormalResidualDistribution, observed: Decimal) -> float:
    sigma = float(distribution.sigma_f)
    z = float((observed - distribution.bias_f) / distribution.sigma_f)
    phi = math.exp(-(z**2) / 2.0) / math.sqrt(2.0 * math.pi)
    cdf = distribution.cdf(observed)
    return sigma * (z * (2.0 * cdf - 1.0) + 2.0 * phi - 1.0 / math.sqrt(math.pi))


def empirical_crps(distribution: EmpiricalResidualDistribution, observed: Decimal) -> float:
    values = distribution.residuals_f
    observed_float = float(observed)
    first = sum(abs(float(value) - observed_float) for value in values) / len(values)
    n = len(values)
    unordered_pair_sum = sum(
        (2 * index - n + 1) * float(value) for index, value in enumerate(values)
    )
    second = unordered_pair_sum / (n * n)
    return first - second


def mean_crps(distribution: ResidualDistribution, observed: Sequence[Decimal]) -> float:
    if not observed:
        raise CalibrationError("distribution diagnostics require validation residuals")
    if isinstance(distribution, NormalResidualDistribution):
        scores = [normal_crps(distribution, value) for value in observed]
    elif isinstance(distribution, EmpiricalResidualDistribution):
        scores = [empirical_crps(distribution, value) for value in observed]
    else:
        raise CalibrationError("unsupported distribution during diagnostics")
    return sum(scores) / len(scores)


def jarque_bera(values: Sequence[Decimal]) -> tuple[float, float]:
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
    return (statistic, math.exp(-statistic / 2.0))


def inner_selection_split(
    samples: Sequence[CalibrationSample],
) -> tuple[tuple[CalibrationSample, ...], tuple[CalibrationSample, ...]]:
    ordered_dates = sorted({sample.market_date for sample in samples})
    if len(ordered_dates) < 2:
        raise CalibrationError("distribution diagnostics require at least two distinct dates")
    split_index = max(1, min(len(ordered_dates) - 1, int(len(ordered_dates) * 0.8)))
    validation_start = ordered_dates[split_index]
    inner_train = tuple(sample for sample in samples if sample.market_date < validation_start)
    inner_validation = tuple(sample for sample in samples if sample.market_date >= validation_start)
    if len(inner_train) < 2 or not inner_validation:
        raise CalibrationError("distribution diagnostic split is too small")
    return (inner_train, inner_validation)


def keys_for_sample(sample: CalibrationSample) -> tuple[CalibrationGroupKey, ...]:
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
