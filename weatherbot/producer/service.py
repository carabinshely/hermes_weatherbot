"""Deterministic versioned public strategy policy over calibrated read-only evidence."""

from __future__ import annotations

from datetime import UTC, datetime

from weatherbot.producer.config import ProducerPolicy
from weatherbot.producer.model import (
    CalibratedMarketCandidate,
    HermesSignal,
    SignalMarketReference,
    make_signal_id,
)
from weatherbot.quoting import QuoteEvaluation, evaluate_executable_buy

PRODUCER_ID = "hermes_weatherbot"
VENUE = "polymarket"


def evaluate_candidate(
    candidate: CalibratedMarketCandidate,
    policy: ProducerPolicy,
    *,
    evaluated_at: datetime,
) -> tuple[HermesSignal | None, QuoteEvaluation]:
    """Apply only immutable producer policy; no bankroll/PAPER/learning state is consulted."""
    evaluation = evaluate_executable_buy(
        probability=candidate.calibrated.model_probability,
        requested_budget=policy.market_reference_notional,
        weather=candidate.weather,
        event=candidate.event,
        order_book=candidate.decision_book,
        evaluated_at=evaluated_at,
        freshness_policy=policy.freshness_policy,
        cost_policy=policy.cost_policy,
        balance=None,
    )
    if not evaluation.accepted:
        return None, evaluation

    validated = evaluation.quote
    assert validated is not None
    quote = validated.quote
    reference = SignalMarketReference(
        kind="executable_read_only",
        order_book_hash=quote.book_hash,
        observed_at_utc=quote.observed_at,
        reference_notional=policy.market_reference_notional,
        best_bid=quote.best_bid,
        best_ask=quote.best_ask,
        average_reference_price=quote.average_price,
        all_in_reference_price=validated.all_in_average_price,
        worst_reference_price=quote.worst_price,
        probability_edge=validated.probability_edge,
        expected_return=validated.expected_return,
        quote_fingerprint=validated.fingerprint,
    )
    calibration_fingerprint = candidate.calibrated.calibration_fingerprint()
    signal_id = make_signal_id(
        producer_id=PRODUCER_ID,
        strategy_id=policy.strategy_id,
        strategy_version=policy.strategy_version,
        policy_fingerprint=policy.fingerprint,
        venue=VENUE,
        event_id=candidate.event_id,
        market_id=candidate.market_id,
        outcome=candidate.outcome,
        token_id=candidate.token_id,
        market_date=candidate.market_date,
        calibration_fingerprint=calibration_fingerprint,
        weather_fingerprint=candidate.calibrated.weather_fingerprint,
        market_reference=reference,
    )
    signal = HermesSignal(
        signal_id=signal_id,
        producer_id=PRODUCER_ID,
        strategy_id=policy.strategy_id,
        strategy_version=policy.strategy_version,
        policy_fingerprint=policy.fingerprint,
        generated_at_utc=evaluated_at.astimezone(UTC),
        venue=VENUE,
        event_id=candidate.event_id,
        market_id=candidate.market_id,
        condition_id=candidate.condition_id,
        outcome=candidate.outcome,
        token_id=candidate.token_id,
        question=candidate.question,
        city_slug=candidate.city_slug,
        city_name=candidate.city_name,
        market_date=candidate.market_date,
        market_timezone=candidate.market_timezone,
        bucket_key=candidate.bucket.key,
        bucket_label=candidate.bucket.label,
        forecast_temperature_f=candidate.weather.signal_temperature_f,
        model_probability=candidate.calibrated.model_probability,
        classification="accepted",
        market_reference=reference,
        model_version=candidate.calibrated.model_version,
        artifact_sha256=candidate.calibrated.artifact_sha256,
        calibration_fingerprint=calibration_fingerprint,
        weather_fingerprint=candidate.calibrated.weather_fingerprint,
        forecast_source=candidate.calibrated.forecast_source,
        calibration_group_key=candidate.calibrated.calibration_group_key,
        fallback_level=candidate.calibrated.fallback_level,
        distribution_type=candidate.calibrated.distribution_type,
        calibration_sample_count=candidate.calibrated.calibration_sample_count,
        training_cutoff=candidate.calibrated.training_cutoff,
    )
    return signal, evaluation
