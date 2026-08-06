"""CLI entry point for one-shot or continuous resolution monitoring."""

from __future__ import annotations

import argparse
from pathlib import Path

from weatherbot.persistence import SQLiteEventStore
from weatherbot.resolution.context import StoredDecisionContextProvider
from weatherbot.resolution.gamma import (
    GammaResolutionSource,
    RequestsGammaResolutionTransport,
)
from weatherbot.resolution.model import ResolutionCycleReport
from weatherbot.resolution.monitor import ResolutionMonitor
from weatherbot.resolution.worker import ResolutionWorker


def _print_report(report: ResolutionCycleReport) -> None:
    print(
        f"Resolution cycle: checked={report.checked} resolved={report.resolved} "
        f"voided={report.voided} settled={report.settled_positions}"
    )
    for item in report.items:
        print(
            f"  {item.market_id}: {item.status.value} | {item.reason} | "
            f"events={item.events_appended} settled={item.positions_settled}"
        )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Resolve pending ledger positions")
    parser.add_argument(
        "--database",
        default="state/ledger.sqlite3",
        help="path to the durable SQLite ledger",
    )
    parser.add_argument(
        "--interval",
        type=float,
        default=600.0,
        help="seconds between resolution cycles",
    )
    parser.add_argument("--once", action="store_true", help="run one cycle and exit")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    path = Path(args.database)
    if not path.exists():
        print(f"No ledger database exists at {path}")
        return 0
    with SQLiteEventStore(path) as store:
        worker = ResolutionWorker(
            store=store,
            source=GammaResolutionSource(RequestsGammaResolutionTransport()),
            context_provider=StoredDecisionContextProvider(),
        )
        monitor = ResolutionMonitor(
            worker=worker,
            interval_seconds=args.interval,
            on_report=_print_report,
        )
        if args.once:
            monitor.run_once()
        else:
            monitor.run_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
