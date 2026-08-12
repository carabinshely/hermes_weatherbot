"""Auditable contracts for portfolio-level pre-trade risk controls."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from enum import StrEnum
from types import MappingProxyType
from typing import Mapping

from weatherbot.domain import Money, RiskDecisionStatus, RiskScope


def _positive_money(value: Money, *, label: str) -> None:
    if value.amount <= 0:
        raise ValueError(f"{label} must be positive")


class PortfolioRiskRejectionReason(StrEnum):
    INVALID_INPUT = "invalid_input"
    VALUATION_MISMATCH = "valuation_mismatch"
    STALE_VALUATION = "stale_valuation"
    MISSING_SCOPE = "missing_scope"
    DUPLICATE_EXPOSURE = "duplicate_exposure"
    MAX_OPEN_POSITIONS = "max_open_positions"
    TOTAL_EXPOSURE = "total_exposure"
    EVENT_EXPOSURE = "event_exposure"
    CITY_DATE_EXPOSURE = "city_date_exposure"
    CORRELATION_EXPOSURE = "correlation_exposure"
    DAILY_LOSS = "daily_loss"
    DRAWDOWN = "drawdown"


@dataclass(frozen=True, slots=True)
class PortfolioRiskPolicy:
    """Fixed portfolio limits shared by paper and future live execution."""

    maximum_total_exposure: Money
    maximum_event_exposure: Money
    maximum_city_date_exposure: Money
    maximum_correlation_group_exposure: Money
    maximum_open_positions: int
    maximum_daily_loss: Money
    maximum_drawdown: Money
    maximum_valuation_age: timedelta = timedelta(seconds=30)
    future_tolerance: timedelta = timedelta(seconds=5)
    loss_timezone: str = "UTC"

    def __post_init__(self) -> None:
        limits = (
            ("maximum_total_exposure", self.maximum_total_exposure),
            ("maximum_event_exposure", self.maximum_event_exposure),
            ("maximum_city_date_exposure", self.maximum_city_date_exposure),
            (
                "maximum_correlation_group_exposure",
                self.maximum_correlation_group_exposure,
            ),
            ("maximum_daily_loss", self.maximum_daily_loss),
            ("maximum_drawdown", self.maximum_drawdown),
        )
        currency = self.maximum_total_exposure.currency
        for label, limit in limits:
            _positive_money(limit, label=label)
            if limit.currency != currency:
                raise ValueError("portfolio risk policy mixes currencies")
        if isinstance(self.maximum_open_positions, bool) or self.maximum_open_positions <= 0:
            raise ValueError("maximum_open_positions must be a positive integer")
        if self.maximum_valuation_age <= timedelta(0):
            raise ValueError("maximum_valuation_age must be positive")
        if self.future_tolerance < timedelta(0):
            raise ValueError("future_tolerance must not be negative")
        if not self.loss_timezone.strip():
            raise ValueError("loss_timezone must not be blank")

    @property
    def currency(self) -> str:
        return self.maximum_total_exposure.currency


@dataclass(frozen=True, slots=True)
class CorrelationExposure:
    group: str
    before: Money
    after: Money

    def __post_init__(self) -> None:
        if not self.group.strip():
            raise ValueError("correlation group must not be blank")
        if self.before.currency != self.after.currency:
            raise ValueError("correlation exposure mixes currencies")
        if self.before.is_negative or self.after.is_negative:
            raise ValueError("correlation exposure must not be negative")


@dataclass(frozen=True, slots=True)
class PortfolioRiskDecision:
    """Complete portfolio permission decision for one proposed BUY reservation."""

    status: RiskDecisionStatus
    proposed_scope: RiskScope
    proposed_cash: Money
    total_exposure_before: Money
    total_exposure_after: Money
    event_exposure_before: Money
    event_exposure_after: Money
    city_date_exposure_before: Money
    city_date_exposure_after: Money
    correlation_exposures: tuple[CorrelationExposure, ...]
    open_positions_before: int
    open_positions_after: int
    realized_pnl_today: Money
    unrealized_pnl: Money
    daily_pnl: Money
    daily_loss: Money
    current_equity: Money
    high_water_mark: Money
    drawdown: Money
    rejection_reason: PortfolioRiskRejectionReason | None = None
    detail: str | None = None
    missing_scope_keys: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        currency = self.proposed_cash.currency
        amounts = (
            self.total_exposure_before,
            self.total_exposure_after,
            self.event_exposure_before,
            self.event_exposure_after,
            self.city_date_exposure_before,
            self.city_date_exposure_after,
            self.realized_pnl_today,
            self.unrealized_pnl,
            self.daily_pnl,
            self.daily_loss,
            self.current_equity,
            self.high_water_mark,
            self.drawdown,
        )
        if any(amount.currency != currency for amount in amounts):
            raise ValueError("portfolio risk decision mixes currencies")
        if self.proposed_cash.is_negative:
            raise ValueError("proposed cash must not be negative")
        for amount in (
            self.total_exposure_before,
            self.total_exposure_after,
            self.event_exposure_before,
            self.event_exposure_after,
            self.city_date_exposure_before,
            self.city_date_exposure_after,
            self.daily_loss,
            self.current_equity,
            self.high_water_mark,
            self.drawdown,
        ):
            if amount.is_negative:
                raise ValueError("nonnegative portfolio risk amount became negative")
        if self.open_positions_before < 0 or self.open_positions_after < 0:
            raise ValueError("open position counts must not be negative")
        if self.status is RiskDecisionStatus.APPROVED:
            if self.rejection_reason is not None:
                raise ValueError("approved portfolio risk decision cannot have rejection reason")
            if self.proposed_cash.is_zero:
                raise ValueError("approved portfolio risk decision requires positive cash")
        elif self.rejection_reason is None:
            raise ValueError("rejected portfolio risk decision requires rejection reason")

    @property
    def correlation_map(self) -> Mapping[str, CorrelationExposure]:
        return MappingProxyType({item.group: item for item in self.correlation_exposures})

    def metadata(self) -> dict[str, object]:
        return {
            "portfolio_risk_status": self.status.value,
            "portfolio_risk_rejection_reason": (
                None if self.rejection_reason is None else self.rejection_reason.value
            ),
            "portfolio_risk_detail": self.detail,
            "portfolio_risk_market_id": str(self.proposed_scope.market_id),
            "portfolio_risk_outcome_id": str(self.proposed_scope.outcome_id),
            "portfolio_risk_event_id": self.proposed_scope.event_id,
            "portfolio_risk_city_key": self.proposed_scope.city_key,
            "portfolio_risk_market_date": self.proposed_scope.market_date.isoformat(),
            "portfolio_risk_correlation_groups": list(self.proposed_scope.all_correlation_groups),
            "portfolio_risk_proposed_cash": format(self.proposed_cash.amount, "f"),
            "portfolio_risk_total_exposure_before": format(self.total_exposure_before.amount, "f"),
            "portfolio_risk_total_exposure_after": format(self.total_exposure_after.amount, "f"),
            "portfolio_risk_event_exposure_before": format(self.event_exposure_before.amount, "f"),
            "portfolio_risk_event_exposure_after": format(self.event_exposure_after.amount, "f"),
            "portfolio_risk_city_date_exposure_before": format(
                self.city_date_exposure_before.amount, "f"
            ),
            "portfolio_risk_city_date_exposure_after": format(
                self.city_date_exposure_after.amount, "f"
            ),
            "portfolio_risk_correlation_exposure": {
                item.group: {
                    "before": format(item.before.amount, "f"),
                    "after": format(item.after.amount, "f"),
                }
                for item in self.correlation_exposures
            },
            "portfolio_risk_open_positions_before": self.open_positions_before,
            "portfolio_risk_open_positions_after": self.open_positions_after,
            "portfolio_risk_realized_pnl_today": format(self.realized_pnl_today.amount, "f"),
            "portfolio_risk_unrealized_pnl": format(self.unrealized_pnl.amount, "f"),
            "portfolio_risk_daily_pnl": format(self.daily_pnl.amount, "f"),
            "portfolio_risk_daily_loss": format(self.daily_loss.amount, "f"),
            "portfolio_risk_current_equity": format(self.current_equity.amount, "f"),
            "portfolio_risk_high_water_mark": format(self.high_water_mark.amount, "f"),
            "portfolio_risk_drawdown": format(self.drawdown.amount, "f"),
            "portfolio_risk_missing_scope_keys": list(self.missing_scope_keys),
        }
