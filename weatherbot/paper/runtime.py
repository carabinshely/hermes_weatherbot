"""Runtime configuration and durable public-book reconstruction for PAPER mode."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path

from weatherbot.domain import Money, PositionKey, PositionStatus
from weatherbot.markets import ConditionId, OrderBookSnapshot, OutcomeTokenId
from weatherbot.paper.ledger import initialize_paper_store
from weatherbot.paper.service import PaperTradingService
from weatherbot.persistence import PortfolioRiskEventStore
from weatherbot.risk import PortfolioRiskPolicy, SizingPolicy


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


def _positive_int(config: Mapping[str, object], key: str, default: int) -> int:
    raw = config.get(key, default)
    if isinstance(raw, bool) or not isinstance(raw, int) or raw <= 0:
        raise ValueError(f"{key} must be a positive integer")
    return raw


def _path(config: Mapping[str, object], key: str, default: str, *, base_dir: Path) -> Path:
    raw = config.get(key, default)
    if not isinstance(raw, str) or not raw.strip():
        raise ValueError(f"{key} must be a non-blank path")
    path = Path(raw.strip())
    return path if path.is_absolute() else base_dir / path


@dataclass(frozen=True, slots=True)
class PaperRuntimeConfig:
    starting_cash: Money
    ledger_path: Path
    archive_directory: Path
    sizing_policy: SizingPolicy
    portfolio_policy: PortfolioRiskPolicy

    @classmethod
    def from_mapping(
        cls,
        config: Mapping[str, object],
        *,
        base_dir: Path,
        currency: str = "USDC",
    ) -> PaperRuntimeConfig:
        starting_cash = Money.of(_decimal(config, "paper_starting_cash", "100"), currency)
        if starting_cash.amount <= 0:
            raise ValueError("paper_starting_cash must be positive")
        maximum_cash = Money.of(_decimal(config, "max_bet", "2"), currency)
        sizing = SizingPolicy(
            fractional_kelly_multiplier=_decimal(config, "kelly_fraction", "0.25"),
            maximum_cash_per_trade=maximum_cash,
        )
        portfolio = PortfolioRiskPolicy(
            maximum_total_exposure=Money.of(
                _decimal(config, "paper_max_total_exposure", "20"), currency
            ),
            maximum_event_exposure=Money.of(
                _decimal(config, "paper_max_event_exposure", "6"), currency
            ),
            maximum_city_date_exposure=Money.of(
                _decimal(config, "paper_max_city_date_exposure", "6"), currency
            ),
            maximum_correlation_group_exposure=Money.of(
                _decimal(config, "paper_max_correlation_exposure", "8"), currency
            ),
            maximum_open_positions=_positive_int(config, "paper_max_open_positions", 10),
            maximum_daily_loss=Money.of(
                _decimal(config, "paper_max_daily_loss", "10"), currency
            ),
            maximum_drawdown=Money.of(
                _decimal(config, "paper_max_drawdown", "20"), currency
            ),
            maximum_valuation_age=timedelta(
                seconds=float(_decimal(config, "max_order_book_age_seconds", "30"))
            ),
            loss_timezone=str(config.get("paper_loss_timezone", "UTC")),
        )
        ledger_path = _path(
            config,
            "paper_ledger_path",
            "state/paper-ledger.sqlite3",
            base_dir=base_dir,
        )
        archive_directory = _path(
            config,
            "paper_archive_directory",
            "state/paper-archive",
            base_dir=base_dir,
        )
        return cls(
            starting_cash=starting_cash,
            ledger_path=ledger_path,
            archive_directory=archive_directory,
            sizing_policy=sizing,
            portfolio_policy=portfolio,
        )

    def open_store(self) -> PortfolioRiskEventStore:
        return initialize_paper_store(
            self.ledger_path,
            starting_cash=self.starting_cash,
        )

    def open_service(self) -> tuple[PortfolioRiskEventStore, PaperTradingService]:
        store = self.open_store()
        return store, PaperTradingService(store)


@dataclass(frozen=True, slots=True)
class PaperBookReference:
    position_key: PositionKey
    condition_id: ConditionId
    token_id: OutcomeTokenId


PaperBookFetcher = Callable[[ConditionId, OutcomeTokenId], OrderBookSnapshot]


def open_position_book_references(
    store: PortfolioRiskEventStore,
) -> tuple[PaperBookReference, ...]:
    """Recover exact public book identities for every durable open PAPER position."""
    state = store.load_state()
    claims_by_intent = {
        claim.intent_id: claim
        for claim in store.list_decision_claims()
        if claim.intent_id is not None
    }
    references: list[PaperBookReference] = []
    for key, position in sorted(
        state.positions.items(),
        key=lambda item: (str(item[0][0]), str(item[0][1])),
    ):
        if position.status is not PositionStatus.OPEN or position.quantity <= 0:
            continue
        relevant_orders = [
            order
            for order in state.orders.values()
            if order.intent.market_id == position.market_id
            and order.intent.outcome_id == position.outcome_id
            and order.filled_quantity > 0
        ]
        condition_ids: set[str] = set()
        for order in relevant_orders:
            claim = claims_by_intent.get(order.intent.intent_id)
            if claim is None:
                continue
            raw_condition = claim.metadata.get("condition_id")
            if isinstance(raw_condition, str) and raw_condition.strip():
                condition_ids.add(raw_condition.strip())
        if len(condition_ids) != 1:
            raise ValueError(
                f"open PAPER position {key[0]}/{key[1]} has no unique durable condition_id"
            )
        references.append(
            PaperBookReference(
                position_key=key,
                condition_id=ConditionId(next(iter(condition_ids))),
                token_id=OutcomeTokenId(str(position.outcome_id)),
            )
        )
    return tuple(references)


def load_open_position_books(
    store: PortfolioRiskEventStore,
    fetch_book: PaperBookFetcher,
) -> dict[PositionKey, OrderBookSnapshot]:
    return {
        reference.position_key: fetch_book(reference.condition_id, reference.token_id)
        for reference in open_position_book_references(store)
    }
