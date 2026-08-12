"""End-to-end fixed-policy paper trading over shared sizing, risk, and ledger contracts."""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from enum import Enum, StrEnum
from typing import cast

from weatherbot.domain import (
    EventId,
    LedgerState,
    MarketId,
    OrderCancelled,
    OrderIntent,
    OrderIntentCreated,
    OrderIntentId,
    OutcomeId,
    PositionKey,
    RiskDecisionStatus,
    RiskScope,
    Side,
    as_decimal,
    fingerprint,
    money_from_unit_price,
)
from weatherbot.forecasting import WeatherInputSnapshot
from weatherbot.markets import OrderBookSnapshot
from weatherbot.paper.execution import PaperExecutionAdapter, build_paper_execution_plan
from weatherbot.paper.model import PaperExecutionPlan, PaperExecutionStatus, PaperStatus
from weatherbot.paper.valuation import build_paper_valuation, paper_status
from weatherbot.persistence import PortfolioRiskEventStore, RecoveryAction, StartupRecovery
from weatherbot.quoting import BalanceSnapshot, CostPolicy, FreshnessPolicy, MarketEventSnapshot
from weatherbot.risk import (
    PortfolioRiskDecision,
    PortfolioRiskPolicy,
    RiskCapitalSnapshot,
    SizingDecision,
    SizingPolicy,
    size_executable_buy,
)


class PaperEntryStatus(StrEnum):
    SIZING_REJECTED = "sizing_rejected"
    RISK_REJECTED = "risk_rejected"
    EXECUTION_REJECTED = "execution_rejected"
    PARTIAL_FILL = "partial_fill"
    FILLED = "filled"
    IDEMPOTENT = "idempotent"


@dataclass(frozen=True, slots=True)
class PaperEntryRequest:
    strategy_id: str
    decision_id: str
    model_version: str
    model_probability: Decimal
    scope: RiskScope
    weather: WeatherInputSnapshot
    event: MarketEventSnapshot
    decision_order_book: OrderBookSnapshot
    execution_order_book: OrderBookSnapshot
    valuation_books: Mapping[PositionKey, OrderBookSnapshot]
    evaluated_at: datetime
    freshness_policy: FreshnessPolicy
    cost_policy: CostPolicy
    sizing_policy: SizingPolicy
    portfolio_policy: PortfolioRiskPolicy
    audit_metadata: Mapping[str, object] = field(
        default_factory=lambda: cast(Mapping[str, object], {})
    )

    def __post_init__(self) -> None:
        for label, value in (
            ("strategy_id", self.strategy_id),
            ("decision_id", self.decision_id),
            ("model_version", self.model_version),
        ):
            if not value.strip():
                raise ValueError(f"{label} must not be blank")
        if self.evaluated_at.tzinfo is None or self.evaluated_at.utcoffset() is None:
            raise ValueError("paper entry evaluation time must be timezone-aware")
        probability = Decimal(self.model_probability)
        if probability <= 0 or probability >= 1:
            raise ValueError("paper model probability must be between zero and one")
        bucket_key = self.audit_metadata.get("bucket_key")
        if not isinstance(bucket_key, str) or not bucket_key.strip():
            raise ValueError("paper entry requires a non-blank bucket_key for resolution")
        declared_source = self.audit_metadata.get("declared_resolution_source")
        if declared_source is not None and (
            not isinstance(declared_source, str) or not declared_source.strip()
        ):
            raise ValueError("declared_resolution_source must be a non-blank string when supplied")
        object.__setattr__(self, "model_probability", probability)


@dataclass(frozen=True, slots=True)
class PaperEntryResult:
    status: PaperEntryStatus
    sizing: SizingDecision | None
    risk_decision: PortfolioRiskDecision | None
    execution_plan: PaperExecutionPlan | None
    state: LedgerState
    appended_events: int


def _safe_metadata(value: object) -> object:
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        return repr(value)
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Enum):
        return str(value.value)
    if isinstance(value, Mapping):
        result: dict[str, object] = {}
        for raw_key, raw_value in cast(Mapping[object, object], value).items():
            key = str(raw_key).strip()
            if not key:
                raise ValueError("paper audit metadata contains a blank key")
            result[key] = _safe_metadata(raw_value)
        return result
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [_safe_metadata(item) for item in cast(Sequence[object], value)]
    raise TypeError(f"unsupported paper audit metadata type: {type(value).__name__}")


