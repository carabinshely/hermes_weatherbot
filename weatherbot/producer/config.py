"""Versioned, execution-independent producer policy."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

from weatherbot.quoting import CostPolicy, DepthPolicy, FreshnessPolicy


@dataclass(frozen=True, slots=True)
class ProducerPolicy:
    schema_version: int
    strategy_id: str
    strategy_version: str
    scan_interval_seconds: int
    min_volume: Decimal
    min_hours: Decimal
    max_hours: Decimal
    market_reference_notional: Decimal
    minimum_expected_return: Decimal
    maximum_all_in_price: Decimal
    maximum_average_slippage: Decimal
    maximum_worst_slippage: Decimal
    maximum_forecast_age_seconds: int
    maximum_event_age_seconds: int
    maximum_order_book_age_seconds: int
    platform_fee_reserve_rate: Decimal
    transaction_cost_reserve: Decimal
    market_reference_safety_margin_rate: Decimal
    depth_policy: DepthPolicy
    signal_log_path: Path

    def __post_init__(self) -> None:
        if self.schema_version != 1:
            raise ValueError("unsupported producer policy schema_version")
        if not self.strategy_id.strip() or not self.strategy_version.strip():
            raise ValueError("strategy identity must not be blank")
        if self.scan_interval_seconds <= 0:
            raise ValueError("scan_interval_seconds must be positive")
        if self.min_volume < 0 or self.min_hours < 0 or self.max_hours <= self.min_hours:
            raise ValueError("producer market filters are invalid")
        if self.market_reference_notional <= 0:
            raise ValueError("market_reference_notional must be positive")
        if self.maximum_forecast_age_seconds <= 0:
            raise ValueError("maximum_forecast_age_seconds must be positive")
        if self.maximum_event_age_seconds <= 0 or self.maximum_order_book_age_seconds <= 0:
            raise ValueError("market freshness limits must be positive")

    @property
    def freshness_policy(self) -> FreshnessPolicy:
        return FreshnessPolicy(
            maximum_forecast_age=timedelta(seconds=self.maximum_forecast_age_seconds),
            maximum_event_age=timedelta(seconds=self.maximum_event_age_seconds),
            maximum_order_book_age=timedelta(seconds=self.maximum_order_book_age_seconds),
            # Public producer never supplies a balance. Keep this positive solely because
            # FreshnessPolicy is shared with execution-oriented historical code.
            maximum_balance_age=timedelta(seconds=1),
        )

    @property
    def cost_policy(self) -> CostPolicy:
        return CostPolicy(
            platform_fee_rate=self.platform_fee_reserve_rate,
            transaction_cost=self.transaction_cost_reserve,
            safety_margin_rate=self.market_reference_safety_margin_rate,
            maximum_average_slippage=self.maximum_average_slippage,
            maximum_worst_slippage=self.maximum_worst_slippage,
            maximum_all_in_price=self.maximum_all_in_price,
            minimum_expected_return=self.minimum_expected_return,
            depth_policy=self.depth_policy,
        )

    def identity_mapping(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "strategy_id": self.strategy_id,
            "strategy_version": self.strategy_version,
            "min_volume": format(self.min_volume, "f"),
            "min_hours": format(self.min_hours, "f"),
            "max_hours": format(self.max_hours, "f"),
            "market_reference_notional": format(self.market_reference_notional, "f"),
            "minimum_expected_return": format(self.minimum_expected_return, "f"),
            "maximum_all_in_price": format(self.maximum_all_in_price, "f"),
            "maximum_average_slippage": format(self.maximum_average_slippage, "f"),
            "maximum_worst_slippage": format(self.maximum_worst_slippage, "f"),
            "maximum_forecast_age_seconds": self.maximum_forecast_age_seconds,
            "maximum_event_age_seconds": self.maximum_event_age_seconds,
            "maximum_order_book_age_seconds": self.maximum_order_book_age_seconds,
            "platform_fee_reserve_rate": format(self.platform_fee_reserve_rate, "f"),
            "transaction_cost_reserve": format(self.transaction_cost_reserve, "f"),
            "market_reference_safety_margin_rate": format(
                self.market_reference_safety_margin_rate, "f"
            ),
            "depth_policy": self.depth_policy.value,
        }

    @property
    def fingerprint(self) -> str:
        encoded = json.dumps(
            self.identity_mapping(), sort_keys=True, separators=(",", ":")
        ).encode()
        return hashlib.sha256(encoded).hexdigest()


def _decimal(data: dict[str, object], key: str) -> Decimal:
    value = Decimal(str(data[key]))
    if not value.is_finite():
        raise ValueError(f"producer policy {key} must be finite")
    return value


def load_producer_policy(
    repository_root: Path,
    *,
    relative_path: Path = Path("config/producer.json"),
) -> ProducerPolicy:
    path = repository_root / relative_path
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("producer policy must be a JSON object")
    data: dict[str, object] = raw
    signal_path = Path(str(data["signal_log_path"]))
    if not signal_path.is_absolute():
        signal_path = repository_root / signal_path
    return ProducerPolicy(
        schema_version=int(data["schema_version"]),
        strategy_id=str(data["strategy_id"]),
        strategy_version=str(data["strategy_version"]),
        scan_interval_seconds=int(data["scan_interval_seconds"]),
        min_volume=_decimal(data, "min_volume"),
        min_hours=_decimal(data, "min_hours"),
        max_hours=_decimal(data, "max_hours"),
        market_reference_notional=_decimal(data, "market_reference_notional"),
        minimum_expected_return=_decimal(data, "minimum_expected_return"),
        maximum_all_in_price=_decimal(data, "maximum_all_in_price"),
        maximum_average_slippage=_decimal(data, "maximum_average_slippage"),
        maximum_worst_slippage=_decimal(data, "maximum_worst_slippage"),
        maximum_forecast_age_seconds=int(data["maximum_forecast_age_seconds"]),
        maximum_event_age_seconds=int(data["maximum_event_age_seconds"]),
        maximum_order_book_age_seconds=int(data["maximum_order_book_age_seconds"]),
        platform_fee_reserve_rate=_decimal(data, "platform_fee_reserve_rate"),
        transaction_cost_reserve=_decimal(data, "transaction_cost_reserve"),
        market_reference_safety_margin_rate=_decimal(
            data, "market_reference_safety_margin_rate"
        ),
        depth_policy=DepthPolicy(str(data["depth_policy"])),
        signal_log_path=signal_path,
    )
