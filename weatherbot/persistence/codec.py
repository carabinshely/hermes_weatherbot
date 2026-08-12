"""Versioned, deterministic JSON encoding for immutable ledger events."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import cast

from weatherbot.domain import (
    AccountOpened,
    EventId,
    FillId,
    FillReceived,
    LedgerEvent,
    MarketId,
    MarketResolution,
    MarketResolutionEvidence,
    MarketResolutionEvidenceRecorded,
    MarketResolved,
    Money,
    ObservationEvidenceStatus,
    OrderAcknowledged,
    OrderCancelled,
    OrderIntent,
    OrderIntentCreated,
    OrderIntentId,
    OrderOutcomeUnknown,
    OrderRejected,
    OrderSubmitted,
    OutcomeId,
    OutcomePayout,
    PortfolioValuation,
    PortfolioValuationRecorded,
    PositionSettled,
    PositionValuation,
    ResolutionEvidenceStatus,
    RiskScope,
    RiskScopeRegistered,
    Side,
    WeatherObservationEvidence,
    WeatherObservationRecorded,
)
from weatherbot.persistence.errors import CorruptLedgerError, SchemaVersionError

EVENT_SCHEMA_VERSION = 1

_EVENT_TYPE_BY_CLASS: dict[type[object], str] = {
    AccountOpened: "account_opened",
    OrderIntentCreated: "order_intent_created",
    OrderSubmitted: "order_submitted",
    OrderAcknowledged: "order_acknowledged",
    FillReceived: "fill_received",
    OrderRejected: "order_rejected",
    OrderCancelled: "order_cancelled",
    OrderOutcomeUnknown: "order_outcome_unknown",
    WeatherObservationRecorded: "weather_observation_recorded",
    MarketResolutionEvidenceRecorded: "market_resolution_evidence_recorded",
    MarketResolved: "market_resolved",
    PositionSettled: "position_settled",
    RiskScopeRegistered: "risk_scope_registered",
    PortfolioValuationRecorded: "portfolio_valuation_recorded",
}


@dataclass(frozen=True, slots=True)
class EncodedEvent:
    event: LedgerEvent
    event_type: str
    schema_version: int
    payload_json: str
    payload_hash: str
    event_id: str
    occurred_at: str
    intent_id: str | None
    decision_id: str | None
    market_id: str | None
    outcome_id: str | None


def _canonical_json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def chain_hash(
    previous_hash: str,
    *,
    schema_version: int,
    event_type: str,
    payload_hash: str,
) -> str:
    material = f"{previous_hash}\n{schema_version}\n{event_type}\n{payload_hash}"
    return sha256_text(material)


def _money_to_data(value: Money) -> dict[str, object]:
    return {"amount": format(value.amount, "f"), "currency": value.currency}


def _intent_to_data(value: OrderIntent) -> dict[str, object]:
    return {
        "intent_id": str(value.intent_id),
        "strategy_id": value.strategy_id,
        "decision_id": value.decision_id,
        "market_id": str(value.market_id),
        "outcome_id": str(value.outcome_id),
        "side": value.side.value,
        "quantity": format(value.quantity, "f"),
        "limit_price": format(value.limit_price, "f"),
        "fee_reserve": _money_to_data(value.fee_reserve),
        "created_at": value.created_at.isoformat(),
    }


def _risk_scope_to_data(value: RiskScope) -> dict[str, object]:
    return {
        "market_id": str(value.market_id),
        "outcome_id": str(value.outcome_id),
        "event_id": value.event_id,
        "city_key": value.city_key,
        "market_date": value.market_date.isoformat(),
        "correlation_groups": list(value.correlation_groups),
    }


def _position_valuation_to_data(value: PositionValuation) -> dict[str, object]:
    return {
        "market_id": str(value.market_id),
        "outcome_id": str(value.outcome_id),
        "quantity": format(value.quantity, "f"),
        "liquidation_value": _money_to_data(value.liquidation_value),
        "observed_at": value.observed_at.isoformat(),
    }


def _portfolio_valuation_to_data(value: PortfolioValuation) -> dict[str, object]:
    return {
        "positions": [_position_valuation_to_data(mark) for mark in value.positions],
        "equity": _money_to_data(value.equity),
        "assembled_at": value.assembled_at.isoformat(),
        "source": value.source,
    }


def _observation_to_data(value: WeatherObservationEvidence) -> dict[str, object]:
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
            value.source_timestamp.isoformat() if value.source_timestamp is not None else None
        ),
        "source_revision": value.source_revision,
        "status": value.status.value,
        "payload_hash": value.payload_hash,
        "supersedes_payload_hash": value.supersedes_payload_hash,
    }


def _evidence_to_data(value: MarketResolutionEvidence) -> dict[str, object]:
    return {
        "market_id": str(value.market_id),
        "condition_id": value.condition_id,
        "source_name": value.source_name,
        "source_url": value.source_url,
        "declared_resolution_source": value.declared_resolution_source,
        "retrieved_at": value.retrieved_at.isoformat(),
        "finalized_at": value.finalized_at.isoformat(),
        "market_date": value.market_date.isoformat(),
        "market_timezone": value.market_timezone,
        "status": value.status.value,
        "resolution_value": value.resolution_value,
        "payouts": [
            {
                "outcome_id": str(payout.outcome_id),
                "payout": format(payout.payout, "f"),
            }
            for payout in value.payouts
        ],
        "payload_hash": value.payload_hash,
    }


def _resolution_to_data(value: MarketResolution) -> dict[str, object]:
    return {
        "market_id": str(value.market_id),
        "payouts": [
            {
                "outcome_id": str(payout.outcome_id),
                "payout": format(payout.payout, "f"),
            }
            for payout in value.payouts
        ],
        "resolved_at": value.resolved_at.isoformat(),
    }


def _event_data(event: LedgerEvent) -> dict[str, object]:
    common: dict[str, object] = {
        "event_id": str(event.event_id),
        "occurred_at": event.occurred_at.isoformat(),
    }
    if isinstance(event, AccountOpened):
        return {**common, "initial_cash": _money_to_data(event.initial_cash)}
    if isinstance(event, OrderIntentCreated):
        return {**common, "intent": _intent_to_data(event.intent)}
    if isinstance(event, OrderSubmitted):
        return {
            **common,
            "intent_id": str(event.intent_id),
            "backend_order_id": event.backend_order_id,
        }
    if isinstance(event, OrderAcknowledged):
        return {**common, "intent_id": str(event.intent_id)}
    if isinstance(event, FillReceived):
        return {
            **common,
            "intent_id": str(event.intent_id),
            "fill_id": str(event.fill_id),
            "quantity": format(event.quantity, "f"),
            "price": format(event.price, "f"),
            "fee": _money_to_data(event.fee),
        }
    if isinstance(event, (OrderRejected, OrderCancelled, OrderOutcomeUnknown)):
        return {
            **common,
            "intent_id": str(event.intent_id),
            "reason": event.reason,
        }
    if isinstance(event, WeatherObservationRecorded):
        return {**common, "evidence": _observation_to_data(event.evidence)}
    if isinstance(event, MarketResolutionEvidenceRecorded):
        return {**common, "evidence": _evidence_to_data(event.evidence)}
    if isinstance(event, MarketResolved):
        return {**common, "resolution": _resolution_to_data(event.resolution)}
    if isinstance(event, RiskScopeRegistered):
        return {**common, "scope": _risk_scope_to_data(event.scope)}
    if isinstance(event, PortfolioValuationRecorded):
        return {**common, "valuation": _portfolio_valuation_to_data(event.valuation)}
    return {
        **common,
        "market_id": str(event.market_id),
        "outcome_id": str(event.outcome_id),
        "fee": _money_to_data(event.fee),
    }


def _index_fields(
    event: LedgerEvent,
) -> tuple[str | None, str | None, str | None, str | None]:
    if isinstance(event, OrderIntentCreated):
        intent = event.intent
        return (
            str(intent.intent_id),
            intent.decision_id,
            str(intent.market_id),
            str(intent.outcome_id),
        )
    if isinstance(
        event,
        (
            OrderSubmitted,
            OrderAcknowledged,
            FillReceived,
            OrderRejected,
            OrderCancelled,
            OrderOutcomeUnknown,
        ),
    ):
        return str(event.intent_id), None, None, None
    if isinstance(event, WeatherObservationRecorded):
        return None, None, str(event.evidence.market_id), None
    if isinstance(event, MarketResolutionEvidenceRecorded):
        return None, None, str(event.evidence.market_id), None
    if isinstance(event, MarketResolved):
        return None, None, str(event.resolution.market_id), None
    if isinstance(event, PositionSettled):
        return None, None, str(event.market_id), str(event.outcome_id)
    if isinstance(event, RiskScopeRegistered):
        return None, None, str(event.scope.market_id), str(event.scope.outcome_id)
    return None, None, None, None


def encode_event(event: LedgerEvent) -> EncodedEvent:
    try:
        event_type = _EVENT_TYPE_BY_CLASS[type(event)]
    except KeyError as exc:
        raise TypeError(f"unsupported ledger event type: {type(event).__name__}") from exc
    envelope: dict[str, object] = {
        "event_schema_version": EVENT_SCHEMA_VERSION,
        "event_type": event_type,
        "data": _event_data(event),
    }
    payload_json = _canonical_json(envelope)
    intent_id, decision_id, market_id, outcome_id = _index_fields(event)
    return EncodedEvent(
        event=event,
        event_type=event_type,
        schema_version=EVENT_SCHEMA_VERSION,
        payload_json=payload_json,
        payload_hash=sha256_text(payload_json),
        event_id=str(event.event_id),
        occurred_at=event.occurred_at.isoformat(),
        intent_id=intent_id,
        decision_id=decision_id,
        market_id=market_id,
        outcome_id=outcome_id,
    )


def _object_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise CorruptLedgerError(f"JSON object contains duplicate key {key!r}")
        result[key] = value
    return result


def _load_json_object(value: str, *, label: str) -> dict[str, object]:
    try:
        decoded = cast(
            object,
            json.loads(value, object_pairs_hook=_object_pairs),
        )
    except (json.JSONDecodeError, TypeError, ValueError) as exc:
        raise CorruptLedgerError(f"{label} is not valid JSON: {exc}") from exc
    if not isinstance(decoded, dict):
        raise CorruptLedgerError(f"{label} must contain a JSON object")
    return cast(dict[str, object], decoded)


def _expect_keys(
    value: Mapping[str, object],
    *,
    required: set[str],
    label: str,
) -> None:
    actual = set(value)
    if actual != required:
        missing = sorted(required - actual)
        unexpected = sorted(actual - required)
        raise CorruptLedgerError(
            f"{label} keys differ from schema; missing={missing}, unexpected={unexpected}"
        )


def _mapping(value: object, *, label: str) -> dict[str, object]:
    if not isinstance(value, dict):
        raise CorruptLedgerError(f"{label} must be an object")
    return cast(dict[str, object], value)


def _sequence(value: object, *, label: str) -> list[object]:
    if not isinstance(value, list):
        raise CorruptLedgerError(f"{label} must be an array")
    return cast(list[object], value)


def _text(value: object, *, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise CorruptLedgerError(f"{label} must be a non-blank string")
    return value


def _optional_text(value: object, *, label: str) -> str | None:
    if value is None:
        return None
    return _text(value, label=label)


def _integer(value: object, *, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise CorruptLedgerError(f"{label} must be an integer")
    return value


def _decimal(value: object, *, label: str) -> Decimal:
    text = _text(value, label=label)
    try:
        result = Decimal(text)
    except (InvalidOperation, ValueError) as exc:
        raise CorruptLedgerError(f"{label} is not a decimal string") from exc
    if not result.is_finite():
        raise CorruptLedgerError(f"{label} must be a finite decimal string")
    return result


def _date(value: object, *, label: str) -> date:
    text = _text(value, label=label)
    try:
        return date.fromisoformat(text)
    except ValueError as exc:
        raise CorruptLedgerError(f"{label} is not an ISO-8601 date") from exc


def _datetime(value: object, *, label: str) -> datetime:
    text = _text(value, label=label)
    try:
        return datetime.fromisoformat(text)
    except ValueError as exc:
        raise CorruptLedgerError(f"{label} is not an ISO-8601 timestamp") from exc


def _money(value: object, *, label: str) -> Money:
    data = _mapping(value, label=label)
    _expect_keys(data, required={"amount", "currency"}, label=label)
    return Money(
        amount=_decimal(data["amount"], label=f"{label}.amount"),
        currency=_text(data["currency"], label=f"{label}.currency"),
    )


def _intent(value: object) -> OrderIntent:
    data = _mapping(value, label="intent")
    _expect_keys(
        data,
        required={
            "intent_id",
            "strategy_id",
            "decision_id",
            "market_id",
            "outcome_id",
            "side",
            "quantity",
            "limit_price",
            "fee_reserve",
            "created_at",
        },
        label="intent",
    )
    side_text = _text(data["side"], label="intent.side")
    try:
        side = Side(side_text)
    except ValueError as exc:
        raise CorruptLedgerError(f"intent.side is unsupported: {side_text!r}") from exc
    return OrderIntent(
        intent_id=OrderIntentId(_text(data["intent_id"], label="intent.intent_id")),
        strategy_id=_text(data["strategy_id"], label="intent.strategy_id"),
        decision_id=_text(data["decision_id"], label="intent.decision_id"),
        market_id=MarketId(_text(data["market_id"], label="intent.market_id")),
        outcome_id=OutcomeId(_text(data["outcome_id"], label="intent.outcome_id")),
        side=side,
        quantity=_decimal(data["quantity"], label="intent.quantity"),
        limit_price=_decimal(data["limit_price"], label="intent.limit_price"),
        fee_reserve=_money(data["fee_reserve"], label="intent.fee_reserve"),
        created_at=_datetime(data["created_at"], label="intent.created_at"),
    )


def _risk_scope(value: object) -> RiskScope:
    data = _mapping(value, label="risk_scope")
    _expect_keys(
        data,
        required={
            "market_id",
            "outcome_id",
            "event_id",
            "city_key",
            "market_date",
            "correlation_groups",
        },
        label="risk_scope",
    )
    groups = tuple(
        _text(group, label=f"risk_scope.correlation_groups[{index}]")
        for index, group in enumerate(
            _sequence(data["correlation_groups"], label="risk_scope.correlation_groups")
        )
    )
    return RiskScope(
        market_id=MarketId(_text(data["market_id"], label="risk_scope.market_id")),
        outcome_id=OutcomeId(_text(data["outcome_id"], label="risk_scope.outcome_id")),
        event_id=_text(data["event_id"], label="risk_scope.event_id"),
        city_key=_text(data["city_key"], label="risk_scope.city_key"),
        market_date=_date(data["market_date"], label="risk_scope.market_date"),
        correlation_groups=groups,
    )


def _portfolio_valuation(value: object) -> PortfolioValuation:
    data = _mapping(value, label="portfolio_valuation")
    _expect_keys(
        data,
        required={"positions", "equity", "assembled_at", "source"},
        label="portfolio_valuation",
    )
    marks: list[PositionValuation] = []
    for index, raw_mark in enumerate(
        _sequence(data["positions"], label="portfolio_valuation.positions")
    ):
        mark = _mapping(raw_mark, label=f"portfolio_valuation.positions[{index}]")
        _expect_keys(
            mark,
            required={
                "market_id",
                "outcome_id",
                "quantity",
                "liquidation_value",
                "observed_at",
            },
            label=f"portfolio_valuation.positions[{index}]",
        )
        marks.append(
            PositionValuation(
                market_id=MarketId(
                    _text(
                        mark["market_id"],
                        label=f"portfolio_valuation.positions[{index}].market_id",
                    )
                ),
                outcome_id=OutcomeId(
                    _text(
                        mark["outcome_id"],
                        label=f"portfolio_valuation.positions[{index}].outcome_id",
                    )
                ),
                quantity=_decimal(
                    mark["quantity"],
                    label=f"portfolio_valuation.positions[{index}].quantity",
                ),
                liquidation_value=_money(
                    mark["liquidation_value"],
                    label=f"portfolio_valuation.positions[{index}].liquidation_value",
                ),
                observed_at=_datetime(
                    mark["observed_at"],
                    label=f"portfolio_valuation.positions[{index}].observed_at",
                ),
            )
        )
    return PortfolioValuation(
        positions=tuple(marks),
        equity=_money(data["equity"], label="portfolio_valuation.equity"),
        assembled_at=_datetime(data["assembled_at"], label="portfolio_valuation.assembled_at"),
        source=_text(data["source"], label="portfolio_valuation.source"),
    )


def _observation(value: object) -> WeatherObservationEvidence:
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
        market_id=MarketId(_text(data["market_id"], label="weather_observation.market_id")),
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
    data = _mapping(value, label="evidence")
    _expect_keys(
        data,
        required={
            "market_id",
            "condition_id",
            "source_name",
            "source_url",
            "declared_resolution_source",
            "retrieved_at",
            "finalized_at",
            "market_date",
            "market_timezone",
            "status",
            "resolution_value",
            "payouts",
            "payload_hash",
        },
        label="evidence",
    )
    payouts: list[OutcomePayout] = []
    for index, raw_payout in enumerate(_sequence(data["payouts"], label="evidence.payouts")):
        payout = _mapping(raw_payout, label=f"evidence.payouts[{index}]")
        _expect_keys(
            payout,
            required={"outcome_id", "payout"},
            label=f"evidence.payouts[{index}]",
        )
        payouts.append(
            OutcomePayout(
                outcome_id=OutcomeId(
                    _text(
                        payout["outcome_id"],
                        label=f"evidence.payouts[{index}].outcome_id",
                    )
                ),
                payout=_decimal(
                    payout["payout"],
                    label=f"evidence.payouts[{index}].payout",
                ),
            )
        )
    status_text = _text(data["status"], label="evidence.status")
    try:
        status = ResolutionEvidenceStatus(status_text)
    except ValueError as exc:
        raise CorruptLedgerError(f"evidence.status is unsupported: {status_text!r}") from exc
    return MarketResolutionEvidence(
        market_id=MarketId(_text(data["market_id"], label="evidence.market_id")),
        condition_id=_text(data["condition_id"], label="evidence.condition_id"),
        source_name=_text(data["source_name"], label="evidence.source_name"),
        source_url=_text(data["source_url"], label="evidence.source_url"),
        declared_resolution_source=_text(
            data["declared_resolution_source"],
            label="evidence.declared_resolution_source",
        ),
        retrieved_at=_datetime(data["retrieved_at"], label="evidence.retrieved_at"),
        finalized_at=_datetime(data["finalized_at"], label="evidence.finalized_at"),
        market_date=_date(data["market_date"], label="evidence.market_date"),
        market_timezone=_text(
            data["market_timezone"],
            label="evidence.market_timezone",
        ),
        status=status,
        resolution_value=_text(
            data["resolution_value"],
            label="evidence.resolution_value",
        ),
        payouts=tuple(payouts),
        payload_hash=_text(data["payload_hash"], label="evidence.payload_hash"),
    )


def _resolution(value: object) -> MarketResolution:
    data = _mapping(value, label="resolution")
    _expect_keys(
        data,
        required={"market_id", "payouts", "resolved_at"},
        label="resolution",
    )
    payouts: list[OutcomePayout] = []
    for index, raw_payout in enumerate(_sequence(data["payouts"], label="resolution.payouts")):
        payout = _mapping(raw_payout, label=f"resolution.payouts[{index}]")
        _expect_keys(
            payout,
            required={"outcome_id", "payout"},
            label=f"resolution.payouts[{index}]",
        )
        payouts.append(
            OutcomePayout(
                outcome_id=OutcomeId(
                    _text(
                        payout["outcome_id"],
                        label=f"resolution.payouts[{index}].outcome_id",
                    )
                ),
                payout=_decimal(
                    payout["payout"],
                    label=f"resolution.payouts[{index}].payout",
                ),
            )
        )
    return MarketResolution(
        market_id=MarketId(_text(data["market_id"], label="resolution.market_id")),
        payouts=tuple(payouts),
        resolved_at=_datetime(data["resolved_at"], label="resolution.resolved_at"),
    )


def _common(data: Mapping[str, object], *, required: set[str]) -> tuple[EventId, datetime]:
    _expect_keys(data, required=required | {"event_id", "occurred_at"}, label="event.data")
    return (
        EventId(_text(data["event_id"], label="event.data.event_id")),
        _datetime(data["occurred_at"], label="event.data.occurred_at"),
    )


def decode_event(payload_json: str) -> LedgerEvent:
    envelope = _load_json_object(payload_json, label="event payload")
    _expect_keys(
        envelope,
        required={"event_schema_version", "event_type", "data"},
        label="event payload",
    )
    schema_version = _integer(
        envelope["event_schema_version"],
        label="event_schema_version",
    )
    if schema_version != EVENT_SCHEMA_VERSION:
        raise SchemaVersionError(
            f"event schema version {schema_version} is unsupported; expected {EVENT_SCHEMA_VERSION}"
        )
    event_type = _text(envelope["event_type"], label="event_type")
    data = _mapping(envelope["data"], label="event.data")

    if event_type == "account_opened":
        event_id, occurred_at = _common(data, required={"initial_cash"})
        return AccountOpened(
            event_id=event_id,
            occurred_at=occurred_at,
            initial_cash=_money(data["initial_cash"], label="initial_cash"),
        )
    if event_type == "order_intent_created":
        event_id, occurred_at = _common(data, required={"intent"})
        return OrderIntentCreated(
            event_id=event_id,
            occurred_at=occurred_at,
            intent=_intent(data["intent"]),
        )
    if event_type == "order_submitted":
        event_id, occurred_at = _common(
            data,
            required={"intent_id", "backend_order_id"},
        )
        return OrderSubmitted(
            event_id=event_id,
            occurred_at=occurred_at,
            intent_id=OrderIntentId(_text(data["intent_id"], label="intent_id")),
            backend_order_id=_text(data["backend_order_id"], label="backend_order_id"),
        )
    if event_type == "order_acknowledged":
        event_id, occurred_at = _common(data, required={"intent_id"})
        return OrderAcknowledged(
            event_id=event_id,
            occurred_at=occurred_at,
            intent_id=OrderIntentId(_text(data["intent_id"], label="intent_id")),
        )
    if event_type == "fill_received":
        event_id, occurred_at = _common(
            data,
            required={"intent_id", "fill_id", "quantity", "price", "fee"},
        )
        return FillReceived(
            event_id=event_id,
            occurred_at=occurred_at,
            intent_id=OrderIntentId(_text(data["intent_id"], label="intent_id")),
            fill_id=FillId(_text(data["fill_id"], label="fill_id")),
            quantity=_decimal(data["quantity"], label="quantity"),
            price=_decimal(data["price"], label="price"),
            fee=_money(data["fee"], label="fee"),
        )
    if event_type in {
        "order_rejected",
        "order_cancelled",
        "order_outcome_unknown",
    }:
        event_id, occurred_at = _common(data, required={"intent_id", "reason"})
        intent_id = OrderIntentId(_text(data["intent_id"], label="intent_id"))
        reason = _text(data["reason"], label="reason")
        if event_type == "order_rejected":
            return OrderRejected(
                event_id=event_id,
                occurred_at=occurred_at,
                intent_id=intent_id,
                reason=reason,
            )
        if event_type == "order_cancelled":
            return OrderCancelled(
                event_id=event_id,
                occurred_at=occurred_at,
                intent_id=intent_id,
                reason=reason,
            )
        return OrderOutcomeUnknown(
            event_id=event_id,
            occurred_at=occurred_at,
            intent_id=intent_id,
            reason=reason,
        )
    if event_type == "weather_observation_recorded":
        event_id, occurred_at = _common(data, required={"evidence"})
        return WeatherObservationRecorded(
            event_id=event_id,
            occurred_at=occurred_at,
            evidence=_observation(data["evidence"]),
        )
    if event_type == "market_resolution_evidence_recorded":
        event_id, occurred_at = _common(data, required={"evidence"})
        return MarketResolutionEvidenceRecorded(
            event_id=event_id,
            occurred_at=occurred_at,
            evidence=_evidence(data["evidence"]),
        )
    if event_type == "market_resolved":
        event_id, occurred_at = _common(data, required={"resolution"})
        return MarketResolved(
            event_id=event_id,
            occurred_at=occurred_at,
            resolution=_resolution(data["resolution"]),
        )
    if event_type == "position_settled":
        event_id, occurred_at = _common(
            data,
            required={"market_id", "outcome_id", "fee"},
        )
        return PositionSettled(
            event_id=event_id,
            occurred_at=occurred_at,
            market_id=MarketId(_text(data["market_id"], label="market_id")),
            outcome_id=OutcomeId(_text(data["outcome_id"], label="outcome_id")),
            fee=_money(data["fee"], label="fee"),
        )
    if event_type == "risk_scope_registered":
        event_id, occurred_at = _common(data, required={"scope"})
        return RiskScopeRegistered(
            event_id=event_id,
            occurred_at=occurred_at,
            scope=_risk_scope(data["scope"]),
        )
    if event_type == "portfolio_valuation_recorded":
        event_id, occurred_at = _common(data, required={"valuation"})
        return PortfolioValuationRecorded(
            event_id=event_id,
            occurred_at=occurred_at,
            valuation=_portfolio_valuation(data["valuation"]),
        )
    raise SchemaVersionError(f"unsupported event type {event_type!r}")


def _metadata_value(value: object, *, path: str) -> object:
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        raise TypeError(f"{path} must not contain binary floating-point values")
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, Mapping):
        result: dict[str, object] = {}
        for raw_key, raw_value in cast(Mapping[object, object], value).items():
            if not isinstance(raw_key, str) or not raw_key:
                raise TypeError(f"{path} contains a non-string or blank key")
            result[raw_key] = _metadata_value(raw_value, path=f"{path}.{raw_key}")
        return result
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [
            _metadata_value(item, path=f"{path}[{index}]")
            for index, item in enumerate(cast(Sequence[object], value))
        ]
    raise TypeError(f"{path} contains unsupported value type {type(value).__name__}")


def encode_metadata(value: Mapping[str, object] | None) -> tuple[str, str]:
    normalized = _metadata_value(value or {}, path="metadata")
    payload_json = _canonical_json(normalized)
    return payload_json, sha256_text(payload_json)


def decode_metadata(payload_json: str, payload_hash: str) -> dict[str, object]:
    actual_hash = sha256_text(payload_json)
    if actual_hash != payload_hash:
        raise CorruptLedgerError(
            f"metadata hash mismatch: expected {payload_hash}, calculated {actual_hash}"
        )
    return _load_json_object(payload_json, label="metadata payload")
