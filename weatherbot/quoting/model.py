"""Backend-neutral freshness, cost, and executable-quote contracts."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from types import MappingProxyType

from weatherbot.markets import ExecutableQuote, OutcomeTokenId


class QuoteValidationError(ValueError):
    """Raised when quote configuration or point-in-time data is invalid."""


def as_decimal(value: Decimal | int | str | float, *, label: str) -> Decimal:
    if isinstance(value, bool):
        raise QuoteValidationError(f"{label} must be decimal")
    try:
        result = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise QuoteValidationError(f"{label} must be decimal") from exc
    if not result.is_finite():
        raise QuoteValidationError(f"{label} must be finite")
    return result


def as_utc(value: datetime, *, label: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise QuoteValidationError(f"{label} must be timezone-aware")
    return value.astimezone(UTC)


class DepthPolicy(StrEnum):
    REJECT = "reject"
    REDUCE = "reduce"


class QuoteRejectionReason(StrEnum):
    INVALID_INPUT = "invalid_input"
    SNAPSHOT_MISMATCH = "snapshot_mismatch"
    STALE_FORECAST = "stale_forecast"
    STALE_EVENT = "stale_event"
    STALE_ORDER_BOOK = "stale_order_book"
    STALE_BALANCE = "stale_balance"
    INSUFFICIENT_BALANCE = "insufficient_balance"
    INSUFFICIENT_DEPTH = "insufficient_depth"
    BELOW_MINIMUM_ORDER = "below_minimum_order"
    PRICE_EXCEEDED = "price_exceeded"
    SLIPPAGE_EXCEEDED = "slippage_exceeded"
    NON_POSITIVE_EDGE = "non_positive_edge"
    FEE_ERASED_EDGE = "fee_erased_edge"
    EXPECTED_RETURN_BELOW_FLOOR = "expected_return_below_floor"


@dataclass(frozen=True, slots=True)
class FreshnessPolicy:
    maximum_forecast_age: timedelta
    maximum_event_age: timedelta
    maximum_order_book_age: timedelta
    maximum_balance_age: timedelta
    future_tolerance: timedelta = timedelta(seconds=5)

    def __post_init__(self) -> None:
        for label, value in (
            ("maximum forecast age", self.maximum_forecast_age),
            ("maximum event age", self.maximum_event_age),
            ("maximum order-book age", self.maximum_order_book_age),
            ("maximum balance age", self.maximum_balance_age),
        ):
            if value <= timedelta(0):
                raise QuoteValidationError(f"{label} must be positive")
        if self.future_tolerance < timedelta(0):
            raise QuoteValidationError("future tolerance must not be negative")


@dataclass(frozen=True, slots=True)
class CostPolicy:
    platform_fee_rate: Decimal
    transaction_cost: Decimal
    safety_margin_rate: Decimal
    maximum_average_slippage: Decimal
    maximum_worst_slippage: Decimal
    maximum_all_in_price: Decimal
    minimum_expected_return: Decimal
    depth_policy: DepthPolicy = DepthPolicy.REJECT

    def __post_init__(self) -> None:
        fee_rate = as_decimal(self.platform_fee_rate, label="platform fee rate")
        transaction_cost = as_decimal(self.transaction_cost, label="transaction cost")
        safety_rate = as_decimal(self.safety_margin_rate, label="safety margin rate")
        average_slippage = as_decimal(
            self.maximum_average_slippage,
            label="maximum average slippage",
        )
        worst_slippage = as_decimal(
            self.maximum_worst_slippage,
            label="maximum worst slippage",
        )
        maximum_price = as_decimal(
            self.maximum_all_in_price,
            label="maximum all-in price",
        )
        minimum_return = as_decimal(
            self.minimum_expected_return,
            label="minimum expected return",
        )
        for label, value in (
            ("platform fee rate", fee_rate),
            ("transaction cost", transaction_cost),
            ("safety margin rate", safety_rate),
            ("maximum average slippage", average_slippage),
            ("maximum worst slippage", worst_slippage),
            ("minimum expected return", minimum_return),
        ):
            if value < 0:
                raise QuoteValidationError(f"{label} must not be negative")
        if maximum_price <= 0 or maximum_price >= 1:
            raise QuoteValidationError(
                "maximum all-in price must be greater than zero and less than one"
            )
        if average_slippage > worst_slippage:
            raise QuoteValidationError(
                "maximum average slippage cannot exceed maximum worst slippage"
            )
        object.__setattr__(self, "platform_fee_rate", fee_rate)
        object.__setattr__(self, "transaction_cost", transaction_cost)
        object.__setattr__(self, "safety_margin_rate", safety_rate)
        object.__setattr__(self, "maximum_average_slippage", average_slippage)
        object.__setattr__(self, "maximum_worst_slippage", worst_slippage)
        object.__setattr__(self, "maximum_all_in_price", maximum_price)
        object.__setattr__(self, "minimum_expected_return", minimum_return)


@dataclass(frozen=True, slots=True)
class MarketEventSnapshot:
    event_id: str
    retrieved_at_utc: datetime
    source_updated_at_utc: datetime | None = None

    def __post_init__(self) -> None:
        event_id = self.event_id.strip()
        if not event_id:
            raise QuoteValidationError("event id must not be blank")
        retrieved = as_utc(self.retrieved_at_utc, label="event retrieval time")
        source_updated = self.source_updated_at_utc
        if source_updated is not None:
            source_updated = as_utc(source_updated, label="event source update time")
            if source_updated > retrieved + timedelta(seconds=5):
                raise QuoteValidationError("event source update time is later than retrieval time")
        object.__setattr__(self, "event_id", event_id)
        object.__setattr__(self, "retrieved_at_utc", retrieved)
        object.__setattr__(self, "source_updated_at_utc", source_updated)


@dataclass(frozen=True, slots=True)
class BalanceSnapshot:
    available_cash: Decimal
    reserved_cash: Decimal
    observed_at_utc: datetime
    source: str

    def __post_init__(self) -> None:
        available = as_decimal(self.available_cash, label="available cash")
        reserved = as_decimal(self.reserved_cash, label="reserved cash")
        if available < 0 or reserved < 0:
            raise QuoteValidationError("balance amounts must not be negative")
        source = self.source.strip()
        if not source:
            raise QuoteValidationError("balance source must not be blank")
        object.__setattr__(self, "available_cash", available)
        object.__setattr__(self, "reserved_cash", reserved)
        object.__setattr__(
            self,
            "observed_at_utc",
            as_utc(self.observed_at_utc, label="balance observation time"),
        )
        object.__setattr__(self, "source", source)


@dataclass(frozen=True, slots=True)
class FreshnessCheck:
    label: str
    observed_at_utc: datetime
    age_seconds: float
    maximum_age_seconds: float

    def __post_init__(self) -> None:
        label = self.label.strip()
        if not label:
            raise QuoteValidationError("freshness label must not be blank")
        observed = as_utc(self.observed_at_utc, label=f"{label} observation time")
        if self.age_seconds < 0 or self.maximum_age_seconds <= 0:
            raise QuoteValidationError("freshness ages must be non-negative and bounded")
        object.__setattr__(self, "label", label)
        object.__setattr__(self, "observed_at_utc", observed)

    @property
    def fresh(self) -> bool:
        return self.age_seconds <= self.maximum_age_seconds


type QuoteMetadataValue = str | float | bool | None


def _empty_freshness() -> MappingProxyType[str, FreshnessCheck]:
    return MappingProxyType({})


@dataclass(frozen=True, slots=True)
class ValidatedExecutableQuote:
    quote: ExecutableQuote
    model_probability: Decimal
    requested_budget: Decimal
    book_budget_limit: Decimal
    executable_budget: Decimal
    platform_fee: Decimal
    transaction_cost: Decimal
    safety_margin: Decimal
    total_all_in_cost: Decimal
    all_in_average_price: Decimal
    gross_expected_payout: Decimal
    expected_profit: Decimal
    expected_return: Decimal
    probability_edge: Decimal
    average_slippage: Decimal
    worst_slippage: Decimal
    depth_reduced: bool
    evaluated_at_utc: datetime
    event_id: str
    freshness: MappingProxyType[str, FreshnessCheck] = field(default_factory=_empty_freshness)

    def __post_init__(self) -> None:
        probability = as_decimal(self.model_probability, label="model probability")
        if probability <= 0 or probability >= 1:
            raise QuoteValidationError("model probability must be between zero and one")
        requested = as_decimal(self.requested_budget, label="requested budget")
        book_limit = as_decimal(self.book_budget_limit, label="book budget limit")
        executable = as_decimal(self.executable_budget, label="executable budget")
        if requested <= 0:
            raise QuoteValidationError("requested budget must be positive")
        if book_limit <= 0 or book_limit > requested:
            raise QuoteValidationError("book budget limit must be positive and within request")
        if executable <= 0 or executable > book_limit:
            raise QuoteValidationError(
                "executable budget must be positive and within the book budget limit"
            )
        values = {
            "platform_fee": self.platform_fee,
            "transaction_cost": self.transaction_cost,
            "safety_margin": self.safety_margin,
            "total_all_in_cost": self.total_all_in_cost,
            "all_in_average_price": self.all_in_average_price,
            "gross_expected_payout": self.gross_expected_payout,
            "average_slippage": self.average_slippage,
            "worst_slippage": self.worst_slippage,
        }
        normalized: dict[str, Decimal] = {}
        for label, value in values.items():
            parsed = as_decimal(value, label=label.replace("_", " "))
            if parsed < 0:
                raise QuoteValidationError(f"{label.replace('_', ' ')} must not be negative")
            normalized[label] = parsed
        expected_profit = as_decimal(self.expected_profit, label="expected profit")
        expected_return = as_decimal(self.expected_return, label="expected return")
        edge = as_decimal(self.probability_edge, label="probability edge")
        evaluated = as_utc(self.evaluated_at_utc, label="quote evaluation time")
        event_id = self.event_id.strip()
        if not event_id:
            raise QuoteValidationError("quote event id must not be blank")
        if executable != self.quote.total_cost:
            raise QuoteValidationError("executable budget must equal order-book cost")
        if normalized["total_all_in_cost"] < self.quote.total_cost:
            raise QuoteValidationError("all-in cost cannot be below order-book cost")
        if normalized["total_all_in_cost"] > requested:
            raise QuoteValidationError("all-in cost exceeds the approved budget")
        if normalized["all_in_average_price"] != (
            normalized["total_all_in_cost"] / self.quote.shares
        ):
            raise QuoteValidationError("all-in average price does not reconcile")
        if normalized["gross_expected_payout"] != probability * self.quote.shares:
            raise QuoteValidationError("expected payout does not reconcile")
        if expected_profit != (
            normalized["gross_expected_payout"] - normalized["total_all_in_cost"]
        ):
            raise QuoteValidationError("expected profit does not reconcile")
        if expected_return != expected_profit / normalized["total_all_in_cost"]:
            raise QuoteValidationError("expected return does not reconcile")
        if edge != probability - normalized["all_in_average_price"]:
            raise QuoteValidationError("probability edge does not reconcile")
        if self.depth_reduced != (executable < book_limit):
            raise QuoteValidationError("depth reduction flag does not match the book budget")
        freshness = MappingProxyType(dict(self.freshness))
        if any(not check.fresh for check in freshness.values()):
            raise QuoteValidationError("validated quote contains a stale freshness check")
        object.__setattr__(self, "model_probability", probability)
        object.__setattr__(self, "requested_budget", requested)
        object.__setattr__(self, "book_budget_limit", book_limit)
        object.__setattr__(self, "executable_budget", executable)
        for label, value in normalized.items():
            object.__setattr__(self, label, value)
        object.__setattr__(self, "expected_profit", expected_profit)
        object.__setattr__(self, "expected_return", expected_return)
        object.__setattr__(self, "probability_edge", edge)
        object.__setattr__(self, "evaluated_at_utc", evaluated)
        object.__setattr__(self, "event_id", event_id)
        object.__setattr__(self, "freshness", freshness)

    @property
    def token_id(self) -> OutcomeTokenId:
        return self.quote.token_id

    @property
    def fingerprint(self) -> str:
        payload = {
            "token_id": str(self.token_id),
            "book_hash": self.quote.book_hash,
            "event_id": self.event_id,
            "shares": format(self.quote.shares, "f"),
            "total_all_in_cost": format(self.total_all_in_cost, "f"),
            "probability": format(self.model_probability, "f"),
            "evaluated_at": self.evaluated_at_utc.isoformat(),
        }
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        return hashlib.sha256(encoded).hexdigest()

    def metadata(self) -> dict[str, QuoteMetadataValue]:
        result: dict[str, QuoteMetadataValue] = {
            "quote_fingerprint": self.fingerprint,
            "quote_evaluated_at_utc": self.evaluated_at_utc.isoformat(),
            "quote_event_id": self.event_id,
            "quote_token_id": str(self.token_id),
            "quote_book_hash": self.quote.book_hash,
            "quote_requested_budget": format(self.requested_budget, "f"),
            "quote_book_budget_limit": format(self.book_budget_limit, "f"),
            "quote_executable_budget": format(self.executable_budget, "f"),
            "quote_depth_reduced": self.depth_reduced,
            "quote_shares": format(self.quote.shares, "f"),
            "quote_book_cost": format(self.quote.total_cost, "f"),
            "quote_platform_fee": format(self.platform_fee, "f"),
            "quote_transaction_cost": format(self.transaction_cost, "f"),
            "quote_safety_margin": format(self.safety_margin, "f"),
            "quote_total_all_in_cost": format(self.total_all_in_cost, "f"),
            "quote_average_price": format(self.quote.average_price, "f"),
            "quote_all_in_average_price": format(self.all_in_average_price, "f"),
            "quote_best_bid": format(self.quote.best_bid, "f"),
            "quote_best_ask": format(self.quote.best_ask, "f"),
            "quote_worst_price": format(self.quote.worst_price, "f"),
            "quote_average_slippage": format(self.average_slippage, "f"),
            "quote_worst_slippage": format(self.worst_slippage, "f"),
            "quote_model_probability": format(self.model_probability, "f"),
            "quote_probability_edge": format(self.probability_edge, "f"),
            "quote_gross_expected_payout": format(self.gross_expected_payout, "f"),
            "quote_expected_profit": format(self.expected_profit, "f"),
            "quote_expected_return": format(self.expected_return, "f"),
        }
        for label, check in self.freshness.items():
            prefix = f"{label}_freshness"
            result[f"{prefix}_observed_at_utc"] = check.observed_at_utc.isoformat()
            result[f"{prefix}_age_seconds"] = check.age_seconds
            result[f"{prefix}_maximum_age_seconds"] = check.maximum_age_seconds
            result[f"{prefix}_passed"] = check.fresh
        return result


@dataclass(frozen=True, slots=True)
class QuoteEvaluation:
    evaluated_at_utc: datetime
    quote: ValidatedExecutableQuote | None = None
    rejection_reason: QuoteRejectionReason | None = None
    detail: str | None = None
    freshness: MappingProxyType[str, FreshnessCheck] = field(default_factory=_empty_freshness)

    def __post_init__(self) -> None:
        evaluated = as_utc(self.evaluated_at_utc, label="quote evaluation time")
        accepted = self.quote is not None
        if accepted == (self.rejection_reason is not None):
            raise QuoteValidationError(
                "quote evaluation must contain either an accepted quote or a rejection"
            )
        if accepted and self.detail is not None:
            raise QuoteValidationError("accepted quote cannot contain rejection detail")
        if not accepted and (self.detail is None or not self.detail.strip()):
            raise QuoteValidationError("rejected quote requires a non-blank detail")
        object.__setattr__(self, "evaluated_at_utc", evaluated)
        object.__setattr__(self, "freshness", MappingProxyType(dict(self.freshness)))

    @property
    def accepted(self) -> bool:
        return self.quote is not None
