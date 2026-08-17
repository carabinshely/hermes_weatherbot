"""Safe configuration assembly for internal PAPER strategy research."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import cast

from weatherbot.paper.runtime import PaperRuntimeConfig
from weatherbot.quoting import CostPolicy, DepthPolicy, FreshnessPolicy


@dataclass(frozen=True, slots=True)
class PaperResearchConfig:
    runtime: PaperRuntimeConfig
    freshness_policy: FreshnessPolicy
    cost_policy: CostPolicy
    scan_interval_seconds: int


def _mapping(value: object, *, label: str) -> Mapping[str, object]:
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be a JSON object")
    return cast(dict[str, object], value)


def _decimal(config: Mapping[str, object], key: str, default: str) -> Decimal:
    raw = config.get(key, default)
    if isinstance(raw, bool):
        raise ValueError(f"{key} must be numeric")
    try:
        value = Decimal(str(raw))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"{key} must be numeric") from exc
    if not value.is_finite():
        raise ValueError(f"{key} must be finite")
    return value


def _positive_seconds(config: Mapping[str, object], key: str, default: str) -> timedelta:
    value = _decimal(config, key, default)
    if value <= 0:
        raise ValueError(f"{key} must be positive")
    return timedelta(seconds=float(value))


def _positive_int(config: Mapping[str, object], key: str, default: int) -> int:
    raw = config.get(key, default)
    if isinstance(raw, bool) or not isinstance(raw, int) or raw <= 0:
        raise ValueError(f"{key} must be a positive integer")
    return raw


def _adaptive_ev_floor(
    repository_root: Path,
    *,
    configured_floor: Decimal,
    relative_path: Path = Path("data/learning/model.json"),
) -> Decimal:
    """Preserve historical PAPER EV adaptation without importing execution code."""
    path = repository_root / relative_path
    if not path.exists():
        return configured_floor
    raw: object = json.loads(path.read_text(encoding="utf-8"))
    model = _mapping(raw, label="PAPER learning model")
    value = model.get("ev_floor", configured_floor)
    if isinstance(value, bool):
        raise ValueError("PAPER learning-model ev_floor must be numeric")
    try:
        floor = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError("PAPER learning-model ev_floor must be numeric") from exc
    if not floor.is_finite() or floor < 0:
        raise ValueError("PAPER learning-model ev_floor must be finite and non-negative")
    return floor


def load_paper_research_config(
    repository_root: Path,
    *,
    relative_path: Path = Path("config.json"),
) -> PaperResearchConfig:
    raw: object = json.loads((repository_root / relative_path).read_text(encoding="utf-8"))
    config = _mapping(raw, label="PAPER configuration")
    configured_ev_floor = _decimal(config, "min_ev", "0.10")
    cost_policy = CostPolicy(
        platform_fee_rate=_decimal(config, "platform_fee_reserve_rate", "0.01"),
        transaction_cost=_decimal(config, "transaction_cost_reserve", "0.01"),
        safety_margin_rate=_decimal(config, "execution_safety_margin_rate", "0.02"),
        maximum_average_slippage=_decimal(config, "max_slippage", "0.03"),
        maximum_worst_slippage=_decimal(config, "max_worst_slippage", "0.05"),
        maximum_all_in_price=_decimal(config, "max_price", "0.45"),
        minimum_expected_return=_adaptive_ev_floor(
            repository_root,
            configured_floor=configured_ev_floor,
        ),
        depth_policy=DepthPolicy(str(config.get("depth_policy", "reject"))),
    )
    freshness_policy = FreshnessPolicy(
        maximum_forecast_age=_positive_seconds(config, "max_forecast_age_seconds", "21600"),
        maximum_event_age=_positive_seconds(config, "max_event_age_seconds", "120"),
        maximum_order_book_age=_positive_seconds(config, "max_order_book_age_seconds", "30"),
        maximum_balance_age=_positive_seconds(config, "max_balance_age_seconds", "30"),
    )
    return PaperResearchConfig(
        runtime=PaperRuntimeConfig.from_mapping(config, base_dir=repository_root),
        freshness_policy=freshness_policy,
        cost_policy=cost_policy,
        scan_interval_seconds=_positive_int(config, "scan_interval", 3600),
    )