def _book_metadata(book: OrderBookSnapshot) -> dict[str, object]:
    return {
        "condition_id": str(book.condition_id),
        "token_id": str(book.token_id),
        "observed_at": book.observed_at.isoformat(),
        "book_hash": book.book_hash,
        "minimum_order_size": format(book.minimum_order_size, "f"),
        "tick_size": format(book.tick_size, "f"),
        "neg_risk": book.neg_risk,
        "bids": [
            {"price": format(level.price, "f"), "size": format(level.size, "f")}
            for level in book.bids
        ],
        "asks": [
            {"price": format(level.price, "f"), "size": format(level.size, "f")}
            for level in book.asks
        ],
    }


def _weather_metadata(weather: WeatherInputSnapshot) -> dict[str, object]:
    forecast = weather.forecast
    observation = weather.observation
    return {
        "fingerprint": fingerprint(weather),
        "assembled_at": weather.assembled_at_utc.isoformat(),
        "forecast": {
            "temperature_f": format(forecast.temperature_f, "f"),
            "market_date": forecast.market_date.isoformat(),
            "market_timezone": forecast.market_timezone,
            "source": forecast.source.value,
            "snapshot_issued_at": forecast.snapshot_issued_at_utc.isoformat(),
            "valid_from": forecast.valid_from_utc.isoformat(),
            "valid_until": forecast.valid_until_utc.isoformat(),
            "retrieved_at": forecast.retrieved_at_utc.isoformat(),
            "model_run_initialized_at": (
                None
                if forecast.model_run_initialized_at_utc is None
                else forecast.model_run_initialized_at_utc.isoformat()
            ),
        },
        "observation": (
            None
            if observation is None
            else {
                "temperature_f": format(observation.temperature_f, "f"),
                "station_id": observation.station_id,
                "market_timezone": observation.market_timezone,
                "source": observation.source.value,
                "issued_at": observation.issued_at_utc.isoformat(),
                "valid_at": observation.valid_at_utc.isoformat(),
                "retrieved_at": observation.retrieved_at_utc.isoformat(),
                "provider_received_at": (
                    None
                    if observation.provider_received_at_utc is None
                    else observation.provider_received_at_utc.isoformat()
                ),
            }
        ),
    }


def _market_metadata(event: MarketEventSnapshot) -> dict[str, object]:
    return {
        "fingerprint": fingerprint(event),
        "event_id": event.event_id,
        "retrieved_at": event.retrieved_at_utc.isoformat(),
        "source_updated_at": (
            None if event.source_updated_at_utc is None else event.source_updated_at_utc.isoformat()
        ),
    }


def _resolution_metadata(request: PaperEntryRequest) -> dict[str, object]:
    bucket_key = request.audit_metadata["bucket_key"]
    assert isinstance(bucket_key, str)
    declared_source = request.audit_metadata.get("declared_resolution_source")
    return {
        "condition_id": str(request.decision_order_book.condition_id),
        "market_date": request.scope.market_date.isoformat(),
        "market_timezone": request.weather.forecast.market_timezone,
        "bucket_key": bucket_key,
        "declared_resolution_source": declared_source,
    }


def _intent_event_id(intent: OrderIntent) -> EventId:
    material = f"paper-intent\n{intent.intent_id}".encode()
    return EventId(f"paper_intent_{hashlib.sha256(material).hexdigest()}")


def _cancel_recovery_event_id(intent_id: OrderIntentId) -> EventId:
    material = f"paper-recovery-cancel\n{intent_id}".encode()
    return EventId(f"paper_recovery_cancel_{hashlib.sha256(material).hexdigest()}")


