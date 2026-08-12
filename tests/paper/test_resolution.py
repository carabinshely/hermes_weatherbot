from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from tests.paper.helpers import MARKET, OTHER_OUTCOME, OUTCOME, entry_request, scope
from tests.quoting.helpers import CONDITION, NOW
from weatherbot.domain import (
    MarketResolution,
    MarketResolutionEvidence,
    Money,
    OutcomePayout,
    PositionStatus,
    ResolutionEvidenceStatus,
    money_from_unit_price,
)
from weatherbot.paper import PaperEntryStatus, PaperTradingService, initialize_paper_store
from weatherbot.resolution import (
    ResolutionContext,
    ResolutionPollResult,
    ResolutionPollStatus,
    ResolutionWorker,
    StoredDecisionContextProvider,
)


@dataclass(frozen=True, slots=True)
class StaticPaperResolutionSource:
    yes: Decimal
    no: Decimal

    def poll(
        self,
        context: ResolutionContext,
        *,
        checked_at: datetime | None = None,
    ) -> ResolutionPollResult:
        checked = checked_at or NOW + timedelta(days=1)
        payouts = (
            OutcomePayout(outcome_id=OUTCOME, payout=self.yes),
            OutcomePayout(outcome_id=OTHER_OUTCOME, payout=self.no),
        )
        is_void = self.yes == self.no == Decimal("0.5")
        status = ResolutionPollStatus.VOID if is_void else ResolutionPollStatus.FINAL
        evidence_status = (
            ResolutionEvidenceStatus.VOID if is_void else ResolutionEvidenceStatus.VERIFIED
        )
        payload_hash = hashlib.sha256(f"paper-resolution:{self.yes}:{self.no}".encode()).hexdigest()
        evidence = MarketResolutionEvidence(
            market_id=MARKET,
            condition_id=str(CONDITION),
            source_name="paper-resolution-fixture",
            source_url="https://example.com/final-resolution",
            declared_resolution_source="https://example.com/resolution-rules",
            retrieved_at=checked,
            finalized_at=checked - timedelta(seconds=1),
            market_date=scope().market_date,
            market_timezone="America/New_York",
            status=evidence_status,
            resolution_value=f"{self.yes}/{self.no}",
            payouts=payouts,
            payload_hash=payload_hash,
        )
        resolution = MarketResolution(
            market_id=MARKET,
            payouts=payouts,
            resolved_at=checked - timedelta(seconds=1),
        )
        return ResolutionPollResult(
            market_id=context.market_id,
            status=status,
            checked_at=checked,
            reason="deterministic paper resolution fixture",
            evidence=evidence,
            resolution=resolution,
        )


@pytest.mark.parametrize(
    ("yes", "no", "expected_status"),
    (
        ("1", "0", ResolutionPollStatus.FINAL),
        ("0", "1", ResolutionPollStatus.FINAL),
        ("0.5", "0.5", ResolutionPollStatus.VOID),
    ),
)
def test_paper_position_win_loss_void_settles_exactly_once_across_restart(
    tmp_path: Path,
    yes: str,
    no: str,
    expected_status: ResolutionPollStatus,
) -> None:
    database = tmp_path / f"paper-resolution-{yes}-{no}.sqlite3"
    with initialize_paper_store(database, starting_cash=Money.of("100"), opened_at=NOW) as store:
        entry = PaperTradingService(store, clock=lambda: NOW).submit_entry(
            entry_request(decision_id=f"resolution-{yes}-{no}"),
            owner_id="paper",
        )
        assert entry.status is PaperEntryStatus.FILLED
        before = store.load_state()
        position_before = before.positions[scope().position_key]
        count_before_resolution = store.event_count()
        times = iter((NOW + timedelta(days=1), NOW + timedelta(days=1, seconds=1)))
        worker = ResolutionWorker(
            store=store,
            source=StaticPaperResolutionSource(Decimal(yes), Decimal(no)),
            context_provider=StoredDecisionContextProvider(),
            clock=lambda: next(times),
        )
        report = worker.run_once()

        assert report.checked == 1
        assert report.items[0].status is expected_status
        assert report.items[0].positions_settled == 1
        after = store.load_state()
        settled = after.positions[scope().position_key]
        payout = Decimal(yes)
        expected_gross = money_from_unit_price(
            payout,
            position_before.quantity,
            before.currency,
        )
        assert settled.status is PositionStatus.SETTLED
        assert settled.quantity == 0
        assert settled.realized_pnl == expected_gross - position_before.cost_basis
        assert after.cash == before.cash + expected_gross
        count_after_resolution = store.event_count()
        assert count_after_resolution == count_before_resolution + 3

    with initialize_paper_store(database, starting_cash=Money.of("100")) as restarted:
        second_times = iter(
            (
                NOW + timedelta(days=1, minutes=1),
                NOW + timedelta(days=1, minutes=1, seconds=1),
            )
        )
        second = ResolutionWorker(
            store=restarted,
            source=StaticPaperResolutionSource(Decimal(yes), Decimal(no)),
            context_provider=StoredDecisionContextProvider(),
            clock=lambda: next(second_times),
        ).run_once()

        assert second.checked == 0
        assert second.settled_positions == 0
        assert restarted.event_count() == count_after_resolution
        restarted.verify_integrity()
