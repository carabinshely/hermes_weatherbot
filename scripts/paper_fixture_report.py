#!/usr/bin/env python3
"""Generate deterministic #27 paper-money completion evidence as JSON."""

from __future__ import annotations

import argparse
import json
import tempfile
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

from tests.paper.helpers import entry_request, paper_book, scope
from tests.paper.test_resolution import StaticPaperResolutionSource
from tests.quoting.helpers import NOW
from weatherbot.domain import Money
from weatherbot.paper import PaperEntryStatus, PaperTradingService, initialize_paper_store
from weatherbot.persistence.codec import event_type_name
from weatherbot.resolution import ResolutionWorker, StoredDecisionContextProvider


def _state_summary(state) -> dict[str, object]:
    positions = []
    for key, position in sorted(
        state.positions.items(),
        key=lambda item: (str(item[0][0]), str(item[0][1])),
    ):
        positions.append(
            {
                "market_id": str(key[0]),
                "outcome_id": str(key[1]),
                "quantity": format(position.quantity, "f"),
                "cost_basis": format(position.cost_basis.amount, "f"),
                "realized_pnl": format(position.realized_pnl.amount, "f"),
                "status": position.status.value,
            }
        )
    return {
        "cash": format(state.cash.amount, "f"),
        "reserved_cash": format(state.reserved_cash.amount, "f"),
        "available_cash": format(state.available_cash.amount, "f"),
        "positions": positions,
        "orders": len(state.orders),
    }


def _events(store) -> list[str]:
    return [event_type_name(event) for event in store.load_events()]


def _settled_scenario(root: Path, label: str, yes: str, no: str) -> dict[str, object]:
    database = root / f"{label}.sqlite3"
    with initialize_paper_store(database, starting_cash=Money.of("100"), opened_at=NOW) as store:
        service = PaperTradingService(store, clock=lambda: NOW)
        entry = service.submit_entry(
            entry_request(decision_id=f"report-{label}"),
            owner_id="paper-report",
        )
        worker = ResolutionWorker(
            store=store,
            source=StaticPaperResolutionSource(Decimal(yes), Decimal(no)),
            context_provider=StoredDecisionContextProvider(),
            clock=lambda: NOW + timedelta(days=1),
        )
        resolution = worker.run_once()
        store.verify_integrity()
        state = store.load_state()
        return {
            "entry_status": entry.status.value,
            "resolution_checked": resolution.checked,
            "settled_positions": resolution.settled_positions,
            "integrity": "verified",
            "events": _events(store),
            "state": _state_summary(state),
        }


def _execution_scenario(
    root: Path,
    label: str,
    *,
    first_size: str,
    expected_status: PaperEntryStatus,
) -> dict[str, object]:
    database = root / f"{label}.sqlite3"
    execution_book = paper_book(
        first_ask="0.40",
        first_ask_size=first_size,
        second_ask="0.60",
        second_ask_size="100",
        book_hash=f"report-{label}-book",
    )
    with initialize_paper_store(database, starting_cash=Money.of("100"), opened_at=NOW) as store:
        entry = PaperTradingService(store, clock=lambda: NOW).submit_entry(
            entry_request(
                decision_id=f"report-{label}",
                execution_book=execution_book,
            ),
            owner_id="paper-report",
        )
        if entry.status is not expected_status:
            raise RuntimeError(
                f"{label} expected {expected_status.value}, got {entry.status.value}"
            )
        store.verify_integrity()
        return {
            "entry_status": entry.status.value,
            "integrity": "verified",
            "events": _events(store),
            "state": _state_summary(store.load_state()),
            "execution": (
                None
                if entry.execution_plan is None
                else {
                    "status": entry.execution_plan.status.value,
                    "requested_quantity": format(entry.execution_plan.requested_quantity, "f"),
                    "filled_quantity": format(entry.execution_plan.filled_quantity, "f"),
                    "book_hash": entry.execution_plan.order_book_hash,
                    "fee": format(entry.execution_plan.fee.amount, "f"),
                }
            ),
        }


def build_report(root: Path) -> dict[str, object]:
    scenarios = {
        "winning": _settled_scenario(root, "winning", "1", "0"),
        "losing": _settled_scenario(root, "losing", "0", "1"),
        "voided": _settled_scenario(root, "voided", "0.5", "0.5"),
        "rejected": _execution_scenario(
            root,
            "rejected",
            first_size="0.5",
            expected_status=PaperEntryStatus.EXECUTION_REJECTED,
        ),
        "depth_limited": _execution_scenario(
            root,
            "depth-limited",
            first_size="2",
            expected_status=PaperEntryStatus.PARTIAL_FILL,
        ),
    }
    return {
        "report": "hermes_weatherbot paper-money completion evidence",
        "generated_from": "deterministic fixtures; no live orders or wallet access",
        "starting_cash": "100",
        "position_key": [str(scope().position_key[0]), str(scope().position_key[1])],
        "scenarios": scenarios,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, help="optional JSON output path")
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="weatherbot-paper-report-") as temporary:
        report = build_report(Path(temporary))
    encoded = json.dumps(report, indent=2, sort_keys=True)
    if args.output is not None:
        args.output.write_text(encoded + "\n", encoding="utf-8")
    print(encoded)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