def _intent_from_sizing(request: PaperEntryRequest, sizing: SizingDecision) -> OrderIntent:
    quote = sizing.quote
    if quote is None or sizing.status is not RiskDecisionStatus.APPROVED:
        raise ValueError("paper intent requires approved sizing")
    quantity = as_decimal(quote.quote.shares)
    limit_price = as_decimal(quote.quote.average_price)
    limit_notional = money_from_unit_price(
        limit_price,
        quantity,
        sizing.target_cash.currency,
    )
    fee_reserve = sizing.target_cash - limit_notional
    if fee_reserve.is_negative:
        raise ValueError("paper intent rounding would exceed the approved sizing cash")
    return OrderIntent.create(
        strategy_id=request.strategy_id,
        decision_id=request.decision_id,
        market_id=MarketId(str(request.scope.market_id)),
        outcome_id=OutcomeId(str(request.scope.outcome_id)),
        side=Side.BUY,
        quantity=quantity,
        limit_price=limit_price,
        fee_reserve=fee_reserve,
        created_at=request.evaluated_at,
    )


class PaperTradingService:
    """Compose #15 sizing, #16 portfolio risk, deterministic fills, and durable recovery."""

    def __init__(
        self,
        store: PortfolioRiskEventStore,
        *,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._store = store
        self._clock = clock

    def _load_adapter_payload(self, intent_id: OrderIntentId) -> Mapping[str, object] | None:
        metadata = self._store.get_adapter_metadata(intent_id)
        if metadata is None:
            return None
        if metadata.backend_name != "paper":
            raise ValueError(
                f"paper intent {intent_id} is assigned to backend {metadata.backend_name!r}"
            )
        return metadata.payload

    def _durable_decision_result(self, decision_id: str) -> PaperEntryResult | None:
        claim = next(
            (
                item
                for item in self._store.list_decision_claims()
                if item.decision_key == decision_id
            ),
            None,
        )
        if claim is None or claim.status == "claimed":
            return None
        if claim.status == "committed":
            self.recover()
        state = self._store.load_state()
        plan: PaperExecutionPlan | None = None
        if claim.intent_id is not None:
            payload = self._load_adapter_payload(claim.intent_id)
            if payload is not None:
                plan = PaperExecutionPlan.from_metadata(payload)
        return PaperEntryResult(
            status=PaperEntryStatus.IDEMPOTENT,
            sizing=None,
            risk_decision=None,
            execution_plan=plan,
            state=state,
            appended_events=0,
        )

    def submit_entry(self, request: PaperEntryRequest, *, owner_id: str) -> PaperEntryResult:
        durable = self._durable_decision_result(request.decision_id)
        if durable is not None:
            return durable

        evaluated_at = request.evaluated_at.astimezone(UTC)
        state = self._store.load_state()
        capital = RiskCapitalSnapshot.from_ledger(state)
        balance = BalanceSnapshot(
            available_cash=capital.available_cash.amount,
            reserved_cash=capital.reserved_cash.amount,
            observed_at_utc=evaluated_at,
            source="paper-durable-ledger",
        )
        sizing = size_executable_buy(
            capital=capital,
            probability=request.model_probability,
            weather=request.weather,
            event=request.event,
            order_book=request.decision_order_book,
            evaluated_at=evaluated_at,
            freshness_policy=request.freshness_policy,
            cost_policy=request.cost_policy,
            sizing_policy=request.sizing_policy,
            balance=balance,
        )
        if sizing.status is RiskDecisionStatus.REJECTED:
            return PaperEntryResult(
                status=PaperEntryStatus.SIZING_REJECTED,
                sizing=sizing,
                risk_decision=None,
                execution_plan=None,
                state=state,
                appended_events=0,
            )

        intent = _intent_from_sizing(request, sizing)
        plan = build_paper_execution_plan(
            intent,
            request.execution_order_book,
            policy=request.cost_policy,
            submitted_at=evaluated_at,
            maximum_book_age=request.freshness_policy.maximum_order_book_age,
        )
        valuation = build_paper_valuation(
            state,
            request.valuation_books,
            policy=request.cost_policy,
            observed_at=evaluated_at,
            maximum_book_age=request.freshness_policy.maximum_order_book_age,
        )
        quote = sizing.quote
        assert quote is not None
        quote_age = max(
            Decimal("0"),
            Decimal(str((evaluated_at - quote.quote.observed_at).total_seconds())),
        )
        metadata: dict[str, object] = {
            "paper_mode": True,
            "paper_audit_version": 1,
            "model_version": request.model_version,
            "model_probability": format(request.model_probability, "f"),
            **_resolution_metadata(request),
            "weather_snapshot": _weather_metadata(request.weather),
            "market_snapshot": _market_metadata(request.event),
            "decision_order_book": _book_metadata(request.decision_order_book),
            "execution_order_book": _book_metadata(request.execution_order_book),
            "quote_age_seconds": format(quote_age, "f"),
            "execution_plan_fingerprint": fingerprint(plan),
            "sizing": sizing.metadata(),
            "caller_audit": _safe_metadata(request.audit_metadata),
        }
        intent_event = OrderIntentCreated(
            event_id=_intent_event_id(intent),
            occurred_at=evaluated_at,
            intent=intent,
        )
        risk_commit = self._store.commit_risk_checked_order_intent(
            intent_event,
            scope=request.scope,
            valuation=valuation,
            policy=request.portfolio_policy,
            evaluated_at=evaluated_at,
            owner_id=owner_id,
            metadata=metadata,
            adapter_backend_name="paper",
            adapter_payload=plan.metadata(),
        )
        if not risk_commit.committed:
            return PaperEntryResult(
                status=PaperEntryStatus.RISK_REJECTED,
                sizing=sizing,
                risk_decision=risk_commit.decision,
                execution_plan=None,
                state=risk_commit.append_result.state,
                appended_events=len(risk_commit.append_result.appended_sequences),
            )

        adapter = PaperExecutionAdapter(
            policy=request.cost_policy,
            maximum_book_age=request.freshness_policy.maximum_order_book_age,
            plan_loader=self._load_adapter_payload,
            clock=self._clock,
        )
        emitted = adapter.submit(intent)
        execution_result = self._store.append_many(emitted)
        appended_count = len(risk_commit.append_result.appended_sequences) + len(
            execution_result.appended_sequences
        )
        if risk_commit.decision is None and not execution_result.appended_sequences:
            status = PaperEntryStatus.IDEMPOTENT
        elif plan.status is PaperExecutionStatus.REJECTED:
            status = PaperEntryStatus.EXECUTION_REJECTED
        elif plan.status is PaperExecutionStatus.PARTIAL_FILL:
            status = PaperEntryStatus.PARTIAL_FILL
        else:
            status = PaperEntryStatus.FILLED
        return PaperEntryResult(
            status=status,
            sizing=sizing,
            risk_decision=risk_commit.decision,
            execution_plan=plan,
            state=execution_result.state,
            appended_events=appended_count,
        )

    def recover(self) -> StartupRecovery:
        """Finish durable paper plans; fail-closed cancel any legacy CREATED intent without one."""
        report = self._store.recover()
        now = self._clock()
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("paper recovery clock must return a timezone-aware value")
        for pending in report.pending_orders:
            if pending.action is not RecoveryAction.RESUME_SUBMISSION:
                continue
            self._store.append(
                OrderCancelled(
                    event_id=_cancel_recovery_event_id(pending.intent_id),
                    occurred_at=now,
                    intent_id=pending.intent_id,
                    reason="paper recovery cancelled an intent with no durable execution plan",
                )
            )

        adapter = PaperExecutionAdapter(
            policy=CostPolicy(
                platform_fee_rate=Decimal("0"),
                transaction_cost=Decimal("0"),
                safety_margin_rate=Decimal("0"),
                maximum_average_slippage=Decimal("0"),
                maximum_worst_slippage=Decimal("0"),
                maximum_all_in_price=Decimal("0.999999"),
                minimum_expected_return=Decimal("0"),
            ),
            maximum_book_age=timedelta(days=36500),
            plan_loader=self._load_adapter_payload,
            clock=self._clock,
        )

        def resolve_adapter(name: str) -> PaperExecutionAdapter:
            if name != "paper":
                raise ValueError(f"paper recovery cannot resolve backend {name!r}")
            return adapter

        return self._store.reconcile_startup(resolve_adapter)

    def status(
        self,
        books: Mapping[PositionKey, OrderBookSnapshot],
        *,
        cost_policy: CostPolicy,
        observed_at: datetime,
        maximum_book_age: timedelta,
    ) -> PaperStatus:
        state = self._store.load_state()
        events = self._store.load_events()
        valuation = build_paper_valuation(
            state,
            books,
            policy=cost_policy,
            observed_at=observed_at,
            maximum_book_age=maximum_book_age,
        )
        return paper_status(state, events, valuation)
