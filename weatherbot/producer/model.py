"""Typed public-producer contracts for Hermes signals."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

from weatherbot.domain import fingerprint
from weatherbot.forecasting import CalibratedProbability, WeatherInputSnapshot
from weatherbot.markets import OrderBookSnapshot, TemperatureBucket
from weatherbot.quoting import MarketEventSnapshot


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamp must be timezone-aware")
    return value.astimezone(UTC)


def _nonblank(value: str, *, label: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{label} must not be blank")
    return normalized


def _decimal_text(value: Decimal) -> str:
    if not value.is_finite():
        raise ValueError("decimal signal values must be finite")
    return format(value, "f")


@dataclass(frozen=True, slots=True)
class CalibratedMarketCandidate:
    city_slug: str
    city_name: str
    horizon: str
    market_date: date
    market_timezone: str
    event_id: str
    market_id: str
    condition_id: str
    outcome: str
    token_id: str
    question: str
    bucket: TemperatureBucket
    volume: Decimal
    weather: WeatherInputSnapshot
    event: MarketEventSnapshot
    decision_book: OrderBookSnapshot
    calibrated: CalibratedProbability

    def __post_init__(self) -> None:
        for label, value in (
            ("city_slug", self.city_slug),
            ("city_name", self.city_name),
            ("horizon", self.horizon),
            ("market_timezone", self.market_timezone),
            ("event_id", self.event_id),
            ("market_id", self.market_id),
            ("condition_id", self.condition_id),
            ("outcome", self.outcome),
            ("token_id", self.token_id),
            ("question", self.question),
        ):
            object.__setattr__(self, label, _nonblank(value, label=label))
        if not self.volume.is_finite() or self.volume < 0:
            raise ValueError("volume must be finite and non-negative")
        if self.market_date != self.weather.forecast.market_date:
            raise ValueError("candidate market date must match weather forecast")
        if self.market_timezone != self.weather.forecast.market_timezone:
            raise ValueError("candidate timezone must match weather forecast")
        if self.event_id != self.event.event_id:
            raise ValueError("candidate event_id must match market event snapshot")
        if self.city_slug != self.calibrated.city_slug:
            raise ValueError("candidate city must match calibrated probability")
        if self.horizon != f"D+{self.calibrated.lead_days}":
            raise ValueError("candidate horizon must match calibrated lead_days")
        if self.bucket.key != self.calibrated.bucket_key:
            raise ValueError("candidate bucket must match calibrated probability")
        if self.calibrated.weather_fingerprint != fingerprint(self.weather):
            raise ValueError("candidate weather must match calibrated probability fingerprint")
        if self.calibrated.forecast_source != self.weather.forecast.source.value:
            raise ValueError("candidate forecast source must match calibrated probability")
        if self.condition_id != str(self.decision_book.condition_id):
            raise ValueError("candidate condition_id must match decision order book")
        if self.token_id != str(self.decision_book.token_id):
            raise ValueError("candidate token_id must match decision order book")


@dataclass(frozen=True, slots=True)
class SignalMarketReference:
    kind: str
    order_book_hash: str
    observed_at_utc: datetime
    reference_notional: Decimal
    best_bid: Decimal
    best_ask: Decimal
    average_reference_price: Decimal
    all_in_reference_price: Decimal
    worst_reference_price: Decimal
    probability_edge: Decimal
    expected_return: Decimal
    quote_fingerprint: str

    def __post_init__(self) -> None:
        kind = _nonblank(self.kind, label="reference kind")
        if kind != "executable_read_only":
            raise ValueError("unsupported market-reference kind")
        object.__setattr__(self, "kind", kind)
        object.__setattr__(
            self, "order_book_hash", _nonblank(self.order_book_hash, label="order_book_hash")
        )
        object.__setattr__(
            self, "quote_fingerprint", _nonblank(self.quote_fingerprint, label="quote_fingerprint")
        )
        object.__setattr__(self, "observed_at_utc", _utc(self.observed_at_utc))
        for value in (
            self.reference_notional,
            self.best_bid,
            self.best_ask,
            self.average_reference_price,
            self.all_in_reference_price,
            self.worst_reference_price,
            self.probability_edge,
            self.expected_return,
        ):
            _decimal_text(value)
        if self.reference_notional <= 0:
            raise ValueError("reference_notional must be positive")
        if not Decimal("0") < self.best_bid < self.best_ask < Decimal("1"):
            raise ValueError("market reference requires a valid uncrossed best bid/ask")
        if not self.best_ask <= self.average_reference_price <= self.worst_reference_price:
            raise ValueError("average reference price must lie between best ask and worst price")
        if not self.average_reference_price <= self.all_in_reference_price < Decimal("1"):
            raise ValueError(
                "all-in reference price must be at least the average price and below one"
            )
        if self.worst_reference_price >= Decimal("1"):
            raise ValueError("worst reference price must be below one")
        if self.probability_edge <= 0 or self.expected_return <= 0:
            raise ValueError(
                "accepted market reference must retain positive edge and expected return"
            )

    def identity_mapping(self) -> dict[str, str]:
        # All stable market-reference economics are part of logical identity. The
        # quote_fingerprint stays audit-only because the historical quote object includes
        # its local evaluated_at processing timestamp.
        return {
            "kind": self.kind,
            "order_book_hash": self.order_book_hash,
            "observed_at_utc": self.observed_at_utc.isoformat(),
            "reference_notional": _decimal_text(self.reference_notional),
            "best_bid": _decimal_text(self.best_bid),
            "best_ask": _decimal_text(self.best_ask),
            "average_reference_price": _decimal_text(self.average_reference_price),
            "all_in_reference_price": _decimal_text(self.all_in_reference_price),
            "worst_reference_price": _decimal_text(self.worst_reference_price),
            "probability_edge": _decimal_text(self.probability_edge),
            "expected_return": _decimal_text(self.expected_return),
        }

    def to_mapping(self) -> dict[str, str]:
        return {
            **self.identity_mapping(),
            "quote_fingerprint": self.quote_fingerprint,
        }


@dataclass(frozen=True, slots=True)
class HermesSignal:
    signal_id: str
    producer_id: str
    strategy_id: str
    strategy_version: str
    policy_fingerprint: str
    generated_at_utc: datetime
    venue: str
    event_id: str
    market_id: str
    condition_id: str
    outcome: str
    token_id: str
    question: str
    city_slug: str
    city_name: str
    climate_region: str
    lead_days: int
    market_date: date
    market_timezone: str
    bucket_key: str
    bucket_label: str
    forecast_temperature_f: Decimal
    model_probability: Decimal
    classification: str
    market_reference: SignalMarketReference
    model_version: str
    artifact_sha256: str
    calibration_fingerprint: str
    weather_fingerprint: str
    forecast_source: str
    calibration_group_key: str
    fallback_level: str
    distribution_type: str
    calibration_sample_count: int
    training_cutoff: date
    contract: str = "hermes.signal"
    schema_version: str = "1"

    def __post_init__(self) -> None:
        for label, value in (
            ("signal_id", self.signal_id),
            ("producer_id", self.producer_id),
            ("strategy_id", self.strategy_id),
            ("strategy_version", self.strategy_version),
            ("policy_fingerprint", self.policy_fingerprint),
            ("venue", self.venue),
            ("event_id", self.event_id),
            ("market_id", self.market_id),
            ("condition_id", self.condition_id),
            ("outcome", self.outcome),
            ("token_id", self.token_id),
            ("question", self.question),
            ("city_slug", self.city_slug),
            ("city_name", self.city_name),
            ("climate_region", self.climate_region),
            ("market_timezone", self.market_timezone),
            ("bucket_key", self.bucket_key),
            ("bucket_label", self.bucket_label),
            ("classification", self.classification),
            ("model_version", self.model_version),
            ("artifact_sha256", self.artifact_sha256),
            ("calibration_fingerprint", self.calibration_fingerprint),
            ("weather_fingerprint", self.weather_fingerprint),
            ("forecast_source", self.forecast_source),
            ("calibration_group_key", self.calibration_group_key),
            ("fallback_level", self.fallback_level),
            ("distribution_type", self.distribution_type),
        ):
            object.__setattr__(self, label, _nonblank(value, label=label))
        if self.contract != "hermes.signal":
            raise ValueError("unsupported Hermes signal contract")
        if self.schema_version != "1":
            raise ValueError("unsupported Hermes signal schema_version")
        if self.classification != "accepted":
            raise ValueError("HermesSignal v1 only represents accepted producer signals")
        object.__setattr__(self, "generated_at_utc", _utc(self.generated_at_utc))
        if self.model_probability <= 0 or self.model_probability >= 1:
            raise ValueError("model_probability must be between zero and one")
        if self.calibration_sample_count <= 0:
            raise ValueError("calibration_sample_count must be positive")

        calibrated = CalibratedProbability(
            model_probability=self.model_probability,
            model_version=self.model_version,
            artifact_sha256=self.artifact_sha256,
            city_slug=self.city_slug,
            climate_region=self.climate_region,
            lead_days=self.lead_days,
            weather_fingerprint=self.weather_fingerprint,
            bucket_key=self.bucket_key,
            forecast_source=self.forecast_source,
            calibration_group_key=self.calibration_group_key,
            fallback_level=self.fallback_level,
            distribution_type=self.distribution_type,
            calibration_sample_count=self.calibration_sample_count,
            training_cutoff=self.training_cutoff,
        )
        if self.calibration_fingerprint != calibrated.calibration_fingerprint():
            raise ValueError("calibration_fingerprint does not match signal provenance")

        expected = make_signal_id(
            producer_id=self.producer_id,
            strategy_id=self.strategy_id,
            strategy_version=self.strategy_version,
            policy_fingerprint=self.policy_fingerprint,
            venue=self.venue,
            event_id=self.event_id,
            market_id=self.market_id,
            condition_id=self.condition_id,
            outcome=self.outcome,
            token_id=self.token_id,
            classification=self.classification,
            market_date=self.market_date,
            calibration_fingerprint=self.calibration_fingerprint,
            weather_fingerprint=self.weather_fingerprint,
            market_reference=self.market_reference,
        )
        if self.signal_id != expected:
            raise ValueError("signal_id does not match canonical signal identity")

    def to_mapping(self) -> dict[str, Any]:
        return {
            "contract": self.contract,
            "schema_version": self.schema_version,
            "signal_id": self.signal_id,
            "producer_id": self.producer_id,
            "strategy_id": self.strategy_id,
            "strategy_version": self.strategy_version,
            "policy_fingerprint": self.policy_fingerprint,
            "generated_at_utc": self.generated_at_utc.isoformat(),
            "venue": self.venue,
            "event_id": self.event_id,
            "market_id": self.market_id,
            "condition_id": self.condition_id,
            "outcome": self.outcome,
            "token_id": self.token_id,
            "question": self.question,
            "city_slug": self.city_slug,
            "city_name": self.city_name,
            "climate_region": self.climate_region,
            "lead_days": self.lead_days,
            "market_date": self.market_date.isoformat(),
            "market_timezone": self.market_timezone,
            "bucket_key": self.bucket_key,
            "bucket_label": self.bucket_label,
            "forecast_temperature_f": _decimal_text(self.forecast_temperature_f),
            "model_probability": _decimal_text(self.model_probability),
            "classification": self.classification,
            "market_reference": self.market_reference.to_mapping(),
            "model_version": self.model_version,
            "artifact_sha256": self.artifact_sha256,
            "calibration_fingerprint": self.calibration_fingerprint,
            "weather_fingerprint": self.weather_fingerprint,
            "forecast_source": self.forecast_source,
            "calibration_group_key": self.calibration_group_key,
            "fallback_level": self.fallback_level,
            "distribution_type": self.distribution_type,
            "calibration_sample_count": self.calibration_sample_count,
            "training_cutoff": self.training_cutoff.isoformat(),
        }


def make_signal_id(
    *,
    producer_id: str,
    strategy_id: str,
    strategy_version: str,
    policy_fingerprint: str,
    venue: str,
    event_id: str,
    market_id: str,
    condition_id: str,
    outcome: str,
    token_id: str,
    classification: str,
    market_date: date,
    calibration_fingerprint: str,
    weather_fingerprint: str,
    market_reference: SignalMarketReference,
) -> str:
    payload = {
        "contract": "hermes.signal",
        "schema_version": "1",
        "producer_id": producer_id,
        "strategy_id": strategy_id,
        "strategy_version": strategy_version,
        "policy_fingerprint": policy_fingerprint,
        "venue": venue,
        "event_id": event_id,
        "market_id": market_id,
        "condition_id": condition_id,
        "outcome": outcome,
        "token_id": token_id,
        "classification": classification,
        "market_date": market_date.isoformat(),
        "calibration_fingerprint": calibration_fingerprint,
        "weather_fingerprint": weather_fingerprint,
        "market_reference": market_reference.identity_mapping(),
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return f"hsig_{hashlib.sha256(encoded).hexdigest()}"
