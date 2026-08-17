from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from typing import cast

import pytest

from tests.paper.helpers import calibrated_probability
from tests.quoting.helpers import (
    CONDITION,
    NOW,
    TOKEN,
    event_snapshot,
    order_book,
    weather_snapshot,
)
from weatherbot.markets import TemperatureBucket, TemperatureUnit
from weatherbot.producer.config import ProducerPolicy
from weatherbot.producer.model import CalibratedMarketCandidate
from weatherbot.producer.service import evaluate_candidate
from weatherbot.quoting import DepthPolicy


def policy(
    *,
    strategy_version: str = "1",
    minimum_return: str = "0.10",
    future_tolerance_seconds: int = 5,
    depth_policy: DepthPolicy = DepthPolicy.REJECT,
) -> ProducerPolicy:
    return ProducerPolicy(
        schema_version=1,
        strategy_id="bot-v3-weather",
        strategy_version=strategy_version,
        scan_interval_seconds=3600,
        min_volume=Decimal("500"),
        min_hours=Decimal("2"),
        max_hours=Decimal("72"),
        market_reference_notional=Decimal("2.00"),
        minimum_expected_return=Decimal(minimum_return),
        maximum_all_in_price=Decimal("0.80"),
        maximum_average_slippage=Decimal("0.03"),
        maximum_worst_slippage=Decimal("0.05"),
        maximum_forecast_age_seconds=21600,
        maximum_event_age_seconds=120,
        maximum_order_book_age_seconds=30,
        future_tolerance_seconds=future_tolerance_seconds,
        platform_fee_reserve_rate=Decimal("0.01"),
        transaction_cost_reserve=Decimal("0.01"),
        market_reference_safety_margin_rate=Decimal("0.02"),
        depth_policy=depth_policy,
        signal_log_path=Path("state/test-signals.jsonl"),
    )


def candidate() -> CalibratedMarketCandidate:
    weather = weather_snapshot()
    event = event_snapshot()
    return CalibratedMarketCandidate(
        city_slug="chicago",
        city_name="Chicago",
        horizon="D+0",
        market_date=weather.forecast.market_date,
        market_timezone=weather.forecast.market_timezone,
        event_id=event.event_id,
        market_id="paper-weather-market",
        condition_id=str(CONDITION),
        outcome="yes",
        token_id=str(TOKEN),
        question="Will it be between 85-86°F?",
        bucket=TemperatureBucket.bounded(85, 86, TemperatureUnit.FAHRENHEIT),
        volume=Decimal("1000"),
        weather=weather,
        event=event,
        decision_book=order_book(first_size="100", second_size="100"),
        calibrated=calibrated_probability(),
    )


def test_policy_fingerprint_covers_all_decision_thresholds() -> None:
    baseline = policy()
    assert baseline.fingerprint == policy().fingerprint
    assert baseline.fingerprint != policy(strategy_version="2").fingerprint
    assert baseline.fingerprint != policy(minimum_return="0.11").fingerprint
    assert baseline.fingerprint != policy(future_tolerance_seconds=6).fingerprint


def test_public_policy_rejects_depth_reduction() -> None:
    with pytest.raises(ValueError, match="reject depth policy"):
        policy(depth_policy=DepthPolicy.REDUCE)


def test_candidate_rejects_event_identity_mixup() -> None:
    with pytest.raises(ValueError, match="event_id"):
        replace(candidate(), event_id="different-event")


def test_candidate_rejects_horizon_calibration_mixup() -> None:
    with pytest.raises(ValueError, match="horizon"):
        replace(candidate(), horizon="D+1")


def test_real_signal_identity_is_stable_across_processing_time_and_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    item = candidate()
    selected_policy = policy()
    monkeypatch.setenv("PK", "different-private-key-that-must-not-matter")
    monkeypatch.setenv("WALLET", "0xdeadbeef")

    first, first_eval = evaluate_candidate(item, selected_policy, evaluated_at=NOW)
    second, second_eval = evaluate_candidate(
        item,
        selected_policy,
        evaluated_at=NOW + timedelta(seconds=1),
    )

    assert first_eval.accepted and second_eval.accepted
    assert first is not None and second is not None
    assert first.signal_id == second.signal_id
    assert first.policy_fingerprint == selected_policy.fingerprint
    assert first.market_reference.reference_notional == Decimal("2.00")
    assert first.market_reference.quote_fingerprint != second.market_reference.quote_fingerprint


def test_real_signal_payload_contains_no_execution_or_paper_state() -> None:
    signal, evaluation = evaluate_candidate(candidate(), policy(), evaluated_at=NOW)
    assert evaluation.accepted
    assert signal is not None
    payload = signal.to_mapping()
    forbidden = {
        "wallet",
        "balance",
        "bankroll",
        "position",
        "positions",
        "ledger",
        "pnl",
        "kelly",
        "order_id",
        "shares_to_buy",
        "bet_size",
    }
    assert forbidden.isdisjoint(payload)
    assert payload["climate_region"] == signal.climate_region
    assert payload["lead_days"] == signal.lead_days
    market_reference_value = payload["market_reference"]
    assert isinstance(market_reference_value, dict)
    market_reference = cast(dict[str, object], market_reference_value)
    assert forbidden.isdisjoint(market_reference)


def test_signal_contract_schema_and_calibration_provenance_are_fixed() -> None:
    signal, evaluation = evaluate_candidate(candidate(), policy(), evaluated_at=NOW)
    assert evaluation.accepted
    assert signal is not None

    with pytest.raises(ValueError, match="contract"):
        replace(signal, contract="other.signal")
    with pytest.raises(ValueError, match="schema_version"):
        replace(signal, schema_version="2")
    with pytest.raises(ValueError, match="accepted producer signals"):
        replace(signal, classification="paper")
    with pytest.raises(ValueError, match="calibration_fingerprint"):
        replace(signal, model_probability=Decimal("0.70"))
    with pytest.raises(ValueError, match="calibration_fingerprint"):
        replace(signal, climate_region="different-region")


def test_signal_id_binds_condition_and_market_reference_economics() -> None:
    signal, evaluation = evaluate_candidate(candidate(), policy(), evaluated_at=NOW)
    assert evaluation.accepted
    assert signal is not None

    with pytest.raises(ValueError, match="signal_id"):
        replace(signal, condition_id="different-condition")
    changed_reference = replace(
        signal.market_reference,
        expected_return=signal.market_reference.expected_return + Decimal("0.01"),
    )
    with pytest.raises(ValueError, match="signal_id"):
        replace(signal, market_reference=changed_reference)


def test_strategy_version_changes_logical_signal_identity() -> None:
    item = candidate()
    first, _ = evaluate_candidate(item, policy(strategy_version="1"), evaluated_at=NOW)
    second, _ = evaluate_candidate(item, policy(strategy_version="2"), evaluated_at=NOW)
    assert first is not None and second is not None
    assert first.signal_id != second.signal_id
